"""Release content and SQL rendering; all database fixtures are local SQLite."""
import json
import re
from uuid import uuid4

import pytest
from sqlalchemy import insert

from services.construction.construction_starter_template_definitions import PHASES
from services.construction.construction_starter_template_pack import (
    PACK_CODES, PACK_LANGUAGES, PACK_PATH,
    StarterTemplatePackError, load_starter_pack,
)
from scripts.render_construction_starter_pack_sql import render_sql, _literal
from test_construction_02_starter_template_catalog import (
    case, onboarding, catalog_case, snapshot,
)


def test_six_complete_multilingual_definitions_have_stable_content():
    entries = load_starter_pack()
    assert tuple(entry.code for entry in entries) == PACK_CODES
    assert len({entry.template_id for entry in entries}) == 6
    assert len({entry.version_id for entry in entries}) == 6
    assert entries == load_starter_pack()
    for entry in entries:
        payload = entry.definition.payload()
        assert set(payload["labels"]) == PACK_LANGUAGES
        assert all(
            set(values) == set(payload["labels"]["en"])
            for values in payload["labels"].values())
        assert tuple(section["work_phase_code"] for section in payload["sections"]) == PHASES
        assert any(section["active"] for section in payload["sections"])
        for section in payload["sections"]:
            gate = section["questions"][0]
            assert [option["value"] for option in gate["options"]] == [
                "NO", "YES", "NEEDS_CLARIFICATION"]
            assert gate["required"] is True and gate["default_source"] == "NONE"
            for question in section["questions"]:
                assert question["work_type_source"] is None
                assert question["work_type_id"] is None
                assert question["default_value"] is None
                if question is not gate:
                    assert question["show_if"] is not None
                if question["question_type"] == "NUMBER":
                    assert question["unit_code"] == "M2"
                    assert question["formula_type"] == "DIRECT"
                    rules = question["show_if"]["all"]
                    assert any(rule["value"] == "YES" for rule in rules)
                    assert any(rule["value"] is True for rule in rules)
    detached = entries[0].definition.payload()
    detached["labels"]["en"]["title"] = "Changed"
    assert load_starter_pack()[0].definition.payload()["labels"]["en"]["title"] != "Changed"


def test_painting_has_a_specific_active_phase_subset():
    entry = next(entry for entry in load_starter_pack() if entry.code == "PAINTING")
    assert {
        section["work_phase_code"]
        for section in entry.definition.payload()["sections"] if section["active"]
    } == {
        "DEMOLITION", "PLASTER_PREP", "PUTTY_PRIMER",
        "PAINT_FINISH", "FINAL_SEALING", "CLEANING_WASTE",
    }


@pytest.mark.parametrize("failure", [
    "hash", "identity", "commercial", "translation", "version", "extra", "order",
])
def test_corrupt_release_package_is_rejected(tmp_path, failure):
    data = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    entry = data["templates"][0]
    if failure == "hash":
        entry["content_hash"] = "0" * 64
    elif failure == "identity":
        entry["version_id"] = str(uuid4())
    elif failure == "commercial":
        entry["definition"]["unit_price"] = "20.00"
    elif failure == "translation":
        del entry["definition"]["labels"]["pt"]
    elif failure == "version":
        entry["version"] = 2
    elif failure == "extra":
        data["automatic_install"] = True
    else:
        data["templates"].reverse()
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(StarterTemplatePackError):
        load_starter_pack(path)


def test_duplicate_json_fields_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(StarterTemplatePackError):
        load_starter_pack(path)


def test_sql_is_deterministic_insert_only_and_preserves_existing_sources():
    sql = render_sql(load_starter_pack())
    assert sql == render_sql(load_starter_pack())
    assert sql.startswith("-- SGHR_STARTERS_V1:")
    assert sql.rstrip().endswith("COMMIT;")
    assert re.findall(r"INSERT INTO ([a-z_.]+)", sql) == [
        "public.construction_starter_measurement_templates",
        "public.construction_starter_measurement_template_versions",
    ]
    assert not re.search(
        r"^\s*(UPDATE|DELETE|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)\b",
        sql, re.MULTILINE)
    assert "IN SHARE ROW EXCLUSIVE MODE" in sql
    assert "immutable source version differs" in sql
    assert "disabled_templates_preserved" in sql
    assert _literal("A'B\\C") == "'A''B\\C'"


@pytest.mark.asyncio
async def test_real_release_content_is_readable_without_onboarding_writes(catalog_case):
    c = catalog_case
    entries = load_starter_pack()
    for entry in entries:
        c.sync.execute(insert(c.catalog_templates).values(
            id=entry.template_id, code=entry.code, active=True, row_version=1,
            created_at=c.now, updated_at=c.now))
        c.sync.execute(insert(c.catalog_versions).values(
            id=entry.version_id, template_id=entry.template_id, version=1,
            definition_schema_version=1, source_language="en",
            definition=entry.definition.payload(),
            content_hash=entry.definition.content_hash,
            published_at=c.now, created_at=c.now))
    c.sync.commit()
    before = snapshot(c)
    page = await c.catalog.list_latest(actor=c.actor)
    assert {item.code for item in page.items} == set(PACK_CODES)
    assert page.next_cursor is None
    for entry in entries:
        result = await c.catalog.get_version(actor=c.actor, version_id=entry.version_id)
        assert result.content_hash == entry.definition.content_hash
        assert result.definition.payload() == entry.definition.payload()
    assert snapshot(c) == before and c.session.commits == 0
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()
