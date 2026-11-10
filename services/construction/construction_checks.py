"""CON-020 internal check results, following rev.12 sections 10.3 and 15.2.

Domain services select applicable checks after authorization and fresh reads.
A report is neither authorization nor proof that all required checks ran.
This module introduces no thresholds, domain entities, persistence or HTTP routes.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from uuid import UUID


class ConstructionCheckContractError(ValueError):
    pass


class ConstructionCheckSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class ConstructionCheckCode(str, Enum):
    OPENINGS_EXCEED_WALL_AREA = "OPENINGS_EXCEED_WALL_AREA"
    PRICE_MISSING = "PRICE_MISSING"
    TAX_MISSING = "TAX_MISSING"
    SUPPLIER_PRICE_STALE = "SUPPLIER_PRICE_STALE"
    NEGATIVE_MARGIN = "NEGATIVE_MARGIN"
    UNUSUAL_DIMENSION = "UNUSUAL_DIMENSION"
    STANDARD_PERCENT_APPLIED = "STANDARD_PERCENT_APPLIED"
    PRICING_PROFILE_APPLIED = "PRICING_PROFILE_APPLIED"
    MINIMUM_ORDER_NOT_REACHED = "MINIMUM_ORDER_NOT_REACHED"


@dataclass(frozen=True)
class ConstructionCheckRule:
    severity: ConstructionCheckSeverity
    message_key: str
    parameter_kinds: tuple[tuple[str, str], ...] = ()


Code, Severity = ConstructionCheckCode, ConstructionCheckSeverity
CHECK_RULES = MappingProxyType({
    Code.OPENINGS_EXCEED_WALL_AREA: ConstructionCheckRule(
        Severity.ERROR, "construction.checks.openings_exceed_wall_area",
        (("opening_area", "nonnegative_decimal"), ("wall_area", "nonnegative_decimal"))),
    Code.PRICE_MISSING: ConstructionCheckRule(Severity.ERROR, "construction.checks.price_missing"),
    Code.TAX_MISSING: ConstructionCheckRule(Severity.ERROR, "construction.checks.tax_missing"),
    Code.SUPPLIER_PRICE_STALE: ConstructionCheckRule(Severity.WARNING, "construction.checks.supplier_price_stale"),
    Code.NEGATIVE_MARGIN: ConstructionCheckRule(
        Severity.WARNING, "construction.checks.negative_margin", (("margin", "negative_decimal"),)),
    Code.UNUSUAL_DIMENSION: ConstructionCheckRule(
        Severity.WARNING, "construction.checks.unusual_dimension", (("value", "decimal"),)),
    Code.STANDARD_PERCENT_APPLIED: ConstructionCheckRule(
        Severity.INFO, "construction.checks.standard_percent_applied", (("percent", "percent"),)),
    Code.PRICING_PROFILE_APPLIED: ConstructionCheckRule(
        Severity.INFO, "construction.checks.pricing_profile_applied", (("profile_id", "uuid"),)),
    Code.MINIMUM_ORDER_NOT_REACHED: ConstructionCheckRule(
        Severity.WARNING, "construction.checks.minimum_order_not_reached"),
})


def _machine_name(value, *, action=False):
    pattern = r"construction\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*" if action else r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*"
    bound = 128 if action else 255
    if not isinstance(value, str) or not 1 <= len(value) <= bound or re.fullmatch(pattern, value) is None:
        raise ConstructionCheckContractError("A bounded machine identifier is required.")


@dataclass(frozen=True)
class ConstructionCheckContext:
    tenant_id: UUID
    user_id: UUID
    action: str
    project_id: UUID | None = None

    def __post_init__(self):
        if (not all(isinstance(value, UUID) for value in (self.tenant_id, self.user_id))
                or self.project_id is not None and not isinstance(self.project_id, UUID)):
            raise ConstructionCheckContractError("Canonical UUID context is required.")
        _machine_name(self.action, action=True)


def _parameters(rule, values):
    if not isinstance(values, Mapping):
        raise ConstructionCheckContractError("Structured check parameters are required.")
    allowed = dict(rule.parameter_kinds)
    if any(not isinstance(name, str) or name not in allowed for name in values):
        raise ConstructionCheckContractError("Unknown check parameter.")
    result = {}
    for name, value in values.items():
        kind = allowed[name]
        if kind == "uuid":
            if not isinstance(value, UUID):
                raise ConstructionCheckContractError("Canonical UUID parameter is required.")
        else:
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ConstructionCheckContractError("Finite exact Decimal parameter is required.")
            if ((kind == "nonnegative_decimal" and value < 0)
                    or (kind == "negative_decimal" and value >= 0)
                    or (kind == "percent" and not Decimal(0) <= value <= Decimal(100))):
                raise ConstructionCheckContractError("Check parameter is outside its domain.")
        result[name] = value
    return MappingProxyType(dict(sorted(result.items())))


@dataclass(frozen=True)
class ConstructionCheckFinding:
    context: ConstructionCheckContext
    code: ConstructionCheckCode
    field_path: str | None = None
    parameters: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.context, ConstructionCheckContext) or not isinstance(self.code, ConstructionCheckCode):
            raise ConstructionCheckContractError("Typed check context and registered code are required.")
        if self.field_path is not None:
            _machine_name(self.field_path)
        object.__setattr__(self, "parameters", _parameters(CHECK_RULES[self.code], self.parameters))

    @property
    def severity(self):
        return CHECK_RULES[self.code].severity

    @property
    def message_key(self):
        return CHECK_RULES[self.code].message_key

    def to_payload(self):
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message_key": self.message_key,
            "field_path": self.field_path,
            "parameters": {name: str(value) for name, value in self.parameters.items()},
        }


@dataclass(frozen=True)
class ConstructionCheckReport:
    context: ConstructionCheckContext
    findings: tuple[ConstructionCheckFinding, ...]

    def __post_init__(self):
        if not isinstance(self.context, ConstructionCheckContext) or not isinstance(self.findings, tuple):
            raise ConstructionCheckContractError("A typed context and immutable finding sequence are required.")
        if any(not isinstance(item, ConstructionCheckFinding) or item.context != self.context for item in self.findings):
            raise ConstructionCheckContractError("All findings must belong to the same actor, tenant, action and project.")

    @property
    def errors(self):
        return tuple(item for item in self.findings if item.severity is Severity.ERROR)

    @property
    def warnings(self):
        return tuple(item for item in self.findings if item.severity is Severity.WARNING)

    @property
    def information(self):
        return tuple(item for item in self.findings if item.severity is Severity.INFO)

    @property
    def is_blocked(self):
        return bool(self.errors)

    @property
    def requires_attention(self):
        return bool(self.warnings)

    def raise_for_errors(self):
        if self.is_blocked:
            raise ConstructionCheckBlockedError(self)

    def to_payload(self):
        return {
            "is_blocked": self.is_blocked,
            "requires_attention": self.requires_attention,
            "findings": [item.to_payload() for item in self.findings],
        }


class ConstructionCheckBlockedError(Exception):
    def __init__(self, report):
        if not isinstance(report, ConstructionCheckReport) or not report.is_blocked:
            raise ConstructionCheckContractError("A report with blocking errors is required.")
        self.report = report
        super().__init__("Construction action has blocking check results.")


def collect_construction_checks(*, context, findings):
    # None, mappings, strings and unfinished async/generator outputs are not empty reports.
    if not isinstance(findings, (tuple, list)):
        raise ConstructionCheckContractError("An evaluated finding sequence is required.")
    return ConstructionCheckReport(context=context, findings=tuple(findings))
