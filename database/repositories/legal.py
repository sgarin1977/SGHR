from typing import Iterable
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import LegalDocument, UserConsent


class LegalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_current_documents(
        self,
        tenant_id: UUID,
        doc_types: Iterable[str],
        language: str,
    ) -> dict[str, LegalDocument]:
        languages = [language, "en", "ru"]

        result = await self.session.execute(
            select(LegalDocument)
            .where(
                LegalDocument.tenant_id == tenant_id,
                LegalDocument.doc_type.in_(list(doc_types)),
                LegalDocument.language.in_(languages),
                LegalDocument.status == "active",
            )
            .order_by(
                LegalDocument.doc_type,
                LegalDocument.language,
                desc(LegalDocument.effective_from),
                desc(LegalDocument.created_at),
                desc(LegalDocument.id),
            )
        )
        docs = result.scalars().all()

        selected = {}
        def language_rank(doc: LegalDocument) -> int:
            return languages.index(doc.language) if doc.language in languages else 99

        def freshness_key(doc: LegalDocument):
            return (
                doc.effective_from or doc.created_at,
                doc.created_at,
                str(doc.id),
            )

        selected = {}
        for doc in docs:
            current = selected.get(doc.doc_type)
            if current is None:
                selected[doc.doc_type] = doc
                continue

            current_rank = language_rank(current)
            doc_rank = language_rank(doc)

            if doc_rank < current_rank:
                selected[doc.doc_type] = doc
                continue

            if doc_rank == current_rank and freshness_key(doc) > freshness_key(current):
                selected[doc.doc_type] = doc

        return selected

    async def has_active_consent(self, user_id: UUID, consent_type: str, version: str) -> bool:
        result = await self.session.execute(
            select(UserConsent.id).where(
                UserConsent.user_id == user_id,
                UserConsent.consent_type == consent_type,
                UserConsent.version == version,
                UserConsent.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none() is not None

    async def accept_consent(
        self,
        tenant_id: UUID,
        user_id: UUID,
        consent_type: str,
        version: str,
        platform: str = "telegram",
    ) -> UserConsent:
        consent = UserConsent(
            tenant_id=tenant_id,
            user_id=user_id,
            consent_type=consent_type,
            version=version,
            platform=platform,
        )
        self.session.add(consent)
        await self.session.flush()
        return consent