from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.white_label import WhiteLabelRepository

from database.models import (
    Language,
    LegalDocument,
    Module,
    PaidFeature,
    Suite,
    Tenant,
    TenantWhiteLabelSetting,
)


class WhiteLabelTenantNotFoundError(
    Exception
):
    pass


@dataclass(frozen=True)
class WhiteLabelBranding:
    logo_url: str | None
    favicon_url: str | None
    primary_color: str | None
    secondary_color: str | None
    accent_color: str | None
    theme_config: dict[str, object]


@dataclass(frozen=True)
class WhiteLabelLanguage:
    code: str
    name: str
    native_name: str | None


@dataclass(frozen=True)
class WhiteLabelLegalDocument:
    doc_type: str
    version: str
    language: str
    title: str | None
    content_url: str | None


@dataclass(frozen=True)
class WhiteLabelPricingItem:
    code: str
    name: str
    description: str | None
    price: Decimal
    currency: str


@dataclass(frozen=True)
class WhiteLabelConfig:
    tenant_slug: str
    tenant_name: str
    default_language: str
    default_currency: str
    branding: WhiteLabelBranding
    languages: tuple[
        WhiteLabelLanguage,
        ...,
    ]
    suites: tuple[str, ...]
    modules: tuple[str, ...]
    legal_documents: tuple[
        WhiteLabelLegalDocument,
        ...,
    ]
    pricing: tuple[
        WhiteLabelPricingItem,
        ...,
    ]


class WhiteLabelRepositoryProtocol(
    Protocol
):
    async def get_active_tenant_for_domain(
        self,
        *,
        domain: str,
    ) -> Tenant | None:
        ...

    async def get_settings(
        self,
        *,
        tenant_id: UUID,
    ) -> TenantWhiteLabelSetting | None:
        ...

    async def list_active_languages(
        self,
        *,
        tenant_id: UUID,
    ) -> list[Language]:
        ...

    async def list_active_suites(
        self,
        *,
        tenant_id: UUID,
        now: datetime,
    ) -> list[Suite]:
        ...

    async def list_enabled_modules(
        self,
        *,
        tenant_id: UUID,
    ) -> list[Module]:
        ...

    async def list_published_legal_documents(
        self,
        *,
        tenant_id: UUID,
    ) -> list[LegalDocument]:
        ...

    async def list_active_pricing(
        self,
        *,
        tenant_id: UUID,
    ) -> list[PaidFeature]:
        ...


class WhiteLabelService:
    def __init__(
        self,
        *,
        repository: (
            WhiteLabelRepositoryProtocol
        ),
    ) -> None:
        self.repository = repository

    @staticmethod
    def normalize_hostname(
        hostname: str,
    ) -> str:
        normalized = (
            hostname
            .strip()
            .lower()
            .rstrip(".")
        )

        if (
            not normalized
            or len(normalized) > 253
            or "/" in normalized
            or "\\" in normalized
            or "@" in normalized
            or ":" in normalized
            or any(
                character.isspace()
                for character in normalized
            )
        ):
            raise WhiteLabelTenantNotFoundError(
                "White Label tenant is not available."
            )

        try:
            normalized = (
                normalized
                .encode("idna")
                .decode("ascii")
            )
        except UnicodeError as error:
            raise WhiteLabelTenantNotFoundError(
                "White Label tenant is not available."
            ) from error

        labels = normalized.split(".")

        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(
                character.isalnum()
                or character == "-"
                for character in label
            )
            for label in labels
        ):
            raise WhiteLabelTenantNotFoundError(
                "White Label tenant is not available."
            )

        return normalized

    async def resolve_tenant(
        self,
        *,
        hostname: str,
    ) -> Tenant:
        domain = self.normalize_hostname(
            hostname
        )

        tenant = await (
            self.repository
            .get_active_tenant_for_domain(
                domain=domain,
            )
        )

        if tenant is None:
            raise WhiteLabelTenantNotFoundError(
                "White Label tenant is not available."
            )

        return tenant

    async def get_config(
        self,
        *,
        hostname: str,
        now: datetime | None = None,
    ) -> WhiteLabelConfig:
        tenant = await self.resolve_tenant(
            hostname=hostname,
        )
        current_time = (
            now
            if now is not None
            else datetime.now(timezone.utc)
        )

        settings = await (
            self.repository.get_settings(
                tenant_id=tenant.id,
            )
        )
        languages = await (
            self.repository
            .list_active_languages(
                tenant_id=tenant.id,
            )
        )
        suites = await (
            self.repository.list_active_suites(
                tenant_id=tenant.id,
                now=current_time,
            )
        )
        modules = await (
            self.repository
            .list_enabled_modules(
                tenant_id=tenant.id,
            )
        )
        legal_documents = await (
            self.repository
            .list_published_legal_documents(
                tenant_id=tenant.id,
            )
        )
        pricing = await (
            self.repository
            .list_active_pricing(
                tenant_id=tenant.id,
            )
        )

        branding = WhiteLabelBranding(
            logo_url=(
                settings.logo_url
                if settings is not None
                else None
            ),
            favicon_url=(
                settings.favicon_url
                if settings is not None
                else None
            ),
            primary_color=(
                settings.primary_color
                if settings is not None
                else None
            ),
            secondary_color=(
                settings.secondary_color
                if settings is not None
                else None
            ),
            accent_color=(
                settings.accent_color
                if settings is not None
                else None
            ),
            theme_config=(
                dict(
                    settings.theme_config
                    or {}
                )
                if settings is not None
                else {}
            ),
        )

        return WhiteLabelConfig(
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
            default_language=(
                tenant.default_language
            ),
            default_currency=(
                tenant.default_currency
            ),
            branding=branding,
            languages=tuple(
                WhiteLabelLanguage(
                    code=language.code,
                    name=language.name,
                    native_name=(
                        language.native_name
                    ),
                )
                for language in languages
            ),
            suites=tuple(
                suite.code
                for suite in suites
            ),
            modules=tuple(
                module.code
                for module in modules
            ),
            legal_documents=tuple(
                WhiteLabelLegalDocument(
                    doc_type=document.doc_type,
                    version=document.version,
                    language=document.language,
                    title=document.title,
                    content_url=(
                        document.content_url
                    ),
                )
                for document
                in legal_documents
            ),
            pricing=tuple(
                WhiteLabelPricingItem(
                    code=item.code,
                    name=item.name,
                    description=item.description,
                    price=Decimal(item.price),
                    currency=item.currency,
                )
                for item in pricing
            ),
        )


def build_white_label_service(
    session: AsyncSession,
) -> WhiteLabelService:
    return WhiteLabelService(
        repository=WhiteLabelRepository(
            session
        ),
    )

