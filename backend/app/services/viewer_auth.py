from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.db.models import Alert, ViewerAccount, ViewerAlertRead, ViewerDevice, ViewerSession


VIEWER_SESSION_COOKIE = "hanyu_viewer_session"
VIEWER_SESSION_TTL_DAYS = 365
VIEWER_SESSION_LAST_SEEN_TOUCH_INTERVAL = timedelta(minutes=5)
PBKDF2_ROUNDS = 240_000
VIEWER_ALLOWED_ROLES = {"viewer_yao", "viewer_boss"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalize_phone(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("手机号不能为空。")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 6 or len(digits) > 20:
        raise ValueError("手机号格式不对。")
    return digits


def ensure_viewer_role(role: str) -> str:
    value = str(role or "").strip().lower()
    if value not in VIEWER_ALLOWED_ROLES:
        raise ValueError("查看角色只支持 viewer_yao 或 viewer_boss。")
    return value


def _pbkdf2(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS).hex()


def hash_password(password: str) -> str:
    raw = str(password or "")
    if len(raw) < 6:
        raise ValueError("密码至少 6 位。")
    salt = secrets.token_bytes(16)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${_pbkdf2(raw, salt)}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, rounds, salt_hex, expected = str(password_hash or "").split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        rounds_int = int(rounds)
        salt = bytes.fromhex(salt_hex)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, rounds_int).hex()
    return hmac.compare_digest(actual, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_device_text(value: object, *, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _device_info_dict(value: object | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()  # type: ignore[no-any-return]
    if hasattr(value, "dict"):
        return value.dict()  # type: ignore[no-any-return]
    return {}


def _client_ip_from_request(request: object | None) -> str:
    if request is None:
        return ""
    headers = getattr(request, "headers", {}) or {}
    forwarded = _clean_device_text(headers.get("x-forwarded-for") if hasattr(headers, "get") else "", limit=200)
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:80]
    real_ip = _clean_device_text(headers.get("x-real-ip") if hasattr(headers, "get") else "", limit=80)
    if real_ip:
        return real_ip
    client = getattr(request, "client", None)
    return _clean_device_text(getattr(client, "host", "") if client else "", limit=80)


def _request_user_agent(request: object | None) -> str:
    if request is None:
        return ""
    headers = getattr(request, "headers", {}) or {}
    return _clean_device_text(headers.get("user-agent") if hasattr(headers, "get") else "", limit=1000)


def _normalize_device_key(raw: object) -> str:
    text = _clean_device_text(raw, limit=96)
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_", "."})
    if cleaned:
        return cleaned[:96]
    return f"server-{secrets.token_hex(8)}"


def _infer_device_type(user_agent: str, platform: str) -> str:
    text = f"{user_agent} {platform}".lower()
    if "iphone" in text:
        return "iPhone"
    if "ipad" in text:
        return "iPad"
    if "android" in text:
        return "Android"
    if "windows" in text:
        return "Windows"
    if "macintosh" in text or "mac os" in text or "macintel" in text:
        return "Mac"
    return "未知设备"


def _infer_browser_name(user_agent: str) -> str:
    text = user_agent.lower()
    if "micromessenger" in text:
        return "微信内置浏览器"
    if "edg/" in text or "edge/" in text:
        return "Edge"
    if "crios/" in text or ("chrome/" in text and "safari/" in text):
        return "Chrome"
    if "firefox/" in text:
        return "Firefox"
    if "safari/" in text:
        return "Safari"
    return "未知浏览器"


def _viewer_device_public(device: ViewerDevice) -> dict[str, Any]:
    return {
        "id": device.id,
        "device_key": device.device_key,
        "device_name": device.device_name,
        "device_remark": device.device_remark or "",
        "device_type": device.device_type,
        "browser_name": device.browser_name,
        "platform": device.platform,
        "ip_address": device.ip_address,
        "language": device.language,
        "timezone_name": device.timezone_name,
        "screen_size": device.screen_size,
        "first_seen_at": device.first_seen_at,
        "last_seen_at": device.last_seen_at,
        "last_login_at": device.last_login_at,
        "login_count": int(device.login_count or 0),
    }


def record_viewer_device_login(
    db: Session,
    account: ViewerAccount,
    *,
    device_info: object | None = None,
    request: object | None = None,
    now: datetime | None = None,
) -> ViewerDevice:
    now = now or _utcnow()
    info = _device_info_dict(device_info)
    user_agent = _clean_device_text(info.get("user_agent"), limit=1000) or _request_user_agent(request)
    platform = _clean_device_text(info.get("platform"), limit=120)
    device_key = _normalize_device_key(info.get("device_id"))
    device = (
        db.query(ViewerDevice)
        .filter(ViewerDevice.account_id == account.id, ViewerDevice.device_key == device_key)
        .first()
    )
    if not device:
        device = ViewerDevice(
            account_id=account.id,
            device_key=device_key,
            first_seen_at=now,
            login_count=0,
        )
        db.add(device)

    device.user_agent = user_agent
    device.platform = platform
    device.device_type = _infer_device_type(user_agent, platform)
    device.browser_name = _infer_browser_name(user_agent)
    device.device_name = " / ".join(part for part in (device.device_type, device.browser_name) if part) or "未知设备"
    device.ip_address = _client_ip_from_request(request)
    device.language = _clean_device_text(info.get("language"), limit=40)
    device.timezone_name = _clean_device_text(info.get("timezone"), limit=80)
    device.screen_size = _clean_device_text(info.get("screen"), limit=40)
    device.last_seen_at = now
    device.last_login_at = now
    device.login_count = int(device.login_count or 0) + 1
    return device


def viewer_public(account: ViewerAccount) -> dict[str, Any]:
    devices = sorted(
        list(getattr(account, "devices", []) or []),
        key=lambda item: item.last_login_at or item.last_seen_at or item.first_seen_at,
        reverse=True,
    )
    return {
        "id": account.id,
        "phone": account.phone,
        "display_name": account.display_name,
        "role": account.role,
        "is_active": bool(account.is_active),
        "created_at": account.created_at,
        "updated_at": account.updated_at,
        "last_login_at": account.last_login_at,
        "devices": [_viewer_device_public(device) for device in devices[:5]],
    }


def issue_viewer_session(
    db: Session,
    account: ViewerAccount,
    *,
    device_info: object | None = None,
    request: object | None = None,
) -> str:
    now = _utcnow()
    active_sessions = (
        db.query(ViewerSession)
        .filter(
            ViewerSession.account_id == account.id,
            ViewerSession.revoked_at.is_(None),
            ViewerSession.expires_at > now,
        )
        .all()
    )
    for item in active_sessions:
        item.revoked_at = now
        item.revoked_reason = "replaced_by_new_login"

    token = secrets.token_urlsafe(32)
    record_viewer_device_login(db, account, device_info=device_info, request=request, now=now)
    db.add(
        ViewerSession(
            account_id=account.id,
            token_hash=_token_hash(token),
            expires_at=now + timedelta(days=VIEWER_SESSION_TTL_DAYS),
            last_seen_at=now,
        )
    )
    account.last_login_at = now
    db.commit()
    return token


def revoke_viewer_session(db: Session, token: str | None, *, reason: str) -> None:
    key = str(token or "").strip()
    if not key:
        return
    session = db.query(ViewerSession).filter(ViewerSession.token_hash == _token_hash(key)).first()
    if not session or session.revoked_at is not None:
        return
    session.revoked_at = _utcnow()
    session.revoked_reason = reason
    db.commit()


def revoke_account_sessions(db: Session, account_id: str, *, reason: str) -> None:
    now = _utcnow()
    sessions = (
        db.query(ViewerSession)
        .filter(ViewerSession.account_id == account_id, ViewerSession.revoked_at.is_(None))
        .all()
    )
    changed = False
    for item in sessions:
        item.revoked_at = now
        item.revoked_reason = reason
        changed = True
    if changed:
        db.commit()


def authenticate_viewer(db: Session, *, phone: str, password: str) -> ViewerAccount:
    normalized_phone = normalize_phone(phone)
    account = db.query(ViewerAccount).filter(ViewerAccount.phone == normalized_phone).first()
    if not account or not verify_password(password, account.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="手机号或密码不对。")
    if not account.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该账号已停用。")
    return account


def _touch_viewer_session(db: Session, session: ViewerSession, now: datetime) -> None:
    last_seen_at = _aware_utc(session.last_seen_at)
    if last_seen_at and now - last_seen_at < VIEWER_SESSION_LAST_SEEN_TOUCH_INTERVAL:
        return
    try:
        session.last_seen_at = now
        db.commit()
    except SQLAlchemyError:
        db.rollback()


def _resolve_account_by_token(db: Session, token: str | None) -> ViewerAccount:
    key = str(token or "").strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录。")

    now = _utcnow()
    session = (
        db.query(ViewerSession)
        .filter(ViewerSession.token_hash == _token_hash(key))
        .first()
    )
    expires_at = _aware_utc(session.expires_at) if session else None
    if not session or session.revoked_at is not None or not expires_at or expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录。")

    account = db.get(ViewerAccount, session.account_id)
    if not account or not account.is_active:
        if session.revoked_at is None:
            session.revoked_at = now
            session.revoked_reason = "account_inactive"
            db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该账号已停用。")

    _touch_viewer_session(db, session, now)
    return account


def viewer_account_dep(
    db: Session = Depends(db_dep),
    viewer_session: str | None = Cookie(default=None, alias=VIEWER_SESSION_COOKIE),
) -> ViewerAccount:
    return _resolve_account_by_token(db, viewer_session)


def alert_last_changed_at(alert: Alert) -> datetime:
    payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
    for key in ("last_changed_at", "resolved_at", "first_opened_at", "opened_at"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = datetime.fromisoformat(raw)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    created = alert.created_at
    return created if created.tzinfo else created.replace(tzinfo=timezone.utc)


def get_alert_read_map(db: Session, *, account_id: str) -> dict[str, ViewerAlertRead]:
    items = db.query(ViewerAlertRead).filter(ViewerAlertRead.account_id == account_id).all()
    return {item.alert_id: item for item in items}
