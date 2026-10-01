import secrets

from fastapi import Header

from app.config import get_settings
from app.db import SessionLocal
from app.errors import ApiError
from app.services import tenants


def current_tenant_id(x_api_key: str | None = Header(None)) -> str:
    if not x_api_key:
        raise ApiError(401, "missing_api_key", "Send your tenant API key in the X-API-Key header.")
    with SessionLocal() as s:
        t = tenants.find_by_api_key(s, x_api_key)
        if t is None:
            raise ApiError(401, "invalid_api_key", "Unknown API key.")
        return t.id


def require_admin(x_admin_token: str | None = Header(None)) -> None:
    if not x_admin_token or not secrets.compare_digest(x_admin_token, get_settings().admin_token):
        raise ApiError(401, "admin_required", "Missing or invalid X-Admin-Token.")
