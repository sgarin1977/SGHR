import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from sqlalchemy import (
    String,
    ForeignKey,
    ForeignKeyConstraint,
    Date,
    DateTime,
    Time,
    Text,
    Integer,
    SmallInteger,
    LargeBinary,
    Boolean,
    Numeric,
    CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
def utcnow_naive() -> datetime:
    """Return UTC compatible with naive DB columns."""
    return (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
    )


class Base(DeclarativeBase):
    pass

class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint(
            "slug",
            name="tenants_slug_key",
        ),
        CheckConstraint(
            "status IN "
            "('active', 'paused', "
            "'suspended', 'deleted')",
            name="chk_tenants_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    default_language: Mapped[str] = (
        mapped_column(
            String(10),
            nullable=False,
            default="ru",
        )
    )
    default_currency: Mapped[str] = (
        mapped_column(
            String(3),
            nullable=False,
            default="EUR",
        )
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
    )
    extra_metadata: Mapped[dict] = (
        mapped_column(
            "metadata",
            JSONB,
            nullable=False,
            default=dict,
        )
    )
    created_at: Mapped[datetime] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(
                timezone.utc
            ),
        )
    )
    updated_at: Mapped[datetime] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(
                timezone.utc
            ),
            onupdate=lambda: datetime.now(
                timezone.utc
            ),
        )
    )


class User(Base):
    __tablename__ = "users"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    active_role: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language_code: Mapped[str] = mapped_column(String(10), default="ru")
    timezone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    country_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("countries.id"), nullable=True)
    city_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("cities.id"), nullable=True)
    profile_completion_score: Mapped[int] = mapped_column(Integer, default=0)
    trust_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, default="active")
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class UserAccount(Base):
    __tablename__ = "user_accounts"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(Text, default="telegram")
    platform_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    campaign_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    referral_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

# 6.3. Таблиця user_roles за ТЗ
class UserRoleMapping(Base):
    __tablename__ = "user_roles"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False) # specialist/client/admin/super_admin
    status: Mapped[str] = mapped_column(Text, default="active") # active/suspended/revoked
    granted_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class RoleScope(Base):
    __tablename__ = "role_scopes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_role_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "user_roles.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True,
    )
    scope_code: Mapped[Optional[str]] = mapped_column(
        String(10),
        ForeignKey(
            "languages.code",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, default="active")
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    created_by_root_identity_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "root_identities.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    revoked_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    revoked_by_root_identity_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "root_identities.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

class RootIdentity(Base):
    __tablename__ = "root_identities"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    linked_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    identity_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    totp_secret_encrypted: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    totp_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="pending_enrollment",
        nullable=False,
    )
    failed_password_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_authenticated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class RootRecoverySession(Base):
    __tablename__ = "root_recovery_sessions"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_root_session_id_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    root_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("root_identities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        Text,
        unique=True,
        nullable=False,
    )
    state: Mapped[str] = mapped_column(
        Text,
        default="mfa_pending",
        nullable=False,
    )
    mfa_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mfa_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    password_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    mfa_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class RootRecoveryCode(Base):
    __tablename__ = "root_recovery_codes"
    __table_args__ = (
        UniqueConstraint(
            "root_identity_id",
            "code_hash",
            name="uq_root_recovery_code_hash",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    root_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("root_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    used_by_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("root_recovery_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class RootRecoveryAction(Base):
    __tablename__ = "root_recovery_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["root_session_id", "tenant_id"],
            [
                "root_recovery_sessions.id",
                "root_recovery_sessions.tenant_id",
            ],
            name="fk_root_action_session",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    root_session_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    target_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action_payload: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    confirmation_token_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    state: Mapped[str] = mapped_column(
        Text,
        default="pending_confirmation",
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class RootSecurityEvent(Base):
    __tablename__ = "root_security_events"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    root_identity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("root_identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    root_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("root_recovery_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    root_action_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("root_recovery_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
    reason_code: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    payload: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class EventLog(Base):
    __tablename__ = "event_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    scope_country_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "countries.id",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    scope_language_code: Mapped[
        Optional[str]
    ] = mapped_column(
        String(10),
        ForeignKey(
            "languages.code",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    platform: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trace_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class LegalDocument(Base):
    __tablename__ = "legal_documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="draft")
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class UserConsent(Base):
    __tablename__ = "user_consents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    consent_type: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    platform: Mapped[str] = mapped_column(Text, default="telegram")
    ip_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

class Country(Base):
    __tablename__ = "countries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(2), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_pt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_es: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_uk: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_pl: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_de: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_nl: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    default_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    phone_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class City(Base):
    __tablename__ = "cities"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    country_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("countries.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_pt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_es: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_uk: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_pl: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_de: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_nl: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class UserLocation(Base):
    __tablename__ = "user_locations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    country_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("countries.id"), nullable=True)
    city_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("cities.id"), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    accuracy_meters: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    location_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visibility_level: Mapped[str] = mapped_column(Text, default="city")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ProfileVisibilitySetting(Base):
    __tablename__ = "profile_visibility_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    profile_type: Mapped[str] = mapped_column(Text, nullable=False)
    visibility_level: Mapped[str] = mapped_column(Text, default="public")
    visible_to_clients: Mapped[bool] = mapped_column(Boolean, default=True)
    visible_to_employers: Mapped[bool] = mapped_column(Boolean, default=True)
    visible_to_agencies: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_direct_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_profile_export: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class DataSubjectRequest(Base):
    __tablename__ = "data_subject_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="requested")
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    result_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class DeletionJob(Base):
    __tablename__ = "deletion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, default="scheduled")
    anonymization_report: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="open")
    priority: Mapped[str] = mapped_column(Text, default="P3")
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_role: Mapped[str] = mapped_column(Text, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class SpecialistCategory(Base):
    __tablename__ = "specialist_categories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("specialist_categories.id"), nullable=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_pt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_es: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_uk: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_pl: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_de: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_nl: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class Profession(Base):
    __tablename__ = "professions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialist_categories.id"), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_ru: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    name_en: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    name_pt: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    name_es: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    name_uk: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    name_pl: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    name_de: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    name_nl: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    normalized_name: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class ProfessionAlias(Base):
    __tablename__ = "profession_aliases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profession_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("professions.id"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_pt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_es: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ProfessionSkill(Base):
    __tablename__ = "profession_skills"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profession_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("professions.id"), nullable=False)
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class UserSkill(Base):
    __tablename__ = "user_skills"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id"), nullable=False)
    level: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class Employer(Base):
    __tablename__ = "employers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            name=(
                "employers_tenant_id_"
                "user_id_key"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    company_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    representative_name: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    company_type: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    country_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey("countries.id"),
        nullable=True,
    )
    city_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey("cities.id"),
        nullable=True,
    )
    email: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    phone: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="active",
        nullable=False,
    )
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )


class Vacancy(Base):
    __tablename__ = "vacancies"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    employer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "employers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    profession_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey("professions.id"),
        nullable=True,
    )
    country_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey("countries.id"),
        nullable=True,
    )
    city_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey("cities.id"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    description: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    salary_min: Mapped[
        Optional[float]
    ] = mapped_column(
        Numeric,
        nullable=True,
    )
    salary_max: Mapped[
        Optional[float]
    ] = mapped_column(
        Numeric,
        nullable=True,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        default="EUR",
        nullable=False,
    )
    employment_type: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    work_format: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    recruitment_mode: Mapped[str] = (
        mapped_column(
            Text,
            default="direct",
            nullable=False,
        )
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="draft",
        nullable=False,
    )
    published_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )


class Seeker(Base):
    __tablename__ = "seekers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            name="seekers_tenant_id_user_id_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    profession_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("professions.id"),
        nullable=True,
    )
    country_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("countries.id"),
        nullable=True,
    )
    city_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("cities.id"),
        nullable=True,
    )
    display_name: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    salary_expectation_min: Mapped[Optional[float]] = mapped_column(
        Numeric,
        nullable=True,
    )
    salary_expectation_max: Mapped[Optional[float]] = mapped_column(
        Numeric,
        nullable=True,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        default="EUR",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="active",
        nullable=False,
    )
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint(
            "vacancy_id",
            "seeker_id",
            name="applications_vacancy_id_seeker_id_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vacancies.id", ondelete="CASCADE"),
        nullable=False,
    )
    seeker_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("seekers.id", ondelete="CASCADE"),
        nullable=False,
    )
    message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="new",
        nullable=False,
    )
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Specialist(Base):
    __tablename__ = "specialists"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            name=(
                "specialists_tenant_id_user_id_key"
            ),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialist_categories.id"), nullable=False)
    profession_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("professions.id"),
        nullable=False,
    )
    active_professional_cabinet_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    country_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("countries.id"),
        nullable=True,
    )
    city_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("cities.id"), nullable=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    short_description: Mapped[str] = mapped_column(Text, nullable=False)
    full_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    experience_years: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_from: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    price_to: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    price_unit: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    work_format: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    service_radius_km: Mapped[int] = mapped_column(Integer, default=0)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    priority_score: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    rating: Mapped[float] = mapped_column(Numeric(3, 2), default=0)
    reviews_count: Mapped[int] = mapped_column(Integer, default=0)
    response_time_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="draft")
    moderation_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class ProfessionalCabinet(Base):
    __tablename__ = "professional_cabinets"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "specialist_id",
            "profession_id",
            name="uq_professional_cabinets_profession",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name=(
                "uq_professional_cabinets_"
                "tenant_id_id"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    specialist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "specialists.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specialist_categories.id"),
        nullable=False,
    )
    profession_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("professions.id"),
        nullable=False,
    )
    title: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    country_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("countries.id"),
        nullable=True,
    )
    city_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("cities.id"),
        nullable=True,
    )
    work_format: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="mixed",
    )
    availability_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="available",
    )
    moderation_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="draft",
    )
    moderation_comment: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )



class ProfessionalCabinetCalendar(Base):
    __tablename__ = (
        "professional_cabinet_calendars"
    )
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "professional_cabinet_id",
            ],
            [
                "professional_cabinets.tenant_id",
                "professional_cabinets.id",
            ],
            name="fk_cabinet_calendars_cabinet",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name=(
                "uq_cabinet_calendars_"
                "tenant_id_id"
            ),
        ),
        UniqueConstraint(
            "tenant_id",
            "professional_cabinet_id",
            name=(
                "uq_cabinet_calendars_cabinet"
            ),
        ),
        CheckConstraint(
            "slot_duration_minutes > 0",
            name=(
                "chk_cabinet_calendars_"
                "slot_duration"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    professional_cabinet_id: Mapped[
        uuid.UUID
    ] = mapped_column(
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        Text,
        default="UTC",
        nullable=False,
    )
    slot_duration_minutes: Mapped[int] = (
        mapped_column(
            Integer,
            default=60,
            nullable=False,
        )
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )


class ProfessionalCabinetWorkInterval(Base):
    __tablename__ = (
        "professional_cabinet_work_intervals"
    )
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "calendar_id",
            ],
            [
                "professional_cabinet_calendars.tenant_id",
                "professional_cabinet_calendars.id",
            ],
            name=(
                "fk_cabinet_work_intervals_"
                "calendar"
            ),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "calendar_id",
            "weekday",
            "start_time",
            "end_time",
            name=(
                "uq_cabinet_work_intervals"
            ),
        ),
        CheckConstraint(
            "weekday BETWEEN 1 AND 7",
            name=(
                "chk_cabinet_work_intervals_"
                "weekday"
            ),
        ),
        CheckConstraint(
            "start_time < end_time",
            name=(
                "chk_cabinet_work_intervals_"
                "time"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    calendar_id: Mapped[uuid.UUID] = (
        mapped_column(
            nullable=False,
        )
    )
    weekday: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    start_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )
    end_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )


class ProfessionalCabinetCalendarException(Base):
    __tablename__ = (
        "professional_cabinet_calendar_exceptions"
    )
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "calendar_id",
            ],
            [
                "professional_cabinet_calendars.tenant_id",
                "professional_cabinet_calendars.id",
            ],
            name=(
                "fk_cabinet_calendar_"
                "exceptions_calendar"
            ),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            (
                "exception_type IN "
                "('unavailable', 'available')"
            ),
            name=(
                "chk_cabinet_calendar_"
                "exception_type"
            ),
        ),
        CheckConstraint(
            (
                "(start_time IS NULL "
                "AND end_time IS NULL) "
                "OR (start_time IS NOT NULL "
                "AND end_time IS NOT NULL "
                "AND start_time < end_time)"
            ),
            name=(
                "chk_cabinet_calendar_"
                "exception_time"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    calendar_id: Mapped[uuid.UUID] = (
        mapped_column(
            nullable=False,
        )
    )
    exception_date: Mapped[date] = (
        mapped_column(
            Date,
            nullable=False,
        )
    )
    exception_type: Mapped[str] = (
        mapped_column(
            Text,
            default="unavailable",
            nullable=False,
        )
    )
    start_time: Mapped[
        Optional[time]
    ] = mapped_column(
        Time,
        nullable=True,
    )
    end_time: Mapped[
        Optional[time]
    ] = mapped_column(
        Time,
        nullable=True,
    )
    reason: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )


class ProfessionalCabinetSkill(Base):
    __tablename__ = "professional_cabinet_skills"
    __table_args__ = (
        UniqueConstraint(
            "professional_cabinet_id",
            "skill_id",
            name="uq_professional_cabinet_skills",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    professional_cabinet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "skills.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    level: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class SpecialistProfession(Base):
    __tablename__ = "specialist_professions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    specialist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specialists.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specialist_categories.id"),
        nullable=False,
    )
    profession_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("professions.id"),
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class SpecialistLocation(Base):
    __tablename__ = "specialist_locations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    specialist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialists.id"), nullable=False)
    country_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("countries.id"), nullable=True)
    city_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("cities.id"), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    accuracy_meters: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    location_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visibility_level: Mapped[str] = mapped_column(Text, default="city")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class SavedSpecialist(Base):
    __tablename__ = "saved_specialists"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "professional_cabinet_id",
            name=(
                "saved_specialists_"
                "tenant_user_cabinet_key"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    professional_cabinet_id: Mapped[
        uuid.UUID
    ] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    specialist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "specialists.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
    )

class Language(Base):
    __tablename__ = "languages"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    native_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class SpecialistLanguage(Base):
    __tablename__ = "specialist_languages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    specialist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialists.id"), nullable=False)
    language_code: Mapped[str] = mapped_column(String(10), nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False, default="basic")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class SpecialistService(Base):
    __tablename__ = "specialist_services"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
    )
    specialist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specialists.id"),
        nullable=False,
    )
    professional_cabinet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("specialist_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    profession_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("professions.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_from: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    price_to: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    price_unit: Mapped[str] = mapped_column(Text, default="service")
    status: Mapped[str] = mapped_column(Text, default="active")
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class ContactRequest(Base):
    __tablename__ = "contact_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    from_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    specialist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specialists.id"),
        nullable=False,
    )
    profession_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "professions.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    professional_cabinet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    original_language: Mapped[str] = mapped_column(String(10), default="ru")
    status: Mapped[str] = mapped_column(Text, default="new")
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow_naive,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class ServiceOrder(Base):
    __tablename__ = "service_orders"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "professional_cabinet_id",
            ],
            [
                "professional_cabinets.tenant_id",
                "professional_cabinets.id",
            ],
            name=(
                "fk_service_orders_tenant_cabinet"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "(start_at IS NULL "
                "AND end_at IS NULL) "
                "OR (start_at IS NOT NULL "
                "AND end_at IS NOT NULL "
                "AND end_at > start_at)"
            ),
            name=(
                "chk_service_orders_"
                "calendar_interval"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("contact_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    specialist_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    specialist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specialists.id", ondelete="CASCADE"),
        nullable=False,
    )
    profession_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "professions.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    professional_cabinet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="draft",
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    due_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    start_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    end_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    agreed_amount: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(Text, default="EUR")
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    confirmed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    completed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelled_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow_naive,
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

class ConversationThread(Base):
    __tablename__ = "conversation_threads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    context_type: Mapped[str] = mapped_column(Text, default="contact_request")
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contact_requests.id"), nullable=False)
    client_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    specialist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specialists.id"),
        nullable=False,
    )
    professional_cabinet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="waiting_specialist",
    )
    completed_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow_naive,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class ConversationParticipant(Base):
    __tablename__ = "conversation_participants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    participant_role: Mapped[str] = mapped_column(Text, default="participant")
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    last_read_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    hidden_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation_threads.id"), nullable=False)
    sender_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    receiver_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    original_language: Mapped[str] = mapped_column(String(10), default="ru")
    translated_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    translated_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    translation_status: Mapped[str] = mapped_column(Text, default="not_needed")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_masked: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class ContactDetectionEvent(Base):
    __tablename__ = "contact_detection_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)
    detected_type: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 2), default=1)
    action_taken: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ThreadRestriction(Base):
    __tablename__ = "thread_restrictions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation_threads.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="active")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class TranslationJob(Base):
    __tablename__ = "translation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class TranslationCache(Base):
    __tablename__ = "translation_cache"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_text_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TranslationLog(Base):
    __tablename__ = "translation_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    job_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("translation_jobs.id"), nullable=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class UserLanguageSetting(Base):
    __tablename__ = "user_language_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    interface_language: Mapped[str] = mapped_column(String(10), default="ru")
    message_language: Mapped[str] = mapped_column(String(10), default="ru")
    translation_mode: Mapped[str] = mapped_column(
        String(20),
        default="standard",
        nullable=False,
    )
    auto_translate_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    show_original_button: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class MessageReadReceipt(Base):
    __tablename__ = "message_read_receipts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    notification_type: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, default="telegram")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(Text, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

class RateLimitRule(Base):
    __tablename__ = "rate_limit_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    limit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    penalty_action: Mapped[str] = mapped_column(Text, default="block")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class AbuseEvent(Base):
    __tablename__ = "abuse_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0)
    action_taken: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class Complaint(Base):
    __tablename__ = "complaints"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    reporter_user_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    target_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    professional_cabinet_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    conversation_thread_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "conversation_threads.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    comment: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="new",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        nullable=False,
    )
    reviewed_by: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    reviewed_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime,
        nullable=True,
    )
class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    professional_cabinet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    service_order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "service_orders.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    context_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    specialist_reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending_moderation")
    published_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ReputationScore(Base):
    __tablename__ = "reputation_scores"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    score: Mapped[float] = mapped_column(Numeric, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    complaint_count: Mapped[int] = mapped_column(Integer, default=0)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class Blacklist(Base):
    __tablename__ = "blacklist"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    platform: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    platform_user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class RiskFlag(Base):
    __tablename__ = "risk_flags"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    flag_code: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, default="medium")
    status: Mapped[str] = mapped_column(Text, default="open")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class AdminAction(Base):
    __tablename__ = "admin_actions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    admin_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scope_country_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "countries.id",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    scope_language_code: Mapped[
        Optional[str]
    ] = mapped_column(
        String(10),
        ForeignKey(
            "languages.code",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    before_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    after_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    professional_cabinet_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    plan_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        default="pending",
        nullable=False,
    )
    billing_period: Mapped[str] = mapped_column(
        Text,
        default="month",
        nullable=False,
    )
    amount: Mapped[float] = mapped_column(
        Numeric,
        default=0,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        default="EUR",
        nullable=False,
    )
    provider: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    provider_subscription_id: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )
    current_period_start: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    current_period_end: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancel_at_period_end: Mapped[bool] = (
        mapped_column(
            Boolean,
            default=False,
            nullable=False,
        )
    )
    cancelled_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
        nullable=False,
    )


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    billing_period: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="active")
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class PaidFeature(Base):
    __tablename__ = "paid_features"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(Text, default="active")
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    payer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    payer_entity_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payer_entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(Text, default="issued")
    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow_naive)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    invoice_pdf_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    payment_method: Mapped[str] = mapped_column(Text, default="manual")
    provider: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending")
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class FinancialLedger(Base):
    __tablename__ = "financial_ledger"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    ledger_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="posted")
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class SpecialistPromotion(Base):
    __tablename__ = "specialist_promotions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    specialist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialists.id"), nullable=False)
    professional_cabinet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    promotion_type: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending_payment")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

class FileStorageObject(Base):
    __tablename__ = "file_storage_objects"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('pending_upload', 'ready', "
            "'failed', 'deleted')",
            name="ck_file_storage_objects_status",
        ),
        CheckConstraint(
            "antivirus_status IN "
            "('pending', 'not_scanned', "
            "'clean', 'infected', "
            "'quarantined', 'scan_failed')",
            name=(
                "ck_file_storage_objects_"
                "antivirus_status"
            ),
        ),
        CheckConstraint(
            "size_bytes > 0",
            name="ck_file_storage_objects_size",
        ),
        CheckConstraint(
            "jsonb_typeof(provider_metadata) "
            "= 'object'",
            name=(
                "ck_file_storage_objects_"
                "provider_metadata_object"
            ),
        ),
        UniqueConstraint(
            "storage_provider",
            "storage_path",
            name=(
                "uq_file_storage_objects_"
                "provider_path"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    entity_type: Mapped[Optional[str]] = (
        mapped_column(
            Text,
            nullable=True,
        )
    )
    entity_id: Mapped[Optional[uuid.UUID]] = (
        mapped_column(nullable=True)
    )
    file_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    mime_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    storage_provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="supabase",
    )
    storage_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    public_url: Mapped[Optional[str]] = (
        mapped_column(
            Text,
            nullable=True,
        )
    )
    visibility_scope: Mapped[str] = (
        mapped_column(
            Text,
            nullable=False,
            default="private",
        )
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending_upload",
    )
    antivirus_status: Mapped[str] = (
        mapped_column(
            Text,
            nullable=False,
            default="pending",
        )
    )
    provider_metadata: Mapped[dict] = (
        mapped_column(
            JSONB,
            nullable=False,
            default=dict,
        )
    )
    completed_at: Mapped[Optional[datetime]] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=True,
        )
    )
    retention_until: Mapped[Optional[datetime]] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )


class SpecialistPortfolioItem(Base):
    __tablename__ = "specialist_portfolio_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    specialist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specialists.id", ondelete="CASCADE"),
        nullable=False,
    )
    professional_cabinet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "professional_cabinets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(Text, default="pending_moderation")
    created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
)

class ApiAuthSession(Base):
    __tablename__ = "api_auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    auth_method: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="telegram",
    )
    device_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    device_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_used_at: Mapped[Optional[datetime]] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=True,
        )
    )
    revoked_at: Mapped[Optional[datetime]] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )

class ApiEmailAuthChallenge(Base):
    __tablename__ = "api_email_auth_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = (
        mapped_column(
            ForeignKey(
                "tenants.id",
                ondelete="CASCADE",
            ),
            nullable=True,
        )
    )
    requested_user_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )
    challenge_hash: Mapped[str] = (
        mapped_column(
            String(64),
            nullable=False,
            unique=True,
        )
    )
    challenge_type: Mapped[str] = (
        mapped_column(
            String(20),
            nullable=False,
            default="otp",
        )
    )
    purpose: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="login",
    )
    device_id: Mapped[Optional[str]] = (
        mapped_column(
            String(255),
            nullable=True,
        )
    )
    device_name: Mapped[Optional[str]] = (
        mapped_column(
            String(255),
            nullable=True,
        )
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    attempts_count: Mapped[int] = (
        mapped_column(
            Integer,
            nullable=False,
            default=0,
        )
    )
    max_attempts: Mapped[int] = (
        mapped_column(
            Integer,
            nullable=False,
            default=5,
        )
    )
    expires_at: Mapped[datetime] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=False,
        )
    )
    used_at: Mapped[Optional[datetime]] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=True,
        )
    )
    created_at: Mapped[datetime] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(
                timezone.utc
            ),
        )
    )
    updated_at: Mapped[datetime] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(
                timezone.utc
            ),
            onupdate=lambda: datetime.now(
                timezone.utc
            ),
        )
    )

class TenantDomain(Base):
    __tablename__ = "tenant_domains"
    __table_args__ = (
        UniqueConstraint(
            "domain",
            name="uq_tenant_domains_domain",
        ),
        CheckConstraint(
            "status IN "
            "('pending', 'active', 'disabled')",
            name="ck_tenant_domains_status",
        ),
        CheckConstraint(
            "domain = lower(btrim(domain))",
            name="ck_tenant_domains_normalized",
        ),
        CheckConstraint(
            "status <> 'active' "
            "OR verified_at IS NOT NULL",
            name="ck_tenant_domains_active_verified",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    domain: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
    )
    verified_at: Mapped[Optional[datetime]] = (
        mapped_column(
            DateTime(timezone=True),
            nullable=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )


class TenantWhiteLabelSetting(Base):
    __tablename__ = (
        "tenant_white_label_settings"
    )
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            name=(
                "uq_white_label_settings_tenant"
            ),
        ),
        CheckConstraint(
            "primary_color IS NULL "
            "OR primary_color ~ "
            "'^#[0-9A-Fa-f]{6}$'",
            name=(
                "ck_white_label_primary_color"
            ),
        ),
        CheckConstraint(
            "secondary_color IS NULL "
            "OR secondary_color ~ "
            "'^#[0-9A-Fa-f]{6}$'",
            name=(
                "ck_white_label_secondary_color"
            ),
        ),
        CheckConstraint(
            "accent_color IS NULL "
            "OR accent_color ~ "
            "'^#[0-9A-Fa-f]{6}$'",
            name=(
                "ck_white_label_accent_color"
            ),
        ),
        CheckConstraint(
            "jsonb_typeof(theme_config) = "
            "'object'",
            name=(
                "ck_white_label_theme_object"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    logo_url: Mapped[Optional[str]] = (
        mapped_column(Text, nullable=True)
    )
    favicon_url: Mapped[Optional[str]] = (
        mapped_column(Text, nullable=True)
    )
    primary_color: Mapped[Optional[str]] = (
        mapped_column(
            String(7),
            nullable=True,
        )
    )
    secondary_color: Mapped[Optional[str]] = (
        mapped_column(
            String(7),
            nullable=True,
        )
    )
    accent_color: Mapped[Optional[str]] = (
        mapped_column(
            String(7),
            nullable=True,
        )
    )
    theme_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )


class TenantLanguage(Base):
    __tablename__ = "tenant_languages"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "language_code",
            name="uq_tenant_languages_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    language_code: Mapped[str] = (
        mapped_column(
            ForeignKey(
                "languages.code",
                ondelete="RESTRICT",
            ),
            nullable=False,
        )
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )


class Suite(Base):
    __tablename__ = "suites"
    __table_args__ = (
        UniqueConstraint(
            "code",
            name="uq_suites_code",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )


class Module(Base):
    __tablename__ = "modules"
    __table_args__ = (
        UniqueConstraint(
            "code",
            name="uq_modules_code",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )


class SuiteModule(Base):
    __tablename__ = "suite_modules"
    __table_args__ = (
        UniqueConstraint(
            "suite_id",
            "module_id",
            name="uq_suite_modules_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    suite_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "suites.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    module_id: Mapped[uuid.UUID] = (
        mapped_column(
            ForeignKey(
                "modules.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        )
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )


class TenantSuite(Base):
    __tablename__ = "tenant_suites"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "suite_id",
            name="uq_tenant_suites_pair",
        ),
        CheckConstraint(
            "status IN "
            "('active', 'suspended', 'disabled')",
            name="ck_tenant_suites_status",
        ),
        CheckConstraint(
            "expires_at IS NULL "
            "OR activated_at IS NULL "
            "OR expires_at > activated_at",
            name="ck_tenant_suites_period",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    suite_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "suites.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
    )
    activated_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )


class TenantModule(Base):
    __tablename__ = "tenant_modules"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "module_id",
            name="uq_tenant_modules_pair",
        ),
        CheckConstraint(
            "source IN ('suite', 'manual')",
            name="ck_tenant_modules_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    module_id: Mapped[uuid.UUID] = (
        mapped_column(
            ForeignKey(
                "modules.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        )
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    source: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )

class ApiClient(Base):
    __tablename__ = "api_clients"
    __table_args__ = (
        CheckConstraint(
            "owner_type IN "
            "('agency', 'partner', "
            "'enterprise', 'service_account')",
            name="ck_api_clients_owner_type",
        ),
        CheckConstraint(
            "status IN "
            "('active', 'suspended', "
            "'disabled')",
            name="ck_api_clients_status",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_api_clients_tenant_id_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    owner_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    owner_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        nullable=True,
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
    )
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )

class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "api_client_id",
            ],
            [
                "api_clients.tenant_id",
                "api_clients.id",
            ],
            name="fk_api_keys_tenant_client",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "environment IN "
            "('sandbox', 'production')",
            name="ck_api_keys_environment",
        ),
        CheckConstraint(
            "status IN "
            "('active', 'revoked', 'expired')",
            name="ck_api_keys_status",
        ),
        UniqueConstraint(
            "key_prefix",
            name="uq_api_keys_prefix",
        ),
        UniqueConstraint(
            "key_hash",
            name="uq_api_keys_hash",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_api_keys_tenant_id_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    api_client_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    key_prefix: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    key_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    environment: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
    )
    expires_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_used_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ip_allowlist: Mapped[
        Optional[list[str]]
    ] = mapped_column(
        ARRAY(INET),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )




class ApiKeyScope(Base):
    __tablename__ = "api_key_scopes"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "api_key_id",
            ],
            [
                "api_keys.tenant_id",
                "api_keys.id",
            ],
            name=(
                "fk_api_key_scopes_"
                "tenant_key"
            ),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "api_key_id",
            "scope",
            name="uq_api_key_scopes_key_scope",
        ),
        CheckConstraint(
            "scope IN ("
            "'specialists.read', "
            "'specialists.search', "
            "'professional_cabinets.read', "
            "'services.read', "
            "'contact_requests.read', "
            "'contact_requests.write', "
            "'service_orders.read', "
            "'service_orders.write', "
            "'reviews.read', "
            "'files.read', "
            "'webhooks.read', "
            "'webhooks.write'"
            ")",
            name="ck_api_key_scopes_whitelist",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )



class ApiLog(Base):
    __tablename__ = "api_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "api_key_id",
            ],
            [
                "api_keys.tenant_id",
                "api_keys.id",
            ],
            name="fk_api_logs_tenant_key",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "api_client_id",
            ],
            [
                "api_clients.tenant_id",
                "api_clients.id",
            ],
            name="fk_api_logs_tenant_client",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status_code BETWEEN 100 AND 599",
            name="ck_api_logs_status_code",
        ),
        CheckConstraint(
            "duration_ms >= 0",
            name="ck_api_logs_duration",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    request_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    api_client_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    user_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    endpoint: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    method: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    status_code: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    ip: Mapped[Optional[str]] = mapped_column(
        INET,
        nullable=True,
    )
    user_agent: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )



class ApiIdempotencyRecord(Base):
    __tablename__ = "api_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "principal_type",
            "principal_id",
            "operation",
            "key_hash",
            name=(
                "uq_api_idempotency_records_"
                "principal_operation_key"
            ),
        ),
        CheckConstraint(
            "principal_type IN ('user', 'api_key')",
            name=(
                "ck_api_idempotency_records_"
                "principal_type"
            ),
        ),
        CheckConstraint(
            "status IN ('processing', 'completed')",
            name=(
                "ck_api_idempotency_records_status"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    principal_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    key_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="processing",
    )
    response_status: Mapped[Optional[int]] = (
        mapped_column(
            SmallInteger,
            nullable=True,
        )
    )
    response_ciphertext: Mapped[
        Optional[bytes]
    ] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    completed_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )



class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "api_client_id",
            ],
            [
                "api_clients.tenant_id",
                "api_clients.id",
            ],
            ondelete="CASCADE",
            name=(
                "fk_webhook_endpoints_"
                "tenant_client"
            ),
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name=(
                "uq_webhook_endpoints_"
                "tenant_id_id"
            ),
        ),
        CheckConstraint(
            "status IN "
            "('active', 'suspended', 'disabled')",
            name="ck_webhook_endpoints_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = (
        mapped_column(
            nullable=False,
        )
    )
    api_client_id: Mapped[uuid.UUID] = (
        mapped_column(
            nullable=False,
        )
    )
    callback_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    secret_ciphertext: Mapped[bytes] = (
        mapped_column(
            LargeBinary,
            nullable=False,
        )
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
    )
    consecutive_failures: Mapped[int] = (
        mapped_column(
            Integer,
            nullable=False,
            default=0,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )



class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "webhook_endpoint_id",
            ],
            [
                "webhook_endpoints.tenant_id",
                "webhook_endpoints.id",
            ],
            ondelete="CASCADE",
            name=(
                "fk_webhook_subscriptions_"
                "tenant_endpoint"
            ),
        ),
        UniqueConstraint(
            "tenant_id",
            "webhook_endpoint_id",
            "event_type",
            name=(
                "uq_webhook_subscriptions_"
                "endpoint_event"
            ),
        ),
        CheckConstraint(
            "event_type IN ("
            "'contact_request.created', "
            "'contact_request.updated', "
            "'service_order.created', "
            "'service_order.confirmed', "
            "'service_order.completed', "
            "'service_order.cancelled', "
            "'review.created', "
            "'review.published', "
            "'professional_cabinet.updated', "
            "'professional_cabinet."
            "availability_changed'"
            ")",
            name=(
                "ck_webhook_subscriptions_"
                "event_type"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = (
        mapped_column(
            nullable=False,
        )
    )
    webhook_endpoint_id: Mapped[
        uuid.UUID
    ] = mapped_column(
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )



class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_webhook_events_tenant_id_id",
        ),
        CheckConstraint(
            "event_type IN ("
            "'contact_request.created', "
            "'contact_request.updated', "
            "'service_order.created', "
            "'service_order.confirmed', "
            "'service_order.completed', "
            "'service_order.cancelled', "
            "'review.created', "
            "'review.published', "
            "'professional_cabinet.updated', "
            "'professional_cabinet."
            "availability_changed'"
            ")",
            name="ck_webhook_events_event_type",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_webhook_events_payload_object",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_webhook_events_expiration",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = (
        mapped_column(
            ForeignKey(
                "tenants.id",
                ondelete="CASCADE",
                name="fk_webhook_events_tenant",
            ),
            nullable=False,
        )
    )
    event_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: (
            datetime.now(timezone.utc)
            + timedelta(days=90)
        ),
    )



class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "tenant_id",
                "webhook_event_id",
            ],
            [
                "webhook_events.tenant_id",
                "webhook_events.id",
            ],
            ondelete="CASCADE",
            name=(
                "fk_webhook_deliveries_"
                "tenant_event"
            ),
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "webhook_endpoint_id",
            ],
            [
                "webhook_endpoints.tenant_id",
                "webhook_endpoints.id",
            ],
            ondelete="CASCADE",
            name=(
                "fk_webhook_deliveries_"
                "tenant_endpoint"
            ),
        ),
        UniqueConstraint(
            "tenant_id",
            "webhook_event_id",
            "webhook_endpoint_id",
            name=(
                "uq_webhook_deliveries_"
                "event_endpoint"
            ),
        ),
        CheckConstraint(
            "status IN ("
            "'pending', "
            "'processing', "
            "'delivered', "
            "'failed'"
            ")",
            name="ck_webhook_deliveries_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 "
            "AND attempt_count <= 5",
            name=(
                "ck_webhook_deliveries_"
                "attempt_count"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = (
        mapped_column(
            nullable=False,
        )
    )
    webhook_event_id: Mapped[
        uuid.UUID
    ] = mapped_column(
        nullable=False,
    )
    webhook_endpoint_id: Mapped[
        uuid.UUID
    ] = mapped_column(
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    next_attempt_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    response_status: Mapped[
        Optional[int]
    ] = mapped_column(
        SmallInteger,
        nullable=True,
    )
    error_category: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    delivered_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
        onupdate=lambda: datetime.now(
            timezone.utc
        ),
    )
