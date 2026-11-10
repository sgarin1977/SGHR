"""Atomic CON-001 Construction onboarding over canonical Platform storage.

Current Release: an active authenticated verified Platform user may create
organizations without an existing membership, role, Construction permission,
creation entitlement or organization-count prerequisite.

The runtime factory uses ReleaseOrganizationCreationPolicy. The retained
product_policy factory argument supplies transactional Construction provisioning,
not an additional decision about permission to create the organization.

The injectable workflow policy remains available for isolated transaction tests.
No collaborator may commit or perform external delivery. Public authentication,
rate limiting and anti-abuse remain responsibilities of the Platform boundary.
"""
import hashlib
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from database.repositories.platform_organization_bootstrap import (
    OPERATION, PlatformOrganizationBootstrapRepository,
)
from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, ApiIdempotencyReservation,
    build_api_idempotency_service,
)
from services.api_platform_principal import ApiPlatformPrincipal
from services.platform_currency_catalog import PlatformCurrencyCodeError, require_platform_currency


class OrganizationBootstrapError(Exception):
    pass


class OrganizationBootstrapDenied(OrganizationBootstrapError):
    pass


class OrganizationBootstrapValidationError(OrganizationBootstrapError):
    pass


class OrganizationBootstrapInProgress(OrganizationBootstrapError):
    code = "idempotency_in_progress"


RECEIPT_FIELDS = frozenset({
    "id", "name", "slug", "default_language", "default_currency",
    "creator_user_id", "membership_id", "owner_grant_id",
})


class PlatformOrganizationBootstrap:
    def __init__(
        self, *, session, repository, grants, events, modules,
        idempotency, product_policy, now_provider=None,
    ):
        methods = (
            (repository, (
                "require_runtime", "lock_creator", "find_receipt",
                "require_language", "create_tenant", "ensure_creator_membership",
            )),
            (grants, ("create_grant",)),
            (events, ("create_event",)),
            (modules, ("is_suite_enabled", "is_module_enabled")),
            (idempotency, ("reserve", "complete")),
            (product_policy, ("require_creation", "provision_construction")),
        )
        collaborators = (
            repository, grants, events, modules, product_policy,
            getattr(idempotency, "repository", None),
        )
        if (
            session is None
            or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(
                not callable(getattr(item, name, None))
                for item, names in methods for name in names
            )
            or any(
                not callable(getattr(session, name, None))
                for name in (
                    "in_transaction", "get_transaction",
                    "get_nested_transaction", "commit", "rollback",
                )
            )
            or getattr(idempotency, "response_codec", None) is None
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Bootstrap requires explicit shared-session collaborators.")
        self.session, self.repository = session, repository
        self.grants, self.events, self.modules = grants, events, modules
        self.idempotency, self.policy = idempotency, product_policy
        self.clock = now_provider or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None or value.utcoffset() is None
        ):
            raise OrganizationBootstrapError("Bootstrap clock is invalid.")
        return value.astimezone(timezone.utc)

    def _transaction(self, expected=None):
        actual = self.session.get_transaction()
        if (
            self.session.in_transaction() is not True
            or actual is None or actual.is_active is not True
            or self.session.get_nested_transaction() is not None
            or (expected is not None and actual is not expected)
        ):
            raise OrganizationBootstrapError("Bootstrap transaction changed.")
        return actual

    @staticmethod
    def _receipt(value, *, user_id, tenant_id, payload):
        if not isinstance(value, dict) or set(value) != RECEIPT_FIELDS:
            raise OrganizationBootstrapError("Bootstrap receipt is invalid.")
        for field in ("id", "creator_user_id", "owner_grant_id", "membership_id"):
            raw = value[field]
            if raw is None and field == "membership_id":
                continue
            if not isinstance(raw, str) or str(UUID(raw)) != raw:
                raise OrganizationBootstrapError("Bootstrap receipt identifier is invalid.")
        if (
            value["id"] != str(tenant_id)
            or value["creator_user_id"] != str(user_id)
            or value["slug"] != "org-" + tenant_id.hex
            or value["name"] != payload["name"].strip()
            or value["default_language"] != payload["language"]
            or value["default_currency"] != payload["currency"]
        ):
            raise OrganizationBootstrapError("Bootstrap receipt binding differs.")
        return dict(value)

    async def _authorize(self, *, user_id, transaction):
        if await self.policy.require_creation(user_id=user_id) is not True:
            raise OrganizationBootstrapDenied("Organization creation is unavailable.")
        self._transaction(transaction)

    async def create(
        self, *, principal, name, language, currency, idempotency_key, trace_id=None,
    ):
        try:
            if not isinstance(principal, ApiPlatformPrincipal):
                raise OrganizationBootstrapDenied("An authenticated Platform principal is required.")
            if (
                not isinstance(name, str) or not name.strip()
                or not isinstance(language, str)
                or re.fullmatch(r"[A-Za-z0-9-]{2,10}", language) is None
                or not isinstance(currency, str)
                or re.fullmatch(r"[A-Z]{3}", currency) is None
                or not isinstance(idempotency_key, str)
                or not idempotency_key.strip() or len(idempotency_key) > 255
                or (trace_id is not None and not isinstance(trace_id, str))
            ):
                raise OrganizationBootstrapValidationError("Invalid organization creation request.")
            transaction = self._transaction()
            await self.repository.require_runtime()
            user_id = principal.user_id
            if await self.repository.lock_creator(user_id=user_id) != user_id:
                raise OrganizationBootstrapDenied("Organization creation is unavailable.")
            self._transaction(transaction)
            await self._authorize(user_id=user_id, transaction=transaction)

            payload = dict(name=name, language=language, currency=currency)
            key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            old = await self.repository.find_receipt(
                user_id=user_id, key_hash=key_hash, as_of=self._now(),
            )
            self._transaction(transaction)
            if old is not None:
                if (
                    not isinstance(old.tenant_id, UUID)
                    or old.principal_type != "user" or old.principal_id != user_id
                    or old.operation != OPERATION or old.key_hash != key_hash
                ):
                    raise OrganizationBootstrapError("Bootstrap receipt context differs.")
                tenant_id = old.tenant_id
            else:
                try:
                    require_platform_currency(currency)
                except PlatformCurrencyCodeError:
                    raise OrganizationBootstrapValidationError("Organization currency is unavailable.") from None
                if await self.repository.require_language(language=language) is not True:
                    raise OrganizationBootstrapValidationError("Organization language is unavailable.")
                tenant_id = uuid4()
                tenant = await self.repository.create_tenant(
                    tenant_id=tenant_id, name=name.strip(),
                    language=language, currency=currency, as_of=self._now(),
                )
                if (
                    tenant.id != tenant_id or tenant.name != name.strip()
                    or tenant.slug != "org-" + tenant_id.hex
                    or tenant.default_language != language
                    or tenant.default_currency != currency or tenant.status != "active"
                ):
                    raise OrganizationBootstrapError("Created organization context differs.")

            reservation = await self.idempotency.reserve(
                tenant_id=tenant_id, principal_type="user", principal_id=user_id,
                operation=OPERATION, idempotency_key=idempotency_key,
                payload=payload, now=self._now(),
            )
            self._transaction(transaction)
            if (
                not isinstance(reservation, ApiIdempotencyReservation)
                or type(reservation.is_replay) is not bool
                or reservation.record.tenant_id != tenant_id
                or reservation.record.principal_type != "user"
                or reservation.record.principal_id != user_id
                or reservation.record.operation != OPERATION
                or reservation.record.key_hash != key_hash
            ):
                raise OrganizationBootstrapError("Bootstrap reservation binding differs.")
            await self._authorize(user_id=user_id, transaction=transaction)

            if old is not None:
                if reservation.is_replay is not True:
                    raise OrganizationBootstrapError("Existing bootstrap receipt was not replayed.")
                if reservation.response_status is None:
                    raise OrganizationBootstrapInProgress("Organization creation is still processing.")
                if reservation.response_status != 201:
                    raise OrganizationBootstrapError("Bootstrap receipt status is invalid.")
                response = self._receipt(
                    reservation.response_payload, user_id=user_id,
                    tenant_id=tenant_id, payload=payload,
                )
                # A receipt reports the original bootstrap. It does not restore
                # revoked membership, OWNER, module access or current authority.
                await self.session.commit()
                return response

            if reservation.is_replay is not False:
                raise OrganizationBootstrapError("Unexpected bootstrap reservation replay.")
            membership_id = await self.repository.ensure_creator_membership(
                tenant_id=tenant_id, user_id=user_id, as_of=self._now(),
            )
            if membership_id is not None and not isinstance(membership_id, UUID):
                raise OrganizationBootstrapError("Creator membership context is invalid.")
            self._transaction(transaction)
            await self.policy.provision_construction(
                tenant_id=tenant_id, user_id=user_id, as_of=self._now(),
            )
            self._transaction(transaction)
            if (
                await self.modules.is_suite_enabled(
                    tenant_id=tenant_id, suite_code="construction", now=self._now(),
                ) is not True
                or await self.modules.is_module_enabled(
                    tenant_id=tenant_id, module_code="construction.core", now=self._now(),
                ) is not True
            ):
                raise OrganizationBootstrapDenied("Construction onboarding is unavailable.")

            grant = await self.grants.create_grant(
                tenant_id=tenant_id, user_id=user_id, role="OWNER",
                scope_type="tenant", scope_id=tenant_id, expires_at=None,
                granted_by_user_id=user_id, granted_at=self._now(),
            )
            if (
                not isinstance(grant.id, UUID) or grant.tenant_id != tenant_id
                or grant.user_id != user_id or grant.role != "OWNER"
                or grant.scope_type != "tenant" or grant.scope_id != tenant_id
                or grant.status != "active" or grant.expires_at is not None
                or grant.granted_by_user_id != user_id
            ):
                raise OrganizationBootstrapError("First OWNER context is invalid.")
            response = self._receipt({
                "id": str(tenant_id), "name": name.strip(),
                "slug": "org-" + tenant_id.hex,
                "default_language": language, "default_currency": currency,
                "creator_user_id": str(user_id),
                "membership_id": str(membership_id) if membership_id is not None else None,
                "owner_grant_id": str(grant.id),
            }, user_id=user_id, tenant_id=tenant_id, payload=payload)
            await self.events.create_event(
                event_type="platform_organization_bootstrapped",
                tenant_id=tenant_id, user_id=user_id,
                entity_type="tenant", entity_id=tenant_id,
                platform="api", trace_id=trace_id,
                payload={
                    "operation": "construction_onboarding",
                    "suite_code": "construction",
                    "module_codes": ["construction.core"],
                    "creator_user_id": str(user_id),
                    "membership_id": response["membership_id"],
                    "owner_grant_id": str(grant.id),
                    "construction_role": "OWNER",
                    "scope_type": "tenant", "scope_id": str(tenant_id),
                },
            )
            self._transaction(transaction)
            completed = await self.idempotency.complete(
                reservation=reservation, response_status=201,
                response_payload=response, now=self._now(),
            )
            if (
                completed is None or completed.id != reservation.record.id
                or completed.status != "completed"
            ):
                raise OrganizationBootstrapError("Bootstrap receipt completion failed.")
            self._transaction(transaction)
            await self.session.commit()
            return response
        except BaseException as error:
            try:
                await self.session.rollback()
            except Exception:
                raise OrganizationBootstrapError("Bootstrap rollback failed.") from None
            if isinstance(error, (
                OrganizationBootstrapError, ApiIdempotencyKeyReusedError,
            )) or not isinstance(error, Exception):
                raise
            raise OrganizationBootstrapError("Organization bootstrap failed.") from None


def build_platform_organization_bootstrap(
    *, session, encryption_key, product_policy=None, now_provider=None,
):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.event import EventRepository
    from database.repositories.white_label import WhiteLabelRepository
    from services.platform_organization_creation_policy import (
        ReleaseOrganizationCreationPolicy,
    )

    repository = PlatformOrganizationBootstrapRepository(session)
    if product_policy is None:
        from services.platform_construction_onboarding import (
            build_platform_construction_onboarding,
        )
        product_policy = build_platform_construction_onboarding(session=session)
    policy = ReleaseOrganizationCreationPolicy(
        repository=repository, provisioner=product_policy,
    )
    return PlatformOrganizationBootstrap(
        session=session,
        repository=repository,
        grants=ConstructionAccessRepository(session),
        events=EventRepository(session),
        modules=WhiteLabelRepository(session),
        idempotency=build_api_idempotency_service(
            session, encryption_key=encryption_key,
        ),
        product_policy=policy, now_provider=now_provider,
    )

# con_012_currency_settings_v1
