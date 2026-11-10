"""Versioned system-label translations in the existing Platform cache."""
from sqlalchemy import func, select
from database.models import Language, TranslationCache


class PlatformCatalogTranslationRepository:
    def __init__(self, session):
        self.session = session

    async def is_active_language(self, code):
        result = await self.session.execute(
            select(Language.code).where(Language.code == code, Language.is_active.is_(True))
            .execution_options(autoflush=False)
        )
        return result.scalar_one_or_none() == code

    async def read_labels(self, *, hashes, source_language, target_language, provider):
        if not hashes:
            return {}
        ranked = select(
            TranslationCache.source_text_hash,
            TranslationCache.translated_text,
            func.row_number().over(
                partition_by=TranslationCache.source_text_hash,
                order_by=(TranslationCache.created_at.desc(), TranslationCache.id.desc()),
            ).label("position"),
        ).where(
            TranslationCache.source_text_hash.in_(tuple(hashes)),
            TranslationCache.source_language == source_language,
            TranslationCache.target_language == target_language,
            TranslationCache.provider == provider,
        ).subquery()
        result = await self.session.execute(
            select(ranked.c.source_text_hash, ranked.c.translated_text)
            .where(ranked.c.position == 1).execution_options(autoflush=False)
        )
        return {row["source_text_hash"]: row["translated_text"]
                for row in result.mappings().all()}

    async def save_labels(self, rows):
        if rows:
            await self.session.execute(TranslationCache.__table__.insert().values(list(rows)))
