from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.dictionaries import (
    DictionaryServiceError,
)
from services.admin_dictionaries import (
    AdminDictionariesAccessError,
    AdminDictionariesService,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user
        self.calls = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.calls.append(platform_user_id)
        return self.user


class FakeModeration:
    def __init__(self, roles):
        self.roles = set(roles)
        self.calls = []

    async def get_admin_roles(
        self,
        user_id,
        *,
        tenant_id,
    ):
        self.calls.append(
            (user_id, tenant_id)
        )
        return set(self.roles)


def build_service(
    *,
    user,
    roles,
):
    users = FakeUsers(user)
    moderation = FakeModeration(roles)
    dictionaries = object()
    service = AdminDictionariesService(
        object(),
        users=users,
        moderation=moderation,
        dictionaries=dictionaries,
    )

    return (
        service,
        users,
        moderation,
        dictionaries,
    )


@pytest.mark.asyncio
async def test_dictionary_actor_is_tenant_aware():
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    (
        service,
        users,
        moderation,
        dictionaries,
    ) = build_service(
        user=user,
        roles={"super_admin", "admin"},
    )

    actor = await service.require_actor(
        platform_user_id=123,
    )

    assert actor.user_id == user_id
    assert actor.tenant_id == tenant_id
    assert actor.roles == frozenset(
        {"super_admin", "admin"}
    )
    assert users.calls == [123]
    assert moderation.calls == [
        (user_id, tenant_id)
    ]
    assert service.dictionaries is dictionaries


@pytest.mark.asyncio
async def test_dictionary_actor_requires_user():
    (
        service,
        _users,
        moderation,
        _dictionaries,
    ) = build_service(
        user=None,
        roles={"super_admin"},
    )

    with pytest.raises(
        AdminDictionariesAccessError,
        match="Dictionary access denied",
    ):
        await service.require_actor(
            platform_user_id=123,
        )

    assert moderation.calls == []


@pytest.mark.asyncio
async def test_dictionary_actor_requires_tenant():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=None,
    )
    (
        service,
        _users,
        moderation,
        _dictionaries,
    ) = build_service(
        user=user,
        roles={"super_admin"},
    )

    with pytest.raises(
        AdminDictionariesAccessError
    ):
        await service.require_actor(
            platform_user_id=123,
        )

    assert moderation.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles",
    [
        set(),
        {"admin"},
        {"moderator"},
        {"support"},
        {"finance_admin"},
    ],
)
async def test_dictionary_actor_requires_super_admin(
    roles,
):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    (
        service,
        _users,
        moderation,
        _dictionaries,
    ) = build_service(
        user=user,
        roles=roles,
    )

    with pytest.raises(
        AdminDictionariesAccessError
    ):
        await service.require_actor(
            platform_user_id=123,
        )

    assert moderation.calls == [
        (user.id, user.tenant_id)
    ]


def test_admin_dictionaries_has_no_handler_dependency():
    from pathlib import Path

    source = Path(
        "services/admin_dictionaries.py"
    ).read_text(encoding="utf-8")

    assert "handlers." not in source
    assert "CallbackQuery" not in source
    assert "FSMContext" not in source
    assert "ui.texts" not in source


class FakeDictionarySession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class FakeCategoryDictionaries:
    def __init__(self):
        self.calls = []

    async def list_category_cards(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("list", kwargs)
        )
        return ["category-1", "category-2"]

    async def get_category_card(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("get", kwargs)
        )
        return "category-card"

    async def create_category(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("create", kwargs)
        )
        return "created-category"


def build_category_service(
    *,
    roles=None,
):
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    session = FakeDictionarySession()
    dictionaries = FakeCategoryDictionaries()
    service = AdminDictionariesService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration(
            roles or {"super_admin"}
        ),
        dictionaries=dictionaries,
    )

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_list_categories_authorizes_and_paginates():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_category_service()

    result = await service.list_categories(
        platform_user_id=123,
        language="uk",
        page=2,
        page_size=1,
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.items == ("category-1",)
    assert result.page == 2
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "list",
            {
                "language": "uk",
                "limit": 2,
                "offset": 2,
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_get_category_authorizes_and_delegates():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_category_service()
    category_id = uuid4()

    result = await service.get_category(
        platform_user_id=123,
        category_id=category_id,
        language="en",
    )

    assert result == "category-card"
    assert dictionaries.calls == [
        (
            "get",
            {
                "category_id": category_id,
                "language": "en",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_create_category_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_category_service()

    result = await service.create_category(
        platform_user_id=123,
        title="Test category",
        language="uk",
    )

    assert result == "created-category"
    assert dictionaries.calls == [
        (
            "create",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "title": "Test category",
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_category_operation_fails_before_domain_call():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_category_service(
        roles={"admin"},
    )

    with pytest.raises(
        AdminDictionariesAccessError
    ):
        await service.list_categories(
            platform_user_id=123,
            language="uk",
            page=0,
            page_size=5,
        )

    assert dictionaries.calls == []
    assert session.commits == 0


def test_category_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_categories_dictionary": (
            "list_categories"
        ),
        "admin_category_create_receive": (
            "create_category"
        ),
        "admin_category_open_receive": (
            "get_category"
        ),
        "admin_category_rename_receive": (
            "rename_category"
        ),
        "admin_category_toggle_visibility": (
            "toggle_category_visibility"
        ),
        "admin_category_archive": (
            "toggle_category_archive"
        ),
        "admin_category_sort_order_receive": (
            "update_category_sort_order"
        ),
        "admin_category_specialist_move_select_prompt": (
            "require_actor"
        ),
        "admin_category_specialist_move_all": (
            "list_category_specialist_ids"
        ),
        "admin_category_specialist_move_numbers_receive": (
            "require_actor"
        ),
        "admin_category_specialists": (
            "list_category_specialists"
        ),
        "admin_multi_move_category_selected": (
            "get_move_target_professions"
        ),
        "admin_multi_move_mode_selected": (
            "preview_multi_move"
        ),
        "admin_multi_move_confirm": (
            "execute_multi_move"
        ),
        "show_admin_multi_move_categories": (
            "list_move_target_categories"
        ),
        "show_admin_multi_move_professions": (
            "get_move_target_professions"
        ),
        "admin_professions_dictionary": (
            "list_professions"
        ),
        "admin_profession_open_receive": (
            "get_profession"
        ),
        "admin_profession_create_receive": (
            "create_profession"
        ),
    }

    for function_name, service_method in expected.items():
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        block = ast.get_source_segment(
            source,
            node,
        )
        calls = set()

        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue

            if isinstance(item.func, ast.Name):
                calls.add(item.func.id)
            elif isinstance(item.func, ast.Attribute):
                calls.add(item.func.attr)

        assert "AdminDictionariesService" in calls
        assert service_method in calls
        assert "get_admin_user_context" not in calls
        assert "DictionaryRepository" not in calls
        assert "DictionaryService" not in calls
        assert "commit" not in calls

    list_node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name == "admin_categories_dictionary"
    )
    list_block = ast.get_source_segment(
        source,
        list_node,
    )

    assert "ADMIN_CATEGORIES_PAGE_SIZE + 1" not in list_block
    assert "has_next = len(" not in list_block


class FakeCategoryMutationDictionaries(
    FakeCategoryDictionaries
):
    def __init__(self, *, archived_at=None):
        super().__init__()
        self.archived_at = archived_at

    async def rename_category(self, **kwargs):
        self.calls.append(("rename", kwargs))
        return "renamed-category"

    async def toggle_category_visibility(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("toggle_visibility", kwargs)
        )
        return "visibility-category"

    async def get_category_card(self, **kwargs):
        self.calls.append(("get", kwargs))
        return SimpleNamespace(
            status_code=(
                "archived"
                if self.archived_at
                else "active"
            )
        )

    async def archive_category(self, **kwargs):
        self.calls.append(("archive", kwargs))
        return "archived-category"

    async def unarchive_category(self, **kwargs):
        self.calls.append(("unarchive", kwargs))
        return "unarchived-category"

    async def update_category_sort_order(
        self,
        **kwargs,
    ):
        self.calls.append(("sort", kwargs))
        return "sorted-category"


def build_category_mutation_service(
    *,
    archived_at=None,
):
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    session = FakeDictionarySession()
    dictionaries = (
        FakeCategoryMutationDictionaries(
            archived_at=archived_at
        )
    )
    service = AdminDictionariesService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration(
            {"super_admin"}
        ),
        dictionaries=dictionaries,
    )

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_rename_category_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_category_mutation_service()
    category_id = uuid4()

    result = await service.rename_category(
        platform_user_id=123,
        category_id=category_id,
        title="Renamed",
        language="uk",
    )

    assert result == "renamed-category"
    assert dictionaries.calls == [
        (
            "rename",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "category_id": category_id,
                "title": "Renamed",
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_toggle_category_visibility_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_category_mutation_service()
    category_id = uuid4()

    result = await (
        service.toggle_category_visibility(
            platform_user_id=123,
            category_id=category_id,
            language="en",
        )
    )

    assert result == "visibility-category"
    assert dictionaries.calls == [
        (
            "toggle_visibility",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "category_id": category_id,
                "language": "en",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("archived_at", "expected_call", "expected_result"),
    [
        (
            None,
            "archive",
            "archived-category",
        ),
        (
            object(),
            "unarchive",
            "unarchived-category",
        ),
    ],
)
async def test_toggle_category_archive_selects_operation(
    archived_at,
    expected_call,
    expected_result,
):
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_category_mutation_service(
        archived_at=archived_at
    )
    category_id = uuid4()

    result = await service.toggle_category_archive(
        platform_user_id=123,
        category_id=category_id,
        language="uk",
    )

    assert result == expected_result
    assert dictionaries.calls == [
        (
            "get",
            {
                "category_id": category_id,
                "language": "uk",
            },
        ),
        (
            expected_call,
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "category_id": category_id,
                "language": "uk",
            },
        ),
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_category_archive_missing_item_does_not_commit():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_category_mutation_service()

    async def missing_category(**kwargs):
        dictionaries.calls.append(
            ("get", kwargs)
        )
        return None

    dictionaries.get_category_card = missing_category

    result = await service.toggle_category_archive(
        platform_user_id=123,
        category_id=uuid4(),
        language="uk",
    )

    assert result is None
    assert len(dictionaries.calls) == 1
    assert session.commits == 0


@pytest.mark.asyncio
async def test_update_category_sort_order_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_category_mutation_service()
    category_id = uuid4()

    result = await (
        service.update_category_sort_order(
            platform_user_id=123,
            category_id=category_id,
            sort_order_text="25",
            language="uk",
        )
    )

    assert result == "sorted-category"
    assert dictionaries.calls == [
        (
            "sort",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "category_id": category_id,
                "sort_order_text": "25",
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


class FakeCategorySpecialistDictionaries:
    def __init__(self):
        self.calls = []
        self.specialist_ids = [
            uuid4(),
            uuid4(),
        ]

    async def list_category_specialists(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("list", kwargs)
        )
        return [
            "specialist-1",
            "specialist-2",
            "specialist-3",
        ]

    async def list_category_specialist_ids(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("ids", kwargs)
        )
        return list(self.specialist_ids)


def build_category_specialist_service(
    *,
    roles=None,
):
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    session = FakeDictionarySession()
    dictionaries = (
        FakeCategorySpecialistDictionaries()
    )
    service = AdminDictionariesService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration(
            roles or {"super_admin"}
        ),
        dictionaries=dictionaries,
    )

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_category_specialists_list_paginates():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_category_specialist_service()
    category_id = uuid4()

    result = await service.list_category_specialists(
        platform_user_id=123,
        category_id=category_id,
        page=3,
        page_size=2,
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.items == (
        "specialist-1",
        "specialist-2",
    )
    assert result.page == 3
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "list",
            {
                "category_id": category_id,
                "limit": 3,
                "offset": 6,
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_category_specialist_ids_are_immutable():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_category_specialist_service()
    category_id = uuid4()

    result = await (
        service.list_category_specialist_ids(
            platform_user_id=123,
            category_id=category_id,
        )
    )

    assert result == tuple(
        dictionaries.specialist_ids
    )
    assert dictionaries.calls == [
        (
            "ids",
            {
                "category_id": category_id,
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_category_specialist_read_fails_closed():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_category_specialist_service(
        roles={"admin"}
    )

    with pytest.raises(
        AdminDictionariesAccessError
    ):
        await service.list_category_specialist_ids(
            platform_user_id=123,
            category_id=uuid4(),
        )

    assert dictionaries.calls == []
    assert session.commits == 0


class FakeMultiMoveDictionaries:
    def __init__(self):
        self.calls = []

    async def list_specialist_move_target_categories(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("categories", kwargs)
        )
        return ["category-1", "category-2"]

    async def get_category_card(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("category", kwargs)
        )
        return "target-category"

    async def list_active_professions_for_category(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("professions", kwargs)
        )
        return ["profession-1", "profession-2"]

    async def preview_multi_profession_move(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("preview", kwargs)
        )
        return "move-preview"

    async def move_specialists_to_multiple_professions(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("execute", kwargs)
        )
        return "move-result"


def build_multi_move_service(
    *,
    roles=None,
):
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    session = FakeDictionarySession()
    dictionaries = FakeMultiMoveDictionaries()
    service = AdminDictionariesService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration(
            roles or {"super_admin"}
        ),
        dictionaries=dictionaries,
    )

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_move_target_categories_are_paginated():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_multi_move_service()

    result = await (
        service.list_move_target_categories(
            platform_user_id=123,
            language="uk",
            page=9,
            page_size=1,
        )
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.items == ("category-1",)
    assert result.page == 0
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "categories",
            {
                "language": "uk",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_move_target_professions_are_combined():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_multi_move_service()
    category_id = uuid4()

    result = await (
        service.get_move_target_professions(
            platform_user_id=123,
            category_id=category_id,
            language="en",
            page=9,
            page_size=1,
        )
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.category == "target-category"
    assert result.professions == (
        "profession-1",
        "profession-2",
    )
    assert result.visible_professions == (
        "profession-1",
    )
    assert result.page == 0
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "category",
            {
                "category_id": category_id,
                "language": "en",
            },
        ),
        (
            "professions",
            {
                "category_id": category_id,
                "language": "en",
            },
        ),
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_multi_move_preview_delegates():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_multi_move_service()
    source_id = uuid4()
    category_id = uuid4()

    result = await service.preview_multi_move(
        platform_user_id=123,
        source_type="category",
        source_id=source_id,
        target_category_id=category_id,
        target_profession_ids=["profession-1"],
        specialist_ids=["specialist-1"],
        mode="add",
        language="uk",
    )

    assert result == "move-preview"
    assert dictionaries.calls == [
        (
            "preview",
            {
                "source_type": "category",
                "source_id": source_id,
                "target_category_id": category_id,
                "target_profession_ids": [
                    "profession-1"
                ],
                "specialist_ids": [
                    "specialist-1"
                ],
                "mode": "add",
                "language": "uk",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_multi_move_execution_attributes_actor():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_multi_move_service()
    source_id = uuid4()
    category_id = uuid4()

    result = await service.execute_multi_move(
        platform_user_id=123,
        source_type="profession",
        source_id=source_id,
        target_category_id=category_id,
        target_profession_ids=["profession-1"],
        specialist_ids=["specialist-1"],
        mode="replace",
        language="uk",
    )

    assert result == "move-result"
    assert dictionaries.calls == [
        (
            "execute",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "source_type": "profession",
                "source_id": source_id,
                "target_category_id": category_id,
                "target_profession_ids": [
                    "profession-1"
                ],
                "specialist_ids": [
                    "specialist-1"
                ],
                "mode": "replace",
                "language": "uk",
            },
        )
    ]

    # Domain DictionaryService owns this transaction.
    assert session.commits == 0


@pytest.mark.asyncio
async def test_multi_move_fails_before_domain_call():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_multi_move_service(
        roles={"admin"}
    )

    with pytest.raises(
        AdminDictionariesAccessError
    ):
        await service.preview_multi_move(
            platform_user_id=123,
            source_type="category",
            source_id=uuid4(),
            target_category_id=uuid4(),
            target_profession_ids=[],
            specialist_ids=[],
            mode="add",
            language="uk",
        )

    assert dictionaries.calls == []
    assert session.commits == 0


def test_multi_move_renderers_receive_real_actor_id():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "show_admin_multi_move_categories": 6,
        "show_admin_multi_move_professions": 4,
    }
    actual = {
        name: 0
        for name in expected
    }

    for function in tree.body:
        if not isinstance(
            function,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue

        for item in ast.walk(function):
            if (
                not isinstance(item, ast.Call)
                or not isinstance(
                    item.func,
                    ast.Name,
                )
                or item.func.id not in expected
            ):
                continue

            actual[item.func.id] += 1
            keywords = {
                keyword.arg
                for keyword in item.keywords
            }

            assert (
                "platform_user_id"
                in keywords
            ), function.name

    assert actual == expected

    for renderer_name in expected:
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == renderer_name
        )
        keyword_names = {
            argument.arg
            for argument
            in node.args.kwonlyargs
        }

        assert "platform_user_id" in keyword_names


class FakeProfessionDictionaries:
    def __init__(self):
        self.calls = []

    async def list_profession_cards(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return [
            "profession-1",
            "profession-2",
            "profession-3",
        ]

    async def get_profession_card(
        self,
        **kwargs,
    ):
        self.calls.append(("get", kwargs))
        return "profession-card"

    async def create_profession(
        self,
        **kwargs,
    ):
        self.calls.append(("create", kwargs))
        return "created-profession"


def build_profession_service(
    *,
    roles=None,
):
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    session = FakeDictionarySession()
    dictionaries = FakeProfessionDictionaries()
    service = AdminDictionariesService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration(
            roles or {"super_admin"}
        ),
        dictionaries=dictionaries,
    )

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_profession_list_paginates():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_service()

    result = await service.list_professions(
        platform_user_id=123,
        language="uk",
        page=2,
        page_size=2,
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.items == (
        "profession-1",
        "profession-2",
    )
    assert result.page == 2
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "list",
            {
                "language": "uk",
                "limit": 3,
                "offset": 4,
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_get_profession_delegates():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_profession_service()
    profession_id = uuid4()

    result = await service.get_profession(
        platform_user_id=123,
        profession_id=profession_id,
        language="en",
    )

    assert result == "profession-card"
    assert dictionaries.calls == [
        (
            "get",
            {
                "profession_id": profession_id,
                "language": "en",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_create_profession_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_service()
    category_id = uuid4()

    result = await service.create_profession(
        platform_user_id=123,
        category_id=category_id,
        category_code=None,
        title="Backend developer",
        language="uk",
    )

    assert result == "created-profession"
    assert dictionaries.calls == [
        (
            "create",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "category_id": category_id,
                "category_code": None,
                "title": "Backend developer",
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_profession_operation_fails_closed():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_profession_service(
        roles={"admin"}
    )

    with pytest.raises(
        AdminDictionariesAccessError
    ):
        await service.list_professions(
            platform_user_id=123,
            language="uk",
            page=0,
            page_size=5,
        )

    assert dictionaries.calls == []
    assert session.commits == 0


class FakeProfessionMutationDictionaries(
    FakeProfessionDictionaries
):
    def __init__(
        self,
        *,
        status_code="active",
        existing=True,
    ):
        super().__init__()
        self.status_code = status_code
        self.existing = existing

    async def get_profession_card(
        self,
        **kwargs,
    ):
        self.calls.append(("get", kwargs))

        if not self.existing:
            return None

        return SimpleNamespace(
            status_code=self.status_code,
        )

    async def rename_profession(
        self,
        **kwargs,
    ):
        self.calls.append(("rename", kwargs))
        return "renamed-profession"

    async def move_profession_to_category(
        self,
        **kwargs,
    ):
        self.calls.append(("move", kwargs))
        return "moved-profession"

    async def toggle_profession_visibility(
        self,
        **kwargs,
    ):
        self.calls.append(("visibility", kwargs))
        return "visibility-profession"

    async def archive_profession(
        self,
        **kwargs,
    ):
        self.calls.append(("archive", kwargs))
        return "archived-profession"

    async def unarchive_profession(
        self,
        **kwargs,
    ):
        self.calls.append(("unarchive", kwargs))
        return "unarchived-profession"


def build_profession_mutation_service(
    *,
    status_code="active",
    existing=True,
):
    (
        service,
        session,
        _dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_service()
    dictionaries = (
        FakeProfessionMutationDictionaries(
            status_code=status_code,
            existing=existing,
        )
    )
    service.dictionaries = dictionaries

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_rename_profession_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_mutation_service()
    profession_id = uuid4()

    result = await service.rename_profession(
        platform_user_id=123,
        profession_id=profession_id,
        title="Python developer",
        language="uk",
    )

    assert result == "renamed-profession"
    assert dictionaries.calls == [
        (
            "rename",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "profession_id": profession_id,
                "title": "Python developer",
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_move_profession_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_mutation_service()
    profession_id = uuid4()

    result = await (
        service.move_profession_to_category(
            platform_user_id=123,
            profession_id=profession_id,
            category_code="development",
            language="en",
        )
    )

    assert result == "moved-profession"
    assert dictionaries.calls == [
        (
            "move",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "profession_id": profession_id,
                "category_code": "development",
                "language": "en",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_toggle_profession_visibility_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_mutation_service()
    profession_id = uuid4()

    result = await (
        service.toggle_profession_visibility(
            platform_user_id=123,
            profession_id=profession_id,
            language="uk",
        )
    )

    assert result == "visibility-profession"
    assert dictionaries.calls == [
        (
            "visibility",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "profession_id": profession_id,
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_active_profession_is_archived():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_mutation_service(
        status_code="active"
    )
    profession_id = uuid4()

    result = await (
        service.toggle_profession_archive(
            platform_user_id=123,
            profession_id=profession_id,
            language="uk",
        )
    )

    assert result.item == "archived-profession"
    assert result.archived is True
    assert dictionaries.calls == [
        (
            "get",
            {
                "profession_id": profession_id,
                "language": "uk",
            },
        ),
        (
            "archive",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "profession_id": profession_id,
                "language": "uk",
            },
        ),
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_archived_profession_is_unarchived():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_mutation_service(
        status_code="archived"
    )
    profession_id = uuid4()

    result = await (
        service.toggle_profession_archive(
            platform_user_id=123,
            profession_id=profession_id,
            language="en",
        )
    )

    assert result.item == "unarchived-profession"
    assert result.archived is False
    assert dictionaries.calls == [
        (
            "get",
            {
                "profession_id": profession_id,
                "language": "en",
            },
        ),
        (
            "unarchive",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "profession_id": profession_id,
                "language": "en",
            },
        ),
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_missing_profession_archive_fails_without_commit():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_profession_mutation_service(
        existing=False
    )

    with pytest.raises(
        DictionaryServiceError,
        match="admin_item_not_found",
    ):
        await service.toggle_profession_archive(
            platform_user_id=123,
            profession_id=uuid4(),
            language="uk",
        )

    assert len(dictionaries.calls) == 1
    assert dictionaries.calls[0][0] == "get"
    assert session.commits == 0


def test_profession_mutation_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_profession_rename_receive": (
            "rename_profession"
        ),
        "admin_profession_move_receive": (
            "move_profession_to_category"
        ),
        "admin_profession_toggle_visibility": (
            "toggle_profession_visibility"
        ),
        "admin_profession_archive": (
            "toggle_profession_archive"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        called_names = set()
        called_attributes = set()

        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue

            if isinstance(item.func, ast.Name):
                called_names.add(item.func.id)

            if isinstance(item.func, ast.Attribute):
                called_attributes.add(
                    item.func.attr
                )

        assert (
            "AdminDictionariesService"
            in called_names
        ), function_name
        assert (
            service_method
            in called_attributes
        ), function_name

        assert (
            "get_admin_user_context"
            not in called_names
        ), function_name
        assert (
            "DictionaryRepository"
            not in called_names
        ), function_name
        assert (
            "DictionaryService"
            not in called_names
        ), function_name
        assert "commit" not in called_attributes, (
            function_name
        )


class FakeProfessionSpecialistDictionaries:
    def __init__(self):
        self.calls = []

    async def list_profession_specialist_ids(
        self,
        **kwargs,
    ):
        self.calls.append(("ids", kwargs))
        return [
            "specialist-1",
            "specialist-2",
        ]

    async def list_profession_specialists(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return [
            "specialist-card-1",
            "specialist-card-2",
            "specialist-card-3",
        ]


def build_profession_specialist_service():
    (
        service,
        session,
        _dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_service()
    dictionaries = (
        FakeProfessionSpecialistDictionaries()
    )
    service.dictionaries = dictionaries

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_list_profession_specialist_ids_delegates():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_profession_specialist_service()
    profession_id = uuid4()

    result = await (
        service.list_profession_specialist_ids(
            platform_user_id=123,
            profession_id=profession_id,
        )
    )

    assert result == (
        "specialist-1",
        "specialist-2",
    )
    assert dictionaries.calls == [
        (
            "ids",
            {
                "profession_id": profession_id,
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_list_profession_specialists_paginates():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_profession_specialist_service()
    profession_id = uuid4()

    result = await (
        service.list_profession_specialists(
            platform_user_id=123,
            profession_id=profession_id,
            page=2,
            page_size=2,
        )
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.items == (
        "specialist-card-1",
        "specialist-card-2",
    )
    assert result.page == 2
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "list",
            {
                "profession_id": profession_id,
                "limit": 3,
                "offset": 4,
            },
        )
    ]
    assert session.commits == 0


def test_profession_specialist_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_specialist_move_all": (
            "list_profession_specialist_ids"
        ),
        "admin_profession_specialists": (
            "list_profession_specialists"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        called_names = set()
        called_attributes = set()

        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue

            if isinstance(item.func, ast.Name):
                called_names.add(item.func.id)

            if isinstance(item.func, ast.Attribute):
                called_attributes.add(
                    item.func.attr
                )

        assert (
            "AdminDictionariesService"
            in called_names
        ), function_name
        assert (
            service_method
            in called_attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in called_names
        ), function_name
        assert (
            "DictionaryRepository"
            not in called_names
        ), function_name
        assert (
            "DictionaryService"
            not in called_names
        ), function_name
        assert "commit" not in called_attributes, (
            function_name
        )


def test_profession_entry_authorization_is_application_service_owned():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function_names = (
        "admin_profession_create_prompt",
        "admin_specialist_move_select_prompt",
    )

    for function_name in function_names:
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        called_names = set()
        called_attributes = set()

        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue

            if isinstance(item.func, ast.Name):
                called_names.add(item.func.id)

            if isinstance(item.func, ast.Attribute):
                called_attributes.add(
                    item.func.attr
                )

        assert (
            "AdminDictionariesService"
            in called_names
        ), function_name
        assert (
            "require_actor"
            in called_attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in called_names
        ), function_name
        assert (
            "DictionaryRepository"
            not in called_names
        ), function_name
        assert (
            "DictionaryService"
            not in called_names
        ), function_name


class FakeCountryDictionaries:
    def __init__(self):
        self.calls = []

    async def list_country_cards(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return [
            "country-1",
            "country-2",
            "country-3",
        ]

    async def get_country_card(
        self,
        **kwargs,
    ):
        self.calls.append(("get", kwargs))
        return "country-card"

    async def create_country(
        self,
        **kwargs,
    ):
        self.calls.append(("create", kwargs))
        return "created-country"


def build_country_service(
    *,
    roles=None,
):
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    session = FakeDictionarySession()
    dictionaries = FakeCountryDictionaries()
    service = AdminDictionariesService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration(
            roles or {"super_admin"}
        ),
        dictionaries=dictionaries,
    )

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_country_list_paginates():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_country_service()

    result = await service.list_countries(
        platform_user_id=123,
        language="uk",
        page=2,
        page_size=2,
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.items == (
        "country-1",
        "country-2",
    )
    assert result.page == 2
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "list",
            {
                "limit": 3,
                "offset": 4,
                "language": "uk",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_get_country_delegates():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_country_service()
    country_id = uuid4()

    result = await service.get_country(
        platform_user_id=123,
        country_id=country_id,
        language="en",
    )

    assert result == "country-card"
    assert dictionaries.calls == [
        (
            "get",
            {
                "country_id": country_id,
                "language": "en",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_create_country_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_country_service()

    result = await service.create_country(
        platform_user_id=123,
        payload="PT | Portugal | Portugal | Portugal",
        language="uk",
    )

    assert result == "created-country"
    assert dictionaries.calls == [
        (
            "create",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "payload": (
                    "PT | Portugal | Portugal | "
                    "Portugal"
                ),
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


def test_country_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_geo_dictionary": (
            "list_countries"
        ),
        "admin_country_create_receive": (
            "create_country"
        ),
        "admin_country_open_receive": (
            "get_country"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        called_names = set()
        called_attributes = set()

        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue

            if isinstance(item.func, ast.Name):
                called_names.add(item.func.id)

            if isinstance(item.func, ast.Attribute):
                called_attributes.add(
                    item.func.attr
                )

        assert (
            "AdminDictionariesService"
            in called_names
        ), function_name
        assert (
            service_method
            in called_attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in called_names
        ), function_name
        assert (
            "DictionaryRepository"
            not in called_names
        ), function_name
        assert (
            "DictionaryService"
            not in called_names
        ), function_name
        assert "commit" not in called_attributes, (
            function_name
        )


class FakeCountryMutationDictionaries(
    FakeCountryDictionaries
):
    async def import_countries(
        self,
        **kwargs,
    ):
        self.calls.append(("import", kwargs))
        return "imported-countries"

    async def update_country(
        self,
        **kwargs,
    ):
        self.calls.append(("update", kwargs))
        return "updated-country"

    async def toggle_country_visibility(
        self,
        **kwargs,
    ):
        self.calls.append(("visibility", kwargs))
        return "visibility-country"


def build_country_mutation_service():
    (
        service,
        session,
        _dictionaries,
        user_id,
        tenant_id,
    ) = build_country_service()
    dictionaries = (
        FakeCountryMutationDictionaries()
    )
    service.dictionaries = dictionaries

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_import_countries_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_country_mutation_service()

    result = await service.import_countries(
        platform_user_id=123,
        payload="code,name_ru,name_en,name_pt",
    )

    assert result == "imported-countries"
    assert dictionaries.calls == [
        (
            "import",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "payload": (
                    "code,name_ru,name_en,name_pt"
                ),
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_update_country_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_country_mutation_service()
    country_id = uuid4()

    result = await service.update_country(
        platform_user_id=123,
        country_id=country_id,
        payload=(
            "Portugal | Portugal | Portugal | "
            "pt | EUR | +351"
        ),
        language="uk",
    )

    assert result == "updated-country"
    assert dictionaries.calls == [
        (
            "update",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "country_id": country_id,
                "payload": (
                    "Portugal | Portugal | Portugal | "
                    "pt | EUR | +351"
                ),
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_toggle_country_visibility_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_country_mutation_service()
    country_id = uuid4()

    result = await (
        service.toggle_country_visibility(
            platform_user_id=123,
            country_id=country_id,
            language="en",
        )
    )

    assert result == "visibility-country"
    assert dictionaries.calls == [
        (
            "visibility",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "country_id": country_id,
                "language": "en",
            },
        )
    ]
    assert session.commits == 1


def test_country_mutation_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_country_import_receive": (
            "import_countries"
        ),
        "admin_country_update_receive": (
            "update_country"
        ),
        "admin_country_toggle_visibility": (
            "toggle_country_visibility"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        called_names = set()
        called_attributes = set()

        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue

            if isinstance(item.func, ast.Name):
                called_names.add(item.func.id)

            if isinstance(item.func, ast.Attribute):
                called_attributes.add(
                    item.func.attr
                )

        assert (
            "AdminDictionariesService"
            in called_names
        ), function_name
        assert (
            service_method
            in called_attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in called_names
        ), function_name
        assert (
            "DictionaryRepository"
            not in called_names
        ), function_name
        assert (
            "DictionaryService"
            not in called_names
        ), function_name
        assert "commit" not in called_attributes, (
            function_name
        )


def test_country_entry_authorization_is_application_service_owned():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function_names = (
        "admin_country_create_prompt",
        "admin_country_import_prompt",
    )

    for function_name in function_names:
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        called_names = set()
        called_attributes = set()

        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue

            if isinstance(item.func, ast.Name):
                called_names.add(item.func.id)

            if isinstance(item.func, ast.Attribute):
                called_attributes.add(
                    item.func.attr
                )

        assert (
            "AdminDictionariesService"
            in called_names
        ), function_name
        assert (
            "require_actor"
            in called_attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in called_names
        ), function_name
        assert (
            "DictionaryRepository"
            not in called_names
        ), function_name
        assert (
            "DictionaryService"
            not in called_names
        ), function_name


class FakeCityDictionaries:
    def __init__(self):
        self.calls = []

    async def list_city_cards(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return [
            "city-1",
            "city-2",
            "city-3",
        ]

    async def get_city_card(
        self,
        **kwargs,
    ):
        self.calls.append(("get", kwargs))
        return "city-card"

    async def create_city(
        self,
        **kwargs,
    ):
        self.calls.append(("create", kwargs))
        return "created-city"


def build_city_service(
    *,
    roles=None,
):
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    session = FakeDictionarySession()
    dictionaries = FakeCityDictionaries()
    service = AdminDictionariesService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration(
            roles or {"super_admin"}
        ),
        dictionaries=dictionaries,
    )

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_city_list_paginates():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_city_service()
    country_id = uuid4()

    result = await service.list_cities(
        platform_user_id=123,
        country_id=country_id,
        language="uk",
        page=2,
        page_size=2,
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.items == (
        "city-1",
        "city-2",
    )
    assert result.page == 2
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "list",
            {
                "country_id": country_id,
                "limit": 3,
                "offset": 4,
                "language": "uk",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_get_city_delegates():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_city_service()
    city_id = uuid4()

    result = await service.get_city(
        platform_user_id=123,
        city_id=city_id,
        language="en",
    )

    assert result == "city-card"
    assert dictionaries.calls == [
        (
            "get",
            {
                "city_id": city_id,
                "language": "en",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_create_city_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_city_service()
    country_id = uuid4()

    result = await service.create_city(
        platform_user_id=123,
        country_id=country_id,
        payload=(
            "Lisbon | Lisbon | Lisboa | "
            "Europe/Lisbon"
        ),
        language="uk",
    )

    assert result == "created-city"
    assert dictionaries.calls == [
        (
            "create",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "country_id": country_id,
                "payload": (
                    "Lisbon | Lisbon | Lisboa | "
                    "Europe/Lisbon"
                ),
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


def test_city_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_country_cities": "list_cities",
        "admin_city_create_receive": (
            "create_city"
        ),
        "admin_city_open_receive": "get_city",
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        called_names = set()
        called_attributes = set()

        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue

            if isinstance(item.func, ast.Name):
                called_names.add(item.func.id)

            if isinstance(item.func, ast.Attribute):
                called_attributes.add(
                    item.func.attr
                )

        assert (
            "AdminDictionariesService"
            in called_names
        ), function_name
        assert (
            service_method
            in called_attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in called_names
        ), function_name
        assert (
            "DictionaryRepository"
            not in called_names
        ), function_name
        assert (
            "DictionaryService"
            not in called_names
        ), function_name
        assert "commit" not in called_attributes, (
            function_name
        )


class FakeCityMutationDictionaries(
    FakeCityDictionaries
):
    async def import_cities(
        self,
        **kwargs,
    ):
        self.calls.append(("import", kwargs))
        return "imported-cities"

    async def update_city(
        self,
        **kwargs,
    ):
        self.calls.append(("update", kwargs))
        return "updated-city"

    async def update_city_geo(
        self,
        **kwargs,
    ):
        self.calls.append(("geo", kwargs))
        return "geo-city"

    async def toggle_city_visibility(
        self,
        **kwargs,
    ):
        self.calls.append(("visibility", kwargs))
        return "visibility-city"


def build_city_mutation_service():
    (
        service,
        session,
        _dictionaries,
        user_id,
        tenant_id,
    ) = build_city_service()
    dictionaries = FakeCityMutationDictionaries()
    service.dictionaries = dictionaries

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_import_cities_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_city_mutation_service()
    country_id = uuid4()

    result = await service.import_cities(
        platform_user_id=123,
        country_id=country_id,
        payload=(
            "name_ru,name_en,name_pt,timezone,"
            "latitude,longitude"
        ),
    )

    assert result == "imported-cities"
    assert dictionaries.calls == [
        (
            "import",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "country_id": country_id,
                "payload": (
                    "name_ru,name_en,name_pt,"
                    "timezone,latitude,longitude"
                ),
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_update_city_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_city_mutation_service()
    city_id = uuid4()

    result = await service.update_city(
        platform_user_id=123,
        city_id=city_id,
        payload=(
            "Lisbon | Lisbon | Lisboa | "
            "Europe/Lisbon"
        ),
        language="uk",
    )

    assert result == "updated-city"
    assert dictionaries.calls == [
        (
            "update",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "city_id": city_id,
                "payload": (
                    "Lisbon | Lisbon | Lisboa | "
                    "Europe/Lisbon"
                ),
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_update_city_geo_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_city_mutation_service()
    city_id = uuid4()

    result = await service.update_city_geo(
        platform_user_id=123,
        city_id=city_id,
        payload="38.7223 | -9.1393",
        language="en",
    )

    assert result == "geo-city"
    assert dictionaries.calls == [
        (
            "geo",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "city_id": city_id,
                "payload": "38.7223 | -9.1393",
                "language": "en",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_toggle_city_visibility_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_city_mutation_service()
    city_id = uuid4()

    result = await (
        service.toggle_city_visibility(
            platform_user_id=123,
            city_id=city_id,
            language="uk",
        )
    )

    assert result == "visibility-city"
    assert dictionaries.calls == [
        (
            "visibility",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "city_id": city_id,
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


def test_city_mutation_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_city_import_receive": (
            "import_cities"
        ),
        "admin_city_update_receive": (
            "update_city"
        ),
        "admin_city_geo_update_receive": (
            "update_city_geo"
        ),
        "admin_city_toggle_visibility": (
            "toggle_city_visibility"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        names = {
            item.func.id
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "AdminDictionariesService" in names
        ), function_name
        assert (
            service_method in attributes
        ), function_name
        assert (
            "get_admin_user_context" not in names
        ), function_name
        assert (
            "DictionaryRepository" not in names
        ), function_name
        assert (
            "DictionaryService" not in names
        ), function_name
        assert "commit" not in attributes, (
            function_name
        )


def test_city_entry_authorization_is_application_service_owned():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for function_name in (
        "admin_city_create_prompt",
        "admin_city_import_prompt",
    ):
        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        names = {
            item.func.id
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "AdminDictionariesService" in names
        ), function_name
        assert (
            "require_actor" in attributes
        ), function_name
        assert (
            "get_admin_user_context" not in names
        ), function_name
        assert (
            "DictionaryRepository" not in names
        ), function_name
        assert (
            "DictionaryService" not in names
        ), function_name


def test_geo_dictionary_handlers_have_no_direct_business_dependencies():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = [
        item
        for item in ast.walk(tree)
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and (
            item.name == "admin_geo_dictionary"
            or item.name.startswith(
                "admin_country_"
            )
            or item.name.startswith(
                "admin_city_"
            )
        )
    ]

    assert functions

    for function in functions:
        called_names = {
            item.func.id
            for item in ast.walk(function)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        called_attributes = {
            item.func.attr
            for item in ast.walk(function)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "get_admin_user_context"
            not in called_names
        ), function.name
        assert (
            "DictionaryRepository"
            not in called_names
        ), function.name
        assert (
            "DictionaryService"
            not in called_names
        ), function.name
        assert (
            "commit"
            not in called_attributes
        ), function.name


class FakeLanguageDictionaries:
    def __init__(self):
        self.calls = []

    async def list_language_cards(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return [
            "language-1",
            "language-2",
            "language-3",
        ]

    async def get_language_card(
        self,
        **kwargs,
    ):
        self.calls.append(("get", kwargs))
        return "language-card"

    async def create_language(
        self,
        **kwargs,
    ):
        self.calls.append(("create", kwargs))
        return "created-language"


def build_language_service(
    *,
    roles=None,
):
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    session = FakeDictionarySession()
    dictionaries = FakeLanguageDictionaries()
    service = AdminDictionariesService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration(
            roles or {"super_admin"}
        ),
        dictionaries=dictionaries,
    )

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_language_list_paginates():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_language_service()

    result = await service.list_languages(
        platform_user_id=123,
        page=2,
        page_size=2,
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.items == (
        "language-1",
        "language-2",
    )
    assert result.page == 2
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "list",
            {
                "limit": 3,
                "offset": 4,
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_get_language_delegates():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_language_service()

    result = await service.get_language(
        platform_user_id=123,
        code="uk",
    )

    assert result == "language-card"
    assert dictionaries.calls == [
        (
            "get",
            {
                "code": "uk",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_create_language_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_language_service()

    result = await service.create_language(
        platform_user_id=123,
        payload="uk | Ukrainian | Українська",
    )

    assert result == "created-language"
    assert dictionaries.calls == [
        (
            "create",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "payload": (
                    "uk | Ukrainian | Українська"
                ),
            },
        )
    ]
    assert session.commits == 1


def test_language_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_languages_dictionary": (
            "list_languages"
        ),
        "admin_language_create_receive": (
            "create_language"
        ),
        "admin_language_open_receive": (
            "get_language"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        names = {
            item.func.id
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "AdminDictionariesService" in names
        ), function_name
        assert (
            service_method in attributes
        ), function_name
        assert (
            "get_admin_user_context" not in names
        ), function_name
        assert (
            "DictionaryRepository" not in names
        ), function_name
        assert (
            "DictionaryService" not in names
        ), function_name
        assert "commit" not in attributes, (
            function_name
        )


class FakeLanguageMutationDictionaries(
    FakeLanguageDictionaries
):
    async def rename_language(
        self,
        **kwargs,
    ):
        self.calls.append(("rename", kwargs))
        return "renamed-language"

    async def toggle_language_visibility(
        self,
        **kwargs,
    ):
        self.calls.append(("visibility", kwargs))
        return "visibility-language"


def build_language_mutation_service():
    (
        service,
        session,
        _dictionaries,
        user_id,
        tenant_id,
    ) = build_language_service()
    dictionaries = (
        FakeLanguageMutationDictionaries()
    )
    service.dictionaries = dictionaries

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_rename_language_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_language_mutation_service()

    result = await service.rename_language(
        platform_user_id=123,
        code="uk",
        payload="Ukrainian | Українська",
    )

    assert result == "renamed-language"
    assert dictionaries.calls == [
        (
            "rename",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "code": "uk",
                "payload": (
                    "Ukrainian | Українська"
                ),
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_toggle_language_visibility_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_language_mutation_service()

    result = await (
        service.toggle_language_visibility(
            platform_user_id=123,
            code="uk",
        )
    )

    assert result == "visibility-language"
    assert dictionaries.calls == [
        (
            "visibility",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "code": "uk",
            },
        )
    ]
    assert session.commits == 1


def test_language_mutation_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_language_rename_receive": (
            "rename_language"
        ),
        "admin_language_toggle_visibility": (
            "toggle_language_visibility"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        names = {
            item.func.id
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "AdminDictionariesService" in names
        ), function_name
        assert (
            service_method in attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in names
        ), function_name
        assert (
            "DictionaryRepository"
            not in names
        ), function_name
        assert (
            "DictionaryService"
            not in names
        ), function_name
        assert "commit" not in attributes, (
            function_name
        )


def test_language_dictionary_handlers_have_no_direct_business_dependencies():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = [
        item
        for item in ast.walk(tree)
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and (
            item.name == (
                "admin_languages_dictionary"
            )
            or item.name.startswith(
                "admin_language_"
            )
        )
    ]

    assert functions

    for function in functions:
        names = {
            item.func.id
            for item in ast.walk(function)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(function)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "get_admin_user_context"
            not in names
        ), function.name
        assert (
            "DictionaryRepository"
            not in names
        ), function.name
        assert (
            "DictionaryService"
            not in names
        ), function.name
        assert "commit" not in attributes, (
            function.name
        )

    create_prompt = next(
        item
        for item in functions
        if item.name == (
            "admin_language_create_prompt"
        )
    )
    create_names = {
        item.func.id
        for item in ast.walk(create_prompt)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
    }
    create_attributes = {
        item.func.attr
        for item in ast.walk(create_prompt)
        if isinstance(item, ast.Call)
        and isinstance(
            item.func,
            ast.Attribute,
        )
    }

    assert (
        "AdminDictionariesService"
        in create_names
    )
    assert "require_actor" in create_attributes


class FakeSkillDictionaries:
    def __init__(self):
        self.calls = []

    async def list_skill_cards(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return [
            "skill-1",
            "skill-2",
            "skill-3",
        ]

    async def get_skill_card(
        self,
        **kwargs,
    ):
        self.calls.append(("get", kwargs))
        return "skill-card"

    async def create_skill(
        self,
        **kwargs,
    ):
        self.calls.append(("create", kwargs))
        return "created-skill"


def build_skill_service(
    *,
    roles=None,
):
    tenant_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    session = FakeDictionarySession()
    dictionaries = FakeSkillDictionaries()
    service = AdminDictionariesService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration(
            roles or {"super_admin"}
        ),
        dictionaries=dictionaries,
    )

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_skill_list_paginates():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_skill_service()

    result = await service.list_skills(
        platform_user_id=123,
        language="uk",
        page=2,
        page_size=2,
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.items == (
        "skill-1",
        "skill-2",
    )
    assert result.page == 2
    assert result.has_next is True
    assert dictionaries.calls == [
        (
            "list",
            {
                "language": "uk",
                "limit": 3,
                "offset": 4,
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_get_skill_delegates():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_skill_service()
    skill_id = uuid4()

    result = await service.get_skill(
        platform_user_id=123,
        skill_id=skill_id,
        language="en",
    )

    assert result == "skill-card"
    assert dictionaries.calls == [
        (
            "get",
            {
                "skill_id": skill_id,
                "language": "en",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_create_skill_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_skill_service()

    result = await service.create_skill(
        platform_user_id=123,
        title="Python",
        language="uk",
    )

    assert result == "created-skill"
    assert dictionaries.calls == [
        (
            "create",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "title": "Python",
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


def test_skill_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_skills_dictionary": "list_skills",
        "admin_skill_create_receive": (
            "create_skill"
        ),
        "admin_skill_open_receive": "get_skill",
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        names = {
            item.func.id
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "AdminDictionariesService" in names
        ), function_name
        assert (
            service_method in attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in names
        ), function_name
        assert (
            "DictionaryRepository"
            not in names
        ), function_name
        assert (
            "DictionaryService"
            not in names
        ), function_name
        assert "commit" not in attributes, (
            function_name
        )


class FakeSkillMutationDictionaries(
    FakeSkillDictionaries
):
    async def rename_skill(
        self,
        **kwargs,
    ):
        self.calls.append(("rename", kwargs))
        return "renamed-skill"

    async def toggle_skill_visibility(
        self,
        **kwargs,
    ):
        self.calls.append(("visibility", kwargs))
        return "visibility-skill"


def build_skill_mutation_service():
    (
        service,
        session,
        _dictionaries,
        user_id,
        tenant_id,
    ) = build_skill_service()
    dictionaries = FakeSkillMutationDictionaries()
    service.dictionaries = dictionaries

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_rename_skill_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_skill_mutation_service()
    skill_id = uuid4()

    result = await service.rename_skill(
        platform_user_id=123,
        skill_id=skill_id,
        title="Python development",
        language="uk",
    )

    assert result == "renamed-skill"
    assert dictionaries.calls == [
        (
            "rename",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "skill_id": skill_id,
                "title": "Python development",
                "language": "uk",
            },
        )
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_toggle_skill_visibility_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_skill_mutation_service()
    skill_id = uuid4()

    result = await (
        service.toggle_skill_visibility(
            platform_user_id=123,
            skill_id=skill_id,
            language="en",
        )
    )

    assert result == "visibility-skill"
    assert dictionaries.calls == [
        (
            "visibility",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "skill_id": skill_id,
                "language": "en",
            },
        )
    ]
    assert session.commits == 1


def test_skill_mutation_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_skill_rename_receive": (
            "rename_skill"
        ),
        "admin_skill_toggle_visibility": (
            "toggle_skill_visibility"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        names = {
            item.func.id
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "AdminDictionariesService" in names
        ), function_name
        assert (
            service_method in attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in names
        ), function_name
        assert (
            "DictionaryRepository"
            not in names
        ), function_name
        assert (
            "DictionaryService"
            not in names
        ), function_name
        assert "commit" not in attributes, (
            function_name
        )


class FakeSkillMergeDictionaries(
    FakeSkillDictionaries
):
    async def preview_skill_merge(
        self,
        **kwargs,
    ):
        self.calls.append(("preview", kwargs))
        return "skill-merge-preview"

    async def merge_skills(
        self,
        **kwargs,
    ):
        self.calls.append(("merge", kwargs))
        return "skill-merge-result"


def build_skill_merge_service():
    (
        service,
        session,
        _dictionaries,
        user_id,
        tenant_id,
    ) = build_skill_service()
    dictionaries = FakeSkillMergeDictionaries()
    service.dictionaries = dictionaries

    return (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    )


@pytest.mark.asyncio
async def test_preview_skill_merge_is_read_only():
    (
        service,
        session,
        dictionaries,
        _user_id,
        _tenant_id,
    ) = build_skill_merge_service()
    source_skill_id = uuid4()

    result = await service.preview_skill_merge(
        platform_user_id=123,
        source_skill_id=source_skill_id,
        target_skill_value="python-3",
        language="uk",
    )

    assert result == "skill-merge-preview"
    assert dictionaries.calls == [
        (
            "preview",
            {
                "source_skill_id": (
                    source_skill_id
                ),
                "target_skill_value": "python-3",
                "language": "uk",
            },
        )
    ]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_merge_skills_owns_transaction():
    (
        service,
        session,
        dictionaries,
        user_id,
        tenant_id,
    ) = build_skill_merge_service()
    source_skill_id = uuid4()

    result = await service.merge_skills(
        platform_user_id=123,
        source_skill_id=source_skill_id,
        target_skill_value="python-3",
        language="en",
    )

    assert result == "skill-merge-result"
    assert dictionaries.calls == [
        (
            "merge",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "source_skill_id": (
                    source_skill_id
                ),
                "target_skill_value": "python-3",
                "language": "en",
            },
        )
    ]
    assert session.commits == 1


def test_skill_merge_transaction_is_application_owned():
    import ast
    from pathlib import Path

    domain_source = Path(
        "services/dictionaries.py"
    ).read_text(encoding="utf-8")
    domain_tree = ast.parse(domain_source)
    domain_function = next(
        item
        for item in ast.walk(domain_tree)
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name == "merge_skills"
    )
    domain_attributes = {
        item.func.attr
        for item in ast.walk(domain_function)
        if isinstance(item, ast.Call)
        and isinstance(
            item.func,
            ast.Attribute,
        )
    }

    assert "commit" not in domain_attributes

    app_source = Path(
        "services/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    app_tree = ast.parse(app_source)
    app_function = next(
        item
        for item in ast.walk(app_tree)
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name == "merge_skills"
    )
    app_attributes = {
        item.func.attr
        for item in ast.walk(app_function)
        if isinstance(item, ast.Call)
        and isinstance(
            item.func,
            ast.Attribute,
        )
    }

    assert "commit" in app_attributes


def test_skill_merge_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "admin_skill_merge_receive": (
            "preview_skill_merge"
        ),
        "admin_skill_merge_confirm": (
            "merge_skills"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        names = {
            item.func.id
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "AdminDictionariesService" in names
        ), function_name
        assert (
            service_method in attributes
        ), function_name
        assert (
            "get_admin_user_context"
            not in names
        ), function_name
        assert (
            "DictionaryRepository"
            not in names
        ), function_name
        assert (
            "DictionaryService"
            not in names
        ), function_name
        assert "commit" not in attributes, (
            function_name
        )


def test_skill_dictionary_handlers_have_no_direct_business_dependencies():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = [
        item
        for item in ast.walk(tree)
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and (
            item.name == "admin_skills_dictionary"
            or item.name.startswith(
                "admin_skill_"
            )
        )
    ]

    assert functions

    for function in functions:
        names = {
            item.func.id
            for item in ast.walk(function)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(function)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "get_admin_user_context"
            not in names
        ), function.name
        assert (
            "DictionaryRepository"
            not in names
        ), function.name
        assert (
            "DictionaryService"
            not in names
        ), function.name
        assert "commit" not in attributes, (
            function.name
        )

    create_prompt = next(
        item
        for item in functions
        if item.name == "admin_skill_create_prompt"
    )
    create_names = {
        item.func.id
        for item in ast.walk(create_prompt)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
    }
    create_attributes = {
        item.func.attr
        for item in ast.walk(create_prompt)
        if isinstance(item, ast.Call)
        and isinstance(
            item.func,
            ast.Attribute,
        )
    }

    assert (
        "AdminDictionariesService"
        in create_names
    )
    assert "require_actor" in create_attributes


def test_all_dictionary_handlers_have_no_direct_business_dependencies():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dictionaries.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    prefixes = (
        "admin_category_",
        "admin_categories_",
        "admin_profession_",
        "admin_professions_",
        "admin_specialist_move_",
        "admin_multi_move_",
        "admin_country_",
        "admin_city_",
        "admin_language_",
        "admin_languages_",
        "admin_skill_",
        "admin_skills_",
    )
    exact_names = {
        "admin_dictionaries_menu",
        "admin_geo_dictionary",
        "show_admin_multi_move_categories",
        "show_admin_multi_move_professions",
        "clear_admin_multi_move_state",
    }

    functions = [
        item
        for item in ast.walk(tree)
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and (
            item.name in exact_names
            or item.name.startswith(prefixes)
        )
    ]

    assert len(functions) >= 50

    for function in functions:
        names = {
            item.func.id
            for item in ast.walk(function)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
        }
        attributes = {
            item.func.attr
            for item in ast.walk(function)
            if isinstance(item, ast.Call)
            and isinstance(
                item.func,
                ast.Attribute,
            )
        }

        assert (
            "get_admin_user_context"
            not in names
        ), function.name
        assert (
            "DictionaryRepository"
            not in names
        ), function.name
        assert (
            "DictionaryService"
            not in names
        ), function.name
        assert "commit" not in attributes, (
            function.name
        )
