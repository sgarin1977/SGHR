"""Core projection of existing public.tenants, not a new table or ORM mapping."""
from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, Text, Uuid

TENANT_SETTINGS = Table(
    "tenants", MetaData(),
    Column("id", Uuid, primary_key=True),
    Column("status", Text),
    Column("default_language", String(10)),
    Column("default_currency", String(3)),
    Column("updated_at", DateTime(timezone=True)),
    Column("row_version", BigInteger),
)

# con_011_row_version_v1

# con_012_currency_settings_v1
