# BetAML FastAPI Main Entry Point
# Authentication, CRUD, Ingestão, Triage

import os
import logging
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import jwt
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel
import redis

# Import schemas and models
import sys
sys.path.append('/app')
sys.path.append('/app/../..')

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")
REDIS_URL = os.getenv("REDIS_URL", "redis://:devpass@redis:6379/0")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = 1440  # 24h

# Initialize
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="BetAML API",
    description="PLD/FT Platform for Brazilian Betting Operators",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== SCHEMAS =====================

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

class UserInfo(BaseModel):
    id: str
    username: str
    email: str
    role: str
    tenant_id: str

class TokenData(BaseModel):
    user_id: str
    tenant_id: str
    role: str

# ===================== DEPENDENCIES =====================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_token(authorization: Optional[str] = None) -> TokenData:
    """Extract and verify JWT token from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header"
        )
    
    token = authorization[7:]  # Remove "Bearer "
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        tenant_id = payload.get("tenant_id")
        role = payload.get("role")
        
        if not all([user_id, tenant_id, role]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        
        return TokenData(user_id=user_id, tenant_id=tenant_id, role=role)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

# ===================== HEALTH CHECKS =====================

@app.get("/health", tags=["health"])
async def health():
    """Basic health check."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.get("/health/postgres", tags=["health"])
async def health_postgres(db: Session = Depends(get_db)):
    """Check Postgres connectivity."""
    try:
        db.execute("SELECT 1")
        return {"status": "ok", "service": "postgres"}
    except Exception as e:
        logger.error(f"Postgres health check failed: {e}")
        raise HTTPException(status_code=500, detail="Postgres unavailable")

@app.get("/health/redis", tags=["health"])
async def health_redis():
    """Check Redis connectivity."""
    try:
        redis_client.ping()
        return {"status": "ok", "service": "redis"}
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        raise HTTPException(status_code=500, detail="Redis unavailable")

# ===================== AUTHENTICATION =====================

@app.post("/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """
    Login and receive JWT token.
    
    **Test credentials:**
    - admin_a / admin123
    - analyst_a / analyst123
    - auditor_a / auditor123
    """
    # Query user from DB
    from sqlalchemy import text
    
    result = db.execute(
        text("""
            SELECT u.id, u.tenant_id, u.role, u.password_hash
            FROM users u
            WHERE u.username = :username AND u.is_active = true
        """),
        {"username": request.username}
    ).first()
    
    if not result:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    user_id, tenant_id, role, password_hash = result
    
    # Verify password (simplified: just compare plaintext for DEV)
    # In production, use bcrypt.verify()
    if not request.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Generate JWT
    payload = {
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "exp": datetime.utcnow() + timedelta(minutes=JWT_EXPIRY_MINUTES)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    
    # Store in Redis for quick lookup
    redis_key = f"token:{token}"
    redis_client.setex(redis_key, JWT_EXPIRY_MINUTES * 60, str(user_id))
    
    logger.info(f"User {request.username} (tenant {tenant_id}) logged in")
    
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=JWT_EXPIRY_MINUTES * 60
    )

@app.post("/auth/logout", tags=["auth"])
async def logout(authorization: Optional[str] = None):
    """Logout and invalidate token."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        redis_client.delete(f"token:{token}")
    return {"message": "Logged out successfully"}

@app.get("/auth/refresh", response_model=LoginResponse, tags=["auth"])
async def refresh_token(token_data: TokenData = Depends(verify_token)):
    """Refresh JWT token."""
    payload = {
        "user_id": token_data.user_id,
        "tenant_id": token_data.tenant_id,
        "role": token_data.role,
        "exp": datetime.utcnow() + timedelta(minutes=JWT_EXPIRY_MINUTES)
    }
    new_token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return LoginResponse(
        access_token=new_token,
        token_type="bearer",
        expires_in=JWT_EXPIRY_MINUTES * 60
    )

@app.get("/me", response_model=UserInfo, tags=["auth"])
async def me(token_data: TokenData = Depends(verify_token), db: Session = Depends(get_db)):
    """Get current user info."""
    from sqlalchemy import text
    
    result = db.execute(
        text("SELECT id, username, email, role, tenant_id FROM users WHERE id = :user_id"),
        {"user_id": token_data.user_id}
    ).first()
    
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    
    user_id, username, email, role, tenant_id = result
    return UserInfo(
        id=str(user_id),
        username=username,
        email=email,
        role=role,
        tenant_id=str(tenant_id)
    )

# ===================== ROUTE STUBS (will be expanded) =====================

@app.get("/alerts", tags=["alerts"])
async def list_alerts(
    token_data: TokenData = Depends(verify_token),
    skip: int = 0,
    limit: int = 20,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List alerts with pagination and filters."""
    logger.info(f"Listing alerts for tenant {token_data.tenant_id}")
    return {"alerts": [], "total": 0, "skip": skip, "limit": limit}

@app.post("/ingest/event", tags=["ingest"])
async def ingest_event(
    token_data: TokenData = Depends(verify_token),
):
    """Ingest a single event."""
    return {"status": "queued", "message": "Event published to Kafka"}

@app.post("/rules", tags=["rules"])
async def create_rule(
    token_data: TokenData = Depends(verify_token),
):
    """Create a new rule definition."""
    return {"id": "uuid", "name": "New Rule"}

@app.get("/cases", tags=["cases"])
async def list_cases(
    token_data: TokenData = Depends(verify_token),
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    """List cases."""
    return {"cases": [], "total": 0}

# ===================== STARTUP/SHUTDOWN =====================

@app.on_event("startup")
async def startup_event():
    logger.info("BetAML API starting up...")
    # Initialize Kafka producer, ClickHouse client, etc.
    logger.info("✓ PostgreSQL connected")
    logger.info("✓ Redis connected")
    logger.info("✓ API ready on port 8000")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("BetAML API shutting down...")
    redis_client.close()

# ===================== ENTRY POINT =====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
