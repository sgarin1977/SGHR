"""Versioned release content. Reading this package never seeds a database."""
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, NAMESPACE_OID, uuid5

from services.construction.construction_starter_template_definitions import (
    ValidatedStarterDefinition, validate_starter_definition,
)

PACK_CODE = "SGHR_STARTERS_V1"
PACK_CODES = (
    "FULL_RENOVATION", "BATHROOM", "PAINTING", "FACADE", "ROOF", "TERRACE",
)
PACK_LANGUAGES = frozenset({"uk", "ru", "en", "pt", "pl", "de", "nl"})
PACK_NAMESPACE = uuid5(
    NAMESPACE_OID, "sghr.construction.starter-measurement-templates")
PACK_PATH = (
    Path(__file__).resolve().parents[2]
    / "database/catalogs/construction/starter_measurement_templates_v1.json"
)


class StarterTemplatePackError(ValueError):
    pass


@dataclass(frozen=True)
class StarterTemplatePackEntry:
    template_id: UUID
    version_id: UUID
    code: str
    definition: ValidatedStarterDefinition

    def record(self):
        return {
            "template_id": str(self.template_id),
            "version_id": str(self.version_id),
            "code": self.code, "version": 1, "definition_schema_version": 1,
            "source_language": self.definition.source_language,
            "content_hash": self.definition.content_hash,
            "definition": self.definition.payload(),
        }


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate package field.")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite package value.")


def load_starter_pack(path=PACK_PATH):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError()
        data = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
            parse_constant=_constant)
        if (
            not isinstance(data, dict)
            or set(data) != {"schema_version", "pack_code", "templates"}
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1 or data["pack_code"] != PACK_CODE
            or not isinstance(data["templates"], list)
            or len(data["templates"]) != len(PACK_CODES)
        ):
            raise ValueError()
        result = []
        fields = {
            "template_id", "version_id", "code", "version",
            "definition_schema_version", "source_language",
            "definition", "content_hash",
        }
        for expected_code, item in zip(PACK_CODES, data["templates"]):
            if (
                not isinstance(item, dict) or set(item) != fields
                or item["code"] != expected_code
                or type(item["version"]) is not int or item["version"] != 1
                or type(item["definition_schema_version"]) is not int
                or item["definition_schema_version"] != 1
                or item["source_language"] != "en"
                or item["template_id"] != str(uuid5(PACK_NAMESPACE, expected_code))
                or item["version_id"] != str(uuid5(PACK_NAMESPACE, expected_code + "@1"))
            ):
                raise ValueError()
            definition = validate_starter_definition(
                item["definition"], source_language=item["source_language"])
            labels = definition.payload()["labels"]
            if (
                item["content_hash"] != definition.content_hash
                or set(labels) != PACK_LANGUAGES
                or any(set(values) != set(labels["en"]) for values in labels.values())
            ):
                raise ValueError()
            result.append(StarterTemplatePackEntry(
                UUID(item["template_id"]), UUID(item["version_id"]),
                expected_code, definition))
        return tuple(result)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise StarterTemplatePackError("Starter release package is invalid.") from None
