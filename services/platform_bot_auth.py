"""Platform session issuance from a trusted bot proof and canonical membership.

No Construction permission model, onboarding, account relink or public route is
introduced. The existing API token/session storage and mandatory audit are used.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from pydantic import SecretStr

from database.repositories.user import PlatformTelegramAccountUnlinkedError
from services.api_auth import ApiAuthAccessError
from services.api_identity import ApiIdentityAccessError
from services.platform_bot_auth_proof import PlatformBotUpdateVerifier


@dataclass(frozen=True)
class PlatformBotTokenResult:
    user_id: UUID
    tenant_id: UUID
    access_token: SecretStr = field(repr=False)
    refresh_token: SecretStr = field(repr=False)
    expires_in: int


@dataclass(frozen=True)
class _TokenSubject:
    id: UUID
    tenant_id: UUID
    status: str


class PlatformBotAuthenticationService:
    def __init__(self, *, session, users, user_service, identity_service, token_service, events, now_provider=None):
        if any(getattr(component, 'session', None) is not session
                for component in (users, user_service, identity_service, token_service, events)):
            raise ValueError('Platform bot authentication requires one shared session.')
        self.session, self.users, self.user_service = session, users, user_service
        self.identity_service, self.token_service, self.events = identity_service, token_service, events
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def authenticate(self, *, verifier, proof, tenant_id):
        try:
            if type(verifier) is not PlatformBotUpdateVerifier or not isinstance(tenant_id, UUID):
                raise ApiAuthAccessError('Platform bot authentication is unavailable.')
            now = self.now_provider()
            identity = verifier.verify(proof, now=now)
            account = await self.users.get_by_platform_account('telegram', str(identity.platform_user_id))
            if (account is None or account.status != 'active'
                    or not isinstance(account.user_id, UUID)):
                raise ApiAuthAccessError('Platform bot authentication is unavailable.')
            # Same canonical user/account locks as existing Telegram login and unlink.
            await self.user_service.lock_telegram_authentication(
                user_id=account.user_id, telegram_user_id=identity.platform_user_id)
            actor = await self.identity_service.require_actor(user_id=account.user_id, tenant_id=tenant_id)
            if (actor.user_id != account.user_id or actor.tenant_id != tenant_id or actor.status != 'active'):
                raise ApiAuthAccessError('Platform bot authentication is unavailable.')
            # A selected membership is the token context; the canonical user's
            # primary tenant is never rewritten merely to select an organization.
            tokens = await self.token_service.prepare_token_pair(
                user=_TokenSubject(actor.user_id, actor.tenant_id, actor.status), auth_method='telegram',
                device_id=f'telegram-bot:{verifier.bot_id}:{identity.platform_user_id}',
                device_name='Telegram bot', now=now)
            await self.events.create_event(event_type='api_telegram_auth_succeeded',
                tenant_id=actor.tenant_id, user_id=actor.user_id, entity_type='user', entity_id=actor.user_id,
                payload={'auth_method': 'telegram', 'transport': 'bot_api'}, platform='api')
            result = PlatformBotTokenResult(actor.user_id, actor.tenant_id,
                SecretStr(tokens.access_token), SecretStr(tokens.refresh_token), tokens.expires_in)
            await self.session.commit()
            return result
        except (ApiIdentityAccessError, PlatformTelegramAccountUnlinkedError):
            await self.session.rollback()
            raise ApiAuthAccessError('Platform bot authentication is unavailable.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_platform_bot_authentication_service(*, session):
    from api.security import AccessTokenCodec, RefreshTokenCodec
    from api.settings import ApiAuthSettings
    from database.repositories.event import EventRepository
    from database.repositories.user import UserRepository
    from services.api_auth_factory import build_api_auth_service
    from services.api_identity import ApiIdentityService
    from services.user import UserService

    settings = ApiAuthSettings.from_env()
    user_service, users, events = UserService(session), UserRepository(session), EventRepository(session)
    codec = AccessTokenCodec(secret=settings.jwt_secret, issuer=settings.jwt_issuer,
        audience=settings.jwt_audience, access_ttl_seconds=settings.access_token_ttl_seconds)
    token_service = build_api_auth_service(session=session, telegram_verifier=None,
        access_token_codec=codec, refresh_token_codec=RefreshTokenCodec())
    identity_service = ApiIdentityService(session, user_service=user_service, user_repository=users,
        event_repository=events)
    return PlatformBotAuthenticationService(session=session, users=users, user_service=user_service,
        identity_service=identity_service, token_service=token_service, events=events)
