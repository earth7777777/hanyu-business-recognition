from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConfigEntry(Base):
    __tablename__ = "config_entries"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class UploadJob(Base):
    __tablename__ = "upload_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(30), default="created")
    created_by: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    files: Mapped[list[UploadedFile]] = relationship(back_populates="job", cascade="all,delete-orphan")
    records: Mapped[list[NormalizedRecord]] = relationship(back_populates="job", cascade="all,delete-orphan")
    tasks: Mapped[list[TaskRun]] = relationship(back_populates="job", cascade="all,delete-orphan")
    alerts: Mapped[list[Alert]] = relationship(back_populates="job", cascade="all,delete-orphan")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("upload_jobs.id", ondelete="CASCADE"), index=True)
    document_type: Mapped[str] = mapped_column(String(30), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    storage_path: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(String(500), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_hash_sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(30), default="active", index=True)
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    parse_status: Mapped[str] = mapped_column(String(30), default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_count: Mapped[int] = mapped_column(Integer, default=0)
    auto_deleted_duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    delete_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    restored_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    restore_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    job: Mapped[UploadJob] = relationship(back_populates="files")
    records: Mapped[list[NormalizedRecord]] = relationship(back_populates="file", cascade="all,delete-orphan")


class NormalizedRecord(Base):
    __tablename__ = "normalized_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("upload_jobs.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id", ondelete="CASCADE"), index=True)
    document_type: Mapped[str] = mapped_column(String(30), index=True)
    source_row: Mapped[int] = mapped_column(Integer, default=0)
    order_unshipped_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(30), default="active", index=True)
    version_status: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    is_current_effective: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    duplicate_of_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    superseded_by_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    supersedes_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    delete_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    delete_origin: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    restored_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    restore_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pre_delete_version_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pre_delete_is_current_effective: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    job: Mapped[UploadJob] = relationship(back_populates="records")
    file: Mapped[UploadedFile] = relationship(back_populates="records")
    links: Mapped[list[GroupRecordLink]] = relationship(back_populates="record", cascade="all,delete-orphan")


class MatchGroup(Base):
    __tablename__ = "match_groups"
    __table_args__ = (UniqueConstraint("job_id", "group_key", name="uq_group_per_job"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("upload_jobs.id", ondelete="CASCADE"), index=True)
    group_key: Mapped[str] = mapped_column(String(255), index=True)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    links: Mapped[list[GroupRecordLink]] = relationship(back_populates="group", cascade="all,delete-orphan")
    alerts: Mapped[list[Alert]] = relationship(back_populates="group", cascade="all,delete-orphan")


class GroupRecordLink(Base):
    __tablename__ = "group_record_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("match_groups.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("normalized_records.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="member")

    group: Mapped[MatchGroup] = relationship(back_populates="links")
    record: Mapped[NormalizedRecord] = relationship(back_populates="links")


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("upload_jobs.id", ondelete="CASCADE"), index=True)
    task_type: Mapped[str] = mapped_column(String(50), default="lobster_feed")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    job: Mapped[UploadJob] = relationship(back_populates="tasks")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("upload_jobs.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("match_groups.id", ondelete="CASCADE"), index=True)
    alert_type: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    job: Mapped[UploadJob] = relationship(back_populates="alerts")
    group: Mapped[MatchGroup] = relationship(back_populates="alerts")


class ViewerAccount(Base):
    __tablename__ = "viewer_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80), default="")
    role: Mapped[str] = mapped_column(String(30), index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sessions: Mapped[list[ViewerSession]] = relationship(back_populates="account", cascade="all,delete-orphan")
    reads: Mapped[list[ViewerAlertRead]] = relationship(back_populates="account", cascade="all,delete-orphan")
    devices: Mapped[list["ViewerDevice"]] = relationship(back_populates="account", cascade="all,delete-orphan")


class ViewerSession(Base):
    __tablename__ = "viewer_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("viewer_accounts.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    account: Mapped[ViewerAccount] = relationship(back_populates="sessions")


class ViewerDevice(Base):
    __tablename__ = "viewer_devices"
    __table_args__ = (UniqueConstraint("account_id", "device_key", name="uq_viewer_device_per_account"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("viewer_accounts.id", ondelete="CASCADE"), index=True)
    device_key: Mapped[str] = mapped_column(String(96), index=True)
    device_name: Mapped[str] = mapped_column(String(120), default="")
    device_remark: Mapped[str] = mapped_column(String(120), default="")
    device_type: Mapped[str] = mapped_column(String(60), default="")
    browser_name: Mapped[str] = mapped_column(String(80), default="")
    platform: Mapped[str] = mapped_column(String(120), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(String(80), default="")
    language: Mapped[str] = mapped_column(String(40), default="")
    timezone_name: Mapped[str] = mapped_column(String(80), default="")
    screen_size: Mapped[str] = mapped_column(String(40), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    login_count: Mapped[int] = mapped_column(Integer, default=0)

    account: Mapped[ViewerAccount] = relationship(back_populates="devices")


class ViewerAlertRead(Base):
    __tablename__ = "viewer_alert_reads"
    __table_args__ = (UniqueConstraint("account_id", "alert_id", name="uq_viewer_alert_read"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("viewer_accounts.id", ondelete="CASCADE"), index=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    last_seen_change_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    account: Mapped[ViewerAccount] = relationship(back_populates="reads")


class ViewerCustomerAlertSetting(Base):
    __tablename__ = "viewer_customer_alert_settings"
    __table_args__ = (UniqueConstraint("customer_key", "alert_type", name="uq_viewer_customer_alert_setting"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    customer_name: Mapped[str] = mapped_column(String(160), index=True)
    customer_key: Mapped[str] = mapped_column(String(200), index=True)
    alert_type: Mapped[str] = mapped_column(String(60), index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_reason: Mapped[str] = mapped_column(Text, default="")
    last_operator_name: Mapped[str] = mapped_column(String(80), default="")
    last_changed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    logs: Mapped[list["ViewerCustomerAlertSettingLog"]] = relationship(
        back_populates="setting",
        cascade="all,delete-orphan",
    )


class ViewerCustomerAlertSettingLog(Base):
    __tablename__ = "viewer_customer_alert_setting_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    setting_id: Mapped[str] = mapped_column(
        ForeignKey("viewer_customer_alert_settings.id", ondelete="CASCADE"),
        index=True,
    )
    customer_name: Mapped[str] = mapped_column(String(160), index=True)
    customer_key: Mapped[str] = mapped_column(String(200), index=True)
    alert_type: Mapped[str] = mapped_column(String(60), index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    operator_name: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    setting: Mapped[ViewerCustomerAlertSetting] = relationship(back_populates="logs")


class ExternalRef(Base):
    __tablename__ = "external_refs"
    __table_args__ = (
        UniqueConstraint("client_id", "request_id", "direction", name="uq_external_request"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("upload_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    client_id: Mapped[str] = mapped_column(String(60), index=True)
    provider: Mapped[str] = mapped_column(String(60), default="")
    request_id: Mapped[str] = mapped_column(String(120))
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), default="inbound")
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
