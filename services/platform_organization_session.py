"""Select an existing Platform membership using an existing refresh session."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.api_auth import ApiAuthRefreshError
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.api_platform_principal import ApiPlatformPrincipal


@dataclass(frozen=True)
class _Subject:
    id: UUID
    tenant_id: UUID
    status: str = "active"


class PlatformOrganizationSessionService:
    def __init__(self, *, session, tokens, identity, serializer, events, now_provider=None):
        if any(getattr(item, "session", None) is not session
               for item in (tokens, identity, serializer, events)):
            raise ValueError("Organization session requires shared-session services.")
        self.session, self.tokens, self.identity = session, tokens, identity
        self.serializer, self.events = serializer, events
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def _actor(self, user_id, tenant_id):
        actor = await self.identity.require_actor(user_id=user_id, tenant_id=tenant_id)
        if (not isinstance(actor, ApiActorContext) or actor.status != "active"
                or actor.user_id != user_id or actor.tenant_id != tenant_id):
            raise ApiIdentityAccessError("Organization is not available.")
        return actor

    async def select(self, *, principal, tenant_id, refresh_token):
        try:
            if not isinstance(principal, ApiPlatformPrincipal):
                raise ApiAuthRefreshError("Refresh session is not available.")
            if not isinstance(tenant_id, UUID):
                raise ValueError("Organization selector is invalid.")
            now = self.now_provider()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("Server time is invalid.")
            now = now.astimezone(timezone.utc)
            try:
                digest = self.tokens.refresh_token_codec.hash_refresh_token(refresh_token)
            except (TypeError, ValueError):
                raise ApiAuthRefreshError("Refresh session is not available.") from None

            # The same barrier as cooperating membership/removal writers.
            # Production serializer requires PostgreSQL READ COMMITTED.
            await self.serializer.lock_member(tenant_id=tenant_id, user_id=principal.user_id)
            with self.session.no_autoflush:
                source = await self.tokens.session_repository.get_active_session_for_update(
                    refresh_token_hash=digest, now=now)
                if (source is None or source.user_id != principal.user_id
                        or source.status != "active" or source.revoked_at is not None
                        or source.auth_method not in {"email", "telegram"}
                        or not isinstance(source.id, UUID)):
                    raise ApiAuthRefreshError("Refresh session is not available.")
                if source.tenant_id is not None:
                    if not isinstance(source.tenant_id, UUID):
                        raise ApiAuthRefreshError("Refresh session is not available.")
                    # A removed tenant session cannot become an identity-only
                    # session or acquire a different context as a fallback.
                    try:
                        await self._actor(principal.user_id, source.tenant_id)
                    except ApiIdentityAccessError:
                        raise ApiAuthRefreshError("Refresh session is not available.") from None

                await self._actor(principal.user_id, tenant_id)
                pair = await self.tokens.prepare_token_pair(
                    user=_Subject(principal.user_id, tenant_id),
                    auth_method=source.auth_method,
                    device_id=source.device_id, device_name=source.device_name, now=now)
                await self.tokens.session_repository.revoke_session(auth_session=source, now=now)
                event = await self.events.create_event(
                    event_type="api_organization_session_selected",
                    tenant_id=tenant_id, user_id=principal.user_id,
                    entity_type="api_auth_session", entity_id=source.id,
                    payload={"source_session_id": str(source.id),
                             "source_tenant_id": None if source.tenant_id is None else str(source.tenant_id),
                             "selected_tenant_id": str(tenant_id)},
                    platform="api")
                if not isinstance(getattr(event, "id", None), UUID):
                    raise RuntimeError("Organization session audit is unavailable.")
                await self._actor(principal.user_id, tenant_id)
            await self.session.commit()
            return pair
        except BaseException:
            await self.session.rollback()
            raise


def build_platform_organization_session_service(*, session, access_codec, refresh_codec):
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership_serialization import PlatformMembershipSerializationRepository
    from services.api_auth_factory import build_api_auth_service
    from services.api_identity import ApiIdentityService

    return PlatformOrganizationSessionService(
        session=session,
        tokens=build_api_auth_service(session=session, telegram_verifier=None,
            access_token_codec=access_codec, refresh_token_codec=refresh_codec),
        identity=ApiIdentityService(session),
        serializer=PlatformMembershipSerializationRepository(session),
        events=EventRepository(session))
