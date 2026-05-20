import asyncio
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parent.parent))

from database.models import LegalDocument, Tenant
from database.session import async_session


LEGAL_DOCS_RU = {
    "terms": {
        "title": "Условия использования SGHR Beta",
        "content_text": (
            "Продолжая, вы соглашаетесь с правилами SGHR Beta. "
            "Платформа предоставляет каталог специалистов, инструменты связи, "
            "модерацию и техническую инфраструктуру. SGHR не является стороной "
            "договора услуги между клиентом и специалистом."
        ),
    },
    "privacy": {
        "title": "Политика конфиденциальности SGHR Beta",
        "content_text": (
            "Мы обрабатываем ваши данные для создания профиля, поиска специалистов, "
            "связи, модерации, безопасности и работы сервиса. Вы можете запросить "
            "удаление или выгрузку данных через настройки."
        ),
    },
    "specialist_consent": {
        "title": "Согласие на публикацию профиля специалиста",
        "content_text": (
            "Я согласен, что мой профиль специалиста, город, описание услуг, цены, "
            "языки, рейтинг и публичные материалы могут быть показаны пользователям SGHR."
        ),
    },
    "geo_consent": {
        "title": "Согласие на использование геолокации",
        "content_text": (
            "Я разрешаю использовать город или геолокацию для поиска специалистов "
            "по расстоянию. Точные координаты публично не показываются."
        ),
    },
    "translation_consent": {
        "title": "Согласие на автоматический перевод",
        "content_text": (
            "Я согласен на автоматический перевод сообщений на язык собеседника. "
            "Оригинал сообщения сохраняется и может быть показан участнику диалога."
        ),
    },
}


async def resolve_tenant_id(session):
    tenant_id = os.getenv("DEFAULT_TENANT_ID")

    if tenant_id:
        return uuid.UUID(tenant_id)

    result = await session.execute(select(Tenant.id).limit(1))
    tenant_id = result.scalar_one_or_none()

    if not tenant_id:
        raise RuntimeError("No tenant found. Seed tenants first or set DEFAULT_TENANT_ID.")

    return tenant_id


async def main():
    async with async_session() as session:
        tenant_id = await resolve_tenant_id(session)
        version = "beta-0.3"

        created = 0
        skipped = 0

        for doc_type, payload in LEGAL_DOCS_RU.items():
            result = await session.execute(
                select(LegalDocument).where(
                    LegalDocument.tenant_id == tenant_id,
                    LegalDocument.doc_type == doc_type,
                    LegalDocument.version == version,
                    LegalDocument.language == "ru",
                    LegalDocument.status == "active",
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            session.add(
                LegalDocument(
                    tenant_id=tenant_id,
                    doc_type=doc_type,
                    version=version,
                    language="ru",
                    title=payload["title"],
                    content_text=payload["content_text"],
                    status="active",
                )
            )
            created += 1

        await session.commit()

        print(f"tenant_id={tenant_id}")
        print(f"created={created}")
        print(f"skipped={skipped}")
        print("OK: beta 0.3 legal documents seeded")


if __name__ == "__main__":
    asyncio.run(main())