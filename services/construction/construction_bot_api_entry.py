"""Telegram presentation adapter using only registered API operations."""
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from time import monotonic
from uuid import UUID

from api.platform_bot_context import make_platform_bot_api_context
from services.construction.construction_api_client import ConstructionApiClientError
from services.platform_bot_api_client import (
    PlatformBotApiClient, PlatformBotApiTokens,
)


def _project_display_context(result):
    meta = getattr(result, "meta", {})
    unavailable = getattr(result, "translation_unavailable", False)
    if not isinstance(meta, dict) or type(unavailable) is not bool:
        raise ConstructionApiClientError(status_code=502, code="invalid_response")
    return {
        "translations": meta.get("translations"),
        "translation_unavailable": unavailable,
    }


def _project_action_flags(result):
    meta = getattr(result, "meta", {})
    if not isinstance(meta, dict):
        raise ConstructionApiClientError(status_code=502, code="invalid_response")
    capabilities = meta.get("capabilities", {})
    legacy_names = ("can_edit_project", "can_plan_activity", "can_open_team")
    names = legacy_names + ("can_read_pricing_adjustments", "can_edit_pricing_adjustments")
    if (
        not isinstance(capabilities, dict)
        or any(
            name in capabilities and type(capabilities[name]) is not bool
            for name in names
        )
    ):
        raise ConstructionApiClientError(status_code=502, code="invalid_response")
    return {
        name: capabilities.get(name) is True
        for name in names if name in legacy_names or name in capabilities
    }


@dataclass
class _CachedSession:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    tokens: object = field(default=None, repr=False)
    renew_at: float = 0
    users: int = 0


class BotApiSessionCache:
    """Bounded process-local credentials; never stored in Telegram FSM."""

    def __init__(self, *, capacity=1024, clock=monotonic):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("Session cache capacity must be positive.")
        self.capacity = capacity
        self.clock = clock
        self._entries = OrderedDict()

    async def get(self, *, api, context, tenant_id):
        identity = context.verifier.verify(context.proof)
        if not isinstance(tenant_id, UUID):
            raise ValueError("Selected organization must be a UUID.")
        key = (
            context.verifier.bot_id, identity.platform_user_id, tenant_id
        )
        entry = self._entries.get(key)
        if entry is None:
            if len(self._entries) >= self.capacity:
                idle = next(
                    (k for k, value in self._entries.items() if value.users == 0),
                    None,
                )
                if idle is None:
                    raise ConstructionApiClientError(
                        status_code=503, code="bot_session_capacity"
                    )
                self._entries.pop(idle)
            entry = _CachedSession()
            self._entries[key] = entry

        self._entries.move_to_end(key)
        entry.users += 1
        try:
            async with entry.lock:
                if entry.tokens is not None and self.clock() < entry.renew_at:
                    return entry.tokens.session
                try:
                    context.verifier.verify(context.proof)
                    started = self.clock()
                    previous = entry.tokens
                    tokens = (
                        await api.authenticate(tenant_id=tenant_id)
                        if previous is None
                        else await api.refresh(tokens=previous)
                    )
                    if (
                        not isinstance(tokens, PlatformBotApiTokens)
                        or tokens.session.tenant_id != tenant_id
                        or (
                            previous is not None
                            and tokens.session.user_id != previous.session.user_id
                        )
                    ):
                        raise ConstructionApiClientError(
                            status_code=502, code="invalid_api_response"
                        )
                    entry.tokens = tokens
                    margin = min(30, tokens.expires_in / 10)
                    entry.renew_at = started + tokens.expires_in - margin
                    return tokens.session
                except BaseException:
                    # A failed/uncertain rotation must not reuse an old secret.
                    entry.tokens = None
                    entry.renew_at = 0
                    raise
        finally:
            entry.users -= 1
            if (
                entry.users == 0
                and entry.tokens is None
                and self._entries.get(key) is entry
            ):
                self._entries.pop(key)


class ConstructionBotApiEntry:
    def __init__(self, *, api, context, sessions):
        self.api, self.context, self.sessions = api, context, sessions

    def _identity(self, telegram_user_id):
        identity = self.context.verifier.verify(self.context.proof)
        if (
            type(telegram_user_id) is not int
            or telegram_user_id != identity.platform_user_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable"
            )
        return identity

    async def unlink_telegram_account(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        account_id, expected_row_version, idempotency_key,
    ):
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # Owner authorization is performed by the existing Platform endpoint.
        # No account reread or version rebasing precedes a keyed retry.
        result = await self.api.construction.unlink_telegram_account(
            session=session, account_id=account_id,
            expected_row_version=expected_row_version,
            idempotency_key=idempotency_key,
        )
        return {
            "account": result, "actor_user_id": str(session.user_id),
            "language": language,
        }

    async def telegram_accounts(
        self, *, telegram_user_id, tenant_id, cursor=None, expected_user_id=None,
    ):
        identity = self._identity(telegram_user_id)
        if not isinstance(tenant_id, UUID):
            raise ValueError("Selected Platform tenant UUID is required.")
        session = await self.sessions.get(
            api=self.api, context=self.context, tenant_id=tenant_id,
        )
        if session.tenant_id != tenant_id or (
            expected_user_id is not None and session.user_id != expected_user_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        result = await self.api.construction.telegram_accounts(
            session=session, cursor=cursor,
        )
        return {
            "items": result.data,
            "next_cursor": result.meta["pagination"]["next_cursor"],
            "actor_user_id": str(session.user_id),
            "language": getattr(identity, "language_code", None) or "ru",
        }

    async def telegram_binding(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        operation, idempotency_key, binding_id=None, expected_row_version=None,
    ):
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        result = await self.api.construction.telegram_binding(
            session=session, operation=operation, idempotency_key=idempotency_key,
            binding_id=binding_id, expected_row_version=expected_row_version,
        )
        return {"binding": result, "actor_user_id": str(session.user_id), "language": language}

    async def organizations(self, *, telegram_user_id, after=None):
        identity = self._identity(telegram_user_id)
        page = await self.api.organizations(after=after, limit=10)
        return {
            "language": identity.language_code,
            "items": page.data,
            "next_after": page.meta["pagination"]["next_cursor"],
        }

    async def organization_form(self, *, telegram_user_id):
        identity = self._identity(telegram_user_id)
        result = await self.api.organization_creation_context()
        return {
            **result.data,
            "language": result.data["language_code"] or identity.language_code,
        }

    async def create_organization(
        self, *, telegram_user_id, expected_user_id, payload, idempotency_key,
    ):
        self._identity(telegram_user_id)
        result = await self.api.create_organization(
            payload=payload, expected_user_id=expected_user_id,
            idempotency_key=idempotency_key,
        )
        return {"item": result.data}

    async def _presentation(self, *, telegram_user_id, tenant_id, include_timezone=False):
        self._identity(telegram_user_id)
        session = await self.sessions.get(
            api=self.api, context=self.context, tenant_id=tenant_id
        )
        me = await self.api.construction.me(session=session)
        options = {"include_timezone": True} if include_timezone else {}
        navigation = await self.api.construction.navigation(session=session, **options)
        return session, {
            **navigation.data,
            "language": me.data.get("language_code"),
            "timezone": me.data.get("timezone"),
            "display_timezone": (
                navigation.meta["display_timezone"] if include_timezone else None
            ),
        }

    async def client_catalog(
        self, *, telegram_user_id, tenant_id, cursor=None, expected_user_id=None,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            session.tenant_id != tenant_id
            or (expected_user_id is not None and session.user_id != expected_user_id)
            or home.get("can_read_clients") is not True
        ):
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        result = await self.api.construction.client_catalog(
            session=session, limit=5, cursor=cursor,
        )
        return {
            "items": result.data,
            "next_cursor": result.meta["pagination"]["next_cursor"],
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "can_create_client": home.get("can_create_project") is True,
        }

    async def _client_identity_session(
        self, *, telegram_user_id, tenant_id, expected_user_id,
    ):
        identity = self._identity(telegram_user_id)
        if not isinstance(tenant_id, UUID) or not isinstance(expected_user_id, UUID):
            raise ValueError("Platform session context UUIDs are required.")
        session = await self.sessions.get(
            api=self.api, context=self.context, tenant_id=tenant_id,
        )
        if session.user_id != expected_user_id or session.tenant_id != tenant_id:
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # The session's tenant selects credentials; the owner API resolves
        # the link's tenant itself. No client read or navigation is required.
        return session, getattr(identity, "language_code", None) or "ru"

    async def own_client_link_state(
        self, *, telegram_user_id, tenant_id, client_id, expected_user_id,
    ):
        identity = self._identity(telegram_user_id)
        if not all(
            isinstance(value, UUID)
            for value in (tenant_id, client_id, expected_user_id)
        ):
            raise ValueError("Own-link context UUIDs are required.")
        result = await self.api.own_client_link_state(
            client_id=client_id, expected_user_id=expected_user_id,
        )
        return {
            "item": result.data, "actor_user_id": str(expected_user_id),
            "language": getattr(identity, "language_code", None) or "ru",
        }

    async def unlink_client(
        self, *, telegram_user_id, tenant_id, client_id, expected_user_id,
        expected_row_version, as_owner=False,
    ):
        if type(as_owner) is not bool:
            raise ValueError("Unlink mode is invalid.")
        if as_owner:
            identity = self._identity(telegram_user_id)
            if not all(
                isinstance(value, UUID)
                for value in (tenant_id, client_id, expected_user_id)
            ):
                raise ValueError("Own-link context UUIDs are required.")
            result = await self.api.unlink_own_client(
                client_id=client_id, expected_user_id=expected_user_id,
                expected_row_version=expected_row_version,
            )
            return {
                "item": result.data, "actor_user_id": str(expected_user_id),
                "language": getattr(identity, "language_code", None) or "ru",
            }
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # The existing endpoint chooses owner/editor authorization.
        # Keep the reviewed version; do not re-read or silently rebase it.
        result = await self.api.construction.unlink_client(
            session=session, client_id=client_id,
            expected_row_version=expected_row_version,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": language,
        }

    async def request_client_user_link(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        client_id, target_email, expected_row_version,
        idempotency_key, previous_user_id=None,
    ):
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # The existing request endpoint owns fresh edit authorization and replay.
        # Mutable client reads must not silently rebase an old request.
        result = await self.api.construction.request_client_user_link(
            session=session, client_id=client_id, target_email=target_email,
            expected_row_version=expected_row_version,
            previous_user_id=previous_user_id, idempotency_key=idempotency_key,
        )
        return {
            "request": result, "actor_user_id": str(session.user_id),
            "language": language,
        }

    async def self_link_client(
        self, *, telegram_user_id, tenant_id, client_id, expected_user_id,
        expected_row_version, previous_user_id=None,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # The registered API checks fresh client edit authority and version.
        # Do not rebase the reviewed version from a newer client read.
        result = await self.api.construction.self_link_client(
            session=session, client_id=client_id,
            expected_row_version=expected_row_version,
            previous_user_id=previous_user_id,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": home["language"],
        }

    async def patch_client(
        self, *, telegram_user_id, tenant_id, client_id,
        expected_user_id, payload,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id
            or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # Preserve the reviewed version; do not re-read/rebase before PATCH.
        result = await self.api.construction.patch_client(
            session=session, client_id=client_id, payload=payload,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": home["language"],
        }

    async def archive_client(
        self, *, telegram_user_id, tenant_id, client_id,
        expected_user_id, expected_row_version,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id
            or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.archive_client(
            session=session, client_id=client_id,
            expected_row_version=expected_row_version,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": home["language"],
        }

    async def client_card(self, *, telegram_user_id, tenant_id, client_id,
                          cursors=None, expected_user_id=None):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if expected_user_id is not None and session.user_id != expected_user_id:
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.client_card(
            session=session, client_id=client_id, cursors=cursors,
        )
        return {
            "item": result.data,
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "can_create_project": home.get("can_create_project") is True,
            "language": home["language"],
            "next_cursors": {
                section: result.meta["pagination"][section + "_next_cursor"]
                for section in ("projects", "leads", "activities", "history")
            },
        }

    async def project_catalog(
        self, *, telegram_user_id, tenant_id, cursor=None, expected_user_id=None,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            session.tenant_id != tenant_id
            or (expected_user_id is not None and session.user_id != expected_user_id)
            or home.get("can_read_projects") is not True
        ):
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        result = await self.api.construction.project_catalog(
            session=session, limit=5, cursor=cursor,
        )
        return {
            "items": result.data,
            "next_cursor": result.meta["pagination"]["next_cursor"],
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "can_create_project": home.get("can_create_project") is True,
        }

    async def project_catalog_card(
        self, *, telegram_user_id, tenant_id, project_id, expected_user_id,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            include_timezone=True,
        )
        if session.user_id != expected_user_id or session.tenant_id != tenant_id:
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.project_details(
            session=session, project_id=project_id,
            include_translations=True, expected_language=home["language"],
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            **_project_action_flags(result),
            **_project_display_context(result),
            "display_timezone": home.get("display_timezone"),
        }

    async def project_card(self, *, telegram_user_id, tenant_id, project_id,
                           client_id, expected_user_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            include_timezone=True,
        )
        if session.user_id != expected_user_id:
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.project_card(
            session=session, project_id=project_id, client_id=client_id,
            include_translations=True, expected_language=home["language"],
        )
        return {
            "item": result.data,
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": home["language"],
            **_project_action_flags(result),
            **_project_display_context(result),
            "display_timezone": home.get("display_timezone"),
        }

    async def project_areas(self, *, telegram_user_id, tenant_id, project_id,
                            expected_user_id, client_id=None, display_offset=None):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        if display_offset is not None and (type(display_offset) is not int or display_offset < 0):
            raise ValueError("Invalid area display page.")
        presentation = {} if display_offset is None else {
            "include_translations": True, "expected_language": home["language"],
        }
        if client_id is not None:
            project = await self.api.construction.project_card(
                session=session, project_id=project_id, client_id=client_id,
                **presentation,
            )
        else:
            project = await self.api.construction.project_details(
                session=session, project_id=project_id,
                **presentation,
            )
        result = await self.api.construction.project_areas(
            session=session, project_id=project_id,
            **({**presentation, "display_offset": display_offset} if display_offset is not None else {}),
        )
        return {
            "items": result.data, "project_id": str(project_id),
            "can_create_area": result.meta.get("capabilities", {}).get("can_create_area") is True,
            "can_edit_area": result.meta.get("capabilities", {}).get("can_edit_area") is True,
            "project_name": project.data["name"],
            "project_presentation": {
                "item": {"name": project.data["name"], "row_version": project.data.get("row_version")},
                **_project_display_context(project),
            },
            "display_offset": getattr(result, "display_offset", None),
            **_project_display_context(result),
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def patch_project_area(
        self, *, telegram_user_id, tenant_id, project_id, area_id,
        expected_user_id, payload,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id
            or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.patch_project_area(
            session=session, project_id=project_id,
            area_id=area_id, payload=payload,
        )
        return {
            "item": result.data,
            "actor_user_id": str(session.user_id),
            "project_id": str(project_id),
            "language": home["language"],
        }

    async def create_project_area(
        self, *, telegram_user_id, tenant_id, project_id,
        expected_user_id, payload, idempotency_key,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # The workflow owns fresh authorization and replay semantics.
        # A later terminal parent must not prevent retrieval of an old receipt.
        result = await self.api.construction.create_project_area(
            session=session, project_id=project_id,
            payload=payload, idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}

    async def project_address_suggestions(
        self, *, telegram_user_id, tenant_id, expected_user_id, query,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id
            or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        if home.get("can_create_project") is not True:
            raise ConstructionApiClientError(status_code=403, code="permission_required")
        result = await self.api.construction.address_suggestions(
            session=session, query=query,
        )
        return {
            "items": result.data,
            "actor_user_id": str(session.user_id),
            "tenant_id": str(session.tenant_id),
            "language": home["language"],
        }

    async def client_project_form(
        self, *, telegram_user_id, tenant_id, client_id, expected_user_id,
    ):
        if any(
            not isinstance(value, UUID)
            for value in (tenant_id, client_id, expected_user_id)
        ):
            raise ValueError("Client project context is invalid.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if session.user_id != expected_user_id or session.tenant_id != tenant_id:
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        if home.get("can_create_project") is not True:
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        card = await self.api.construction.client_card(
            session=session, client_id=client_id,
        )
        item = card.data
        if (
            item["id"] != str(client_id)
            or item["tenant_id"] != str(tenant_id)
            or not isinstance(item["display_name"], str)
            or not item["display_name"].strip()
        ):
            raise ConstructionApiClientError(
                status_code=502, code="invalid_response",
            )
        return {
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "can_choose_responsible": home.get("can_manage_team") is True,
            "client_id": str(client_id),
            "client_name": " ".join(item["display_name"].split())[:180],
        }


    async def project_form(self, *, telegram_user_id, tenant_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if home.get("can_create_project") is not True:
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        return {
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "can_choose_responsible": home.get("can_manage_team") is True,
        }

    async def create_project(self, *, telegram_user_id, tenant_id,
                             expected_user_id, payload, assign_self,
                             idempotency_key, client_id=None,
                             responsible_user_id=None):
        if (
            type(assign_self) is not bool
            or not isinstance(payload, dict)
            or set(payload) != {"name", "address_raw", "comment"}
            or (client_id is not None and not isinstance(client_id, UUID))
            or (responsible_user_id is not None and (
                not isinstance(responsible_user_id, UUID) or assign_self
            ))
        ):
            raise ValueError("Invalid standalone project form.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if session.user_id != expected_user_id:
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        if home.get("can_create_project") is not True:
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        result = await self.api.construction.create_project(
            session=session,
            payload={
                **payload, "client_id": str(client_id) if client_id else None,
                "responsible_user_id": (
                    str(session.user_id) if assign_self
                    else str(responsible_user_id) if responsible_user_id else None
                ),
            },
            idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}

    async def project_edit_address_suggestions(
        self, *, telegram_user_id, tenant_id, project_id,
        expected_user_id, query,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or not isinstance(project_id, UUID)
            or session.user_id != expected_user_id
            or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        # The existing GET owns permission/scope checks. Editing does not
        # require permission to create a new project.
        result = await self.api.construction.address_suggestions(
            session=session, query=query,
        )
        return {
            "items": result.data,
            "actor_user_id": str(session.user_id),
            "tenant_id": str(session.tenant_id),
            "project_id": str(project_id),
            "language": home["language"],
        }

    async def patch_project(
        self, *, telegram_user_id, tenant_id, project_id,
        expected_user_id, payload,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id
            or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # The original reviewed version is sent directly; no read/rebase here.
        result = await self.api.construction.patch_project(
            session=session, project_id=project_id, payload=payload,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": home["language"],
        }

    async def read_created_project(self, *, telegram_user_id, tenant_id,
                                   project_id, expected_user_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            include_timezone=True,
        )
        if session.user_id != expected_user_id:
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.project_details(
            session=session, project_id=project_id,
            include_translations=True, expected_language=home["language"],
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            **_project_action_flags(result),
            **_project_display_context(result),
            "display_timezone": home.get("display_timezone"),
            "can_create_project": home.get("can_create_project") is True,
        }

    async def client_form(self, *, telegram_user_id, tenant_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        # The existing client service uses the same creation permission.
        if home.get("can_create_project") is not True:
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        return {
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def create_client(self, *, telegram_user_id, tenant_id,
                            expected_user_id, payload, idempotency_key):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if session.user_id != expected_user_id:
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        if home.get("can_create_project") is not True:
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        result = await self.api.construction.create_client(
            session=session, payload=dict(payload), idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}

    async def project_planned_activities(
        self, *, telegram_user_id, tenant_id, project_id,
        expected_user_id, client_id=None, cursor=None,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID) or not isinstance(project_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        if client_id is None:
            project = await self.api.construction.project_details(
                session=session, project_id=project_id,
            )
        else:
            project = await self.api.construction.project_card(
                session=session, project_id=project_id, client_id=client_id,
            )
        activities = await self.api.construction.project_planned_activities(
            session=session, project_id=project_id, limit=5, cursor=cursor,
        )
        return {
            "items": activities.data,
            "next_cursor": activities.meta["pagination"]["next_cursor"],
            "project_id": str(project_id), "project_name": project.data["name"],
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def project_scope_names(
        self, *, telegram_user_id, tenant_id, expected_user_id, project_ids,
    ):
        if (
            not isinstance(project_ids, (tuple, list))
            or not 1 <= len(project_ids) <= 5
            or any(not isinstance(value, UUID) for value in project_ids)
            or len(set(project_ids)) != len(project_ids)
        ):
            raise ValueError("At most five distinct project UUIDs are required.")
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        if session.user_id != expected_user_id or session.tenant_id != tenant_id:
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")

        async def read(project_id):
            try:
                result = await self.api.construction.project_details(
                    session=session, project_id=project_id,
                )
            except ConstructionApiClientError as error:
                if error.status_code in (403, 404):
                    return str(project_id), None
                raise
            item = result.data
            if (
                not isinstance(item, dict)
                or item.get("id") != str(project_id)
                or item.get("tenant_id") != str(tenant_id)
                or not isinstance(item.get("name"), str)
                or not item["name"].strip()
            ):
                raise ConstructionApiClientError(status_code=502, code="invalid_response")
            return str(project_id), " ".join(item["name"].split())[:150]

        results = await asyncio.gather(
            *(read(project_id) for project_id in project_ids),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return {
            "project_names": dict(results),
            "actor_user_id": str(session.user_id),
            "tenant_id": str(session.tenant_id),
        }

    async def project_team(
        self, *, telegram_user_id, tenant_id, project_id,
        expected_user_id, client_id=None, selection=False, grant_selection=False,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or not isinstance(project_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        if client_id is not None:
            project = await self.api.construction.project_card(
                session=session, project_id=project_id, client_id=client_id,
            )
        else:
            project = await self.api.construction.project_details(
                session=session, project_id=project_id,
            )
        members = await self.api.construction.team_members(
            session=session, project_id=project_id,
        )
        result = {
            "items": members.data, "project_id": str(project_id),
            "project_name": project.data["name"],
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "can_add": home.get("can_manage_team") is True,
        }
        if selection:
            # Tenant-wide discovery uses its existing server capability.
            # Project-only authority does not grant tenant-wide discovery.
            if home.get("can_manage_team") is not True:
                raise ConstructionApiClientError(
                    status_code=403, code="permission_required",
                )
            options = await self.api.construction.project_role_options(
                session=session, project_id=project_id,
            )
            candidates = await self.api.construction.team_members(session=session)
            result.update(
                roles=options.data["roles"], candidates=candidates.data,
            )
        if grant_selection and not selection:
            options = await self.api.construction.project_role_options(
                session=session, project_id=project_id,
            )
            result["roles"] = options.data["roles"]
        return result

    async def replace_project_participant(
        self, *, telegram_user_id, tenant_id, project_id, expected_user_id,
        target_user_id, grant, new_role, idempotency_key, expires_at=...,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # The frozen command is replayed through the existing authorized workflow.
        # An active-team lookup must not substitute a different grant on retry.
        result = await self.api.construction.replace_project_participant(
            session=session, project_id=project_id, target_user_id=target_user_id,
            grant=grant, new_role=new_role, idempotency_key=idempotency_key,
            **({"expires_at": expires_at} if expires_at is not Ellipsis else {}),
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "project_id": str(project_id), "language": home["language"],
        }

    async def revoke_project_participant(
        self, *, telegram_user_id, tenant_id, project_id, expected_user_id,
        target_user_id, grant, idempotency_key,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # Replay uses the frozen command and fresh API authorization.
        # A team-list read must not hide a successful historical receipt.
        result = await self.api.construction.revoke_project_participant(
            session=session, project_id=project_id, target_user_id=target_user_id,
            grant=grant, idempotency_key=idempotency_key,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "project_id": str(project_id), "language": home["language"],
        }

    async def create_project_participant(
        self, *, telegram_user_id, tenant_id, project_id, expected_user_id,
        target_user_id, role, idempotency_key, expires_at=None,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # The existing API/workflow performs fresh authorization and replay.
        # Discovery and mutable project reads must not block an old receipt.
        result = await self.api.construction.create_project_participant(
            session=session, project_id=project_id, target_user_id=target_user_id,
            role=role, idempotency_key=idempotency_key, expires_at=expires_at,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "project_id": str(project_id), "language": home["language"],
        }

    async def remove_organization_member(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        target_user_id, idempotency_key,
    ):
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # Platform authorization is performed by the existing removal command.
        # Do not replace it with Construction navigation or role inference.
        # Do not require an active-team lookup when replaying an old receipt.
        result = await self.api.construction.remove_organization_member(
            session=session, target_user_id=target_user_id,
            idempotency_key=idempotency_key,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": language,
        }

    async def organization_revoke_context(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        target_user_id, grant_id,
    ):
        from services.construction.construction_team_transport import Grant
        if not isinstance(target_user_id, UUID) or not isinstance(grant_id, UUID):
            raise ValueError("Organization member and grant UUIDs are required.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        members = await self.api.construction.team_members(session=session)
        member = next((
            item for item in members.data if item["user_id"] == str(target_user_id)
        ), None)
        selected = next((
            grant for grant in member["grants"] if grant["id"] == str(grant_id)
        ), None) if member is not None else None
        if selected is None:
            raise ConstructionApiClientError(status_code=404, code="grant_unavailable")
        original = Grant.model_validate(selected)
        roles = await self.api.construction.organization_role_options(session=session)
        if (
            original.scope_type != "tenant" or original.scope_id != tenant_id
            or original.role not in ("OWNER", "ADMIN")
            or original.role not in roles
        ):
            raise ConstructionApiClientError(status_code=403, code="permission_required")
        return {
            "grant": original.model_dump(mode="json"),
            "target_user_id": str(target_user_id),
            "actor_user_id": str(session.user_id),
            "member_name": member.get("display_name"),
            "organization": home["name"], "language": home["language"],
        }

    async def revoke_organization_access(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        target_user_id, original_grant, idempotency_key,
    ):
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # Retry uses the original grant, including after it has been revoked.
        # Current authorization and the last-OWNER boundary belong to the API.
        result = await self.api.construction.revoke_organization_access(
            session=session, target_user_id=target_user_id,
            original_grant=original_grant, idempotency_key=idempotency_key,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": language,
        }

    async def organization_block_context(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        target_user_id, allow_absent=False,
    ):
        from services.construction.construction_team_transport import Grant
        if not isinstance(target_user_id, UUID) or type(allow_absent) is not bool:
            raise ValueError("Organization block context is invalid.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        members = await self.api.construction.team_members(session=session)
        member = next((
            item for item in members.data if item["user_id"] == str(target_user_id)
        ), None)
        if member is None and not allow_absent:
            raise ConstructionApiClientError(status_code=404, code="member_unavailable")
        counts = {"tenant": 0, "project": 0, "document": 0}
        for value in member["grants"] if member is not None else ():
            item = Grant.model_validate(value)
            counts[item.scope_type] += 1
        return {
            "scope_counts": counts,
            "actor_user_id": str(session.user_id),
            "target_user_id": str(target_user_id),
            "member_name": member.get("display_name") if member is not None else None,
            "organization": home["name"], "language": home["language"],
        }

    async def block_organization_access(
        self, *, telegram_user_id, tenant_id, expected_user_id, target_user_id,
    ):
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # The existing API checks all current grants, current authority,
        # membership serialization and the last-OWNER boundary atomically.
        result = await self.api.construction.block_organization_access(
            session=session, target_user_id=target_user_id,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": language,
        }

    async def organization_access_context(
        self, *, telegram_user_id, tenant_id, expected_user_id, target_user_id,
        grant_id=None,
    ):
        from services.construction.construction_team_transport import Grant
        if (
            not isinstance(target_user_id, UUID)
            or (grant_id is not None and not isinstance(grant_id, UUID))
        ):
            raise ValueError("Organization member/grant UUID is required.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        members = await self.api.construction.team_members(session=session)
        member = next((
            item for item in members.data if item["user_id"] == str(target_user_id)
        ), None)
        if member is None:
            raise ConstructionApiClientError(status_code=404, code="member_unavailable")
        roles = await self.api.construction.organization_role_options(session=session)
        original = None
        if grant_id is not None:
            selected = next((
                grant for grant in member["grants"] if grant["id"] == str(grant_id)
            ), None)
            if selected is None:
                raise ConstructionApiClientError(status_code=404, code="grant_unavailable")
            original = Grant.model_validate(selected)
            if (
                original.scope_type != "tenant" or original.scope_id != tenant_id
                or original.role not in ("OWNER", "ADMIN")
                or original.role not in roles
            ):
                raise ConstructionApiClientError(status_code=403, code="permission_required")
        existing = {
            grant["role"] for grant in member["grants"]
            if grant["scope_type"] == "tenant"
            and grant["scope_id"] == str(tenant_id)
            and (original is None or grant["id"] != str(original.id))
        }
        return {
            "roles": [role for role in roles if role not in existing],
            "original_grant": original.model_dump(mode="json") if original else None,
            "member_name": member.get("display_name"),
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def replace_organization_access(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        target_user_id, original_grant, role, expires_at, idempotency_key,
    ):
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # Replay preserves the old grant ID. A current-team read must not
        # replace it with a newer grant or prevent its historical receipt.
        result = await self.api.construction.replace_organization_access(
            session=session, target_user_id=target_user_id,
            original_grant=original_grant, role=role, expires_at=expires_at,
            idempotency_key=idempotency_key,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": language,
        }

    async def create_organization_access(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        target_user_id, role, expires_at, idempotency_key,
    ):
        session, language = await self._client_identity_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # A historical request is replayed without substituting another member
        # or requiring the old role to remain in the current role-options list.
        result = await self.api.construction.create_organization_access(
            session=session, target_user_id=target_user_id,
            role=role, expires_at=expires_at, idempotency_key=idempotency_key,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "language": language,
        }

    async def team_members(self, *, telegram_user_id, tenant_id,
                           expected_user_id=None, project_id=None):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if expected_user_id is not None and session.user_id != expected_user_id:
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        if project_id is None and home.get("can_manage_team") is not True:
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        result = await self.api.construction.team_members(
            session=session, project_id=project_id,
        )
        return {
            "items": result.data, "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def invitation_context(
        self, *, telegram_user_id, tenant_id, expected_user_id, project_id=None,
    ):
        from services.construction.construction_invitation_api_client import ConstructionInvitationApiClient
        session, home = await self._company_logo_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        if project_id is not None and not isinstance(project_id, UUID):
            raise ValueError("Project UUID is required.")
        if project_id is None and home.get("can_manage_team") is not True:
            raise ConstructionApiClientError(status_code=403, code="permission_required")
        protocol = ConstructionInvitationApiClient(self.api.construction)
        items = await protocol.list(session=session, project_id=project_id)
        try:
            roles = await protocol.role_options(session=session, project_id=project_id)
        except ConstructionApiClientError as error:
            if error.status_code != 403:
                raise
            roles = []
        return {
            "items": items, "roles": roles,
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def invitation_write(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        operation, payload, idempotency_key, invitation_id=None, project_id=None,
    ):
        from services.construction.construction_invitation_api_client import ConstructionInvitationApiClient
        session, home = await self._company_logo_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        if project_id is not None and not isinstance(project_id, UUID):
            raise ValueError("Project UUID is required.")
        if project_id is None and home.get("can_manage_team") is not True:
            raise ConstructionApiClientError(status_code=403, code="permission_required")
        if operation == "create":
            expected_scope = [{
                "scope_type": "project" if project_id is not None else "tenant",
                "scope_id": str(project_id if project_id is not None else tenant_id),
            }]
            if (
                not isinstance(payload, dict)
                or not {"email", "role", "scopes"} <= set(payload)
                or set(payload) - {"email", "role", "scopes", "grant_expires_at"}
                or payload["scopes"] != expected_scope
            ):
                raise ValueError("Invitation form scope differs.")
        # Do not reject a keyed replay based on the invitation's later status.
        item = await ConstructionInvitationApiClient(self.api.construction).write(
            session=session, operation=operation, payload=payload,
            idempotency_key=idempotency_key, invitation_id=invitation_id,
            project_id=project_id,
        )
        return {"item": item, "actor_user_id": str(session.user_id)}

    async def construction_company_settings(
        self, *, telegram_user_id, tenant_id, expected_user_id,
    ):
        from services.construction.construction_company_settings_api import (
            ConstructionCompanySettingsApi, problem,
        )
        from services.construction.construction_company_logo_api import (
            MAX_LOGO_BYTES, SAFE_TYPES, origin_parts,
        )
        session, home = await self._company_logo_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        caps = await self.api.construction.company_profile_capabilities(session=session)
        raw = caps.data
        try:
            if type(raw["can_manage_settings"]) is not bool:
                raise ValueError("Settings capability differs.")
            if type(raw["image_max_size_bytes"]) is not int or raw["image_max_size_bytes"] <= 0:
                raise ValueError("Image limit differs.")
            origin = raw["storage_origin"]
            if origin is not None:
                origin_parts(origin)
            mimes = tuple(sorted({
                item["mime_type"] for item in raw["image_types"]
                if item["mime_type"] in SAFE_TYPES
                and item["extension"] in SAFE_TYPES[item["mime_type"]]
            }))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        item = await ConstructionCompanySettingsApi(self.api.construction).read(session=session)
        return {
            "item": item, "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "can_manage": raw["can_manage_settings"],
            "can_upload_logo": (
                raw["can_manage_profile"] is True and origin is not None and bool(mimes)
            ),
            "can_preview_logo": origin is not None,
            "max_bytes": min(raw["image_max_size_bytes"], MAX_LOGO_BYTES),
            "mime_types": mimes,
        }

    async def save_construction_company_settings(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        changes, expected_row_version,
    ):
        from services.construction.construction_company_settings_api import (
            ConstructionCompanySettingsApi, problem,
        )
        session, home = await self._company_logo_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        protocol = ConstructionCompanySettingsApi(self.api.construction)
        changes = protocol.validate_changes(changes)
        caps = await self.api.construction.company_profile_capabilities(session=session)
        if caps.data.get("can_manage_settings") is not True:
            raise problem(403, "permission_required")
        if expected_row_version is None and all(value is None for value in changes.values()):
            current = await protocol.read(session=session)
            if current is not None:
                raise problem(409, "ROW_VERSION_CONFLICT")
            return {
                "item": None, "changed": False,
                "actor_user_id": str(session.user_id),
            }
        item = await protocol.write(
            session=session, changes=changes,
            expected_row_version=expected_row_version,
        )
        return {
            "item": item, "actor_user_id": str(session.user_id),
            "changed": expected_row_version is None or item["row_version"] != expected_row_version,
        }

    async def download_construction_company_settings_logo(
        self, *, telegram_user_id, tenant_id, expected_user_id, expected_file_id,
    ):
        from services.construction.construction_company_settings_api import (
            ConstructionCompanySettingsApi, problem,
        )
        from services.construction.construction_company_logo_api import CompanyLogoApi
        session, home = await self._company_logo_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        item = await ConstructionCompanySettingsApi(self.api.construction).read(session=session)
        if (
            not isinstance(expected_file_id, UUID) or item is None
            or item["logo_file_id_override"] != str(expected_file_id)
        ):
            raise problem(404, "file_not_found")
        result = await CompanyLogoApi(self.api.construction).download(
            session=session, file_id=expected_file_id,
        )
        return {**result, "actor_user_id": str(session.user_id)}

    async def _company_logo_session(
        self, *, telegram_user_id, tenant_id, expected_user_id,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        return session, home

    async def company_logo_context(
        self, *, telegram_user_id, tenant_id, expected_user_id,
    ):
        from services.construction.construction_company_logo_api import CompanyLogoApi
        session, home = await self._company_logo_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        protocol = CompanyLogoApi(self.api.construction)
        options = await protocol.options(session=session)
        profile = await self.api.construction.company_profile(session=session)
        item = profile.data
        return {
            "profile_id": item["id"] if item else None,
            "row_version": item["row_version"] if item else None,
            "logo_file_id": item["logo_file_id"] if item else None,
            "can_manage": options.can_manage,
            "can_transfer": options.origin is not None,
            "max_bytes": options.max_bytes, "mime_types": tuple(options.extensions),
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def upload_company_logo(
        self, *, telegram_user_id, tenant_id, expected_user_id, content, mime,
    ):
        from services.construction.construction_company_logo_api import CompanyLogoApi
        session, home = await self._company_logo_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        item = await CompanyLogoApi(self.api.construction).upload(
            session=session, content=content, mime=mime,
        )
        return {"item": item, "actor_user_id": str(session.user_id)}

    async def save_company_logo(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        profile_id, expected_row_version, logo_file_id, legal_name=None,
    ):
        from services.construction.construction_company_logo_api import CompanyLogoApi
        session, home = await self._company_logo_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        item = await CompanyLogoApi(self.api.construction).save(
            session=session, profile_id=profile_id,
            expected_row_version=expected_row_version,
            logo_file_id=logo_file_id, legal_name=legal_name,
        )
        return {"item": item, "actor_user_id": str(session.user_id)}

    async def download_company_logo(
        self, *, telegram_user_id, tenant_id, expected_user_id, expected_file_id,
    ):
        from services.construction.construction_company_logo_api import CompanyLogoApi
        session, home = await self._company_logo_session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        profile = await self.api.construction.company_profile(session=session)
        if (
            not isinstance(expected_file_id, UUID) or profile.data is None
            or profile.data["logo_file_id"] != str(expected_file_id)
        ):
            raise ConstructionApiClientError(
                status_code=404, code="file_not_found",
            )
        item = await CompanyLogoApi(self.api.construction).download(
            session=session, file_id=expected_file_id,
        )
        return {**item, "actor_user_id": str(session.user_id)}

    async def company_bank_accounts(
        self, *, telegram_user_id, tenant_id, expected_user_id,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        profile = await self.api.construction.company_profile(session=session)
        capabilities = await self.api.construction.company_profile_capabilities(
            session=session,
        )
        accounts = await self.api.construction.company_bank_accounts(session=session)
        if profile.data is None and accounts.data:
            raise ConstructionApiClientError(
                status_code=502, code="invalid_api_response",
            )
        return {
            "items": accounts.data,
            "profile_id": profile.data["id"] if profile.data else None,
            "can_manage": capabilities.data["can_manage_profile"],
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def mutate_company_bank_account(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        operation, payload, account_id=None,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        if operation not in ("create", "update", "deactivate"):
            raise ValueError("Unknown bank account operation.")
        capabilities = await self.api.construction.company_profile_capabilities(
            session=session,
        )
        if capabilities.data.get("can_manage_profile") is not True:
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        if operation != "create":
            if not isinstance(account_id, UUID):
                raise ValueError("Bank account UUID is required.")
            current = await self.api.construction.company_bank_accounts(session=session)
            if not any(item["id"] == str(account_id) for item in current.data):
                raise ConstructionApiClientError(
                    status_code=404, code="company_bank_account_not_found",
                )
            # Preserve the caller's expected version; never rebase after this read.
        receipt = await self.api.construction.mutate_company_bank_account(
            session=session, operation=operation,
            payload=payload, account_id=account_id,
        )
        return {"item": receipt.data, "actor_user_id": str(session.user_id)}

    async def company_identifiers(
        self, *, telegram_user_id, tenant_id, expected_user_id,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        profile = await self.api.construction.company_profile(session=session)
        capabilities = await self.api.construction.company_profile_capabilities(
            session=session,
        )
        identifiers = await self.api.construction.company_identifiers(session=session)
        if profile.data is None and identifiers.data:
            raise ConstructionApiClientError(
                status_code=502, code="invalid_api_response",
            )
        return {
            "items": identifiers.data,
            "profile_id": profile.data["id"] if profile.data else None,
            "can_manage": capabilities.data["can_manage_profile"],
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def mutate_company_identifier(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        operation, identifier_id=None, payload=None,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        if operation not in ("create", "replace", "delete"):
            raise ValueError("Unknown identifier operation.")
        capabilities = await self.api.construction.company_profile_capabilities(
            session=session,
        )
        if capabilities.data.get("can_manage_profile") is not True:
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        if operation != "create":
            if not isinstance(identifier_id, UUID):
                raise ValueError("Identifier UUID is required.")
            current = await self.api.construction.company_identifiers(session=session)
            if not any(item["id"] == str(identifier_id) for item in current.data):
                raise ConstructionApiClientError(
                    status_code=404, code="company_identifier_not_found",
                )
        receipt = await self.api.construction.mutate_company_identifier(
            session=session, operation=operation,
            identifier_id=identifier_id, payload=payload,
        )
        return {"item": receipt.data, "actor_user_id": str(session.user_id)}

    async def save_company_profile(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        operation, payload, expected_profile_id=None,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id
            or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        capabilities = await self.api.construction.company_profile_capabilities(
            session=session,
        )
        if capabilities.data.get("can_manage_profile") is not True:
            raise ConstructionApiClientError(
                status_code=403, code="permission_required",
            )
        result = await self.api.construction.save_company_profile(
            session=session, operation=operation, payload=payload,
            expected_profile_id=expected_profile_id,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
        }

    async def company_profile(
        self, *, telegram_user_id, tenant_id, expected_user_id=None,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            session.tenant_id != tenant_id
            or (expected_user_id is not None and session.user_id != expected_user_id)
        ):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.company_profile(session=session)
        capabilities = await self.api.construction.company_profile_capabilities(
            session=session,
        )
        return {
            "item": result.data, "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "can_manage_profile": capabilities.data["can_manage_profile"],
        }

    async def home(self, *, telegram_user_id, tenant_id):
        _, data = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id
        )
        return data

    async def leads(
        self, *, telegram_user_id, tenant_id, cursor=None,
        status=None, expected_user_id=None, mine_only=False,
    ):
        from services.construction.construction_lead_rules import CONSTRUCTION_LEAD_STATUSES
        if status is not None and status not in CONSTRUCTION_LEAD_STATUSES:
            raise ValueError("Unknown lead status filter.")
        if type(mine_only) is not bool:
            raise ValueError("Responsible filter must be boolean.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id
        )
        if (
            session.tenant_id != tenant_id
            or (expected_user_id is not None and session.user_id != expected_user_id)
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        options = {"session": session, "limit": 10, "cursor": cursor}
        filters = {}
        if status is not None:
            filters["status"] = status
        if mine_only:
            filters["responsible_user_id"] = str(session.user_id)
        if filters:
            options["filters"] = filters
        page = await self.api.construction.leads(**options)
        if status is not None and any(item.get("status") != status for item in page.data):
            raise ConstructionApiClientError(status_code=502, code="invalid_api_response")
        if mine_only and any(
            item.get("responsible_user_id") != str(session.user_id) for item in page.data
        ):
            raise ConstructionApiClientError(status_code=502, code="invalid_api_response")
        return {
            "language": home["language"],
            "organization": home["name"],
            "items": page.data,
            "next_cursor": page.meta["pagination"]["next_cursor"],
            "actor_user_id": str(session.user_id),
        }

    async def lead(self, *, telegram_user_id, tenant_id, lead_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id
        )
        item = await self.api.construction.lead(
            session=session, lead_id=lead_id
        )
        return {
            "language": home["language"],
            "organization": home["name"],
            "item": item.data,
        }



    async def change_lead_status(self, *, telegram_user_id, tenant_id,
                                 lead_id, next_status, expected_row_version):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        result = await self.api.construction.change_lead_status(
            session=session, lead_id=lead_id,
            next_status=next_status,
            expected_row_version=expected_row_version,
        )
        return {
            "language": home["language"],
            "organization": home["name"],
            "item": result.data,
        }


    async def activity_form(self, *, telegram_user_id, tenant_id, lead_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        lead = await self.api.construction.lead(session=session, lead_id=lead_id)
        return {
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": home["language"],
            "timezone": home.get("timezone"),
            "can_choose_responsible": home.get("can_manage_team") is True,
            "lead_name": lead.data["name"],
        }

    async def project_activity_form(
        self, *, telegram_user_id, tenant_id, project_id, expected_user_id, resume=False,
    ):
        if not isinstance(project_id, UUID) or type(resume) is not bool:
            raise ValueError("Project activity context is invalid.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        result = {
            "project_id": str(project_id), "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "timezone": home.get("timezone"),
            "can_choose_responsible": home.get("can_manage_team") is True,
        }
        if not resume:
            project = await self.api.construction.project_details(
                session=session, project_id=project_id,
            )
            result["subject_name"] = project.data["name"]
        return result

    async def create_project_activity(
        self, *, telegram_user_id, tenant_id, project_id, expected_user_id,
        payload, assign_self, idempotency_key, responsible_user_id=None,
    ):
        if (
            type(assign_self) is not bool
            or (responsible_user_id is not None and (
                not isinstance(responsible_user_id, UUID) or assign_self
            ))
        ):
            raise ValueError("Invalid responsible selection.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        result = await self.api.construction.create_project_activity(
            session=session, project_id=project_id, payload=dict(payload),
            responsible_user_id=session.user_id if assign_self else responsible_user_id,
            idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}

    async def client_activity_form(
        self, *, telegram_user_id, tenant_id, client_id, expected_user_id, resume=False,
    ):
        if not isinstance(client_id, UUID) or type(resume) is not bool:
            raise ValueError("Client activity context is invalid.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        result = {
            "client_id": str(client_id), "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "timezone": home.get("timezone"),
            "can_choose_responsible": home.get("can_manage_team") is True,
        }
        if not resume:
            card = await self.api.construction.client_card(
                session=session, client_id=client_id,
            )
            result["subject_name"] = card.data["display_name"]
        return result

    async def create_client_activity(
        self, *, telegram_user_id, tenant_id, client_id, expected_user_id,
        payload, assign_self, idempotency_key, responsible_user_id=None,
    ):
        if (
            type(assign_self) is not bool
            or (responsible_user_id is not None and (
                not isinstance(responsible_user_id, UUID) or assign_self
            ))
        ):
            raise ValueError("Invalid responsible selection.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        # Replay uses the frozen client and command, without a mutable card read.
        result = await self.api.construction.create_client_activity(
            session=session, client_id=client_id, payload=dict(payload),
            responsible_user_id=session.user_id if assign_self else responsible_user_id,
            idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}

    async def create_lead_activity(self, *, telegram_user_id, tenant_id,
                                   lead_id, expected_user_id, payload,
                                   assign_self, idempotency_key,
                                   responsible_user_id=None):
        if (
            type(assign_self) is not bool
            or (responsible_user_id is not None and (
                not isinstance(responsible_user_id, UUID) or assign_self
            ))
        ):
            raise ValueError("Invalid responsible selection.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if str(session.user_id) != str(expected_user_id):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.create_lead_activity(
            session=session, lead_id=lead_id, payload=dict(payload),
            responsible_user_id=session.user_id if assign_self else responsible_user_id,
            idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}


    async def project_activity_terminal_context(
        self, *, telegram_user_id, tenant_id, project_id, activity_id,
        expected_user_id, client_id=None, cursor=None,
    ):
        page = await self.project_planned_activities(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            project_id=project_id, expected_user_id=expected_user_id,
            client_id=client_id, cursor=cursor,
        )
        activity = next((
            item for item in page["items"] if item["id"] == str(activity_id)
        ), None)
        if activity is None:
            raise ConstructionApiClientError(status_code=404, code="not_found")
        return {
            "activity": activity, "project_id": str(project_id),
            "actor_user_id": page["actor_user_id"],
            "subject_name": page["project_name"],
            "organization": page["organization"], "language": page["language"],
        }

    async def terminal_project_activity(
        self, *, telegram_user_id, tenant_id, project_id, activity_id,
        expected_user_id, expected_links, operation, payload, idempotency_key,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or not isinstance(project_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        # A replay must not depend on presence in the planned activity list.
        result = await self.api.construction.terminal_project_activity(
            session=session, project_id=project_id, activity_id=activity_id,
            expected_links=dict(expected_links), operation=operation,
            payload=dict(payload), idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}

    async def client_activity_terminal_context(
        self, *, telegram_user_id, tenant_id, client_id, activity_id,
        expected_user_id, cursors,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        card = await self.api.construction.client_card(
            session=session, client_id=client_id, cursors=dict(cursors),
        )
        activity = next((
            item for item in card.data["activities"] if item["id"] == str(activity_id)
        ), None)
        if activity is None:
            raise ConstructionApiClientError(status_code=404, code="not_found")
        return {
            "activity": activity, "client_id": str(client_id),
            "actor_user_id": str(session.user_id), "subject_name": card.data["display_name"],
            "organization": home["name"], "language": home["language"],
        }

    async def terminal_client_activity(
        self, *, telegram_user_id, tenant_id, client_id, activity_id,
        expected_user_id, expected_links, operation, payload, idempotency_key,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        # No planned-list read here: a completed activity may already be absent.
        result = await self.api.construction.terminal_client_activity(
            session=session, client_id=client_id, activity_id=activity_id,
            expected_links=dict(expected_links), operation=operation,
            payload=dict(payload), idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}

    async def activity_terminal_context(self, *, telegram_user_id, tenant_id, lead_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        lead = await self.api.construction.lead(session=session, lead_id=lead_id)
        return {
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": home["language"],
            "lead_name": lead.data["name"],
            "next_action": lead.data["next_action"],
        }

    async def terminal_lead_activity(self, *, telegram_user_id, tenant_id,
                                     lead_id, activity_id, expected_user_id,
                                     operation, payload, idempotency_key):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if str(session.user_id) != str(expected_user_id):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # Replay targets the original activity even after next_action changes.
        result = await self.api.construction.terminal_lead_activity(
            session=session, lead_id=lead_id, activity_id=activity_id,
            operation=operation, payload=dict(payload),
            idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}



    async def conversion_search(self, *, telegram_user_id, tenant_id,
                                expected_user_id, query):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if str(session.user_id) != str(expected_user_id):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.address_suggestions(
            session=session, query=query,
        )
        return {"items": result.data, "language": home["language"]}

    async def select_conversion_client(self, *, telegram_user_id, tenant_id,
                                       expected_user_id, candidate):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if str(session.user_id) != str(expected_user_id):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        if not isinstance(candidate, dict):
            raise ValueError("Invalid selection.")
        entity_id = UUID(candidate["entity_id"])
        if candidate["entity_type"] == "client":
            client_id = entity_id
        elif candidate["entity_type"] == "project":
            project = await self.api.construction.project(
                session=session, project_id=entity_id,
            )
            value = project.data.get("client_id")
            if value is None:
                raise ConstructionApiClientError(
                    status_code=404, code="not_found",
                )
            client_id = UUID(value)
        else:
            raise ValueError("Unsupported selection.")
        # Read the current client through its normal authorized API.
        client = await self.api.construction.client_summary(
            session=session, client_id=client_id,
        )
        return {"client": client.data, "language": home["language"]}

    async def conversion_projects(self, *, telegram_user_id, tenant_id,
                                  expected_user_id, client_id, cursor=None):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if str(session.user_id) != str(expected_user_id):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.conversion_projects(
            session=session, client_id=client_id, cursor=cursor,
        )
        return {
            "items": result.data,
            "next_cursor": result.meta["pagination"]["next_cursor"],
            "language": home["language"],
        }

    async def select_conversion_project(self, *, telegram_user_id, tenant_id,
                                        expected_user_id, client_id, project_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if str(session.user_id) != str(expected_user_id):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        result = await self.api.construction.conversion_project(
            session=session, client_id=client_id, project_id=project_id,
        )
        if (
            result.data.get("id") != str(project_id)
            or result.data.get("tenant_id") != str(session.tenant_id)
            or result.data.get("client_id") != str(client_id)
        ):
            raise ConstructionApiClientError(
                status_code=404, code="not_found",
            )
        return {"project": result.data, "language": home["language"]}

    async def conversion_form(self, *, telegram_user_id, tenant_id, lead_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        result = await self.api.construction.lead(
            session=session, lead_id=lead_id,
        )
        return {
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": home["language"],
            "item": result.data,
        }

    async def convert_lead(self, *, telegram_user_id, tenant_id, lead_id,
                           expected_user_id, payload, idempotency_key):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if str(session.user_id) != str(expected_user_id):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        # Do not reject a replay merely because the lead is now won.
        result = await self.api.construction.convert_lead(
            session=session, lead_id=lead_id, payload=dict(payload),
            idempotency_key=idempotency_key,
        )
        return {"item": result.data, "language": home["language"]}

    async def lead_address_suggestions(
        self, *, telegram_user_id, tenant_id, expected_user_id, query,
    ):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or session.user_id != expected_user_id
            or session.tenant_id != tenant_id
        ):
            raise ConstructionApiClientError(status_code=403, code="account_unavailable")
        result = await self.api.construction.address_suggestions(
            session=session, query=query,
        )
        return {
            "items": result.data,
            "actor_user_id": str(session.user_id),
            "tenant_id": str(session.tenant_id),
            "language": home["language"],
        }

    async def lead_form(self, *, telegram_user_id, tenant_id):
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        return {
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": home["language"],
            "can_choose_responsible": home.get("can_manage_team") is True,
        }

    async def create_lead(self, *, telegram_user_id, tenant_id,
                          expected_user_id, payload, assign_self,
                          idempotency_key, responsible_user_id=None):
        if type(assign_self) is not bool or not isinstance(payload, dict):
            raise ValueError("Invalid lead form.")
        if responsible_user_id is not None and (
            not isinstance(responsible_user_id, UUID) or assign_self
        ):
            raise ValueError("Invalid lead responsible selection.")
        allowed = {
            "source", "name", "work_type_description",
            "phone", "email", "address_raw", "comment",
        }
        if set(payload) - allowed:
            raise ValueError("Caller identity fields are forbidden.")
        session, home = await self._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if str(session.user_id) != str(expected_user_id):
            raise ConstructionApiClientError(
                status_code=403, code="account_unavailable",
            )
        responsible = session.user_id if assign_self else responsible_user_id
        result = await self.api.construction.create_lead(
            session=session,
            payload={
                **payload,
                "responsible_user_id": str(responsible) if responsible else None,
            },
            idempotency_key=idempotency_key,
        )
        return {
            "language": home["language"],
            "organization": home["name"],
            "item": result.data,
        }


@lru_cache(maxsize=1)
def get_construction_bot_api_runtime():
    from api.platform_bot_application import create_platform_bot_application
    return create_platform_bot_application(), BotApiSessionCache()


@asynccontextmanager
async def construction_bot_api_entry(*, bot, event_update):
    # Proof is built before runtime/database dependencies are reached.
    context = make_platform_bot_api_context(bot=bot, update=event_update)
    application, sessions = get_construction_bot_api_runtime()
    async with PlatformBotApiClient(
        application=application, context=context
    ) as api:
        yield ConstructionBotApiEntry(
            api=api, context=context, sessions=sessions
        )

# project_activity_terminal_v1

# standalone_project_address_suggestions_v1

# project_edit_address_suggestions_v1

# lead_address_suggestions_v1

# construction_scope_names_v1

# universal_area_editor_v1

# client_link_email_http_v1
