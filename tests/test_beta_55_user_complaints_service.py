from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_complaints import (
    UserComplaintsAccessError,
    UserComplaintsSelectionError,
    UserComplaintsService,
)
from services.user_settings import (
    UserSettingsNotFoundError,
)


class FakeSettings:
    def __init__(
        self,
        *,
        context=None,
        error=None,
    ):
        self.context = context
        self.error = error
        self.calls = []

    async def get_context(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        return self.context


class FakeModeration:
    def __init__(self):
        self.resolve_calls = []
        self.create_calls = []
        self.confirm_calls = []
        self.target = (
            "specialist",
            uuid4(),
            uuid4(),
        )
        self.complaint = SimpleNamespace(
            id=uuid4()
        )

    async def resolve_thread_complaint_target(
        self,
        **kwargs,
    ):
        self.resolve_calls.append(kwargs)
        return self.target

    async def create_complaint(
        self,
        **kwargs,
    ):
        self.create_calls.append(kwargs)
        return self.complaint

    async def confirm_complaint(
        self,
        **kwargs,
    ):
        self.confirm_calls.append(kwargs)


def actor_context():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="uk",
    )


def build_service(
    *,
    context=None,
    error=None,
):
    settings = FakeSettings(
        context=context,
        error=error,
    )
    moderation = FakeModeration()
    service = UserComplaintsService(
        object(),
        settings=settings,
        moderation=moderation,
    )
    return service, settings, moderation


@pytest.mark.asyncio
async def test_complaints_require_actor():
    service, _, moderation = build_service(
        error=UserSettingsNotFoundError()
    )

    with pytest.raises(
        UserComplaintsAccessError,
        match="reporter",
    ):
        await service.resolve_thread_target(
            platform_user_id=123,
            thread_id=uuid4(),
        )

    assert moderation.resolve_calls == []


@pytest.mark.asyncio
async def test_thread_target_uses_actor_scope():
    context = actor_context()
    thread_id = uuid4()
    service, settings, moderation = (
        build_service(context=context)
    )

    result = await (
        service.resolve_thread_target(
            platform_user_id=456,
            thread_id=str(thread_id),
        )
    )

    assert result.actor.user_id == (
        context.user_id
    )
    assert result.actor.language == "uk"
    assert (
        result.target_type
        == moderation.target[0]
    )
    assert result.target_id == (
        moderation.target[1]
    )
    assert (
        result.conversation_thread_id
        == moderation.target[2]
    )
    assert settings.calls == [
        {
            "platform_user_id": 456,
        }
    ]
    assert moderation.resolve_calls == [
        {
            "tenant_id": context.tenant_id,
            "reporter_user_id": (
                context.user_id
            ),
            "thread_id": thread_id,
        }
    ]


@pytest.mark.asyncio
async def test_invalid_thread_fails_closed():
    service, _, moderation = build_service(
        context=actor_context()
    )

    with pytest.raises(
        UserComplaintsSelectionError,
        match="Invalid complaint thread",
    ):
        await service.resolve_thread_target(
            platform_user_id=123,
            thread_id="invalid",
        )

    assert moderation.resolve_calls == []


@pytest.mark.asyncio
async def test_create_and_confirm_complaint():
    context = actor_context()
    target_id = uuid4()
    thread_id = uuid4()
    service, _, moderation = build_service(
        context=context
    )

    result = await service.create_complaint(
        platform_user_id=789,
        target_type="specialist",
        target_id=str(target_id),
        reason="fake",
        comment="details",
        conversation_thread_id=(
            str(thread_id)
        ),
    )

    assert result.actor.user_id == (
        context.user_id
    )
    assert (
        result.complaint
        is moderation.complaint
    )
    assert moderation.create_calls == [
        {
            "tenant_id": context.tenant_id,
            "reporter_user_id": (
                context.user_id
            ),
            "target_type": "specialist",
            "target_id": target_id,
            "reason": "fake",
            "comment": "details",
            "conversation_thread_id": (
                thread_id
            ),
        }
    ]
    assert moderation.confirm_calls == [
        {
            "reporter_user_id": (
                context.user_id
            ),
            "complaint_id": (
                moderation.complaint.id
            ),
        }
    ]


@pytest.mark.asyncio
async def test_invalid_target_fails_before_write():
    service, _, moderation = build_service(
        context=actor_context()
    )

    with pytest.raises(
        UserComplaintsSelectionError,
        match="Invalid complaint target",
    ):
        await service.create_complaint(
            platform_user_id=123,
            target_type="specialist",
            target_id="invalid",
            reason="fake",
        )

    assert moderation.create_calls == []
    assert moderation.confirm_calls == []


def test_thread_report_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "report_thread_pending"
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert "UserComplaintsService" in called_names
    assert (
        "resolve_thread_target"
        in called_methods
    )
    assert not (
        called_names
        & {
            "UUID",
            "ModerationRepository",
            "ModerationService",
            "get_requester_context",
        }
    )


def test_create_complaint_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "create_search_complaint"
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert "UserComplaintsService" in called_names
    assert "create_complaint" in called_methods

    assert not (
        called_names
        & {
            "UUID",
            "ModerationRepository",
            "ModerationService",
            "get_requester_context",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "store_post_auth_action" in block
    assert 'action="report"' in block
    assert 'post_auth_action="report"' in block
