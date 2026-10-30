class ConstructionCompanyProfileScopeError(Exception):
    pass


class ConstructionCompanyProfileService:
    def __init__(
        self, *, profile_reader, identifier_reader=None,
        bank_account_reader=None, presentation_reader=None,
    ):
        self.profile_reader = profile_reader
        self.identifier_reader = identifier_reader
        self.bank_account_reader = bank_account_reader
        self.presentation_reader = presentation_reader

    async def get_inherited_profile(self, *, actor):
        if actor.status != "active":
            raise ConstructionCompanyProfileScopeError(
                "Company profile is not available."
            )

        profile = await self.profile_reader.get_for_tenant(
            tenant_id=actor.tenant_id
        )
        if profile is None:
            return None

        if getattr(profile, "tenant_id", None) != actor.tenant_id:
            raise ConstructionCompanyProfileScopeError(
                "Company profile is not available."
            )

        return profile

    async def list_inherited_identifiers(self, *, actor):
        profile = await self.get_inherited_profile(actor=actor)
        if profile is None:
            return []
        if self.identifier_reader is None:
            raise ConstructionCompanyProfileScopeError(
                "Company identifiers are not available."
            )

        identifiers = await self.identifier_reader.list_for_tenant(
            tenant_id=actor.tenant_id
        )
        if any(
            getattr(identifier, "tenant_company_profile_id", None)
            != profile.id
            for identifier in identifiers
        ):
            raise ConstructionCompanyProfileScopeError(
                "Company identifiers are not available."
            )
        return identifiers

    async def list_inherited_document_bank_accounts(self, *, actor):
        from services.platform_company_bank_accounts import (
            project_document_bank_account,
        )

        profile = await self.get_inherited_profile(actor=actor)
        if profile is None:
            return []
        if self.bank_account_reader is None:
            raise ConstructionCompanyProfileScopeError(
                "Company bank accounts are not available."
            )

        accounts = await self.bank_account_reader.list_document_visible_for_tenant(
            tenant_id=actor.tenant_id,
        )
        if any(
            getattr(account, "tenant_company_profile_id", None) != profile.id
            for account in accounts
        ):
            raise ConstructionCompanyProfileScopeError(
                "Company bank accounts are not available."
            )
        return [
            project_document_bank_account(account=account)
            for account in accounts
        ]

    async def get_document_company_data(
        self, *, actor, platform_display_name,
        tenant_ui_name, platform_ui_accent_color,
    ):
        if self.presentation_reader is None:
            raise ConstructionCompanyProfileScopeError(
                "Company presentation reader is not configured."
            )
        legal_identity = await self.get_legal_identity(actor=actor)
        identifiers = await self.list_inherited_identifiers(actor=actor)
        bank_accounts = await self.list_inherited_document_bank_accounts(
            actor=actor,
        )
        presentation = await self.presentation_reader.get_effective_for_actor(
            actor=actor,
            platform_display_name=platform_display_name,
            tenant_ui_name=tenant_ui_name,
            platform_ui_accent_color=platform_ui_accent_color,
        )
        identifier_fields = (
            "country_code", "identifier_type", "identifier_value", "is_primary",
        )
        return {
            "legal_identity": legal_identity,
            "presentation": presentation,
            "identifiers": [
                {field: getattr(item, field) for field in identifier_fields}
                for item in identifiers
            ],
            "bank_accounts": bank_accounts,
        }

    async def get_legal_identity(self, *, actor):
        profile = await self.get_inherited_profile(actor=actor)
        if profile is None:
            return None

        return {
            "legal_name": profile.legal_name,
            "commercial_name": profile.commercial_name,
            "registered_address_raw": profile.registered_address_raw,
            "operational_address_raw": profile.operational_address_raw,
            "signatory_name": profile.signatory_name,
            "signatory_position": profile.signatory_position,
        }


def build_construction_company_profile_service(*, construction_session):
    from database.repositories.construction.construction_tenant_settings import (
        ConstructionTenantSettingsRepository,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsService,
    )
    from database.repositories.company_profiles import (
        TenantCompanyProfileRepository,
        TenantCompanyIdentifierRepository,
        TenantCompanyBankAccountRepository,
    )

    if construction_session is None:
        raise ValueError("Construction session is required.")

    return ConstructionCompanyProfileService(
        profile_reader=TenantCompanyProfileRepository(construction_session),
        identifier_reader=TenantCompanyIdentifierRepository(construction_session),
        bank_account_reader=TenantCompanyBankAccountRepository(construction_session),
        presentation_reader=ConstructionTenantSettingsService(
            settings_reader=ConstructionTenantSettingsRepository(
                construction_session,
            ),
            profile_reader=TenantCompanyProfileRepository(
                construction_session,
            ),
        ),
    )
