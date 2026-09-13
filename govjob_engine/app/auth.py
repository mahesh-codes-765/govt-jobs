"""HTTP Basic auth for admin / review / PII routes.

Fails closed when ADMIN_PASS is empty — protected routes never stay open.
"""
from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

security = HTTPBasic(auto_error=True)


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Validate Basic credentials against Settings.admin_user / admin_pass.

    Returns the authenticated username on success.
    """
    configured_pass = settings.admin_pass or ""
    if not configured_pass:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin auth not configured (ADMIN_PASS is empty)",
            headers={"WWW-Authenticate": "Basic"},
        )

    expected_user = settings.admin_user or "admin"
    user_ok = hmac.compare_digest(
        credentials.username.encode("utf-8"),
        expected_user.encode("utf-8"),
    )
    pass_ok = hmac.compare_digest(
        credentials.password.encode("utf-8"),
        configured_pass.encode("utf-8"),
    )
    # Constant-time-ish: both compared, then combined (secrets.compare_digest
    # already used above; combine without short-circuit leaking which failed).
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def check_basic_header(authorization: str | None) -> bool:
    """Validate a raw Authorization header (for middleware / static protect).

    Returns True only when credentials match a configured non-empty password.
    """
    if not (settings.admin_pass or ""):
        return False
    if not authorization or not authorization.lower().startswith("basic "):
        return False
    import base64

    try:
        raw = base64.b64decode(authorization.split(" ", 1)[1].strip()).decode("utf-8")
        username, _, password = raw.partition(":")
    except Exception:
        return False
    expected_user = settings.admin_user or "admin"
    user_ok = hmac.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
    pass_ok = hmac.compare_digest(password.encode("utf-8"), settings.admin_pass.encode("utf-8"))
    return bool(user_ok and pass_ok)
