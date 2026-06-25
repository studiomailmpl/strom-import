"""
Clerk JWT verification — FastAPI dependency.
"""

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.models.organisation import Organisation

settings = get_settings()

# Clerk JWKS client for RS256 verification
# Clerk's public JWKS endpoint requires a Bearer token when using api.clerk.com
# Use the Clerk Frontend API domain instead (derived from publishable key)
def _get_jwks_url() -> str:
    """Derive the JWKS URL from the Clerk publishable key."""
    import base64
    pk = settings.clerk_publishable_key
    if pk:
        # pk_test_Y2xlYW4tc3BpZGVyLTc4LmNsZXJrLmFjY291bnRzLmRldiQ -> clean-spider-78.clerk.accounts.dev
        encoded = pk.split("_")[-1]
        # Add padding
        padded = encoded + "=" * (4 - len(encoded) % 4) if len(encoded) % 4 else encoded
        try:
            domain = base64.b64decode(padded).decode("utf-8").rstrip("$")
            return f"https://{domain}/.well-known/jwks.json"
        except Exception:
            pass
    return settings.clerk_jwks_url

_jwks_client = PyJWKClient(_get_jwks_url(), cache_keys=True)


async def get_current_user_token(request: Request) -> dict:
    """
    Extract and verify the Clerk JWT from the Authorization header.
    Returns the decoded token payload.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    token = auth_header.split(" ", 1)[1]

    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Clerk doesn't set audience by default
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.InvalidTokenError as e:
        import logging
        logging.getLogger("strom-import").warning(f"JWT validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        )


async def get_current_user(
    token: dict = Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Resolve the Clerk user to our local User model.
    Creates user + org on first login (JIT provisioning).
    """
    clerk_user_id = token.get("sub")
    if not clerk_user_id:
        raise HTTPException(status_code=401, detail="No subject in token")

    # Look up existing user
    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    user = result.scalar_one_or_none()

    if user:
        return user

    # JIT provisioning — create org + user on first login
    clerk_org_id = token.get("org_id")
    email = token.get("email", "")
    full_name = token.get("name", "")

    if clerk_org_id:
        # Check if org already exists
        org_result = await db.execute(
            select(Organisation).where(Organisation.clerk_org_id == clerk_org_id)
        )
        org = org_result.scalar_one_or_none()

        if not org:
            org_name = token.get("org_name", "Unnamed")
            org_slug = token.get("org_slug", clerk_org_id)
            org = Organisation(
                name=org_name,
                slug=org_slug,
                clerk_org_id=clerk_org_id,
            )
            db.add(org)
            await db.flush()
    else:
        # Personal workspace — create a personal org
        org = Organisation(
            name=f"{full_name or email}'s workspace",
            slug=clerk_user_id,
            clerk_org_id=f"personal_{clerk_user_id}",
        )
        db.add(org)
        await db.flush()

    user = User(
        clerk_user_id=clerk_user_id,
        email=email,
        full_name=full_name,
        organisation_id=org.id,
        role="admin" if not clerk_org_id else token.get("org_role", "member"),
    )
    db.add(user)
    await db.flush()

    return user
