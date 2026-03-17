-- Migration v15: Refresh Token Rotation Support
-- Adds refresh_token_jti column to users table for secure token rotation with 7-day sliding window

BEGIN;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS refresh_token_jti TEXT;

CREATE INDEX IF NOT EXISTS idx_users_refresh_token_jti ON users(refresh_token_jti);

COMMENT ON COLUMN users.refresh_token_jti IS 'JTI do refresh token ativo (rotacionado a cada /auth/refresh, NULL quando revogado no logout)';

COMMIT;
