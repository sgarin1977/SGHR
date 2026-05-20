from uuid import UUID

from database.repositories.legal import LegalRepository


REQUIRED_SPECIALIST_CONSENTS = (
    "terms",
    "privacy",
    "specialist_consent",
    "geo_consent",
    "translation_consent",
)


class MissingLegalDocumentError(Exception):
    pass


class LegalService:
    def __init__(self, repository: LegalRepository):
        self.repository = repository

    async def get_missing_specialist_consents(
        self,
        tenant_id: UUID,
        user_id: UUID,
        language: str = "ru",
    ):
        documents = await self.repository.get_current_documents(
            tenant_id=tenant_id,
            doc_types=REQUIRED_SPECIALIST_CONSENTS,
            language=language,
        )

        missing_doc_types = [
            doc_type for doc_type in REQUIRED_SPECIALIST_CONSENTS
            if doc_type not in documents
        ]
        if missing_doc_types:
            raise MissingLegalDocumentError(
                f"Missing active legal documents: {', '.join(missing_doc_types)}"
            )

        missing = []
        for doc_type in REQUIRED_SPECIALIST_CONSENTS:
            doc = documents[doc_type]
            accepted = await self.repository.has_active_consent(
                user_id=user_id,
                consent_type=doc.doc_type,
                version=doc.version,
            )
            if not accepted:
                missing.append(doc)

        return missing

    async def has_required_specialist_consents(
        self,
        tenant_id: UUID,
        user_id: UUID,
        language: str = "ru",
    ) -> bool:
        missing = await self.get_missing_specialist_consents(tenant_id, user_id, language)
        return not missing

    async def accept_required_specialist_consents(
        self,
        tenant_id: UUID,
        user_id: UUID,
        language: str = "ru",
        platform: str = "telegram",
    ) -> None:
        missing = await self.get_missing_specialist_consents(tenant_id, user_id, language)

        for doc in missing:
            await self.repository.accept_consent(
                tenant_id=tenant_id,
                user_id=user_id,
                consent_type=doc.doc_type,
                version=doc.version,
                platform=platform,
            )

        await self.repository.session.commit()