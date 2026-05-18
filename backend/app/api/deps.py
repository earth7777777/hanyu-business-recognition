from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.security import get_role
from app.db.session import get_db


def db_dep(db: Session = Depends(get_db)) -> Session:
    return db


def role_dep(role: str = Depends(get_role)) -> str:
    return role
