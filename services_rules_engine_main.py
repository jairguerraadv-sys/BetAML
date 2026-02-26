# Rules Engine: Kafka Consumer → DSL Evaluation → Alerts
# Consumes canonical events + features and evaluates DSL rules

import os
import json
import logging
import sys
from datetime import datetime
from typing import Dict, Any
from kafka import KafkaConsumer, KafkaProducer
import redis
import time
from uuid import uuid4

# Add libs to path
sys.path.append('/app/..')

from libs_dsl_parser import parse_rule_dsl, evaluate_rule
from libs_schemas import CanonicalEventEnvelope

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_GROUP_ID = "rules-engine"
REDIS_URL = os.getenv("REDIS_URL", "redis://:devpass@redis:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")

# Connections
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

consumer = KafkaConsumer(
    'canonical.transactions',
    'canonical.bets',
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    group_id=KAFKA_GROUP_ID,
    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
    auto_offset_reset='earliest',
    enable_auto_commit=False,
    max_poll_records=50
)

producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    acks='all',
    retries=3
)

# ===================== RULE CACHE =====================

def load_active_rules(tenant_id: str) -> Dict[str, Dict[str, Any]]:
    """Load active rules for tenant from Redis cache."""
    cache_key = f"rules:active:{tenant_id}"
    cached = redis_client.get(cache_key)
    
    if cached:
        return json.loads(cached)
    
    # TODO: Load from PostgreSQL and cache
    return {}

# ===================== ALERT GENERATION =====================

class AlertGenerator:
    """Generates alerts when rules match."""
    
    def __init__(self, producer):
        self.producer = producer
    
    def generate_alert(
        self,
        tenant_id: str,
        player_id: str,
        player_cpf: str,
        rule_id: str,
        rule_name: str,
        severity: str,
        evidence: Dict[str, Any]
    ):
        """Create and publish alert."""
        alert = {
            'id': str(uuid4()),
            'tenant_id': tenant_id,
            'player_id': player_id,
            'player_cpf': player_cpf,
            'severity': severity,
            'status': 'NEW',
            'alert_type': 'RULE',
            'rule_id': rule_id,
            'evidence': evidence,
            'created_at': datetime.utcnow().isoformat()
        }
        
        self.producer.send('scoring.alerts', value=alert)
        logger.info(f"Alert generated: {alert['id']} for player {player_id} (rule: {rule_name})")
        
        return alert

# ===================== DEFAULT RULES =======================

DEFAULT_RULES = {
    "spike_vs_baseline": {
        "id": "rule-spike-001",
        "name": "Spike vs Baseline",
        "dsl": "features.zscore_current_deposit_vs_baseline > 3.0 AND features.deposit_sum_24h > 5000",
        "severity": "HIGH",
        "category": "SPIKE"
    },
    "structuring": {
        "id": "rule-struct-001",
        "name": "Structuring Pattern",
        "dsl": "features.deposit_count_24h >= 5 AND features.deposit_sum_24h > 3000",
        "severity": "MEDIUM",
        "category": "STRUCTURING"
    },
    "rapid_withdrawal": {
        "id": "rule-rapid-001",
        "name": "Rapid Withdrawal After Deposit",
        "dsl": "features.withdrawal_sum_24h > 0 AND features.deposit_sum_24h > 0",
        "severity": "MEDIUM",
        "category": "RAPID_WITHDRAWAL"
    },
    "high_withdrawal_ratio": {
        "id": "rule-ratio-001",
        "name": "High Withdrawal/Deposit Ratio",
        "dsl": "features.ratio_withdrawal_to_deposit_7d > 0.8",
        "severity": "HIGH",
        "category": "HIGH_RATIO"
    },
    "pep_risk": {
        "id": "rule-pep-001",
        "name": "PEP with High Deviation",
        "dsl": "player.pepFlag = true AND features.zscore_current_deposit_vs_baseline > 2.0",
        "severity": "CRITICAL",
        "category": "PEP_RISK"
    }
}

# ===================== MAIN LOOP =====================

def main():
    """Main rules engine processing loop."""
    logger.info("Rules Engine starting...")
    
    alert_gen = AlertGenerator(producer)
    
    # Wait for Kafka
    max_retries = 30
    for attempt in range(max_retries):
        try:
            consumer.poll(timeout_ms=1000)
            logger.info("✓ Connected to Kafka")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Waiting for Kafka... ({attempt+1}/{max_retries})")
                time.sleep(1)
            else:
                logger.error(f"Failed to connect to Kafka: {e}")
                return
    
    logger.info("Evaluating rules from canonical events...")
    
    try:
        for message in consumer:
            try:
                event = message.value
                
                tenant_id = event.get('tenantId')
                entity_type = event.get('entityType')
                payload = event.get('payload', {})
                
                if not tenant_id:
                    continue
                
                player_id = payload.get('playerId') or payload.get('playerCpf')
                
                if not player_id:
                    logger.debug(f"Skipping event without player ID")
                    continue
                
                # Load features for player (from Redis cache)
                features_key = f"{tenant_id}:{player_id}:features"
                features_json = redis_client.get(features_key)
                features = json.loads(features_json) if features_json else {}
                
                # Build evaluation context
                context = {
                    'transaction': payload if entity_type == 'TRANSACTION' else {},
                    'bet': payload if entity_type == 'BET' else {},
                    'features': features,
                    'player': {},  # TODO: load from DB
                    'event': event
                }
                
                # Load tenant's active rules
                rules = load_active_rules(tenant_id) or DEFAULT_RULES
                
                # Evaluate each rule
                for rule_key, rule in rules.items():
                    try:
                        dsl_expression = rule.get('dsl')
                        if not dsl_expression:
                            continue
                        
                        # Parse and evaluate DSL
                        result = evaluate_rule(dsl_expression, context)
                        
                        if result:
                            # Rule matched! Generate alert
                            alert_gen.generate_alert(
                                tenant_id=str(tenant_id),
                                player_id=str(player_id),
                                player_cpf=payload.get('cpf') or payload.get('playerCpf'),
                                rule_id=rule.get('id'),
                                rule_name=rule.get('name'),
                                severity=rule.get('severity', 'MEDIUM'),
                                evidence={
                                    'triggered_rule': rule.get('name'),
                                    'features': features,
                                    'event_type': entity_type,
                                    'dsl_expression': dsl_expression
                                }
                            )
                    
                    except Exception as e:
                        logger.error(f"Error evaluating rule {rule_key}: {e}")
                        continue
                
                # Commit offset
                consumer.commit()
                
            except Exception as e:
                logger.error(f"Error processing message: {e}", exc_info=True)
                continue
    
    except KeyboardInterrupt:
        logger.info("Rules Engine shutting down...")
    finally:
        consumer.close()
        producer.close()
        redis_client.close()

if __name__ == "__main__":
    main()
