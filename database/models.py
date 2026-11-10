import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    String,
    CHAR,
    Index,
    ForeignKey,
    ForeignKeyConstraint,
    Date,
    DateTime,
    Time,
    Text,
    Integer,
    BigInteger,
    SmallInteger,
    LargeBinary,
    Boolean,
    Numeric,
    CheckConstraint,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, ExcludeConstraint
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


class TenantCompanyProfile(Base):
    __tablename__ = "tenant_company_profiles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            name="uq_tenant_company_profiles_tenant_id",
        ),
        CheckConstraint(
            "length(btrim(legal_name)) > 0",
            name="ck_tenant_company_profiles_legal_name",
        ),
        CheckConstraint(
            "row_version > 0",
            name="ck_tenant_company_profiles_row_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
    )
    legal_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    commercial_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    registered_address_raw: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    operational_address_raw: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    phone: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    email: Mapped[Optional[str]] = mapped_column(
        String(320),
        nullable=True,
    )
    website: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    signatory_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    signatory_position: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    logo_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("file_storage_objects.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    __mapper_args__ = {
        "version_id_col": row_version,
    }


class TenantCompanyIdentifier(Base):
    __tablename__ = "tenant_company_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "country_code",
            "identifier_type",
            "identifier_value_normalized",
            name="uq_tenant_company_identifiers_normalized",
        ),
        Index(
            "uq_tenant_company_identifiers_primary_scope",
            "tenant_company_profile_id",
            "country_code",
            "identifier_type",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_company_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant_company_profiles.id"),
        nullable=False,
    )
    country_code: Mapped[str] = mapped_column(
        CHAR(2),
        nullable=False,
    )
    identifier_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    identifier_value: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    identifier_value_normalized: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
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

    # Canonical account connection lifecycle; SQL migration precedes this ORM.
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active", server_default=text("'active'"))
    row_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=text("1"))
    __table_args__ = (
        CheckConstraint("status IN ('active','unlinked')", name="ck_user_accounts_status"),
        CheckConstraint("status <> 'unlinked' OR platform = 'telegram'", name="ck_user_accounts_unlinked_platform"),
        CheckConstraint("row_version > 0", name="ck_user_accounts_row_version"),
    )
    __mapper_args__ = {"version_id_col": row_version}

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
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
        ),
        nullable=True,
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


class ConstructionBackgroundJobRecord(Base):
    __tablename__ = "construction_background_jobs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "job_type",
            "idempotency_key",
            name=(
                "uq_construction_background_jobs_"
                "idempotency"
            ),
        ),
        CheckConstraint(
            "status IN ("
            "'QUEUED', "
            "'RUNNING', "
            "'RETRY', "
            "'READY', "
            "'FAILED', "
            "'CANCELLED'"
            ")",
            name=(
                "ck_construction_background_jobs_"
                "status"
            ),
        ),
        CheckConstraint(
            "attempts >= 0",
            name=(
                "ck_construction_background_jobs_"
                "attempts"
            ),
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name=(
                "ck_construction_background_jobs_"
                "progress"
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
        ForeignKey("users.id"),
        nullable=False,
    )
    resource_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="QUEUED",
    )
    idempotency_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    lease_owner: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    lease_expires_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    error_code: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    result_reference: Mapped[
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


class ConstructionAccessGrant(Base):
    __tablename__ = "construction_access_grants"
    __table_args__ = (
        CheckConstraint(
            "role IN ("
            "'OWNER', "
            "'ADMIN', "
            "'SURVEYOR', "
            "'ESTIMATOR', "
            "'PROJECT_MANAGER', "
            "'EXECUTOR', "
            "'OBSERVER'"
            ")",
            name=(
                "ck_construction_access_grants_role"
            ),
        ),
        CheckConstraint(
            "scope_type IN ("
            "'tenant', "
            "'project', "
            "'document'"
            ")",
            name=(
                "ck_construction_access_grants_"
                "scope_type"
            ),
        ),
        CheckConstraint(
            "status IN ("
            "'active', "
            "'revoked'"
            ")",
            name=(
                "ck_construction_access_grants_status"
            ),
        ),
        CheckConstraint(
            "scope_type <> 'tenant' "
            "OR scope_id = tenant_id",
            name=(
                "ck_construction_access_grants_"
                "tenant_scope"
            ),
        ),
        CheckConstraint(
            "scope_type <> 'tenant' "
            "OR role IN ('OWNER', 'ADMIN')",
            name=(
                "ck_construction_access_grants_"
                "tenant_role_scope"
            ),
        ),
        CheckConstraint(
            "expires_at IS NULL "
            "OR expires_at > granted_at",
            name=(
                "ck_construction_access_grants_"
                "expiration"
            ),
        ),
        CheckConstraint(
            "("
            "status = 'active' "
            "AND revoked_at IS NULL "
            "AND revoked_by_user_id IS NULL"
            ") OR ("
            "status = 'revoked' "
            "AND revoked_at IS NOT NULL "
            "AND revoked_by_user_id IS NOT NULL"
            ")",
            name=(
                "ck_construction_access_grants_"
                "revocation"
            ),
        ),
        ExcludeConstraint(
            ("tenant_id", "="),
            ("user_id", "="),
            ("role", "="),
            ("scope_type", "="),
            ("scope_id", "="),
            (
                text(
                    "tstzrange("
                    "granted_at, "
                    "expires_at, "
                    "'[)'"
                    ")"
                ),
                "&&",
            ),
            where=text(
                "status = 'active'"
            ),
            using="gist",
            name=(
                "ex_construction_access_grants_"
                "active_period"
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
    role: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    scope_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(
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
    granted_by_user_id: Mapped[
        uuid.UUID
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
    revoked_by_user_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    revoked_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ConstructionClient(Base):
    __tablename__ = "construction_clients"
    __table_args__ = (
        CheckConstraint(
            "client_type IN ("
            "'person', "
            "'company'"
            ")",
            name=(
                "ck_construction_clients_"
                "client_type"
            ),
        ),
        CheckConstraint(
            "row_version > 0",
            name="ck_construction_clients_row_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name=(
                "uq_construction_clients_"
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
    display_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    client_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    phone: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    email: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    notes: Mapped[
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
    deleted_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


    linked_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_construction_clients_linked_user",
            onupdate="NO ACTION",
            ondelete="NO ACTION",
        ),
        nullable=True,
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    __mapper_args__ = {"version_id_col": row_version}


class ConstructionProject(Base):
    __tablename__ = "construction_projects"
    # Native ck_construction_projects_currency enforces the reviewed PG format.
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'draft', "
            "'planning', "
            "'active', "
            "'completed', "
            "'cancelled'"
            ")",
            name=(
                "ck_construction_projects_status"
            ),
        ),
        CheckConstraint(
            "address_verification_status IN ("
            "'pending', "
            "'verified', "
            "'failed'"
            ")",
            name=(
                "ck_construction_projects_"
                "address_status"
            ),
        ),
        CheckConstraint(
            "row_version > 0",
            name=(
                "ck_construction_projects_"
                "row_version"
            ),
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name=(
                "uq_construction_projects_"
                "tenant_id_id"
            ),
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "client_id",
            ],
            [
                "construction_clients.tenant_id",
                "construction_clients.id",
            ],
            name=(
                "fk_construction_projects_"
                "tenant_client"
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
    client_id: Mapped[
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
        default="draft",
    )
    responsible_user_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    comment: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    address_raw: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    address_formatted: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    country_code: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    region: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    city: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    postal_code: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    latitude: Mapped[
        Optional[Decimal]
    ] = mapped_column(
        Numeric,
        nullable=True,
    )
    longitude: Mapped[
        Optional[Decimal]
    ] = mapped_column(
        Numeric,
        nullable=True,
    )
    address_provider: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    provider_place_id: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )
    address_verification_status: Mapped[
        str
    ] = mapped_column(
        Text,
        nullable=False,
        default="pending",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
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
    deleted_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    row_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    __mapper_args__ = {
        "version_id_col": row_version,
    }


class ConstructionProjectArea(Base):
    __tablename__ = "construction_project_areas"
    __table_args__ = (
        CheckConstraint(
            "area_type IN ("
            "'ROOM', "
            "'PROJECT_GENERAL', "
            "'EXTERIOR', "
            "'OTHER'"
            ")",
            name=(
                "ck_construction_project_areas_type"
            ),
        ),
        CheckConstraint(
            "status IN ("
            "'draft', "
            "'measured', "
            "'confirmed'"
            ")",
            name=(
                "ck_construction_project_areas_"
                "status"
            ),
        ),
        CheckConstraint(
            "row_version > 0",
            name=(
                "ck_construction_project_areas_"
                "row_version"
            ),
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name=(
                "uq_construction_project_areas_"
                "tenant_id_id"
            ),
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "project_id",
            ],
            [
                "construction_projects.tenant_id",
                "construction_projects.id",
            ],
            ondelete="CASCADE",
            name=(
                "fk_construction_project_areas_"
                "tenant_project"
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
    project_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    area_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    sort_order: Mapped[
        Optional[int]
    ] = mapped_column(
        Integer,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="draft",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
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
    deleted_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    row_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    __mapper_args__ = {
        "version_id_col": row_version,
    }


class ConstructionAreaElement(Base):
    __tablename__ = (
        "construction_area_elements"
    )
    __table_args__ = (
        CheckConstraint(
            "element_type IN ("
            "'WALL', "
            "'SLAB', "
            "'CEILING', "
            "'FLOOR', "
            "'WINDOW', "
            "'DOOR', "
            "'NICHE', "
            "'OPENING', "
            "'ENGINEERING_POINT', "
            "'LINEAR_ROUTE', "
            "'OTHER'"
            ")",
            name=(
                "ck_construction_area_elements_type"
            ),
        ),
        CheckConstraint(
            "length(btrim(name)) > 0",
            name=(
                "ck_construction_area_elements_name"
            ),
        ),
        CheckConstraint(
            "jsonb_typeof(geometry_json) = "
            "'object' "
            "AND geometry_json ? "
            "'schema_version' "
            "AND jsonb_typeof("
            "geometry_json -> 'schema_version'"
            ") = 'number' "
            "AND geometry_json ->> "
            "'schema_version' = '1'",
            name=(
                "ck_construction_area_elements_"
                "geometry"
            ),
        ),
        CheckConstraint(
            "parent_element_id IS NULL "
            "OR element_type IN ("
            "'WINDOW', "
            "'DOOR', "
            "'OPENING'"
            ")",
            name=(
                "ck_construction_area_elements_"
                "parent_type"
            ),
        ),
        CheckConstraint(
            "row_version > 0",
            name=(
                "ck_construction_area_elements_"
                "row_version"
            ),
        ),
        UniqueConstraint(
            "tenant_id",
            "project_area_id",
            "id",
            name=(
                "uq_construction_area_elements_"
                "tenant_area_id"
            ),
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "project_area_id",
            ],
            [
                "construction_project_areas."
                "tenant_id",
                "construction_project_areas.id",
            ],
            name=(
                "fk_construction_area_elements_"
                "tenant_area"
            ),
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "project_area_id",
                "parent_element_id",
            ],
            [
                "construction_area_elements."
                "tenant_id",
                "construction_area_elements."
                "project_area_id",
                "construction_area_elements.id",
            ],
            name=(
                "fk_construction_area_elements_"
                "parent_wall"
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
    project_area_id: Mapped[
        uuid.UUID
    ] = mapped_column(
        nullable=False,
    )
    parent_element_id: Mapped[
        Optional[uuid.UUID]
    ] = mapped_column(
        nullable=True,
    )
    element_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    sort_order: Mapped[
        Optional[int]
    ] = mapped_column(
        Integer,
        nullable=True,
    )
    geometry_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
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
    deleted_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    row_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    __mapper_args__ = {
        "version_id_col": row_version,
    }


class ConstructionTenantSetting(Base):
    __tablename__ = "construction_tenant_settings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "onboarding_tax_profile_id"],
            ["construction_tax_profiles.tenant_id", "construction_tax_profiles.id"],
            name="fk_construction_settings_onboarding_tax_profile",
            onupdate="NO ACTION", ondelete="RESTRICT",
        ),
        CheckConstraint(
            "accent_color IS NULL OR "
            "accent_color ~ '^#[0-9A-Fa-f]{6}$'",
            name="ck_construction_tenant_settings_accent_color",
        ),
        CheckConstraint(
            "row_version > 0",
            name="ck_construction_tenant_settings_row_version",
        ),
        CheckConstraint(
            "(onboarding_current_step IS NULL) = (onboarding_step_states IS NULL)",
            name="ck_construction_tenant_settings_onboarding_pair",
        ),
        CheckConstraint(
            "onboarding_current_step IS NULL OR onboarding_current_step IN "
            "('COMPANY', 'LOCALIZATION', 'TAXES', 'PRICING_PROFILES', "
            "'WORK_PRICES', 'MATERIALS_SUPPLIERS', 'MEASUREMENT_TEMPLATES')",
            name="ck_construction_tenant_settings_onboarding_current_step",
        ),
        CheckConstraint(
            "onboarding_step_states IS NULL OR CASE "
            "WHEN jsonb_typeof(onboarding_step_states) = 'object' THEN COALESCE("
            "onboarding_step_states ?& ARRAY['COMPANY', 'LOCALIZATION', 'TAXES', 'PRICING_PROFILES', "
            "'WORK_PRICES', 'MATERIALS_SUPPLIERS', 'MEASUREMENT_TEMPLATES']::text[] "
            "AND onboarding_step_states - ARRAY['COMPANY', 'LOCALIZATION', 'TAXES', 'PRICING_PROFILES', "
            "'WORK_PRICES', 'MATERIALS_SUPPLIERS', 'MEASUREMENT_TEMPLATES']::text[] = '{}'::jsonb "
            "AND onboarding_step_states ->> 'COMPANY' IN ('PENDING', 'COMPLETED') "
            "AND onboarding_step_states ->> 'LOCALIZATION' IN ('PENDING', 'COMPLETED') "
            "AND onboarding_step_states ->> 'TAXES' IN ('PENDING', 'COMPLETED') "
            "AND onboarding_step_states ->> 'PRICING_PROFILES' IN ('PENDING', 'COMPLETED') "
            "AND onboarding_step_states ->> 'WORK_PRICES' IN ('PENDING', 'COMPLETED', 'SKIPPED') "
            "AND onboarding_step_states ->> 'MATERIALS_SUPPLIERS' IN ('PENDING', 'COMPLETED', 'SKIPPED') "
            "AND onboarding_step_states ->> 'MEASUREMENT_TEMPLATES' IN ('PENDING', 'COMPLETED'), false) "
            "ELSE false END",
            name="ck_construction_tenant_settings_onboarding_step_states",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"),
        primary_key=True,
        nullable=False,
    )
    display_name_override: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    phone_override: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    email_override: Mapped[Optional[str]] = mapped_column(
        String(320),
        nullable=True,
    )
    website_override: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    logo_file_id_override: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("file_storage_objects.id"),
        nullable=True,
    )
    document_header_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    document_footer_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    accent_color: Mapped[Optional[str]] = mapped_column(
        String(7),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    onboarding_current_step: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    onboarding_step_states: Mapped[Optional[dict]] = mapped_column(
        JSONB(none_as_null=True), nullable=True,
    )
    onboarding_tax_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True,
    )

    __mapper_args__ = {
        "version_id_col": row_version,
    }


class TenantCompanyBankAccount(Base):
    __tablename__ = "tenant_company_bank_accounts"
    __table_args__ = (
        CheckConstraint(
            "NULLIF(btrim(iban), '') IS NOT NULL OR "
            "NULLIF(btrim(account_number), '') IS NOT NULL",
            name="ck_tenant_company_bank_accounts_number_present",
        ),
        CheckConstraint(
            "row_version > 0",
            name="ck_tenant_company_bank_accounts_row_version",
        ),
        Index(
            "uq_tenant_company_bank_accounts_active_primary_currency",
            "tenant_company_profile_id",
            "currency",
            unique=True,
            postgresql_where=text("is_active AND is_primary"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_company_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant_company_profiles.id"),
        nullable=False,
    )
    label: Mapped[Optional[str]] = mapped_column(String(100))
    account_holder_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    bank_name: Mapped[Optional[str]] = mapped_column(String(255))
    country_code: Mapped[Optional[str]] = mapped_column(CHAR(2))
    iban: Mapped[Optional[str]] = mapped_column(String(34))
    bic_swift: Mapped[Optional[str]] = mapped_column(String(11))
    account_number: Mapped[Optional[str]] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    show_in_documents: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    __mapper_args__ = {"version_id_col": row_version}


class ConstructionInvitation(Base):
    __tablename__ = "construction_invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'expired', 'revoked')",
            name="ck_construction_invitations_status",
        ),
        CheckConstraint(
            "role IN ('OWNER', 'ADMIN', 'SURVEYOR', 'ESTIMATOR', "
            "'PROJECT_MANAGER', 'EXECUTOR', 'OBSERVER')",
            name="ck_construction_invitations_role",
        ),
        CheckConstraint(
            "length(btrim(email)) > 0",
            name="ck_construction_invitations_email",
        ),
        CheckConstraint(
            "row_version > 0",
            name="ck_construction_invitations_row_version",
        ),
        UniqueConstraint(
            "tenant_id", "id",
            name="uq_construction_invitations_tenant_id_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'"))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    grant_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    delivery_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        server_default=text("'{}'::jsonb"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc))
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"))

    __mapper_args__ = {"version_id_col": row_version}


class ConstructionInvitationScope(Base):
    __tablename__ = "construction_invitation_scopes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "invitation_id"],
            ["construction_invitations.tenant_id", "construction_invitations.id"],
            name="fk_construction_invitation_scopes_tenant_invitation",
        ),
        UniqueConstraint(
            "tenant_id", "invitation_id", "scope_type", "scope_id",
            name="uq_construction_invitation_scopes_target",
        ),
        CheckConstraint(
            "scope_type IN ('tenant', 'project', 'document')",
            name="ck_construction_invitation_scopes_type",
        ),
        CheckConstraint(
            "scope_type <> 'tenant' OR scope_id = tenant_id",
            name="ck_construction_invitation_scopes_tenant_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, nullable=False, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    invitation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


class ConstructionLead(Base):
    __tablename__ = "construction_leads"
    __table_args__ = (
        CheckConstraint('length(btrim(source)) > 0', name='ck_construction_leads_source'),
        CheckConstraint(
            "status IN ('new', 'contacted', 'measurement_scheduled', 'measurement_completed', "
            "'estimate_preparation', 'proposal_sent', 'negotiation', 'won', 'lost')",
            name="ck_construction_leads_status",
        ),
        CheckConstraint("length(btrim(name)) > 0", name="ck_construction_leads_name"),
        CheckConstraint('length(btrim(work_type_description)) > 0', name='ck_construction_leads_work_type_description'),
        CheckConstraint(
            "(phone IS NOT NULL AND length(btrim(phone)) > 0) OR "
            "(email IS NOT NULL AND length(btrim(email)) > 0)",
            name="ck_construction_leads_contact",
        ),
        CheckConstraint("row_version > 0", name="ck_construction_leads_row_version"),
        UniqueConstraint("tenant_id", "id", name="uq_construction_leads_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "converted_client_id"],
            ["construction_clients.tenant_id", "construction_clients.id"],
            name="fk_construction_leads_tenant_converted_client",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "converted_project_id"],
            ["construction_projects.tenant_id", "construction_projects.id"],
            name="fk_construction_leads_tenant_converted_project",
        ),
        Index("ix_construction_leads_tenant_updated_id", "tenant_id", "updated_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    address_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    work_type_description: Mapped[str] = mapped_column(Text, nullable=False)
    responsible_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="new", server_default=text("'new'"),
    )
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    converted_client_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    converted_project_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"),
    )

    __mapper_args__ = {"version_id_col": row_version}


class ConstructionActivity(Base):
    __tablename__ = "construction_activities"
    __table_args__ = (
        CheckConstraint(
            "type IN ('call', 'message', 'meeting', 'site_visit', 'follow_up', 'task', 'other')",
            name="ck_construction_activities_type",
        ),
        CheckConstraint("status IN ('planned', 'completed', 'cancelled')",
                        name="ck_construction_activities_status"),
        CheckConstraint("lead_id IS NOT NULL OR client_id IS NOT NULL OR project_id IS NOT NULL",
                        name="ck_construction_activities_links"),
        CheckConstraint("length(btrim(timezone)) > 0", name="ck_construction_activities_timezone"),
        CheckConstraint("row_version > 0", name="ck_construction_activities_row_version"),
        CheckConstraint(
            "(status = 'planned' AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND completed_at IS NULL AND cancelled_at IS NOT NULL)",
            name="ck_construction_activities_completion",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_construction_activities_tenant_id_id"),
        ForeignKeyConstraint(["tenant_id", "lead_id"],
            ["construction_leads.tenant_id", "construction_leads.id"],
            name="fk_construction_activities_tenant_lead"),
        ForeignKeyConstraint(["tenant_id", "client_id"],
            ["construction_clients.tenant_id", "construction_clients.id"],
            name="fk_construction_activities_tenant_client"),
        ForeignKeyConstraint(["tenant_id", "project_id"],
            ["construction_projects.tenant_id", "construction_projects.id"],
            name="fk_construction_activities_tenant_project"),
        Index("ix_construction_activities_next_action_lead", "tenant_id", "lead_id", "due_at", "id",
              postgresql_where=text("status = 'planned' AND deleted_at IS NULL"),
              sqlite_where=text("status = 'planned' AND deleted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="planned", server_default=text("'planned'"))
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    responsible_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=text("1"))

    __mapper_args__ = {"version_id_col": row_version}


class PlatformTelegramBindingToken(Base):
    """Platform verification challenge; canonical accounts remain UserAccount.

    The secret is hash-only. Target user, initiation tenant and initiator are
    persisted from authorized server context and are not changed by consume.
    Token mutations and their mandatory audit must share the caller transaction.
    """

    __tablename__ = "platform_telegram_binding_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_platform_telegram_binding_tokens_hash"),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_platform_telegram_binding_tokens_hash",
        ),
        CheckConstraint(
            "expires_at = issued_at + interval '15 minutes'",
            name="ck_platform_telegram_binding_tokens_ttl",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR (consumed_at >= issued_at AND consumed_at < expires_at)",
            name="ck_platform_telegram_binding_tokens_consumed_at",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_platform_telegram_binding_tokens_revoked_at",
        ),
        CheckConstraint("row_version > 0", name="ck_platform_telegram_binding_tokens_row_version"),
        Index("ix_platform_telegram_binding_tokens_user_issued", "user_id", "issued_at", "id"),
        Index("ix_platform_telegram_binding_tokens_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
    )
    initiated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"),
    )

    __mapper_args__ = {"version_id_col": row_version}

class ConstructionClientUserLinkRequest(Base):
    """One-time confirmation context, not a user/account/membership or grant.

    The target confirms a specific client/base version and previous identity.
    The previous identity is a historical snapshot, not a new identity relation.
    Only the token hash is persisted. Expiry is explicit; no Telegram TTL is
    inherited. A later consume service must authenticate the target, recheck
    edit authority and atomically update client, request and mandatory audit.
    """

    __tablename__ = "construction_client_user_link_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["construction_clients.tenant_id", "construction_clients.id"],
            name="fk_con_client_link_requests_client",
            onupdate="NO ACTION", ondelete="NO ACTION",
        ),
        UniqueConstraint("token_hash", name="uq_con_client_link_requests_hash"),
        CheckConstraint(
            "length(token_hash) = 64 AND translate(token_hash, '0123456789abcdef', '') = ''",
            name="ck_con_client_link_requests_hash",
        ),
        CheckConstraint("expires_at > issued_at", name="ck_con_client_link_requests_expiry"),
        CheckConstraint(
            "consumed_at IS NULL OR (consumed_at >= issued_at AND consumed_at < expires_at)",
            name="ck_con_client_link_requests_consumed_at",
        ),
        CheckConstraint("expected_client_row_version > 0", name="ck_con_client_link_requests_base_version"),
        CheckConstraint("row_version > 0", name="ck_con_client_link_requests_version"),
        CheckConstraint(
            "invalidated_at IS NULL OR (invalidated_at >= issued_at AND invalidated_at < expires_at)",
            name="ck_con_client_link_requests_invalidated_at",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR invalidated_at IS NULL",
            name="ck_con_client_link_requests_terminal",
        ),
        Index("ix_con_client_link_requests_client", "tenant_id", "client_id", "issued_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    initiated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_con_client_link_requests_initiator",
            onupdate="NO ACTION", ondelete="NO ACTION"), nullable=False,
    )
    target_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_con_client_link_requests_target",
            onupdate="NO ACTION", ondelete="NO ACTION"), nullable=False,
    )
    expected_linked_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    expected_client_row_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"),
    )
    invalidated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    __mapper_args__ = {"version_id_col": row_version}


class ConstructionTaxProfile(Base):
    """Organization tax configuration; rates and document snapshots are separate."""
    __tablename__ = "construction_tax_profiles"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="construction_tax_profiles_pkey"),
        UniqueConstraint("tenant_id", "id", name="uq_construction_tax_profiles_tenant_id_id"),
        CheckConstraint("name = btrim(name) AND char_length(name) BETWEEN 1 AND 255",
                        name="ck_construction_tax_profiles_name"),
        CheckConstraint("client_price_tax_mode IN ('TAX_EXCLUSIVE', 'TAX_INCLUSIVE')",
                        name="ck_construction_tax_profiles_client_mode"),
        CheckConstraint("material_cost_basis IN ('NET', 'GROSS')",
                        name="ck_construction_tax_profiles_material_basis"),
        CheckConstraint("row_version > 0", name="ck_construction_tax_profiles_row_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_construction_tax_profiles_tenant",
                   ondelete="CASCADE", onupdate="NO ACTION"), nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    client_price_tax_mode: Mapped[str] = mapped_column(Text, nullable=False)
    material_cost_basis: Mapped[str] = mapped_column(Text, nullable=False)
    row_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=text("1"))
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_construction_tax_profiles_creator",
                   ondelete="RESTRICT", onupdate="NO ACTION"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __mapper_args__ = {"version_id_col": row_version}


class ConstructionTaxRule(Base):
    """Tenant/profile-bound tax rate; document snapshots are independent."""
    __tablename__ = "construction_tax_rules"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="construction_tax_rules_pkey"),
        UniqueConstraint(
            "tenant_id", "id",
            name="uq_construction_tax_rules_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id", "tax_profile_id", "category",
            name="uq_construction_tax_rules_profile_category",
        ),
        CheckConstraint(
            "category IN ('WORK', 'MATERIAL', 'SERVICE', 'EQUIPMENT', 'OTHER')",
            name="ck_construction_tax_rules_category",
        ),
        CheckConstraint(
            "rate_percent BETWEEN 0 AND 100",
            name="ck_construction_tax_rules_rate_percent",
        ),
        CheckConstraint(
            "row_version > 0",
            name="ck_construction_tax_rules_row_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tax_profile_id"],
            ["construction_tax_profiles.tenant_id", "construction_tax_profiles.id"],
            name="fk_construction_tax_rules_profile",
            ondelete="CASCADE", onupdate="NO ACTION",
        ),
        Index(
            "ix_construction_tax_rules_profile_category",
            "tenant_id", "tax_profile_id", "category", "id",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tenants.id", name="fk_construction_tax_rules_tenant",
            ondelete="CASCADE", onupdate="NO ACTION",
        ),
        nullable=False,
    )
    tax_profile_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    rate_percent: Mapped[Decimal] = mapped_column(
        Numeric(asdecimal=True), nullable=False,
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"),
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id", name="fk_construction_tax_rules_creator",
            ondelete="RESTRICT", onupdate="NO ACTION",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    __mapper_args__ = {"version_id_col": row_version}


class ConstructionPricingProfile(Base):
    """CON-017 organization price levels; historical snapshots are independent."""
    __tablename__ = "construction_pricing_profiles"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="construction_pricing_profiles_pkey"),
        UniqueConstraint("tenant_id", "id",
                         name="uq_construction_pricing_profiles_tenant_id_id"),
        UniqueConstraint("tenant_id", "code",
                         name="uq_construction_pricing_profiles_tenant_code"),
        CheckConstraint("code IN ('ECONOMY', 'STANDARD', 'PREMIUM')",
                        name="ck_construction_pricing_profiles_code"),
        CheckConstraint(
            "work_coefficient > 0 AND work_coefficient::text "
            "NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_construction_pricing_profiles_work_coefficient"),
        CheckConstraint(
            "material_markup_percent >= 0 AND material_markup_percent <= 100",
            name="ck_construction_pricing_profiles_material_markup_percent"),
        CheckConstraint("row_version > 0",
                        name="ck_construction_pricing_profiles_row_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_construction_pricing_profiles_tenant",
                   ondelete="CASCADE", onupdate="NO ACTION"), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    work_coefficient: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    material_markup_percent: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"))
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_construction_pricing_profiles_creator",
                   ondelete="RESTRICT", onupdate="NO ACTION"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __mapper_args__ = {"version_id_col": row_version}


class ConstructionProjectPricingAdjustment(Base):
    """Explicit project sale-price coefficient; published snapshots are independent."""
    __tablename__ = "construction_project_pricing_adjustments"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="construction_project_pricing_adjustments_pkey"),
        UniqueConstraint("tenant_id", "id",
                         name="uq_construction_project_pricing_adjustments_tenant_id_id"),
        CheckConstraint("name = btrim(name) AND char_length(name) BETWEEN 1 AND 255",
                        name="ck_construction_project_pricing_adjustments_name"),
        CheckConstraint("coefficient > 0 AND coefficient::text NOT IN ('NaN', 'Infinity', '-Infinity')",
                        name="ck_construction_project_pricing_adjustments_coefficient"),
        CheckConstraint("row_version > 0",
                        name="ck_construction_project_pricing_adjustments_row_version"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"], ["construction_projects.tenant_id", "construction_projects.id"],
            name="fk_construction_project_pricing_adjustments_project",
            ondelete="CASCADE", onupdate="NO ACTION"),
        Index("ix_construction_project_pricing_adjustments_project", "tenant_id", "project_id", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_construction_project_pricing_adjustments_tenant",
                   ondelete="CASCADE", onupdate="NO ACTION"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    coefficient: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    row_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=text("1"))
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_construction_project_pricing_adjustments_creator",
                   ondelete="RESTRICT", onupdate="NO ACTION"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __mapper_args__ = {"version_id_col": row_version}


class ConstructionCalculationSetting(Base):
    """Organization calculation configuration; document rounding is separate."""
    __tablename__ = "construction_calculation_settings"
    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id", name="construction_calculation_settings_pkey"),
        CheckConstraint(
            "money_input_precision BETWEEN 0 AND 6",
            name="ck_con019_money_precision"),
        CheckConstraint(
            "quantity_precision BETWEEN 0 AND 6",
            name="ck_con019_quantity_precision"),
        CheckConstraint(
            "minimum_job_amount IS NULL OR "
            "(minimum_job_amount >= 0 AND minimum_job_amount < 'Infinity'::numeric)",
            name="ck_con019_minimum_amount"),
        CheckConstraint(
            "(minimum_job_amount IS NULL) = (minimum_job_currency IS NULL)",
            name="ck_con019_minimum_pair"),
        CheckConstraint(
            "minimum_job_currency IS NULL OR "
            "(char_length(minimum_job_currency) = 3 AND "
            "translate(minimum_job_currency, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', '') = '')",
            name="ck_con019_minimum_currency"),
        CheckConstraint(
            "supplier_stale_price_days BETWEEN 0 AND 3650",
            name="ck_con019_supplier_days"),
        CheckConstraint(
            "default_technological_waste_percent IS NULL OR "
            "default_technological_waste_percent BETWEEN 0 AND 100",
            name="ck_con019_waste_percent"),
        CheckConstraint(
            "default_local_repair_percent IS NULL OR "
            "default_local_repair_percent BETWEEN 0 AND 100",
            name="ck_con019_local_repair_percent"),
        CheckConstraint("row_version > 0", name="ck_con019_row_version"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_con019_tenant", ondelete="CASCADE"),
        primary_key=True, nullable=False)
    money_input_precision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quantity_precision: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=4, server_default=text("4"))
    minimum_job_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(asdecimal=True), nullable=True)
    minimum_job_currency: Mapped[Optional[str]] = mapped_column(
        String(3), nullable=True)
    supplier_stale_price_days: Mapped[int] = mapped_column(Integer, nullable=False)
    default_technological_waste_percent: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(asdecimal=True), nullable=True)
    default_local_repair_percent: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(asdecimal=True), nullable=True)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"))
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_con019_creator", ondelete="RESTRICT"),
        nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))

    __mapper_args__ = {"version_id_col": row_version}

# con016_canonical_categories_unique_v1

# onboarding_explicit_tax_profile_v1


class ConstructionStarterMeasurementTemplate(Base):
    """Platform-owned starter identity; Construction runtime reads only."""
    __tablename__ = "construction_starter_measurement_templates"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="construction_starter_measurement_templates_pkey"),
        UniqueConstraint("code", name="uq_con_starter_templates_code"),
        CheckConstraint(
            "code = btrim(code) AND code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_con_starter_templates_code"),
        CheckConstraint("row_version > 0", name="ck_con_starter_templates_version"),
        CheckConstraint(
            "isfinite(created_at) AND isfinite(updated_at)",
            name="ck_con_starter_templates_time"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __mapper_args__ = {"version_id_col": row_version}


class ConstructionStarterMeasurementTemplateVersion(Base):
    """Published immutable source; database triggers reject mutation/truncate."""
    __tablename__ = "construction_starter_measurement_template_versions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="construction_starter_measurement_template_versions_pkey"),
        UniqueConstraint("template_id", "version", name="uq_con_starter_versions_template_version"),
        UniqueConstraint("template_id", "id", name="uq_con_starter_versions_template_id_id"),
        CheckConstraint("version > 0", name="ck_con_starter_versions_version"),
        CheckConstraint("definition_schema_version = 1", name="ck_con_starter_versions_schema"),
        CheckConstraint(
            "source_language = btrim(source_language) "
            "AND char_length(source_language) BETWEEN 1 AND 10 "
            "AND translate(source_language, "
            "'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-', '') = ''",
            name="ck_con_starter_versions_language"),
        CheckConstraint(
            "length(content_hash) = 64 AND translate(content_hash, '0123456789abcdef', '') = ''",
            name="ck_con_starter_versions_hash"),
        CheckConstraint(
            "isfinite(published_at) AND isfinite(created_at)",
            name="ck_con_starter_versions_time"),
        CheckConstraint("""
            CASE WHEN jsonb_typeof(definition) = 'object' THEN COALESCE(
                definition ?& ARRAY['title_key','description_key','sections','labels']::text[]
                AND definition - ARRAY['title_key','description_key','sections','labels']::text[] = '{}'::jsonb
                AND jsonb_typeof(definition -> 'title_key') = 'string'
                AND char_length(definition ->> 'title_key') BETWEEN 1 AND 128
                AND jsonb_typeof(definition -> 'labels') = 'object'
                AND jsonb_typeof(definition -> 'labels' -> source_language) = 'object'
                AND jsonb_typeof(
                    definition -> 'labels' -> source_language -> (definition ->> 'title_key')
                ) = 'string'
                AND CASE WHEN jsonb_typeof(definition -> 'description_key') = 'null'
                    THEN true ELSE
                    jsonb_typeof(definition -> 'description_key') = 'string'
                    AND char_length(definition ->> 'description_key') BETWEEN 1 AND 128
                    AND jsonb_typeof(
                        definition -> 'labels' -> source_language -> (definition ->> 'description_key')
                    ) = 'string'
                END
                AND CASE WHEN jsonb_typeof(definition -> 'sections') = 'array'
                    THEN jsonb_array_length(definition -> 'sections') > 0 ELSE false END,
                false)
            ELSE false END
        """, name="ck_con_starter_versions_definition"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "construction_starter_measurement_templates.id",
            name="fk_con_starter_versions_template",
            onupdate="NO ACTION", ondelete="RESTRICT"),
        nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB(none_as_null=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))


# con_o003_organization_template_orm_v1
from sqlalchemy import FetchedValue as _ConTemplateFetchedValue


class ConstructionMeasurementTemplate(Base):
    """Organization-owned template identity."""
    __tablename__ = "construction_measurement_templates"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="con_mt_pk"),
        UniqueConstraint("tenant_id", "id", name="con_mt_tenant_id_uq"),
        UniqueConstraint("tenant_id", "code", name="con_mt_tenant_code_uq"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="con_mt_tenant_fk", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="con_mt_creator_fk", ondelete="RESTRICT"),
        CheckConstraint("code ~ '^[A-Z][A-Z0-9_]{0,63}$'", name="con_mt_code_ck"),
        CheckConstraint("row_version > 0", name="con_mt_version_ck"),
        CheckConstraint(
            "isfinite(created_at) AND isfinite(updated_at)",
            name="con_mt_time_ck"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"),
        server_onupdate=_ConTemplateFetchedValue())
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
        server_onupdate=_ConTemplateFetchedValue())

    __mapper_args__ = {"version_id_col": row_version, "eager_defaults": True}


class ConstructionMeasurementTemplateVersion(Base):
    """Version header; published immutability is enforced by the installed guard."""
    __tablename__ = "construction_measurement_template_versions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="con_mtv_pk"),
        UniqueConstraint("tenant_id", "id", name="con_mtv_tenant_id_uq"),
        UniqueConstraint(
            "tenant_id", "template_id", "version", name="con_mtv_number_uq"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="con_mtv_tenant_fk", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            ["construction_measurement_templates.tenant_id",
             "construction_measurement_templates.id"],
            name="con_mtv_template_fk", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["source_starter_version_id"],
            ["construction_starter_measurement_template_versions.id"],
            name="con_mtv_source_fk", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="con_mtv_creator_fk", ondelete="RESTRICT"),
        CheckConstraint("version > 0", name="con_mtv_number_ck"),
        CheckConstraint("row_version > 0", name="con_mtv_row_version_ck"),
        CheckConstraint("definition_schema_version = 1", name="con_mtv_schema_ck"),
        CheckConstraint(
            "(status = 'DRAFT' AND published_at IS NULL) OR "
            "(status = 'PUBLISHED' AND published_at IS NOT NULL "
            "AND content_hash IS NOT NULL)",
            name="con_mtv_lifecycle_ck"),
        CheckConstraint(
            "source_language ~ '^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$'",
            name="con_mtv_language_ck"),
        CheckConstraint(
            "title_key ~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$' AND "
            "(description_key IS NULL OR "
            "description_key ~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$')",
            name="con_mtv_keys_ck"),
        CheckConstraint("""
            COALESCE(
                jsonb_typeof(labels) = 'object'
                AND jsonb_typeof(labels -> source_language) = 'object'
                AND jsonb_typeof(labels -> source_language -> title_key) = 'string'
                AND (
                    description_key IS NULL
                    OR jsonb_typeof(
                        labels -> source_language -> description_key
                    ) = 'string'
                ),
                false
            )
        """, name="con_mtv_labels_ck"),
        CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name="con_mtv_hash_ck"),
        CheckConstraint(
            "isfinite(created_at) AND isfinite(updated_at) AND "
            "(published_at IS NULL OR isfinite(published_at))",
            name="con_mtv_time_ck"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_starter_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    definition_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    title_key: Mapped[str] = mapped_column(Text, nullable=False)
    description_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    labels: Mapped[dict] = mapped_column(JSONB(none_as_null=True), nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"),
        server_onupdate=_ConTemplateFetchedValue())
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
        server_onupdate=_ConTemplateFetchedValue())
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        server_onupdate=_ConTemplateFetchedValue())

    __mapper_args__ = {"version_id_col": row_version, "eager_defaults": True}


class ConstructionMeasurementTemplateSection(Base):
    """Section belonging to exactly one organization template version."""
    __tablename__ = "construction_measurement_template_sections"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="con_mts_pk"),
        UniqueConstraint(
            "tenant_id", "template_version_id", "id",
            name="con_mts_context_uq"),
        UniqueConstraint(
            "tenant_id", "template_version_id", "id", "work_phase_code",
            name="con_mts_phase_context_uq"),
        UniqueConstraint(
            "tenant_id", "template_version_id", "code", name="con_mts_code_uq"),
        UniqueConstraint(
            "tenant_id", "template_version_id", "sort_order", name="con_mts_order_uq"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="con_mts_tenant_fk", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "template_version_id"],
            ["construction_measurement_template_versions.tenant_id",
             "construction_measurement_template_versions.id"],
            name="con_mts_version_fk", ondelete="RESTRICT"),
        CheckConstraint(
            "code ~ '^[A-Z][A-Z0-9_]{0,63}$'", name="con_mts_code_ck"),
        CheckConstraint(
            "label_key ~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$'",
            name="con_mts_label_ck"),
        CheckConstraint("""
            work_phase_code IN (
                'DEMOLITION', 'PARTITIONS_OPENINGS', 'CHASING',
                'ROUGH_ELECTRICAL', 'ROUGH_WATER', 'ROUGH_DRAINAGE',
                'HVAC_VENTILATION', 'PLASTER_PREP', 'WATERPROOFING',
                'SCREED_LEVELING', 'TILING_STONE', 'PUTTY_PRIMER',
                'PAINT_FINISH', 'FINISH_FLOOR', 'DOORS_WINDOWS',
                'FINISH_ELECTRICAL', 'FINISH_PLUMBING',
                'EQUIPMENT_FURNITURE', 'FINAL_SEALING', 'CLEANING_WASTE'
            )
        """, name="con_mts_phase_ck"),
        CheckConstraint("sort_order >= 0", name="con_mts_order_ck"),
        CheckConstraint("row_version > 0", name="con_mts_row_version_ck"),
        CheckConstraint(
            "isfinite(created_at) AND isfinite(updated_at)", name="con_mts_time_ck"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    label_key: Mapped[str] = mapped_column(Text, nullable=False)
    work_phase_code: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"),
        server_onupdate=_ConTemplateFetchedValue())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
        server_onupdate=_ConTemplateFetchedValue())

    __mapper_args__ = {"version_id_col": row_version, "eager_defaults": True}


class ConstructionMeasurementTemplateQuestion(Base):
    """Data-driven question; work-type identities are resolved by domain services."""
    __tablename__ = "construction_measurement_template_questions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="con_mtq_pk"),
        UniqueConstraint(
            "tenant_id", "template_version_id", "code", name="con_mtq_code_uq"),
        UniqueConstraint(
            "tenant_id", "template_section_id", "sort_order", name="con_mtq_order_uq"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="con_mtq_tenant_fk", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "template_version_id"],
            ["construction_measurement_template_versions.tenant_id",
             "construction_measurement_template_versions.id"],
            name="con_mtq_version_fk", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "template_version_id", "template_section_id"],
            ["construction_measurement_template_sections.tenant_id",
             "construction_measurement_template_sections.template_version_id",
             "construction_measurement_template_sections.id"],
            name="con_mtq_section_fk", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "template_version_id", "template_section_id", "work_phase_code"],
            ["construction_measurement_template_sections.tenant_id",
             "construction_measurement_template_sections.template_version_id",
             "construction_measurement_template_sections.id",
             "construction_measurement_template_sections.work_phase_code"],
            name="con_mtq_section_phase_fk", ondelete="RESTRICT",
            deferrable=True, initially="DEFERRED"),
        CheckConstraint(
            "code ~ '^[A-Z][A-Z0-9_]{0,63}$'", name="con_mtq_code_ck"),
        CheckConstraint(
            "label_key ~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$'",
            name="con_mtq_label_ck"),
        CheckConstraint(
            "question_type IN ('BOOLEAN', 'NUMBER', 'TEXT', 'SINGLE_CHOICE', "
            "'MULTI_CHOICE', 'DATE', 'PHOTO', 'ELEMENT', 'CALCULATED')",
            name="con_mtq_type_ck"),
        CheckConstraint("""
            unit_code IS NULL OR (
                question_type IN ('NUMBER', 'CALCULATED')
                AND unit_code IN (
                    'MM', 'CM', 'M', 'M2', 'M3', 'ML', 'L', 'G', 'KG', 'PCS',
                    'PACKAGE', 'ROLL', 'BAG', 'SET', 'HOUR', 'DAY'
                )
            )
        """, name="con_mtq_unit_ck"),
        CheckConstraint("""
            (
                formula_type IS NULL OR (
                    question_type IN ('NUMBER', 'CALCULATED')
                    AND formula_type IN (
                        'DIRECT', 'RECTANGLE', 'WALL_AREA', 'WALL_NET_AREA',
                        'PERCENT_OF_BASE', 'PERIMETER', 'REVEAL_THREE_SIDES',
                        'LINEAR_LENGTH', 'COUNT', 'VOLUME'
                    )
                )
            )
            AND (question_type <> 'CALCULATED' OR formula_type IS NOT NULL)
        """, name="con_mtq_formula_ck"),
        CheckConstraint("""
            jsonb_typeof(input_schema) = 'object'
            AND jsonb_typeof(validation_schema) = 'object'
            AND (show_if IS NULL OR jsonb_typeof(show_if) = 'object')
        """, name="con_mtq_schemas_ck"),
        CheckConstraint("""
            jsonb_typeof(options) = 'array'
            AND jsonb_array_length(options) <= 128
            AND (
                (question_type IN ('SINGLE_CHOICE', 'MULTI_CHOICE')
                 AND jsonb_array_length(options) >= 1)
                OR
                (question_type NOT IN ('SINGLE_CHOICE', 'MULTI_CHOICE')
                 AND options = '[]'::jsonb)
            )
        """, name="con_mtq_options_ck"),
        CheckConstraint("""
            default_source IN ('NONE', 'ORG_CONSTANT', 'PROJECT_VALUE')
            AND CASE WHEN default_source = 'NONE' THEN
                default_value IS NULL
            ELSE COALESCE(
                jsonb_typeof(default_value) = 'object'
                AND jsonb_typeof(default_value -> 'field') = 'string'
                AND default_value - 'field' = '{}'::jsonb
                AND (default_value ->> 'field')
                    ~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$',
                false
            ) END
        """, name="con_mtq_default_ck"),
        CheckConstraint("""
            COALESCE(
                CASE WHEN work_type_source IS NULL THEN
                    work_type_id IS NULL
                    AND work_type_version_policy IS NULL
                    AND pinned_work_type_version_id IS NULL
                ELSE
                    work_type_source IN ('MASTER', 'ORGANIZATION')
                    AND work_type_id IS NOT NULL
                    AND (
                        (work_type_version_policy = 'CURRENT_PUBLISHED'
                         AND pinned_work_type_version_id IS NULL)
                        OR
                        (work_type_version_policy = 'PINNED'
                         AND pinned_work_type_version_id IS NOT NULL)
                    )
                END,
                false
            )
        """, name="con_mtq_work_binding_ck"),
        CheckConstraint(
            "photo_policy IN ('NONE', 'OPTIONAL', 'REQUIRED') "
            "AND (question_type <> 'PHOTO' OR photo_policy <> 'NONE')",
            name="con_mtq_photo_ck"),
        CheckConstraint("""
            (help_text_key IS NULL
             OR help_text_key ~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$')
            AND (help_media_ref IS NULL
                 OR help_media_ref ~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$')
        """, name="con_mtq_help_ck"),
        CheckConstraint("sort_order >= 0", name="con_mtq_order_ck"),
        CheckConstraint("row_version > 0", name="con_mtq_row_version_ck"),
        CheckConstraint(
            "isfinite(created_at) AND isfinite(updated_at)", name="con_mtq_time_ck"),
        Index(
            "ix_con_mtq_section", "tenant_id", "template_version_id",
            "template_section_id", "sort_order", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    template_section_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(Text, nullable=False)
    label_key: Mapped[str] = mapped_column(Text, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unit_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    formula_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict] = mapped_column(JSONB(none_as_null=True), nullable=False)
    validation_schema: Mapped[dict] = mapped_column(JSONB(none_as_null=True), nullable=False)
    options: Mapped[list] = mapped_column(JSONB(none_as_null=True), nullable=False)
    show_if: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    default_source: Mapped[str] = mapped_column(Text, nullable=False)
    default_value: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    work_type_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    work_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    work_type_version_policy: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pinned_work_type_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    work_phase_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_policy: Mapped[str] = mapped_column(Text, nullable=False)
    help_text_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    help_media_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"),
        server_onupdate=_ConTemplateFetchedValue())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
        server_onupdate=_ConTemplateFetchedValue())

    __mapper_args__ = {"version_id_col": row_version, "eager_defaults": True}


# CON_020B_CONTENT_TRANSLATION_MODEL_V1
class ConstructionContentTranslation(Base):
    __tablename__ = "construction_content_translations"
    __table_args__ = (
        PrimaryKeyConstraint(
            "id", name="construction_content_translations_pkey"),
        UniqueConstraint(
            "tenant_id", "entity_type", "entity_id", "field_name",
            "source_language", "target_language",
            "source_version", "source_text_hash",
            name="uq_con_content_translation_source"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_con_content_translation_tenant", ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["source_language"], ["languages.code"],
            name="fk_con_content_translation_source_language"),
        ForeignKeyConstraint(
            ["target_language"], ["languages.code"],
            name="fk_con_content_translation_target_language"),
        ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"],
            name="fk_con_content_translation_reviewer"),
        CheckConstraint(
            "entity_type ~ '^[a-z][a-z0-9_]*([.][a-z0-9_]+)*$'",
            name="ck_con_content_translation_entity_type"),
        CheckConstraint(
            "field_name ~ '^[a-z][a-z0-9_]*([.][a-z0-9_]+)*$'",
            name="ck_con_content_translation_field_name"),
        CheckConstraint(
            "source_language ~ '^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$'",
            name="ck_con_content_translation_source_language"),
        CheckConstraint(
            "target_language ~ '^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$'",
            name="ck_con_content_translation_target_language"),
        CheckConstraint(
            "source_version > 0",
            name="ck_con_content_translation_source_version"),
        CheckConstraint(
            "source_text_hash ~ '^[0-9a-f]{64}$'",
            name="ck_con_content_translation_source_hash"),
        CheckConstraint(
            "translated_text ~ '[^[:space:]]' "
            "AND octet_length(translated_text) <= 1048576",
            name="ck_con_content_translation_text"),
        CheckConstraint(
            "translation_status IN ('VERIFIED', 'AUTO_TRANSLATED', 'STALE')",
            name="ck_con_content_translation_status"),
        CheckConstraint(
            "row_version > 0",
            name="ck_con_content_translation_row_version"),
        CheckConstraint(
            "source_language <> target_language",
            name="ck_con_content_translation_distinct_languages"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_text_hash: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    translation_status: Mapped[str] = mapped_column(Text, nullable=False)
    translated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc))

    __mapper_args__ = {"version_id_col": row_version, "eager_defaults": True}
