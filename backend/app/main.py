from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from app.api.router import api_router
from app.core.settings import ensure_dirs, settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.backup_service import start_backup_scheduler, stop_backup_scheduler
from app.services.config_service import ConfigService
from app.services.lifecycle_service import update_slow_request_runtime_status
from app.services.log_retention_service import (
    get_operations_logger,
    start_log_cleanup_scheduler,
    stop_log_cleanup_scheduler,
)


def create_app() -> FastAPI:
    ensure_dirs()
    init_db()
    operations_logger = get_operations_logger()

    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def record_slow_request(request: Request, call_next):
        path = str(request.url.path or "")
        started = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
        finally:
            if path.startswith(settings.api_prefix):
                duration_ms = int((time.perf_counter() - started) * 1000)
                status_code = int(response.status_code) if response is not None else 500
                try:
                    with SessionLocal() as db:
                        config = ConfigService(db)
                        monitoring_policy = config.get("operations_monitoring_policy")
                        threshold_ms = max(int(monitoring_policy.get("slow_request_threshold_ms") or 1500), 1)
                        keep_latest = max(int(monitoring_policy.get("slow_request_keep_latest") or 10), 1)
                        if duration_ms >= threshold_ms:
                            update_slow_request_runtime_status(
                                db,
                                request_item={
                                    "observed_at": datetime.now(timezone.utc).isoformat(),
                                    "method": request.method,
                                    "path": path,
                                    "query": str(request.url.query or "")[:200],
                                    "status_code": status_code,
                                    "duration_ms": duration_ms,
                                },
                                keep_latest=keep_latest,
                            )
                            db.commit()
                except Exception:
                    pass
        return response

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.on_event("startup")
    def startup_backup_scheduler():
        start_backup_scheduler()
        start_log_cleanup_scheduler()
        operations_logger.info("应用启动：日志留存与自动清理已就绪")

    @app.on_event("shutdown")
    def shutdown_backup_scheduler():
        operations_logger.info("应用停止：正在关闭日志清理与备份调度")
        stop_log_cleanup_scheduler()
        stop_backup_scheduler()

    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
