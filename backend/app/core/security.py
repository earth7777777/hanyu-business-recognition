from __future__ import annotations

from fastapi import Header, HTTPException, status


ALLOWED_ROLES = {"upload", "admin"}


def get_role(x_role: str | None = Header(default=None)) -> str:
    if not x_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Role header. Use 'upload' or 'admin'.",
        )
    role = x_role.strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Unsupported role '{x_role}'. Allowed: upload/admin.",
        )
    return role


def require_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required for this action.",
        )
