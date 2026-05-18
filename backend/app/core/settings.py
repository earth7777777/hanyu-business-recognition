from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
PROJECT_DIR = BASE_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return Path(raw).expanduser()


def _default_backup_root_dir() -> Path:
    if len(PROJECT_DIR.parts) >= 3 and PROJECT_DIR.parts[1] == "mnt":
        return Path("/mnt") / PROJECT_DIR.parts[2] / "月总项目备份"
    return PROJECT_DIR / "ops-backups"


STORAGE_DIR = _env_path("STORAGE_DIR", BASE_DIR / "storage")
BACKUP_ROOT_DIR = _env_path("BACKUP_ROOT_DIR", _default_backup_root_dir())
LOG_ROOT_DIR = _env_path("LOG_ROOT_DIR", BACKUP_ROOT_DIR / "logs")


def _build_database_url() -> str:
    direct = os.getenv("DATABASE_URL", "").strip()
    if direct:
        return direct

    host = os.getenv("DB_HOST", "").strip()
    port = os.getenv("DB_PORT", "").strip() or "3306"
    user = os.getenv("DB_USER", "").strip()
    password = os.getenv("DB_PASSWORD", "")
    name = os.getenv("DB_NAME", "").strip()

    if host and user and name:
        user_enc = quote_plus(user)
        pwd_enc = quote_plus(password)
        return f"mysql+pymysql://{user_enc}:{pwd_enc}@{host}:{port}/{name}?charset=utf8mb4"

    raise RuntimeError(
        "MariaDB connection is required. Set DATABASE_URL, or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME."
    )


class Settings:
    app_name: str = "Sales Fulfillment & Invoice Warning V1"
    api_prefix: str = "/v1"
    database_url: str = _build_database_url()
    lobster_mode: str = os.getenv("LOBSTER_MODE", "mock")


settings = Settings()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_ROOT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_ROOT_DIR.mkdir(parents=True, exist_ok=True)
