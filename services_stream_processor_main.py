# Stream Processor: Kafka Consumer → Features Computation → Redis/ClickHouse
# Consumes canonical events and computes features for ML and rules

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError
import redis
import requests
from collections import defaultdict
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_GROUP_ID = "stream-processor"
REDIS_URL = os.getenv("REDIS_URL", "redis://:devpass@redis:6379/0")
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", 9000))

# Connect to Redis and Kafka
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Kafka Consumer for canonical events
consumer = KafkaConsumer(
    'canonical.transactions',
    'canonical.bets',
    'canonical.device_events',
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    group_id=KAFKA_GROUP_ID,
    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
    auto_offset_reset='earliest',
    enable_auto_commit=False,
    max_poll_records=100
)

# Kafka Producer for publishing features
producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    acks='all',
    retries=3
)

# ===================== FEATURE COMPUTATION =====================

class FeatureComputer:
    """Computes player features from streaming events."""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.transaction_buffer = defaultdict(list)
        self.bet_buffer = defaultdict(list)
    
    def add_transaction(self, tenant_id: str, player_id: str, transaction: Dict[str, Any]):
        """Buffer transaction for aggregation."""
        key = f"{tenant_id}:{player_id}:txn_buffer"
        self.redis.lpush(key, json.dumps(transaction))
        self.redis.expire(key, 86400)  # 24h TTL
    
    def add_bet(self, tenant_id: str, player_id: str, bet: Dict[str, Any]):
        """Buffer bet for aggregation."""
        key = f"{tenant_id}:{player_id}:bet_buffer"
        self.redis.lpush(key, json.dumps(bet))
        self.redis.expire(key, 86400)
    
    def compute_features(self, tenant_id: str, player_id: str) -> Dict[str, Any]:
        """Compute all features for a player at current time."""
        features = {
            "tenant_id": tenant_id,
            "player_id": player_id,
            "computed_at": datetime.utcnow().isoformat(),
        }
        
        now = datetime.utcnow()
        window_24h = now - timedelta(hours=24)
        window_7d = now - timedelta(days=7)
        window_30d = now - timedelta(days=30)
        
        # Get buffered transactions
        txn_key = f"{tenant_id}:{player_id}:txn_buffer"
        txn_list = self.redis.lrange(txn_key, 0, -1)
        transactions = [json.loads(t) for t in txn_list] if txn_list else []
        
        # Get buffered bets
        bet_key = f"{tenant_id}:{player_id}:bet_buffer"
        bet_list = self.redis.lrange(bet_key, 0, -1)
        bets = [json.loads(b) for b in bet_list] if bet_list else []
        
        # ========== DEPOSIT FEATURES ==========
        deposits_24h = [t for t in transactions if t.get('type') == 'DEPOSIT' and self._parse_datetime(t.get('occurredAt')) > window_24h]
        deposits_7d = [t for t in transactions if t.get('type') == 'DEPOSIT' and self._parse_datetime(t.get('occurredAt')) > window_7d]
        deposits_30d = [t for t in transactions if t.get('type') == 'DEPOSIT' and self._parse_datetime(t.get('occurredAt')) > window_30d]
        
        features['deposit_sum_24h'] = sum(float(t.get('amount', 0)) for t in deposits_24h)
        features['deposit_sum_7d'] = sum(float(t.get('amount', 0)) for t in deposits_7d)
        features['deposit_sum_30d'] = sum(float(t.get('amount', 0)) for t in deposits_30d)
        features['deposit_count_24h'] = len(deposits_24h)
        features['deposit_count_7d'] = len(deposits_7d)
        
        # ========== WITHDRAWAL FEATURES ==========
        withdrawals_24h = [t for t in transactions if t.get('type') == 'WITHDRAWAL' and self._parse_datetime(t.get('occurredAt')) > window_24h]
        withdrawals_7d = [t for t in transactions if t.get('type') == 'WITHDRAWAL' and self._parse_datetime(t.get('occurredAt')) > window_7d]
        withdrawals_30d = [t for t in transactions if t.get('type') == 'WITHDRAWAL' and self._parse_datetime(t.get('occurredAt')) > window_30d]
        
        features['withdrawal_sum_24h'] = sum(float(t.get('amount', 0)) for t in withdrawals_24h)
        features['withdrawal_sum_7d'] = sum(float(t.get('amount', 0)) for t in withdrawals_7d)
        features['withdrawal_sum_30d'] = sum(float(t.get('amount', 0)) for t in withdrawals_30d)
        features['withdrawal_count_24h'] = len(withdrawals_24h)
        features['withdrawal_count_7d'] = len(withdrawals_7d)
        
        # ========== BET FEATURES ==========
        bets_24h = [b for b in bets if self._parse_datetime(b.get('placedAt')) > window_24h]
        bets_7d = [b for b in bets if self._parse_datetime(b.get('placedAt')) > window_7d]
        
        features['bet_stake_sum_24h'] = sum(float(b.get('stakeAmount', 0)) for b in bets_24h)
        features['bet_stake_sum_7d'] = sum(float(b.get('stakeAmount', 0)) for b in bets_7d)
        features['bet_count_24h'] = len(bets_24h)
        features['bet_count_7d'] = len(bets_7d)
        
        # ========== RATIO FEATURES ==========
        deposit_sum_7d = features['deposit_sum_7d']
        withdrawal_sum_7d = features['withdrawal_sum_7d']
        features['ratio_withdrawal_to_deposit_7d'] = (
            withdrawal_sum_7d / deposit_sum_7d if deposit_sum_7d > 0 else 0.0
        )
        
        # ========== BASELINE & ZSCORE ==========
        baseline_key = f"{tenant_id}:{player_id}:baseline_avg_daily_deposit"
        baseline_avg = float(self.redis.get(baseline_key) or 0)
        features['baseline_avg_daily_deposit'] = baseline_avg
        
        stddev_key = f"{tenant_id}:{player_id}:baseline_stddev_deposit"
        baseline_stddev = float(self.redis.get(stddev_key) or 1.0)
        features['baseline_stddev_deposit'] = baseline_stddev
        
        current_daily_avg = features['deposit_sum_24h'] / 1.0  # Simplified
        zscore = (current_daily_avg - baseline_avg) / baseline_stddev if baseline_stddev > 0 else 0.0
        features['zscore_current_deposit_vs_baseline'] = zscore
        
        # ========== FLAG FEATURES ==========
        features['new_payment_instrument_flag'] = 1 if len([t for t in transactions if t.get('paymentInstrument', {}).get('new')]) > 0 else 0
        features['new_device_flag'] = 1 if len([t for t in transactions if t.get('newDevice')]) > 0 else 0
        
        # ========== CORRELATION FEATURES ==========
        # Placeholder: these would require querying across players
        features['shared_device_count'] = 1  # TODO: compute from device table
        features['shared_bank_account_count'] = 1  # TODO: compute from payment instruments
        
        return features
    
    def _parse_datetime(self, dt_str: str) -> datetime:
        """Parse ISO datetime string."""
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except:
            return datetime.utcnow()


# ===================== MAIN LOOP =====================

def main():
    """Main streaming processing loop."""
    logger.info("Stream Processor starting...")
    
    feature_computer = FeatureComputer(redis_client)
    
    # Wait for Kafka to be ready
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
    
    logger.info("Consuming events from Kafka...")
    
    try:
        for message in consumer:
            try:
                event = message.value
                
                tenant_id = event.get('tenantId')
                player_id = event.get('payload', {}).get('playerId') or event.get('payload', {}).get('playerCpf')
                entity_type = event.get('entityType')
                
                if not tenant_id or not player_id:
                    logger.warning(f"Skipping event without tenant_id or player_id: {event}")
                    continue
                
                # Route by entity type
                if entity_type == 'TRANSACTION':
                    feature_computer.add_transaction(str(tenant_id), str(player_id), event.get('payload'))
                elif entity_type == 'BET':
                    feature_computer.add_bet(str(tenant_id), str(player_id), event.get('payload'))
                
                # Compute features periodically (every 5 seconds or 100 events)
                if message.offset % 100 == 0:
                    features = feature_computer.compute_features(str(tenant_id), str(player_id))
                    
                    # Store in Redis (online features)
                    redis_key = f"{tenant_id}:{player_id}:features"
                    redis_client.setex(redis_key, 3600, json.dumps(features))
                    
                    # Publish to features topic
                    producer.send('features.player_daily', value={
                        'tenant_id': str(tenant_id),
                        'player_id': str(player_id),
                        'features': features,
                        'timestamp': datetime.utcnow().isoformat()
                    })
                    
                    logger.debug(f"Computed features for {player_id}: {features}")
                
                # Commit offset
                consumer.commit()
                
            except Exception as e:
                logger.error(f"Error processing message: {e}", exc_info=True)
                continue
    
    except KeyboardInterrupt:
        logger.info("Stream Processor shutting down...")
    finally:
        consumer.close()
        producer.close()
        redis_client.close()

if __name__ == "__main__":
    main()
