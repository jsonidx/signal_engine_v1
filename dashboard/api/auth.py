"""
dashboard/api/auth.py — Authentication middleware for Signal Engine API
========================================================================
Supports two auth methods (FastAPI dependency injection):

  1. Supabase JWT   — Authorization: Bearer <supabase-jwt>
                      Verified with SUPABASE_JWT_SECRET from .env
                      User ID extracted from JWT sub claim

  2. API Key        — X-API-Key: <raw-key>
                      SHA-256 hash looked up in Supabase user_api_keys table
                      For programmatic access / sharing with friends

Usage in routes
---------------
    from dashboard.api.auth import get_current_user, AuthUser

    @app.get("/api/portfolio/summary")
    async def portfolio(user: AuthUser = Depends(get_current_user)):
        # user.user_id is guaranteed to be a valid UUID string
        ...

    # Optional auth (backward compat — returns None if not authenticated)
    @app.get("/api/signals/latest")
    async def signals(user = Depends(get_optional_user)):
        ...

Environment variables required
-------------------------------
    SUPABASE_JWT_SECRET   — from Supabase dashboard → Settings → API → JWT Secret
    (optional) SUPABASE_URL, SUPABASE_ANON_KEY — for client-side auth flows
"""

import hashlib
import logging
import os
import secrets
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security schemes (FastAPI picks these up for OpenAPI docs)
# ---------------------------------------------------------------------------
_JWT_SCHEME = HTTPBearer(auto_error=False)
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# ---------------------------------------------------------------------------
# Supabase JWT secret (set in .env → SUPABASE_JWT_SECRET)
# ---------------------------------------------------------------------------
_JWT_SECRET: str = os.getenv("SUPABASE_JWT_SECRET", "")


# ---------------------------------------------------------------------------
# Auth user model
# ---------------------------------------------------------------------------

class AuthUser:
    """Authenticated request context passed to route handlers."""

    def __init__(self, user_id: str, email: str = "", auth_method: str = "jwt"):
        self.user_id = user_id          # Supabase auth.uid() — UUID string
        self.email = email
        self.auth_method = auth_method  # "jwt" | "api_key"

    def __repr__(self) -> str:
        return f"AuthUser(id={self.user_id[:8]}…, method={self.auth_method})"


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------

def _verify_jwt(token: str) -> AuthUser:
    """
    Decode and verify a Supabase JWT token.
    Raises HTTPException(401) on invalid/expired tokens.
    Raises HTTPException(500) if SUPABASE_JWT_SECRET is not configured.
    """
    if not _JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_JWT_SECRET not configured — add it to .env",
        )
    try:
        import jwt as pyjwt
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PyJWT not installed — run: pip install PyJWT",
        )
    try:
        payload = pyjwt.decode(
            token,
            _JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return AuthUser(
            user_id=payload["sub"],
            email=payload.get("email", ""),
            auth_method="jwt",
        )
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired — please log in again",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# API key verification
# ---------------------------------------------------------------------------

def _verify_api_key(raw_key: str) -> Optional[AuthUser]:
    """
    Look up an API key in Supabase user_api_keys by its SHA-256 hash.
    Returns AuthUser on success, None if key not found or revoked.
    Updates last_used timestamp on successful auth.
    """
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT id, user_id, email FROM user_api_keys
               WHERE key_hash = %s AND revoked = FALSE""",
            (key_hash,),
        )
        row = cur.fetchone()
        if row:
            # Bump last_used (non-blocking — ignore failure)
            try:
                cur.execute(
                    "UPDATE user_api_keys SET last_used = %s WHERE id = %s",
                    (datetime.utcnow().isoformat(), row["id"]),
                )
                conn.commit()
            except Exception:
                pass
        conn.close()
        if row:
            return AuthUser(
                user_id=str(row["user_id"]),
                email=row.get("email") or "",
                auth_method="api_key",
            )
    except Exception as exc:
        logger.warning("API key lookup failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    jwt_creds: Optional[HTTPAuthorizationCredentials] = Security(_JWT_SCHEME),
    api_key: Optional[str] = Security(_API_KEY_HEADER),
) -> AuthUser:
    """
    FastAPI dependency — require authentication.
    Accepts: Authorization: Bearer <jwt>  OR  X-API-Key: <key>
    Raises HTTP 401 if neither is provided or both are invalid.
    """
    if jwt_creds and jwt_creds.credentials:
        return _verify_jwt(jwt_creds.credentials)

    if api_key:
        user = _verify_api_key(api_key)
        if user:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required — provide Bearer JWT or X-API-Key header",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_user(
    jwt_creds: Optional[HTTPAuthorizationCredentials] = Security(_JWT_SCHEME),
    api_key: Optional[str] = Security(_API_KEY_HEADER),
) -> Optional[AuthUser]:
    """
    FastAPI dependency — optional authentication.
    Returns AuthUser if credentials provided and valid, None otherwise.
    Use for read-only routes that work for both logged-in and anonymous users.
    """
    try:
        return await get_current_user(jwt_creds, api_key)
    except HTTPException:
        return None


# ---------------------------------------------------------------------------
# API key generation helpers (used by management endpoints)
# ---------------------------------------------------------------------------

def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.
    Returns (raw_key, key_hash, key_prefix).
    Store key_hash in DB — never store raw_key.
    Show raw_key to user once (it cannot be recovered).
    """
    raw_key = "se_" + secrets.token_hex(32)          # 67-char key, "se_" prefix
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:11]                          # "se_" + 8 chars
    return raw_key, key_hash, key_prefix


def create_api_key(user_id: str, email: str = "", name: str = "") -> dict:
    """
    Create and persist a new API key for user_id.
    Returns dict with raw_key (show once), key_prefix, id, created_at.
    """
    raw_key, key_hash, key_prefix = generate_api_key()
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO user_api_keys (user_id, email, key_hash, key_prefix, name, created_at)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at""",
            (user_id, email, key_hash, key_prefix, name or "Default key",
             datetime.utcnow().isoformat()),
        )
        row = cur.fetchone()
        conn.commit()
        conn.close()
        return {
            "id": row["id"],
            "raw_key": raw_key,     # show once — user must copy this now
            "key_prefix": key_prefix,
            "name": name or "Default key",
            "created_at": str(row["created_at"]),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create API key: {exc}")


def revoke_api_key(key_id: int, user_id: str) -> bool:
    """Revoke an API key. Only the owning user can revoke their own keys."""
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """UPDATE user_api_keys SET revoked = TRUE, revoked_at = %s
               WHERE id = %s AND user_id = %s AND revoked = FALSE""",
            (datetime.utcnow().isoformat(), key_id, user_id),
        )
        updated = cur.rowcount
        conn.commit()
        conn.close()
        return updated > 0
    except Exception:
        return False
