import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from database.construction_session import (
    construction_transaction,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv(
            "RUN_CONSTRUCTION_SMOKE",
            "",
        ) != "1",
        reason=(
            "Set RUN_CONSTRUCTION_SMOKE=1 "
            "to run real Supabase smoke tests."
        ),
    ),
]


async def test_con_005_supabase_schema_and_rls_smoke():
    tenant_id = uuid4()

    async with construction_transaction(
        tenant_id=tenant_id,
    ) as session:
        identity = (
            await session.execute(
                text(
                    """
                    SELECT
                        current_user AS role_name,
                        current_setting(
                            'app.current_tenant_id',
                            true
                        ) AS tenant_context
                    """
                )
            )
        ).mappings().one()

        assert identity["role_name"] == (
            "construction_api"
        )
        assert identity["tenant_context"] == str(
            tenant_id
        )

        table_state = (
            await session.execute(
                text(
                    """
                    SELECT
                        c.relrowsecurity AS rls_enabled,
                        c.relforcerowsecurity AS rls_forced
                    FROM pg_class AS c
                    JOIN pg_namespace AS n
                      ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relname = (
                          'construction_project_areas'
                      )
                    """
                )
            )
        ).mappings().one()

        assert table_state["rls_enabled"] is True

        policies = (
            await session.execute(
                text(
                    """
                    SELECT policyname
                    FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = (
                          'construction_project_areas'
                      )
                    """
                )
            )
        ).scalars().all()

        assert policies == [
            "construction_tenant_isolation"
        ]

        sort_order = (
            await session.execute(
                text(
                    """
                    SELECT
                        is_nullable,
                        column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = (
                          'construction_project_areas'
                      )
                      AND column_name = 'sort_order'
                    """
                )
            )
        ).mappings().one()

        assert sort_order["is_nullable"] == "YES"
        assert sort_order["column_default"] is None

        client_grants = (
            await session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.table_privileges
                    WHERE table_schema = 'public'
                      AND table_name = (
                          'construction_project_areas'
                      )
                      AND grantee IN (
                          'anon',
                          'authenticated'
                      )
                    """
                )
            )
        ).scalar_one()

        assert client_grants == 0


async def test_con_005_real_area_lifecycle_and_cross_tenant_rls_smoke():
    from datetime import UTC, datetime

    from sqlalchemy import text

    from database.construction import (
        set_construction_tenant_context,
    )
    from database.construction_session import (
        get_construction_session_factory,
        verify_construction_database_identity,
    )
    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
        ConstructionProjectRepository,
    )
    from database.session import async_session

    async with async_session() as setup_session:
        fixture = (
            await setup_session.execute(
                text(
                    """
                    SELECT
                        t.id AS tenant_id,
                        u.id AS user_id
                    FROM tenants AS t
                    JOIN users AS u
                      ON u.tenant_id = t.id
                    WHERE t.status = 'active'
                      AND u.status = 'active'
                    ORDER BY t.created_at, u.created_at
                    LIMIT 1
                    """
                )
            )
        ).mappings().one_or_none()

    assert fixture is not None, (
        "An active platform tenant/user fixture "
        "is required for Construction smoke."
    )

    tenant_id = fixture["tenant_id"]
    user_id = fixture["user_id"]
    foreign_tenant_id = uuid4()
    project_id = None
    area_id = None

    factory = get_construction_session_factory()

    async with factory() as session:
        transaction = await session.begin()

        try:
            role_name = (
                await verify_construction_database_identity(
                    session
                )
            )
            assert role_name == "construction_api"

            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )

            project_repository = (
                ConstructionProjectRepository(
                    session
                )
            )
            area_repository = (
                ConstructionProjectAreaRepository(
                    session
                )
            )

            project = (
                await project_repository.create_project(
                    tenant_id=tenant_id,
                    client_id=None,
                    name="CON-005 smoke project",
                    responsible_user_id=None,
                    comment=None,
                    address_raw="Smoke address",
                    created_by=user_id,
                )
            )
            project_id = project.id

            area = await area_repository.create_area(
                tenant_id=tenant_id,
                project_id=project_id,
                area_type="ROOM",
                name="Smoke room",
                sort_order=None,
                created_by=user_id,
            )
            area_id = area.id

            loaded = (
                await area_repository.get_active_area(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )
            assert loaded is not None
            assert loaded.id == area_id

            await set_construction_tenant_context(
                session,
                tenant_id=foreign_tenant_id,
            )

            hidden = (
                await area_repository.get_active_area(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )
            assert hidden is None

            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )

            locked = (
                await area_repository
                .get_active_area_for_update(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )
            assert locked is not None

            await area_repository.rename_area(
                area=locked,
                name="Renamed smoke room",
            )
            assert locked.name == (
                "Renamed smoke room"
            )

            await area_repository.set_area_status(
                area=locked,
                status="measured",
            )
            await area_repository.set_area_status(
                area=locked,
                status="confirmed",
            )
            assert locked.status == "confirmed"

            await area_repository.set_area_status(
                area=locked,
                status="draft",
            )
            assert locked.status == "draft"

            deleted_at = datetime.now(UTC)
            await area_repository.soft_delete_area(
                area=locked,
                deleted_at=deleted_at,
            )

            archived = (
                await area_repository.get_active_area(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )
            assert archived is None
        finally:
            if transaction.is_active:
                await transaction.rollback()

    assert project_id is not None
    assert area_id is not None

    async with async_session() as verify_session:
        remaining = (
            await verify_session.execute(
                text(
                    """
                    SELECT
                        (
                            SELECT count(*)
                            FROM construction_projects
                            WHERE id = :project_id
                        )
                        +
                        (
                            SELECT count(*)
                            FROM construction_project_areas
                            WHERE id = :area_id
                        )
                    """
                ),
                {
                    "project_id": project_id,
                    "area_id": area_id,
                },
            )
        ).scalar_one()

    assert remaining == 0


async def test_con_005_real_sort_order_contract_smoke():
    from sqlalchemy import text

    from database.construction import (
        set_construction_tenant_context,
    )
    from database.construction_session import (
        get_construction_session_factory,
        verify_construction_database_identity,
    )
    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
        ConstructionProjectRepository,
    )
    from database.session import async_session

    async with async_session() as setup_session:
        fixture = (
            await setup_session.execute(
                text(
                    """
                    SELECT
                        t.id AS tenant_id,
                        u.id AS user_id
                    FROM tenants AS t
                    JOIN users AS u
                      ON u.tenant_id = t.id
                    WHERE t.status = 'active'
                      AND u.status = 'active'
                    ORDER BY t.created_at, u.created_at
                    LIMIT 1
                    """
                )
            )
        ).mappings().one_or_none()

    assert fixture is not None

    tenant_id = fixture["tenant_id"]
    user_id = fixture["user_id"]
    factory = get_construction_session_factory()

    async with factory() as session:
        transaction = await session.begin()

        try:
            assert (
                await verify_construction_database_identity(
                    session
                )
                == "construction_api"
            )

            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )

            project_repository = (
                ConstructionProjectRepository(
                    session
                )
            )
            area_repository = (
                ConstructionProjectAreaRepository(
                    session
                )
            )

            project = (
                await project_repository.create_project(
                    tenant_id=tenant_id,
                    client_id=None,
                    name="CON-005 order smoke",
                    responsible_user_id=None,
                    comment=None,
                    address_raw="Smoke address",
                    created_by=user_id,
                )
            )

            sort_orders = (
                2,
                None,
                1,
                2,
                None,
            )
            created = []

            for index, sort_order in enumerate(
                sort_orders,
                start=1,
            ):
                area = await area_repository.create_area(
                    tenant_id=tenant_id,
                    project_id=project.id,
                    area_type="ROOM",
                    name=f"Smoke area {index}",
                    sort_order=sort_order,
                    created_by=user_id,
                )
                created.append(area)

            assert [
                area.sort_order
                for area in created
            ] == list(sort_orders)

            listed = (
                await area_repository.list_active_areas(
                    tenant_id=tenant_id,
                    project_id=project.id,
                )
            )

            expected = sorted(
                created,
                key=lambda area: (
                    area.sort_order is None,
                    (
                        area.sort_order
                        if area.sort_order is not None
                        else 0
                    ),
                    area.created_at,
                    area.id,
                ),
            )

            assert [
                area.id
                for area in listed
            ] == [
                area.id
                for area in expected
            ]

            assert [
                area.sort_order
                for area in listed
            ] == [
                1,
                2,
                2,
                None,
                None,
            ]
        finally:
            if transaction.is_active:
                await transaction.rollback()




async def test_con_006_real_element_lifecycle_parent_scope_and_rls_smoke():
    from datetime import UTC, datetime

    from database.construction import (
        set_construction_tenant_context,
    )
    from database.construction_session import (
        get_construction_session_factory,
        verify_construction_database_identity,
    )
    from database.repositories.construction import (
        ConstructionAreaElementRepository,
        ConstructionProjectAreaRepository,
        ConstructionProjectRepository,
    )
    from database.session import async_session

    async with async_session() as setup_session:
        fixture = (
            await setup_session.execute(
                text(
                    """
                    SELECT
                        t.id AS tenant_id,
                        u.id AS user_id
                    FROM tenants AS t
                    JOIN users AS u
                      ON u.tenant_id = t.id
                    WHERE t.status = 'active'
                      AND u.status = 'active'
                    ORDER BY
                        t.created_at,
                        u.created_at
                    LIMIT 1
                    """
                )
            )
        ).mappings().one_or_none()

    assert fixture is not None, (
        "An active platform tenant/user fixture "
        "is required for Construction smoke."
    )

    tenant_id = fixture["tenant_id"]
    user_id = fixture["user_id"]
    foreign_tenant_id = uuid4()

    project_id = None
    area_id = None
    wall_id = None
    window_id = None

    factory = get_construction_session_factory()

    async with factory() as session:
        transaction = await session.begin()

        try:
            assert (
                await verify_construction_database_identity(
                    session
                )
                == "construction_api"
            )

            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )

            table_state = (
                await session.execute(
                    text(
                        """
                        SELECT relrowsecurity
                        FROM pg_class
                        WHERE oid = (
                            'public.'
                            'construction_area_elements'
                        )::regclass
                        """
                    )
                )
            ).scalar_one()

            assert table_state is True

            policies = (
                await session.execute(
                    text(
                        """
                        SELECT policyname
                        FROM pg_policies
                        WHERE schemaname = 'public'
                          AND tablename = (
                              'construction_area_elements'
                          )
                        ORDER BY policyname
                        """
                    )
                )
            ).scalars().all()

            assert policies == [
                "construction_tenant_isolation"
            ]

            project_repository = (
                ConstructionProjectRepository(
                    session
                )
            )
            area_repository = (
                ConstructionProjectAreaRepository(
                    session
                )
            )
            element_repository = (
                ConstructionAreaElementRepository(
                    session
                )
            )

            project = (
                await project_repository.create_project(
                    tenant_id=tenant_id,
                    client_id=None,
                    name="CON-006 smoke project",
                    responsible_user_id=None,
                    comment=None,
                    address_raw="Smoke address",
                    created_by=user_id,
                )
            )
            project_id = project.id

            area = await area_repository.create_area(
                tenant_id=tenant_id,
                project_id=project_id,
                area_type="ROOM",
                name="CON-006 smoke room",
                sort_order=None,
                created_by=user_id,
            )
            area_id = area.id

            wall_geometry = {
                "schema_version": 1,
                "length": "6.00",
                "height": "2.70",
                "future_data": {
                    "preserved": True,
                },
            }
            wall = (
                await element_repository
                .create_element(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                    parent_element_id=None,
                    element_type="WALL",
                    name="Wall A",
                    sort_order=1,
                    geometry_json=wall_geometry,
                    created_by=user_id,
                )
            )
            wall_id = wall.id

            window_geometry = {
                "schema_version": 1,
                "width": "1.50",
                "height": "1.20",
            }
            window = (
                await element_repository
                .create_element(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                    parent_element_id=wall_id,
                    element_type="WINDOW",
                    name="Window A",
                    sort_order=None,
                    geometry_json=window_geometry,
                    created_by=user_id,
                )
            )
            window_id = window.id

            loaded_parent = (
                await element_repository
                .get_active_parent_wall(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                    parent_element_id=wall_id,
                )
            )

            assert loaded_parent is not None
            assert loaded_parent.id == wall_id
            assert (
                loaded_parent.geometry_json
                == wall_geometry
            )

            listed = (
                await element_repository
                .list_active_elements(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                )
            )

            assert [
                item.id
                for item in listed
            ] == [
                wall_id,
                window_id,
            ]
            assert [
                item.sort_order
                for item in listed
            ] == [
                1,
                None,
            ]

            assert (
                await element_repository
                .has_active_wall_children(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                    wall_element_id=wall_id,
                )
                is True
            )

            await set_construction_tenant_context(
                session,
                tenant_id=foreign_tenant_id,
            )

            hidden = (
                await element_repository
                .get_active_element(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                    element_id=wall_id,
                )
            )
            assert hidden is None

            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )

            locked_window = (
                await element_repository
                .get_active_element_for_update(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                    element_id=window_id,
                )
            )
            assert locked_window is not None

            await element_repository.soft_delete_element(
                element=locked_window,
                deleted_at=datetime.now(UTC),
            )

            assert (
                await element_repository
                .has_active_wall_children(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                    wall_element_id=wall_id,
                )
                is False
            )

            locked_wall = (
                await element_repository
                .get_active_element_for_update(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                    element_id=wall_id,
                )
            )
            assert locked_wall is not None

            await element_repository.soft_delete_element(
                element=locked_wall,
                deleted_at=datetime.now(UTC),
            )

            assert (
                await element_repository
                .get_active_element(
                    tenant_id=tenant_id,
                    project_area_id=area_id,
                    element_id=wall_id,
                )
                is None
            )
        finally:
            if transaction.is_active:
                await transaction.rollback()

    assert project_id is not None
    assert area_id is not None
    assert wall_id is not None
    assert window_id is not None

    async with async_session() as verify_session:
        remaining = (
            await verify_session.execute(
                text(
                    """
                    SELECT
                        (
                            SELECT count(*)
                            FROM construction_projects
                            WHERE id = :project_id
                        )
                        +
                        (
                            SELECT count(*)
                            FROM construction_project_areas
                            WHERE id = :area_id
                        )
                        +
                        (
                            SELECT count(*)
                            FROM construction_area_elements
                            WHERE id IN (
                                :wall_id,
                                :window_id
                            )
                        )
                    """
                ),
                {
                    "project_id": project_id,
                    "area_id": area_id,
                    "wall_id": wall_id,
                    "window_id": window_id,
                },
            )
        ).scalar_one()

    assert remaining == 0


async def test_con_007_real_event_log_insert_and_cross_tenant_rls_smoke():
    from sqlalchemy.exc import DBAPIError

    from database.construction import (
        set_construction_tenant_context,
    )
    from database.construction_session import (
        get_construction_session_factory,
        verify_construction_database_identity,
    )
    from database.repositories.event import (
        EventRepository,
    )
    from database.session import async_session

    async with async_session() as setup_session:
        fixture = (
            await setup_session.execute(
                text(
                    """
                    SELECT
                        t.id AS tenant_id,
                        u.id AS user_id
                    FROM tenants AS t
                    JOIN users AS u
                      ON u.tenant_id = t.id
                    WHERE t.status = 'active'
                      AND u.status = 'active'
                    ORDER BY
                        t.created_at,
                        u.created_at
                    LIMIT 1
                    """
                )
            )
        ).mappings().one_or_none()

    assert fixture is not None, (
        "An active platform tenant/user fixture "
        "is required for Construction smoke."
    )

    tenant_id = fixture["tenant_id"]
    user_id = fixture["user_id"]
    foreign_tenant_id = uuid4()
    entity_id = uuid4()
    allowed_trace_id = (
        f"con007-allowed-{uuid4().hex}"
    )
    denied_trace_id = (
        f"con007-denied-{uuid4().hex}"
    )

    factory = get_construction_session_factory()

    async with factory() as session:
        transaction = await session.begin()

        try:
            assert (
                await verify_construction_database_identity(
                    session
                )
                == "construction_api"
            )

            grants = (
                await session.execute(
                    text(
                        """
                        SELECT privilege_type
                        FROM information_schema
                             .table_privileges
                        WHERE table_schema = 'public'
                          AND table_name = 'event_logs'
                          AND grantee = (
                              'construction_api'
                          )
                        ORDER BY privilege_type
                        """
                    )
                )
            ).scalars().all()

            assert grants == ["INSERT"]

            policy = (
                await session.execute(
                    text(
                        """
                        SELECT
                            cmd,
                            roles,
                            with_check
                        FROM pg_policies
                        WHERE schemaname = 'public'
                          AND tablename = 'event_logs'
                          AND policyname = (
                              'event_logs_'
                              'construction_insert'
                          )
                        """
                    )
                )
            ).mappings().one()

            assert policy["cmd"] == "INSERT"
            assert (
                "construction_api"
                in policy["roles"]
            )
            assert (
                "app.current_tenant_id"
                in policy["with_check"]
            )

            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )

            event_repository = EventRepository(
                session
            )
            event = await event_repository.create_event(
                event_type=(
                    "construction_client_created"
                ),
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type=(
                    "construction_client"
                ),
                entity_id=entity_id,
                payload={
                    "operation": "create",
                    "changes": [],
                    "reason": None,
                },
                platform="api",
                trace_id=allowed_trace_id,
            )

            assert event.tenant_id == tenant_id
            assert event.user_id == user_id
            assert event.trace_id == (
                allowed_trace_id
            )

            savepoint = await session.begin_nested()
            try:
                await set_construction_tenant_context(
                    session,
                    tenant_id=foreign_tenant_id,
                )

                with pytest.raises(DBAPIError):
                    await EventRepository(
                        session
                    ).create_event(
                        event_type=(
                            "construction_client_created"
                        ),
                        tenant_id=tenant_id,
                        user_id=user_id,
                        entity_type=(
                            "construction_client"
                        ),
                        entity_id=uuid4(),
                        payload={
                            "operation": "create",
                            "changes": [],
                            "reason": None,
                        },
                        platform="api",
                        trace_id=denied_trace_id,
                    )
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()
        finally:
            if transaction.is_active:
                await transaction.rollback()

    async with async_session() as verify_session:
        remaining = (
            await verify_session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM event_logs
                    WHERE trace_id = :allowed_trace_id
                       OR trace_id = :denied_trace_id
                    """
                ),
                {
                    "allowed_trace_id": (
                        allowed_trace_id
                    ),
                    "denied_trace_id": (
                        denied_trace_id
                    ),
                },
            )
        ).scalar_one()

    assert remaining == 0



async def test_con_009_all_stage_one_tables_enforce_tenant_rls_and_server_only_grants():
    stage_one_tables = (
        "construction_access_grants",
        "construction_clients",
        "construction_projects",
        "construction_project_areas",
        "construction_area_elements",
    )
    expected_server_grants = [
        "INSERT",
        "SELECT",
        "UPDATE",
    ]

    async with construction_transaction(
        tenant_id=uuid4(),
    ) as session:
        for table_name in stage_one_tables:
            table_state = (
                await session.execute(
                    text(
                        """
                        SELECT
                            c.relrowsecurity
                                AS rls_enabled
                        FROM pg_class AS c
                        JOIN pg_namespace AS n
                          ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relname = :table_name
                        """
                    ),
                    {
                        "table_name": table_name,
                    },
                )
            ).mappings().one_or_none()

            assert table_state is not None, (
                f"Missing Stage 1 table: "
                f"{table_name}"
            )
            assert (
                table_state["rls_enabled"]
                is True
            )

            policies = (
                await session.execute(
                    text(
                        """
                        SELECT policyname
                        FROM pg_policies
                        WHERE schemaname = 'public'
                          AND tablename = :table_name
                        ORDER BY policyname
                        """
                    ),
                    {
                        "table_name": table_name,
                    },
                )
            ).scalars().all()

            assert policies == [
                "construction_tenant_isolation"
            ]

            client_grants = (
                await session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM information_schema
                             .table_privileges
                        WHERE table_schema = 'public'
                          AND table_name = :table_name
                          AND grantee IN (
                              'anon',
                              'authenticated'
                          )
                        """
                    ),
                    {
                        "table_name": table_name,
                    },
                )
            ).scalar_one()

            assert client_grants == 0

            server_grants = (
                await session.execute(
                    text(
                        """
                        SELECT privilege_type
                        FROM information_schema
                             .table_privileges
                        WHERE table_schema = 'public'
                          AND table_name = :table_name
                          AND grantee = (
                              'construction_api'
                          )
                        ORDER BY privilege_type
                        """
                    ),
                    {
                        "table_name": table_name,
                    },
                )
            ).scalars().all()

            assert (
                server_grants
                == expected_server_grants
            )



async def test_con_009_real_access_grant_cross_tenant_rls_smoke():
    from datetime import UTC, datetime

    from database.construction import (
        set_construction_tenant_context,
    )
    from database.construction_session import (
        get_construction_session_factory,
        verify_construction_database_identity,
    )
    from database.repositories.construction import (
        ConstructionAccessRepository,
    )
    from database.session import async_session

    now = datetime.now(UTC)

    async with async_session() as setup_session:
        fixture = (
            await setup_session.execute(
                text(
                    """
                    SELECT
                        t.id AS tenant_id,
                        u.id AS user_id
                    FROM tenants AS t
                    JOIN users AS u
                      ON u.tenant_id = t.id
                    WHERE t.status = 'active'
                      AND u.status = 'active'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM construction_access_grants
                              AS grant_row
                          WHERE grant_row.tenant_id = t.id
                            AND grant_row.user_id = u.id
                            AND grant_row.role = 'OBSERVER'
                            AND grant_row.scope_type = 'tenant'
                            AND grant_row.scope_id = t.id
                            AND grant_row.status = 'active'
                            AND (
                                grant_row.expires_at IS NULL
                                OR grant_row.expires_at > now()
                            )
                      )
                    ORDER BY
                        t.created_at,
                        u.created_at
                    LIMIT 1
                    """
                )
            )
        ).mappings().one_or_none()

    assert fixture is not None, (
        "An active platform tenant/user without "
        "an overlapping grant is required."
    )

    tenant_id = fixture["tenant_id"]
    user_id = fixture["user_id"]
    foreign_tenant_id = uuid4()
    grant_id = None

    factory = get_construction_session_factory()

    async with factory() as session:
        transaction = await session.begin()

        try:
            assert (
                await verify_construction_database_identity(
                    session
                )
                == "construction_api"
            )

            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )

            repository = ConstructionAccessRepository(
                session
            )

            grant = await repository.create_grant(
                tenant_id=tenant_id,
                user_id=user_id,
                role="ADMIN",
                scope_type="tenant",
                scope_id=tenant_id,
                expires_at=None,
                granted_by_user_id=user_id,
                granted_at=now,
            )
            grant_id = grant.id

            visible = await repository.list_active_grants(
                tenant_id=tenant_id,
                user_id=user_id,
                as_of=now,
            )

            assert grant_id in {
                item.id
                for item in visible
            }

            await set_construction_tenant_context(
                session,
                tenant_id=foreign_tenant_id,
            )

            hidden = await repository.list_active_grants(
                tenant_id=tenant_id,
                user_id=user_id,
                as_of=now,
            )

            assert hidden == []
        finally:
            if transaction.is_active:
                await transaction.rollback()

    assert grant_id is not None

    async with async_session() as verify_session:
        remaining = (
            await verify_session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM construction_access_grants
                    WHERE id = :grant_id
                    """
                ),
                {
                    "grant_id": grant_id,
                },
            )
        ).scalar_one()

    assert remaining == 0



async def test_con_009_real_client_and_project_cross_tenant_rls_smoke():
    from database.construction import (
        set_construction_tenant_context,
    )
    from database.construction_session import (
        get_construction_session_factory,
        verify_construction_database_identity,
    )
    from database.repositories.construction import (
        ConstructionClientRepository,
        ConstructionProjectRepository,
    )
    from database.session import async_session

    async with async_session() as setup_session:
        fixture = (
            await setup_session.execute(
                text(
                    """
                    SELECT
                        t.id AS tenant_id,
                        u.id AS user_id
                    FROM tenants AS t
                    JOIN users AS u
                      ON u.tenant_id = t.id
                    WHERE t.status = 'active'
                      AND u.status = 'active'
                    ORDER BY
                        t.created_at,
                        u.created_at
                    LIMIT 1
                    """
                )
            )
        ).mappings().one_or_none()

    assert fixture is not None, (
        "An active platform tenant/user fixture "
        "is required for Construction smoke."
    )

    tenant_id = fixture["tenant_id"]
    user_id = fixture["user_id"]
    foreign_tenant_id = uuid4()
    client_id = None
    project_id = None

    factory = get_construction_session_factory()

    async with factory() as session:
        transaction = await session.begin()

        try:
            assert (
                await verify_construction_database_identity(
                    session
                )
                == "construction_api"
            )

            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )

            client_repository = (
                ConstructionClientRepository(
                    session
                )
            )
            project_repository = (
                ConstructionProjectRepository(
                    session
                )
            )

            client = await client_repository.create_client(
                tenant_id=tenant_id,
                display_name="CON-009 RLS client",
                client_type="person",
                phone=None,
                email=None,
                notes=None,
            )
            client_id = client.id

            project = await project_repository.create_project(
                tenant_id=tenant_id,
                client_id=client_id,
                name="CON-009 RLS project",
                responsible_user_id=None,
                comment=None,
                address_raw="RLS smoke address",
                created_by=user_id,
            )
            project_id = project.id

            assert (
                await client_repository.get_active_client(
                    tenant_id=tenant_id,
                    client_id=client_id,
                )
                is not None
            )
            assert (
                await project_repository.get_active_project(
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
                is not None
            )

            await set_construction_tenant_context(
                session,
                tenant_id=foreign_tenant_id,
            )

            hidden_client = (
                await client_repository.get_active_client(
                    tenant_id=tenant_id,
                    client_id=client_id,
                )
            )
            hidden_project = (
                await project_repository.get_active_project(
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
            )

            assert hidden_client is None
            assert hidden_project is None
        finally:
            if transaction.is_active:
                await transaction.rollback()

    assert client_id is not None
    assert project_id is not None

    async with async_session() as verify_session:
        remaining = (
            await verify_session.execute(
                text(
                    """
                    SELECT
                        (
                            SELECT count(*)
                            FROM construction_clients
                            WHERE id = :client_id
                        )
                        +
                        (
                            SELECT count(*)
                            FROM construction_projects
                            WHERE id = :project_id
                        )
                    """
                ),
                {
                    "client_id": client_id,
                    "project_id": project_id,
                },
            )
        ).scalar_one()

    assert remaining == 0



async def test_con_009_real_cross_tenant_client_insert_is_denied():
    from sqlalchemy.exc import DBAPIError

    from database.construction import (
        set_construction_tenant_context,
    )
    from database.construction_session import (
        get_construction_session_factory,
        verify_construction_database_identity,
    )
    from database.repositories.construction import (
        ConstructionClientRepository,
    )
    from database.session import async_session

    async with async_session() as setup_session:
        tenant_id = (
            await setup_session.execute(
                text(
                    """
                    SELECT id
                    FROM tenants
                    WHERE status = 'active'
                    ORDER BY created_at
                    LIMIT 1
                    """
                )
            )
        ).scalar_one_or_none()

    assert tenant_id is not None, (
        "An active platform tenant fixture "
        "is required for Construction smoke."
    )

    foreign_tenant_id = uuid4()
    factory = get_construction_session_factory()

    async with factory() as session:
        transaction = await session.begin()

        try:
            assert (
                await verify_construction_database_identity(
                    session
                )
                == "construction_api"
            )

            await set_construction_tenant_context(
                session,
                tenant_id=foreign_tenant_id,
            )

            savepoint = await session.begin_nested()

            try:
                with pytest.raises(DBAPIError):
                    await ConstructionClientRepository(
                        session
                    ).create_client(
                        tenant_id=tenant_id,
                        display_name=(
                            "Forbidden cross-tenant client"
                        ),
                        client_type="person",
                        phone=None,
                        email=None,
                        notes=None,
                    )
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()
        finally:
            if transaction.is_active:
                await transaction.rollback()



async def test_con_002_real_tenant_scope_role_constraint_smoke():
    from datetime import UTC, datetime

    from sqlalchemy.exc import DBAPIError

    from database.construction import (
        set_construction_tenant_context,
    )
    from database.construction_session import (
        get_construction_session_factory,
        verify_construction_database_identity,
    )
    from database.repositories.construction import (
        ConstructionAccessRepository,
    )
    from database.session import async_session

    async with async_session() as setup_session:
        fixture = (
            await setup_session.execute(
                text(
                    """
                    SELECT
                        t.id AS tenant_id,
                        u.id AS user_id
                    FROM tenants AS t
                    JOIN users AS u
                      ON u.tenant_id = t.id
                    WHERE t.status = 'active'
                      AND u.status = 'active'
                    ORDER BY
                        t.created_at,
                        u.created_at
                    LIMIT 1
                    """
                )
            )
        ).mappings().one_or_none()

    assert fixture is not None, (
        "An active platform tenant/user fixture "
        "is required for Construction smoke."
    )

    tenant_id = fixture["tenant_id"]
    user_id = fixture["user_id"]
    factory = get_construction_session_factory()

    async with factory() as session:
        transaction = await session.begin()

        try:
            assert (
                await verify_construction_database_identity(
                    session
                )
                == "construction_api"
            )

            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )

            savepoint = await session.begin_nested()

            try:
                with pytest.raises(DBAPIError):
                    await ConstructionAccessRepository(
                        session
                    ).create_grant(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        role="OBSERVER",
                        scope_type="tenant",
                        scope_id=tenant_id,
                        expires_at=None,
                        granted_by_user_id=user_id,
                        granted_at=datetime.now(UTC),
                    )
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()
        finally:
            if transaction.is_active:
                await transaction.rollback()
