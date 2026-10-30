"""Telegram read transport over canonical Platform identity and Construction services."""
from contextlib import asynccontextmanager
from uuid import UUID

from api.construction_lead_cursors import decode_lead_list_cursor, encode_lead_list_cursor
from database.repositories.construction import ConstructionAccessRepository, ConstructionOrganizationRepository
from database.repositories.construction.construction_bot_entry import ConstructionBotOrganizationRepository
from database.repositories.user import UserRepository
from services.api_identity import ApiIdentityAccessError, ApiIdentityService
from services.construction.construction_foundation import ConstructionOrganizationService
from services.construction.construction_grants import build_construction_access_context_resolver
from services.construction.construction_lead_list_read import ConstructionLeadListFilters, build_construction_lead_list_read_service
from services.construction.construction_lead_read import ConstructionLeadReadService, build_construction_lead_read_service
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.module_access import build_tenant_module_access_service, build_tenant_suite_access_service


class ConstructionBotEntryService:
    def __init__(self, *, platform_session, construction_transaction, rates):
        self.platform_session = platform_session
        self.construction_transaction = construction_transaction
        self.rates = rates

    async def _identity(self, session, telegram_user_id):
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ApiIdentityAccessError('Telegram identity is unavailable.')
        users = UserRepository(session)
        account = await users.get_by_platform_account('telegram', str(telegram_user_id))
        if account is None or account.status != 'active' or not isinstance(account.user_id, UUID):
            raise ApiIdentityAccessError('Telegram identity is unavailable.')
        user = await users.get_by_id(account.user_id)
        if user is None or user.id != account.user_id or user.status != 'active':
            raise ApiIdentityAccessError('Telegram identity is unavailable.')
        await self.rates.ensure_platform_user_allowed(user_id=user.id)
        return user

    async def organizations(self, *, telegram_user_id, after=None):
        async with self.platform_session() as session:
            user = await self._identity(session, telegram_user_id)
            rows = await ConstructionBotOrganizationRepository(session).list_organizations(
                user_id=user.id, after=after, limit=10,
            )
            return dict(language=user.language_code, items=rows[:10],
                next_after=str(rows[9]['id']) if len(rows) > 10 else None)

    @asynccontextmanager
    async def _authorized(self, *, telegram_user_id, tenant_id):
        if not isinstance(tenant_id, UUID):
            raise ApiIdentityAccessError('Organization is unavailable.')
        async with self.platform_session() as platform:
            user = await self._identity(platform, telegram_user_id)
            # The callback tenant is a selector, never a source of authority.
            actor = await ApiIdentityService(platform).require_actor(user_id=user.id, tenant_id=tenant_id)
            await build_tenant_suite_access_service(platform).require_enabled(
                tenant_id=actor.tenant_id, suite_code='construction')
            await build_tenant_module_access_service(platform).require_enabled(
                tenant_id=actor.tenant_id, module_code='construction.core')
            async with self.construction_transaction(tenant_id=actor.tenant_id) as session:
                if not await ConstructionAccessRepository(session).has_active_platform_membership(
                        user_id=actor.user_id, tenant_id=actor.tenant_id):
                    raise ApiIdentityAccessError('Organization is unavailable.')
                access = await build_construction_access_context_resolver(session).resolve(actor=actor)
                if (access.user_id != actor.user_id or access.tenant_id != actor.tenant_id
                        or not access.roles or not access.grants):
                    raise ConstructionPermissionDeniedError('Construction access is unavailable.')
                organization = await ConstructionOrganizationService(
                    repository=ConstructionOrganizationRepository(session)).require_current(actor=actor)
                yield actor, access, organization, session

    async def home(self, *, telegram_user_id, tenant_id):
        async with self._authorized(telegram_user_id=telegram_user_id, tenant_id=tenant_id) as (actor, access, org, _):
            can_read_leads = True
            try:
                ConstructionLeadReadService._authorize(actor, access)
            except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
                can_read_leads = False
            return dict(language=actor.language_code, tenant_id=str(actor.tenant_id), name=org.name,
                currency=org.default_currency, roles=access.roles, can_read_leads=can_read_leads)

    async def leads(self, *, telegram_user_id, tenant_id, cursor=None):
        async with self._authorized(telegram_user_id=telegram_user_id, tenant_id=tenant_id) as (actor, access, org, session):
            ConstructionLeadReadService._authorize(actor, access)
            filters = ConstructionLeadListFilters()
            after = decode_lead_list_cursor(cursor, tenant_id=actor.tenant_id, user_id=actor.user_id, filters=filters)
            page = await build_construction_lead_list_read_service(session=session).list_leads(
                actor=actor, access_context=access, filters=filters, limit=10, after=after)
            return dict(language=actor.language_code, organization=org.name, items=page.items,
                next_cursor=encode_lead_list_cursor(page.next_cursor,
                    tenant_id=actor.tenant_id, user_id=actor.user_id, filters=filters))

    async def lead(self, *, telegram_user_id, tenant_id, lead_id):
        if not isinstance(lead_id, UUID):
            raise ValueError('Invalid lead.')
        async with self._authorized(telegram_user_id=telegram_user_id, tenant_id=tenant_id) as (actor, access, org, session):
            ConstructionLeadReadService._authorize(actor, access)
            item = await build_construction_lead_read_service(session=session).get_lead(
                actor=actor, access_context=access, lead_id=lead_id)
            return dict(language=actor.language_code, organization=org.name, item=item)


def build_construction_bot_entry_service():
    from services.construction.construction_bot_rate_limit import ConstructionBotPostgresRateLimiter
    from database.construction_session import construction_transaction
    from database.session import get_session
    return ConstructionBotEntryService(platform_session=get_session,
        construction_transaction=construction_transaction,
        rates=ConstructionBotPostgresRateLimiter(session_factory=get_session))
