"""Read services over local normalized copies; no runtime DB or native RLS claim."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, update

from database.repositories.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateRepository,
)
from services.construction.construction_organization_template_definitions import (
    validate_organization_template_definition,
)
from services.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateService as Service,
    ConstructionOrganizationTemplateOperationError as Operation,
    ConstructionOrganizationTemplateNotFoundError as NotFound,
    ConstructionOrganizationTemplateValidationError as Validation,
    OrganizationTemplateCursor as Cursor,
)
from services.construction.construction_starter_template_definitions import (
    validate_starter_definition, StarterTemplateDefinitionError,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_02_template_copy import (
    case, onboarding, catalog_case, copy_case, put, definition,
    copy as create_copy, snapshot,
)


@pytest.fixture
def read_case(copy_case):
    c = copy_case
    put(c)
    repository = ConstructionOrganizationTemplateRepository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.copy_repo, name))
    c.read_repo = repository
    c.reader = Service(
        session=c.session, repository=repository, authorizer=c.copier.authorizer)
    yield c


async def read(c, version_id):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.reader.get_version(actor=c.actor, version_id=version_id)


async def listing(c, **arguments):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.reader.list_templates(actor=c.actor, **arguments)


async def add_version(c, copied, *, number, status="PUBLISHED", future=False, org=False):
    # Explicit local fixtures; production publication still uses its installed guard.
    original = await c.copy_repo.get_copy(
        tenant_id=c.tenant, template_id=copied.template_id,
        version_id=copied.template_version_id)
    payload = (await read(c, copied.template_version_id)).definition.payload()
    work_id, pinned_id = uuid4(), uuid4()
    if org:
        payload["sections"][0]["questions"][1].update(
            work_type_source="ORGANIZATION", work_type_id=str(work_id),
            work_type_version_policy="PINNED",
            pinned_work_type_version_id=str(pinned_id))
    checked = validate_organization_template_definition(payload, source_language="en")
    version_id = uuid4()
    instant = c.now + timedelta(days=1) if future else c.now
    header = dict(original["version"]) | dict(
        id=version_id, version=number, status=status,
        row_version=2 if status == "PUBLISHED" else 1,
        content_hash=checked.content_hash if status == "PUBLISHED" else None,
        published_at=instant if status == "PUBLISHED" else None,
        updated_at=instant, source_starter_version_id=None if org else c.source_id)
    c.sync.execute(insert(c.copy_repo.versions).values(**header))
    section_ids = {}
    for row in original["sections"]:
        section_ids[row["id"]] = uuid4()
        c.sync.execute(insert(c.copy_repo.sections).values(**(
            dict(row) | dict(id=section_ids[row["id"]], template_version_id=version_id))))
    for row in original["questions"]:
        values = dict(row) | dict(
            id=uuid4(), template_version_id=version_id,
            template_section_id=section_ids[row["template_section_id"]])
        if org and row["code"] == "AREA":
            values.update(
                work_type_source="ORGANIZATION", work_type_id=work_id,
                work_type_version_policy="PINNED", pinned_work_type_version_id=pinned_id)
        c.sync.execute(insert(c.copy_repo.questions).values(**values))
    c.sync.commit()
    return version_id


def test_organization_binding_extension_does_not_relax_starter_policy():
    value = definition()
    value["sections"][0]["questions"][1].update(
        work_type_source="ORGANIZATION", work_type_id=str(uuid4()),
        work_type_version_policy="CURRENT_PUBLISHED")
    checked = validate_organization_template_definition(value, source_language="en")
    assert checked.payload()["sections"][0]["questions"][1]["work_type_source"] == "ORGANIZATION"
    with pytest.raises(StarterTemplateDefinitionError):
        validate_starter_definition(value, source_language="en")
    baseline = definition()
    assert validate_organization_template_definition(
        baseline, source_language="en").canonical_json == validate_starter_definition(
            baseline, source_language="en").canonical_json


@pytest.mark.asyncio
async def test_empty_reads_do_not_create_templates_or_progress(read_case):
    c = read_case
    before = snapshot(c)
    page = await listing(c)
    assert page.items == () and page.next_cursor is None
    assert snapshot(c) == before and c.session.commits == 0
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_exact_content_and_persistent_question_ids_are_read_only(read_case, role):
    c = read_case
    copied = await create_copy(c)
    c.grant.role = role
    before = snapshot(c)
    commits, barriers = c.session.commits, c.serialization.lock_member.await_count
    state = await read(c, copied.template_version_id)
    assert state.content_hash == copied.content_hash
    assert state.source_starter_version_id == copied.source_starter_version_id
    assert state.definition.payload() == validate_starter_definition(
        definition(), source_language="en").payload()
    assert len(state.sections) == 2
    assert sum(len(section.questions) for section in state.sections) == 3
    stored = await c.copy_repo.get_copy(
        tenant_id=c.tenant, template_id=copied.template_id,
        version_id=copied.template_version_id)
    assert {section.id for section in state.sections} == {
        row["id"] for row in stored["sections"]}
    assert {question.id for section in state.sections for question in section.questions} == {
        row["id"] for row in stored["questions"]}
    detached = state.definition.payload()
    detached["labels"]["en"]["title"] = "Changed"
    assert state.definition.payload()["labels"]["en"]["title"] == "Starter"
    assert snapshot(c) == before and c.session.commits == commits
    assert c.serialization.lock_member.await_count == barriers


@pytest.mark.asyncio
async def test_disabled_template_and_disabled_source_do_not_erase_exact_version(read_case):
    c = read_case
    copied = await create_copy(c)
    c.sync.execute(update(c.copy_repo.templates).where(
        c.copy_repo.templates.c.id == copied.template_id
    ).values(active=False, code="RENAMED", row_version=2))
    c.sync.execute(update(c.catalog_templates).where(
        c.catalog_templates.c.id == UUID(int=101)).values(active=False))
    c.sync.commit()
    c.catalog.get_version = AsyncMock(side_effect=AssertionError("No source reads."))
    state = await read(c, copied.template_version_id)
    assert state.active is False and state.code == "RENAMED"
    assert state.template_row_version == 2 and state.content_hash == copied.content_hash
    assert (await listing(c)).items[0].active is False
    c.catalog.get_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_latest_published_ignores_new_draft_and_future_version(read_case):
    c = read_case
    copied = await create_copy(c)
    c.source_id = copied.source_starter_version_id
    draft = await add_version(c, copied, number=2, status="DRAFT")
    future = await add_version(c, copied, number=3, future=True)
    page = await listing(c)
    assert page.items[0].version_id == copied.template_version_id
    for version_id in (draft, future, uuid4()):
        with pytest.raises(NotFound):
            await read(c, version_id)


@pytest.mark.asyncio
async def test_owned_published_version_can_contain_organization_bindings(read_case):
    c = read_case
    copied = await create_copy(c)
    version_id = await add_version(c, copied, number=2, org=True)
    state = await read(c, version_id)
    question = state.definition.payload()["sections"][0]["questions"][1]
    assert state.version == 2 and state.source_starter_version_id is None
    assert question["work_type_source"] == "ORGANIZATION"
    assert question["work_type_version_policy"] == "PINNED"
    assert (await listing(c)).items[0].version_id == version_id


@pytest.mark.asyncio
async def test_pagination_and_cursor_context_are_bound_to_the_actor(read_case):
    c = read_case
    await create_copy(c)
    await create_copy(c, code="SECOND_COPY")
    first = await listing(c, limit=1)
    second = await listing(c, limit=1, after=first.next_cursor)
    assert len(first.items) == len(second.items) == 1
    assert first.items[0].template_id < second.items[0].template_id
    assert second.next_cursor is None
    for cursor in (
        Cursor(c.foreign, c.user, first.items[0].template_id),
        Cursor(c.tenant, uuid4(), first.items[0].template_id),
    ):
        with pytest.raises(Validation):
            await listing(c, limit=1, after=cursor)


@pytest.mark.asyncio
async def test_foreign_version_is_hidden_and_never_added_to_the_list(read_case):
    c = read_case
    own = await create_copy(c)
    original_tenant = c.tenant
    c.tenant = c.foreign
    c.actor.tenant_id = c.foreign
    c.grant.scope_id = c.foreign
    foreign = await create_copy(c)
    c.tenant = original_tenant
    c.actor.tenant_id = original_tenant
    c.grant.scope_id = original_tenant
    page = await listing(c)
    assert [item.version_id for item in page.items] == [own.template_version_id]
    with pytest.raises(NotFound):
        await read(c, foreign.template_version_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["membership", "scope", "expiry"])
async def test_current_authority_is_required(read_case, gate):
    c = read_case
    copied = await create_copy(c)
    if gate == "membership":
        c.membership = False
    elif gate == "scope":
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    else:
        c.grant.expires_at = c.now
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c, copied.template_version_id)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_authority_is_refreshed_after_content_query(read_case):
    c = read_case
    copied = await create_copy(c)
    original = c.read_repo.get_content
    async def revoked(**values):
        result = await original(**values)
        c.membership = False
        return result
    c.read_repo.get_content = revoked
    before = snapshot(c)
    with pytest.raises(ConstructionPermissionDeniedError):
        await read(c, copied.template_version_id)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["hash", "tenant", "section", "type"])
async def test_corrupt_content_or_context_is_never_returned(read_case, bad):
    c = read_case
    copied = await create_copy(c)
    if bad == "hash":
        original = c.read_repo.get_version_header
        async def broken(**values):
            row = await original(**values)
            row["content_hash"] = "0" * 64
            return row
        c.read_repo.get_version_header = broken
    else:
        original = c.read_repo.get_content
        async def broken(**values):
            result = deepcopy(await original(**values))
            row = result["questions"][0]
            if bad == "tenant":
                row["tenant_id"] = c.foreign
            elif bad == "section":
                row["template_section_id"] = uuid4()
            else:
                row["active"] = 1
            return result
        c.read_repo.get_content = broken
    before = snapshot(c)
    with pytest.raises(Operation):
        await read(c, copied.template_version_id)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_invalid_lookahead_header_does_not_return_a_partial_page(read_case):
    c = read_case
    await create_copy(c)
    await create_copy(c, code="SECOND_COPY")
    original = c.read_repo.list_latest
    async def broken(**values):
        rows = list(await original(**values))
        rows[-1]["content_hash"] = "not-a-hash"
        return rows
    c.read_repo.list_latest = broken
    with pytest.raises(Operation):
        await listing(c, limit=1)
