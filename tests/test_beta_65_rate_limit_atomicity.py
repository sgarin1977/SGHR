import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.rate_limit import (
    RateLimitService,
)


@pytest.mark.asyncio
async def test_rate_limit_locks_before_counting():
    tenant_id = uuid4()
    user_id = uuid4()
    calls = []

    class FakeRepository:
        async def get_active_rule(
            self,
            *,
            scope,
            action,
        ):
            calls.append(
                ("rule", scope, action)
            )
            return SimpleNamespace(
                window_seconds=60,
                limit_count=5,
                penalty_action="block",
            )

        async def acquire_action_lock(
            self,
            *,
            tenant_id,
            user_id,
            action,
        ):
            calls.append(
                (
                    "lock",
                    tenant_id,
                    user_id,
                    action,
                )
            )

        async def log_rate_limit_exceeded(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Limit should not be exceeded"
            )

    async def counter(window_seconds):
        calls.append(
            ("count", window_seconds)
        )
        return 2

    service = RateLimitService(
        FakeRepository()
    )

    result = await service.ensure_action_allowed(
        tenant_id=tenant_id,
        user_id=user_id,
        action="chat_message",
        counter=counter,
    )

    assert result.allowed is True
    assert calls == [
        (
            "rule",
            "user",
            "chat_message",
        ),
        (
            "lock",
            tenant_id,
            user_id,
            "chat_message",
        ),
        (
            "count",
            60,
        ),
    ]


def test_rate_limit_repository_uses_transaction_lock():
    source = Path(
        "database/repositories/rate_limit.py"
    ).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    repository = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "RateLimitRepository"
    )

    matches = [
        node
        for node in repository.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "acquire_action_lock"
    ]

    assert len(matches) == 1

    block = (
        ast.get_source_segment(
            source,
            matches[0],
        )
        or ""
    )

    assert "pg_advisory_xact_lock" in block
    assert "tenant_id" in block
    assert "user_id" in block
    assert "action" in block
    assert ".commit(" not in block


@pytest.mark.asyncio
async def test_rate_limit_abuse_event_uses_independent_transaction():
    from database.repositories.rate_limit import (
        RateLimitRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()

    class MainSession:
        def add(self, instance):
            raise AssertionError(
                "Abuse event must not use "
                "the business session"
            )

    class AuditSession:
        def __init__(self):
            self.added = []
            self.commits = 0
            self.rollbacks = 0

        async def __aenter__(self):
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            return False

        def add(self, instance):
            self.added.append(instance)

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    audit_session = AuditSession()
    factory_calls = []

    def audit_session_factory():
        factory_calls.append(True)
        return audit_session

    repository = RateLimitRepository(
        MainSession(),
        audit_session_factory=(
            audit_session_factory
        ),
    )

    event = await (
        repository.log_rate_limit_exceeded(
            tenant_id=tenant_id,
            user_id=user_id,
            action="chat_message",
            limit_count=5,
            window_seconds=60,
            current_count=5,
            penalty_action="block",
        )
    )

    assert factory_calls == [True]
    assert audit_session.added == [event]
    assert audit_session.commits == 1
    assert audit_session.rollbacks == 0
    assert event.tenant_id == tenant_id
    assert event.user_id == user_id
    assert event.details == {
        "action": "chat_message",
        "limit_count": 5,
        "window_seconds": 60,
        "current_count": 5,
    }



@pytest.mark.asyncio
async def test_rate_limit_audit_transaction_rolls_back_on_failure():
    from database.repositories.rate_limit import (
        RateLimitRepository,
    )

    class MainSession:
        def add(self, instance):
            raise AssertionError(
                "Business session must not be used"
            )

    class AuditSession:
        def __init__(self):
            self.added = []
            self.commits = 0
            self.rollbacks = 0

        async def __aenter__(self):
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            return False

        def add(self, instance):
            self.added.append(instance)

        async def commit(self):
            self.commits += 1
            raise RuntimeError(
                "audit commit failed"
            )

        async def rollback(self):
            self.rollbacks += 1

    audit_session = AuditSession()

    repository = RateLimitRepository(
        MainSession(),
        audit_session_factory=(
            lambda: audit_session
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="audit commit failed",
    ):
        await repository.log_rate_limit_exceeded(
            tenant_id=uuid4(),
            user_id=uuid4(),
            action="chat_message",
            current_count=5,
            limit_count=5,
            window_seconds=60,
            penalty_action="block",
        )

    assert len(audit_session.added) == 1
    assert audit_session.commits == 1
    assert audit_session.rollbacks == 1
