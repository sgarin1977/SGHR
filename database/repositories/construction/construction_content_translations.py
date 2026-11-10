"""Tenant-scoped exact translation cache; caller owns authority and transaction.

This repository does not resolve entities, call a provider, verify a translation,
mark historical versions stale, commit or roll back. Canonical source freshness
and access must be checked by the calling service before persistence.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from database.models import ConstructionContentTranslation
from services.construction.construction_content_translation import (
    ContentTranslationKey,
    ContentTranslationRecord,
    ContentTranslationStatus,
)

KEY_FIELDS = (
    "entity_type", "entity_id", "field_name",
    "source_language", "target_language",
    "source_version", "source_text_hash",
)
UNIQUE_COLUMNS = ("tenant_id",) + KEY_FIELDS
MAX_VERSION = 2**63 - 1


class ConstructionContentTranslationRepository:
    def __init__(self, session):
        if session is None or not callable(getattr(session, "execute", None)):
            raise ValueError("A transaction session is required.")
        self.session = session
        self.table = ConstructionContentTranslation.__table__

    def _insert(self):
        return insert(self.table)

    @staticmethod
    def _context(tenant_id, key):
        if (
            not isinstance(tenant_id, UUID)
            or not isinstance(key, ContentTranslationKey)
        ):
            raise ValueError("Exact tenant and translation key are required.")

    @staticmethod
    def _automatic(record):
        if (
            not isinstance(record, ContentTranslationRecord)
            or record.translation_status is not ContentTranslationStatus.AUTO_TRANSLATED
            or record.reviewed_by is not None
            or record.key.source_language == record.key.target_language
        ):
            raise ValueError("Only an unreviewed automatic translation is accepted.")

    @staticmethod
    def _time(value):
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("An aware persistence timestamp is required.")
        return value.astimezone(timezone.utc)

    def _where(self, tenant_id, key):
        self._context(tenant_id, key)
        return (
            self.table.c.tenant_id == tenant_id,
            *(self.table.c[name] == getattr(key, name) for name in KEY_FIELDS),
        )

    @staticmethod
    def _values(record):
        return {
            **{name: getattr(record.key, name) for name in KEY_FIELDS},
            "translated_text": record.translated_text,
            "translation_status": record.translation_status.value,
            "translated_at": record.translated_at,
            "reviewed_by": None,
        }

    async def get_exact(self, *, tenant_id, key, for_update=False):
        if type(for_update) is not bool:
            raise ValueError("Lock mode must be a boolean.")
        statement = select(self.table).where(*self._where(tenant_id, key))
        if for_update:
            statement = statement.with_for_update()
        row = (await self.session.execute(
            statement.execution_options(autoflush=False)
        )).mappings().one_or_none()
        return None if row is None else dict(row)

    async def insert_automatic(self, *, tenant_id, record, as_of):
        self._automatic(record)
        self._context(tenant_id, record.key)
        instant = self._time(as_of)
        statement = self._insert().values(
            tenant_id=tenant_id, **self._values(record),
            row_version=1, created_at=instant, updated_at=instant,
        ).on_conflict_do_nothing(
            index_elements=[self.table.c[name] for name in UNIQUE_COLUMNS],
        ).returning(self.table)
        row = (await self.session.execute(
            statement.execution_options(autoflush=False)
        )).mappings().one_or_none()
        # A conflict is returned to the service for a fresh authorized reread.
        return None if row is None else dict(row)

    async def replace_stale_automatic(
        self, *, tenant_id, record, expected_row_version, as_of,
    ):
        self._automatic(record)
        self._context(tenant_id, record.key)
        if (
            type(expected_row_version) is not int
            or not 1 <= expected_row_version < MAX_VERSION
        ):
            raise ValueError("An incrementable expected cache version is required.")
        instant = self._time(as_of)
        t = self.table
        statement = update(t).where(
            *self._where(tenant_id, record.key),
            t.c.translation_status == ContentTranslationStatus.STALE.value,
            t.c.row_version == expected_row_version,
        ).values(
            translated_text=record.translated_text,
            translation_status=ContentTranslationStatus.AUTO_TRANSLATED.value,
            translated_at=record.translated_at,
            reviewed_by=None,
            row_version=t.c.row_version + 1,
            updated_at=instant,
        ).returning(t)
        row = (await self.session.execute(
            statement.execution_options(autoflush=False)
        )).mappings().one_or_none()
        return None if row is None else dict(row)
