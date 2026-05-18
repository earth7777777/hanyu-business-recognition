from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import db_dep, role_dep
from app.core.security import require_admin
from app.services.config_service import ConfigService

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/{key}")
def get_config(key: str, db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    _ = role
    service = ConfigService(db)
    try:
        return {"key": key, "value": service.get(key)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/{key}")
def update_config(key: str, body: dict, db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    require_admin(role)
    value = (body or {}).get("value")
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="body.value must be an object")
    service = ConfigService(db)
    stored = service.set(key, value)
    return {"key": key, "value": stored}
