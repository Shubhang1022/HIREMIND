"""Supabase JWT authentication for FastAPI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

security = HTTPBearer(auto_error=False)


@dataclass
class AuthUser:
    id: str
    email: str
    role: str = "recruiter"


def _decode_jwt(token: str) -> dict:
    """Decode Supabase JWT. In production, verify with SUPABASE_JWT_SECRET."""
    secret = settings.supabase_jwt_secret
    try:
        if secret:
            return jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")
        # Development: decode without verification
        return jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> AuthUser:
    token = None
    if credentials:
        token = credentials.credentials
    elif "authorization" in request.headers:
        parts = request.headers["authorization"].split()
        if len(parts) == 2:
            token = parts[1]

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = _decode_jwt(token)
    user_id = payload.get("sub")
    email = payload.get("email", "")

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    return AuthUser(
        id=user_id,
        email=email,
        role=payload.get("role", "recruiter"),
    )



async def get_optional_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[AuthUser]:
    if not credentials:
        return None

    try:
        return await get_current_user(
            request=request,
            credentials=credentials
        )
    except HTTPException:
        return None

import inspect
print("AUTH CHECK")
print("get_current_user signature:", inspect.signature(get_current_user))
print("get_optional_user signature:", inspect.signature(get_optional_user))