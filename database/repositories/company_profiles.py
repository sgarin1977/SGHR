from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    TenantCompanyBankAccount,
    TenantCompanyIdentifier,
    TenantCompanyProfile,
)


class TenantCompanyProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_tenant(
        self,
        *,
        tenant_id: UUID,
    ) -> TenantCompanyProfile | None:
        statement = (
            select(TenantCompanyProfile)
            .where(
                TenantCompanyProfile.tenant_id
                == tenant_id
            )
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_profile(
        self,
        *,
        tenant_id: UUID,
        legal_name: str,
        commercial_name: str | None = None,
        registered_address_raw: str | None = None,
        operational_address_raw: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        website: str | None = None,
        signatory_name: str | None = None,
        signatory_position: str | None = None,
        logo_file_id: UUID | None = None,
    ) -> TenantCompanyProfile:
        profile = TenantCompanyProfile(
            tenant_id=tenant_id,
            legal_name=legal_name,
            commercial_name=commercial_name,
            registered_address_raw=registered_address_raw,
            operational_address_raw=operational_address_raw,
            phone=phone,
            email=email,
            website=website,
            signatory_name=signatory_name,
            signatory_position=signatory_position,
            logo_file_id=logo_file_id,
        )
        self.session.add(profile)
        await self.session.flush()
        return profile

    async def get_for_tenant_for_update(
        self,
        *,
        tenant_id: UUID,
    ) -> TenantCompanyProfile | None:
        statement = (
            select(TenantCompanyProfile)
            .where(
                TenantCompanyProfile.tenant_id
                == tenant_id
            )
            .limit(1)
            .execution_options(
                populate_existing=True,
            )
            .with_for_update()
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def update_profile(
        self,
        *,
        profile: TenantCompanyProfile,
        values: dict,
    ) -> TenantCompanyProfile:
        allowed = {
            "legal_name",
            "commercial_name",
            "registered_address_raw",
            "operational_address_raw",
            "phone",
            "email",
            "website",
            "signatory_name",
            "signatory_position",
            "logo_file_id",
        }
        if not values or not set(values).issubset(allowed):
            raise ValueError("Invalid company profile update.")

        for name, value in values.items():
            setattr(profile, name, value)

        await self.session.flush()
        return profile


class TenantCompanyBankAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_account(
        self,
        *,
        profile: TenantCompanyProfile,
        account_holder_name: str,
        currency: str,
        iban: str | None,
        account_number: str | None,
        bic_swift: str | None = None,
        country_code: str | None = None,
        label: str | None = None,
        bank_name: str | None = None,
        is_primary: bool = False,
        show_in_documents: bool = False,
    ) -> TenantCompanyBankAccount:
        account = TenantCompanyBankAccount(
            tenant_company_profile_id=profile.id,
            account_holder_name=account_holder_name,
            currency=currency,
            iban=iban,
            account_number=account_number,
            bic_swift=bic_swift,
            country_code=country_code,
            label=label,
            bank_name=bank_name,
            is_primary=is_primary,
            is_active=True,
            show_in_documents=show_in_documents,
        )
        self.session.add(account)
        await self.session.flush()
        return account

    async def get_for_tenant_for_update(
        self,
        *,
        tenant_id: UUID,
        account_id: UUID,
    ) -> TenantCompanyBankAccount | None:
        statement = (
            select(TenantCompanyBankAccount)
            .join(
                TenantCompanyProfile,
                TenantCompanyBankAccount.tenant_company_profile_id
                == TenantCompanyProfile.id,
            )
            .where(
                TenantCompanyProfile.tenant_id == tenant_id,
                TenantCompanyBankAccount.id == account_id,
            )
            .limit(1)
            .execution_options(populate_existing=True)
            .with_for_update(of=TenantCompanyBankAccount)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def update_account(
        self,
        *,
        account: TenantCompanyBankAccount,
        values: dict,
    ) -> TenantCompanyBankAccount:
        allowed = {
            "label",
            "account_holder_name",
            "bank_name",
            "country_code",
            "iban",
            "bic_swift",
            "account_number",
            "currency",
            "is_primary",
            "show_in_documents",
        }
        if (
            not isinstance(values, dict)
            or not values
            or not set(values).issubset(allowed)
        ):
            raise ValueError("Invalid bank account update.")

        for name, value in values.items():
            setattr(account, name, value)

        await self.session.flush()
        return account

    async def deactivate_account(
        self,
        *,
        account: TenantCompanyBankAccount,
    ) -> TenantCompanyBankAccount:
        account.is_active = False
        await self.session.flush()
        return account

    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
    ) -> list[TenantCompanyBankAccount]:
        statement = (
            select(TenantCompanyBankAccount)
            .join(
                TenantCompanyProfile,
                TenantCompanyBankAccount.tenant_company_profile_id
                == TenantCompanyProfile.id,
            )
            .where(TenantCompanyProfile.tenant_id == tenant_id)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def list_document_visible_for_tenant(
        self,
        *,
        tenant_id: UUID,
    ) -> list[TenantCompanyBankAccount]:
        statement = (
            select(TenantCompanyBankAccount)
            .join(
                TenantCompanyProfile,
                TenantCompanyBankAccount.tenant_company_profile_id
                == TenantCompanyProfile.id,
            )
            .where(
                TenantCompanyProfile.tenant_id == tenant_id,
                TenantCompanyBankAccount.is_active.is_(True),
                TenantCompanyBankAccount.show_in_documents.is_(True),
            )
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())


class TenantCompanyIdentifierRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
    ) -> list[TenantCompanyIdentifier]:
        statement = (
            select(TenantCompanyIdentifier)
            .join(
                TenantCompanyProfile,
                TenantCompanyIdentifier.tenant_company_profile_id
                == TenantCompanyProfile.id,
            )
            .where(
                TenantCompanyProfile.tenant_id == tenant_id,
            )
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def create_identifier(
        self,
        *,
        profile: TenantCompanyProfile,
        country_code: str,
        identifier_type: str,
        identifier_value: str,
        identifier_value_normalized: str,
        is_primary: bool,
    ) -> TenantCompanyIdentifier:
        identifier = TenantCompanyIdentifier(
            tenant_company_profile_id=profile.id,
            country_code=country_code,
            identifier_type=identifier_type,
            identifier_value=identifier_value,
            identifier_value_normalized=identifier_value_normalized,
            is_primary=is_primary,
        )
        self.session.add(identifier)
        await self.session.flush()
        return identifier

    async def get_for_tenant_for_update(
        self,
        *,
        tenant_id: UUID,
        identifier_id: UUID,
    ) -> TenantCompanyIdentifier | None:
        statement = (
            select(TenantCompanyIdentifier)
            .join(
                TenantCompanyProfile,
                TenantCompanyIdentifier.tenant_company_profile_id
                == TenantCompanyProfile.id,
            )
            .where(
                TenantCompanyProfile.tenant_id == tenant_id,
                TenantCompanyIdentifier.id == identifier_id,
            )
            .limit(1)
            .execution_options(populate_existing=True)
            .with_for_update(of=TenantCompanyIdentifier)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def delete_locked_identifier(
        self,
        *,
        identifier: TenantCompanyIdentifier,
    ) -> None:
        await self.session.delete(identifier)
        await self.session.flush()
