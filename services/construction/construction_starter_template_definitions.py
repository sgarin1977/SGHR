"""Data-only starter definition v1. No execution, prices or work creation."""
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator
from services.construction.construction_units import UnitCode

PHASES = (
    "DEMOLITION", "PARTITIONS_OPENINGS", "CHASING", "ROUGH_ELECTRICAL",
    "ROUGH_WATER", "ROUGH_DRAINAGE", "HVAC_VENTILATION", "PLASTER_PREP",
    "WATERPROOFING", "SCREED_LEVELING", "TILING_STONE", "PUTTY_PRIMER",
    "PAINT_FINISH", "FINISH_FLOOR", "DOORS_WINDOWS", "FINISH_ELECTRICAL",
    "FINISH_PLUMBING", "EQUIPMENT_FURNITURE", "FINAL_SEALING", "CLEANING_WASTE",
)
QUESTION_TYPES = (
    "BOOLEAN", "NUMBER", "TEXT", "SINGLE_CHOICE", "MULTI_CHOICE",
    "DATE", "PHOTO", "ELEMENT", "CALCULATED",
)
FORMULAS = frozenset({
    "DIRECT", "RECTANGLE", "WALL_AREA", "WALL_NET_AREA", "PERCENT_OF_BASE",
    "PERIMETER", "REVEAL_THREE_SIDES", "LINEAR_LENGTH", "COUNT", "VOLUME",
})
GATE_VALUES = ("NO", "YES", "NEEDS_CLARIFICATION")
MAX_BYTES = 1024 * 1024
COMMERCIAL_NAMES = (
    "UNITPRICE", "PRICE", "UNITCOST", "COST", "CURRENCY", "MARKUP",
    "DISCOUNT", "MARGIN", "TAXRATE", "PRICELISTID",
    "ORGANIZATIONWORKITEMID", "COMMERCIALWORKITEMID",
    "DEFAULTTECHNOLOGICALWASTEPERCENT", "DEFAULTLOCALREPAIRPERCENT",
)


class StarterTemplateDefinitionError(ValueError):
    pass


def _code(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) is None:
        raise ValueError("A neutral canonical code is required.")
    return value


def _key(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", value) is None:
        raise ValueError("A stable label/reference key is required.")
    return value


def _language(value):
    if (
        not isinstance(value, str) or len(value) > 10
        or re.fullmatch(r"[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,6})?", value) is None
    ):
        raise ValueError("A source language code is required.")
    return value


def _commercial(value):
    normalized = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return any(normalized.endswith(name) for name in COMMERCIAL_NAMES)


def _decimal(value):
    if type(value) is int:
        return Decimal(value)
    if not isinstance(value, str) or re.fullmatch(
        r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value
    ) is None:
        raise ValueError("An exact numeric bound is required.")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Numeric bounds must be finite.")
    return result


def _walk(value, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if depth > 20 or count[0] > 20000:
        raise ValueError("Definition complexity exceeds its bounds.")
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("JSON keys must be strings.")
        for child in value.values():
            _walk(child, depth + 1, count)
    elif isinstance(value, list):
        for child in value:
            _walk(child, depth + 1, count)
    elif value is not None and type(value) not in (str, int, bool):
        raise ValueError("Use JSON values and exact decimal strings, never floats.")


def _schema(value, depth=0):
    allowed = {
        "type", "format", "minimum", "maximum", "exclusiveMinimum",
        "exclusiveMaximum", "minLength", "maxLength", "minItems", "maxItems",
        "pattern", "enum", "const", "properties", "items", "required",
        "additionalProperties",
    }
    if not isinstance(value, dict) or depth > 10 or not set(value).issubset(allowed):
        raise ValueError("Unsupported input/validation schema.")
    types = {"string", "number", "integer", "boolean", "array", "object", "null"}
    if "type" in value:
        choices = value["type"] if isinstance(value["type"], list) else [value["type"]]
        if (
            not choices or any(type(item) is not str or item not in types for item in choices)
            or len(set(choices)) != len(choices)
        ):
            raise ValueError("Invalid schema type.")
    if "format" in value and value["format"] not in ("decimal", "date", "date-time", "uuid"):
        raise ValueError("Unsupported schema format.")
    for name in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if name in value:
            _decimal(value[name])
    if "minimum" in value and "maximum" in value and _decimal(value["minimum"]) > _decimal(value["maximum"]):
        raise ValueError("Reversed numeric bounds.")
    for name in ("minLength", "maxLength", "minItems", "maxItems"):
        if name in value and (type(value[name]) is not int or value[name] < 0):
            raise ValueError("Invalid schema length/count bound.")
    if "pattern" in value and (not isinstance(value["pattern"], str) or len(value["pattern"]) > 512):
        raise ValueError("Invalid schema pattern.")
    if "additionalProperties" in value and type(value["additionalProperties"]) is not bool:
        raise ValueError("additionalProperties must be boolean.")
    if "enum" in value and (
        not isinstance(value["enum"], list) or not 1 <= len(value["enum"]) <= 128
        or any(isinstance(item, (dict, list)) for item in value["enum"])
    ):
        raise ValueError("Invalid schema enum.")
    properties = value.get("properties", {})
    if not isinstance(properties, dict) or len(properties) > 128:
        raise ValueError("Invalid schema properties.")
    for name, child in properties.items():
        _key(name)
        if _commercial(name):
            raise ValueError("Commercial fields are not starter structure.")
        _schema(child, depth + 1)
    if "items" in value:
        _schema(value["items"], depth + 1)
    if "required" in value:
        required = value["required"]
        if (
            not isinstance(required, list)
            or any(type(name) is not str for name in required)
            or len(set(required)) != len(required)
            or not set(required).issubset(properties)
        ):
            raise ValueError("Invalid required property references.")


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Option(Closed):
    value: StrictStr
    label_key: StrictStr


class DefaultReference(Closed):
    field: StrictStr

    @model_validator(mode="after")
    def reference(self):
        _key(self.field)
        if _commercial(self.field):
            raise ValueError("Commercial defaults are not part of starter structure.")
        return self


class Question(Closed):
    code: StrictStr
    question_type: Literal[
        "BOOLEAN", "NUMBER", "TEXT", "SINGLE_CHOICE", "MULTI_CHOICE",
        "DATE", "PHOTO", "ELEMENT", "CALCULATED",
    ]
    label_key: StrictStr
    required: StrictBool
    sort_order: StrictInt = Field(ge=0)
    active: StrictBool
    unit_code: UnitCode | None = None
    formula_type: StrictStr | None = None
    input_schema: dict = Field(default_factory=dict)
    validation_schema: dict = Field(default_factory=dict)
    options: tuple[Option, ...] = Field(default=(), max_length=128)
    show_if: dict | None = None
    default_source: Literal["NONE", "ORG_CONSTANT", "PROJECT_VALUE"] = "NONE"
    default_value: DefaultReference | None = None
    work_type_source: Literal["MASTER"] | None = None
    work_type_id: UUID | None = None
    work_type_version_policy: Literal["CURRENT_PUBLISHED", "PINNED"] | None = None
    pinned_work_type_version_id: UUID | None = None
    work_phase_code: StrictStr | None = None
    photo_policy: Literal["NONE", "OPTIONAL", "REQUIRED"] = "NONE"
    help_text_key: StrictStr | None = None
    help_media_ref: StrictStr | None = None

    @model_validator(mode="after")
    def coherent_question(self):
        _code(self.code)
        _key(self.label_key)
        if _commercial(self.code):
            raise ValueError("Commercial questions are not starter structure.")
        for key in (self.help_text_key, self.help_media_ref):
            if key is not None:
                _key(key)
        _schema(self.input_schema)
        _schema(self.validation_schema)
        values = [option.value for option in self.options]
        for option in self.options:
            _code(option.value)
            _key(option.label_key)
        if len(set(values)) != len(values):
            raise ValueError("Duplicate option values.")
        if self.question_type in ("SINGLE_CHOICE", "MULTI_CHOICE"):
            if not values:
                raise ValueError("Choice questions require explicit options.")
        elif values:
            raise ValueError("Only choice questions have options.")
        if self.formula_type is not None and (
            self.formula_type not in FORMULAS
            or self.question_type not in ("NUMBER", "CALCULATED")
        ):
            raise ValueError("Unsupported formula type.")
        if self.question_type == "CALCULATED" and self.formula_type is None:
            raise ValueError("A calculated result needs an explicit formula type.")
        if self.unit_code is not None and self.question_type not in ("NUMBER", "CALCULATED"):
            raise ValueError("Units belong to numeric facts/results.")
        if self.question_type == "PHOTO" and self.photo_policy == "NONE":
            raise ValueError("Photo questions require an explicit photo policy.")
        if (self.default_source == "NONE") != (self.default_value is None):
            raise ValueError("Default sources require coherent explicit references.")
        bindings = (
            self.work_type_id, self.work_type_version_policy,
            self.pinned_work_type_version_id,
        )
        if self.work_type_source is None:
            if any(value is not None for value in bindings):
                raise ValueError("Partial work-type binding.")
        else:
            if self.work_type_id is None or self.work_type_version_policy is None:
                raise ValueError("A master binding requires identity and version policy.")
            if (self.work_type_version_policy == "PINNED") != (
                self.pinned_work_type_version_id is not None
            ):
                raise ValueError("Pinned work-type version differs from its policy.")
        return self


class Section(Closed):
    code: StrictStr
    label_key: StrictStr
    work_phase_code: StrictStr
    sort_order: StrictInt = Field(ge=0)
    active: StrictBool
    questions: tuple[Question, ...] = Field(min_length=1, max_length=2000)


def _condition(rule, previous, depth=0):
    if not isinstance(rule, dict) or depth > 8:
        raise ValueError("Invalid show_if condition.")
    if set(rule) in ({"all"}, {"any"}):
        children = next(iter(rule.values()))
        if not isinstance(children, list) or not 1 <= len(children) <= 32:
            raise ValueError("Invalid condition group.")
        for child in children:
            _condition(child, previous, depth + 1)
        return
    if not {"question_code", "operator"}.issubset(rule) or not set(rule).issubset({
        "question_code", "operator", "value",
    }):
        raise ValueError("Unknown condition fields.")
    code, operator = rule["question_code"], rule["operator"]
    if not isinstance(code, str) or code not in previous:
        raise ValueError("Conditions require an earlier question; cycles/forward refs are forbidden.")
    question = previous[code]
    if operator == "IS_SET":
        if "value" in rule:
            raise ValueError("IS_SET has no literal value.")
        return
    if set(rule) != {"question_code", "operator", "value"}:
        raise ValueError("Condition value is missing.")
    value = rule["value"]
    if question.question_type == "MULTI_CHOICE":
        if operator != "CONTAINS" or not isinstance(value, str) or value not in {
            option.value for option in question.options
        }:
            raise ValueError("Invalid multi-choice condition.")
        return
    if operator not in ("EQ", "NE", "IN"):
        raise ValueError("Unsupported comparison operator.")
    values = value if operator == "IN" else [value]
    if not isinstance(values, list) or not 1 <= len(values) <= 128:
        raise ValueError("Invalid condition values.")
    for item in values:
        if question.question_type == "BOOLEAN":
            valid = type(item) is bool
        elif question.question_type == "SINGLE_CHOICE":
            valid = isinstance(item, str) and item in {option.value for option in question.options}
        elif question.question_type == "NUMBER":
            _decimal(item)
            valid = True
        elif question.question_type in ("TEXT", "DATE", "ELEMENT"):
            valid = isinstance(item, str)
            if valid and question.question_type == "DATE":
                valid = date.fromisoformat(item).isoformat() == item
            if valid and question.question_type == "ELEMENT":
                valid = str(UUID(item)) == item
        else:
            valid = False
        if not valid:
            raise ValueError("Condition value does not match its referenced question.")


class Definition(Closed):
    title_key: StrictStr
    description_key: StrictStr | None
    sections: tuple[Section, ...] = Field(min_length=1, max_length=100)
    labels: dict[StrictStr, dict[StrictStr, StrictStr]]

    @model_validator(mode="after")
    def structure(self):
        used = {_key(self.title_key)}
        if self.description_key is not None:
            used.add(_key(self.description_key))
        section_codes, previous = set(), {}
        phase_rank, section_order = -1, -1
        total = 0
        for section in self.sections:
            _code(section.code)
            used.add(_key(section.label_key))
            if (
                section.code in section_codes
                or section.work_phase_code not in PHASES
                or section.sort_order <= section_order
            ):
                raise ValueError("Section identity/order differs.")
            rank = PHASES.index(section.work_phase_code)
            if rank < phase_rank:
                raise ValueError("Base technological phases cannot be reordered.")
            section_codes.add(section.code)
            phase_rank, section_order = rank, section.sort_order
            gate = section.questions[0]
            if (
                gate.question_type != "SINGLE_CHOICE"
                or tuple(option.value for option in gate.options) != GATE_VALUES
                or gate.required is not True or gate.active is not True
                or gate.show_if is not None or gate.default_source != "NONE"
                or gate.work_type_source is not None
            ):
                raise ValueError("Each phase starts with an explicit NO/YES/NEEDS_CLARIFICATION gate.")
            question_order = -1
            for question in section.questions:
                total += 1
                if total > 2000 or question.code in previous or question.sort_order <= question_order:
                    raise ValueError("Question identity/order differs.")
                if question.work_phase_code not in (None, section.work_phase_code):
                    raise ValueError("Question phase differs from its section.")
                if question.show_if is not None:
                    _condition(question.show_if, previous)
                previous[question.code] = question
                question_order = question.sort_order
                used.add(question.label_key)
                if question.help_text_key is not None:
                    used.add(question.help_text_key)
                used.update(option.label_key for option in question.options)
        if not self.labels or len(self.labels) > 64:
            raise ValueError("Source labels are required.")
        for language, labels in self.labels.items():
            _language(language)
            if set(labels) - used:
                raise ValueError("Unknown label references.")
            for key, text in labels.items():
                _key(key)
                if (
                    not text or text != text.strip() or len(text) > 120
                    or any(ord(char) < 32 or ord(char) == 127 for char in text)
                ):
                    raise ValueError("Invalid versioned catalog label.")
        return self


@dataclass(frozen=True)
class ValidatedStarterDefinition:
    source_language: str
    canonical_json: str
    content_hash: str

    def payload(self):
        return json.loads(self.canonical_json)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _validate_definition(value, *, source_language, definition_model):
    try:
        _language(source_language)
        if isinstance(value, (str, bytes)):
            text = value.decode("utf-8-sig") if isinstance(value, bytes) else value.lstrip("\ufeff")
            if len(text.encode("utf-8")) > MAX_BYTES:
                raise ValueError("Definition is too large.")
            value = json.loads(
                text, object_pairs_hook=_unique_pairs,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON.")))
        if not isinstance(value, dict):
            raise ValueError("Definition must be a JSON object.")
        _walk(value)
        model = definition_model.model_validate(value)
        normalized = model.model_dump(mode="json")
        labels = normalized["labels"].get(source_language)
        if labels is None:
            raise ValueError("Complete source-language labels are required.")
        required = {normalized["title_key"]}
        if normalized["description_key"] is not None:
            required.add(normalized["description_key"])
        for section in normalized["sections"]:
            required.add(section["label_key"])
            for question in section["questions"]:
                required.add(question["label_key"])
                if question["help_text_key"] is not None:
                    required.add(question["help_text_key"])
                required.update(option["label_key"] for option in question["options"])
        if set(labels) != required:
            raise ValueError("Source-language labels are incomplete.")
        canonical = json.dumps(
            normalized, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"))
        encoded = canonical.encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError("Definition is too large.")
        return ValidatedStarterDefinition(
            source_language, canonical, sha256(encoded).hexdigest())
    except (ValueError, TypeError, RecursionError, ArithmeticError):
        raise StarterTemplateDefinitionError("Starter template definition is invalid.") from None


# con_o003_shared_definition_validation_v1
def validate_starter_definition(value, *, source_language):
    return _validate_definition(
        value, source_language=source_language, definition_model=Definition)
