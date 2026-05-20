import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, ForeignKey, DateTime, Text, Integer, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

# Оголошуємо модель tenants, щоб SQLAlchemy бачила зв'язок для ForeignKey
class Tenant(Base):
    __tablename__ = "tenants"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

# 6.1. Таблиця users за ТЗ
class User(Base):
    __tablename__ = "users"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    active_role: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language_code: Mapped[str] = mapped_column(String(10), default="ru")
    profile_completion_score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, default="active") # active/blocked/deleted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# ... далі йдуть класи UserAccount та UserRoleMapping без змін

# 6.2. Таблиця user_accounts за ТЗ
class UserAccount(Base):
    __tablename__ = "user_accounts"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(Text, default="telegram")
    platform_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# 6.3. Таблиця user_roles за ТЗ
class UserRoleMapping(Base):
    __tablename__ = "user_roles"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False) # specialist/client/admin/super_admin
    status: Mapped[str] = mapped_column(Text, default="active") # active/suspended/revoked
    granted_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)