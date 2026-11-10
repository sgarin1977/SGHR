"""CON-014 exact arithmetic, context binding and reproducible conversion snapshots."""
from dataclasses import FrozenInstanceError
from decimal import (
    Decimal, Inexact, MAX_EMAX, ROUND_DOWN, Rounded, localcontext,
)
import json

import pytest

from services.construction.construction_units import UnitCode
from services.construction.construction_unit_conversion import (
    DocumentedUnitFactor, ConstructionUnitConversionError,
    ConstructionUnitFactorRequired, ConstructionUnitContextMismatch,
    ConstructionUnitArithmeticError, convert_unit_quantity, quantity_in_base_unit,
)


def factor(**changes):
    values = dict(
        source_unit=UnitCode.L, target_unit=UnitCode.KG, factor=Decimal("1.6"),
        reference="material-specification:revision-2", version=2,
        context_key="tenant-a:material-1",
    )
    values.update(changes)
    return DocumentedUnitFactor(**values)


@pytest.mark.parametrize("amount,source,target,expected", [
    ("35", "CM", "M", "0.35"),
    ("350", "MM", "M", "0.350"),
    ("1", "M", "MM", "1000"),
    ("35", "MM", "CM", "3.5"),
    ("250", "ML", "L", "0.25"),
    ("1", "L", "ML", "1000"),
    ("1", "M3", "L", "1000"),
    ("1", "L", "M3", "0.001"),
    ("1", "ML", "M3", "0.000001"),
    ("1500", "G", "KG", "1.5"),
    ("1.25", "KG", "G", "1250"),
    ("-35", "CM", "M", "-0.35"),
    ("0", "MM", "M", "0"),
])
def test_standard_conversions_are_exact(amount, source, target, expected):
    result = convert_unit_quantity(Decimal(amount), source, target)
    assert result.quantity == Decimal(expected)
    assert result.source_quantity == Decimal(amount)
    assert result.source_unit == UnitCode(source) and result.target_unit == UnitCode(target)
    assert result.rule == "standard" and result.evidence is None


@pytest.mark.parametrize("codes", [("MM", "CM", "M"), ("ML", "L", "M3"), ("G", "KG")])
def test_all_standard_pairs_round_trip_without_losing_digits(codes):
    amount = Decimal("12345678901234567890.12345678901234567890")
    for source in codes:
        for target in codes:
            converted = convert_unit_quantity(amount, source, target)
            restored = convert_unit_quantity(converted.quantity, target, source)
            assert restored.quantity == amount


@pytest.mark.parametrize("unit", list(UnitCode))
def test_identity_conversion_preserves_every_canonical_unit(unit):
    amount = Decimal("12.3400")
    result = convert_unit_quantity(amount, unit, unit)
    assert result.quantity.as_tuple() == amount.as_tuple()
    assert result.multiplier == 1 and result.rule == "identity"


@pytest.mark.parametrize("source,base,expected", [
    ("CM", "M", "0.35"), ("M2", "M2", "35"),
    ("L", "L", "35"), ("ML", "L", "0.035"),
    ("G", "KG", "0.035"), ("PCS", "PCS", "35"), ("HOUR", "HOUR", "35"),
])
def test_base_normalization_uses_canonical_physical_units(source, base, expected):
    result = quantity_in_base_unit(Decimal("35"), source)
    assert result.target_unit == UnitCode(base)
    assert result.quantity == Decimal(expected)


@pytest.mark.parametrize("source,target", [
    ("L", "KG"), ("KG", "L"), ("M2", "M"), ("PACKAGE", "PCS"),
    ("ROLL", "M"), ("BAG", "KG"), ("SET", "PCS"), ("DAY", "HOUR"), ("HOUR", "DAY"),
])
def test_no_implicit_density_packaging_geometry_or_workday_factor(source, target):
    with pytest.raises(ConstructionUnitFactorRequired) as caught:
        convert_unit_quantity(Decimal("1"), source, target)
    assert caught.value.code == "UNIT_CONVERSION_FACTOR_REQUIRED"
    assert caught.value.message_key.startswith("construction.unit_conversion.")


@pytest.mark.parametrize("unit", ["PACKAGE", "ROLL", "BAG", "SET", "DAY"])
def test_contextual_units_have_no_guessed_base(unit):
    with pytest.raises(ConstructionUnitFactorRequired):
        quantity_in_base_unit(Decimal("1"), unit)


def test_documented_density_is_directional_versioned_and_resource_bound():
    evidence = factor()
    result = convert_unit_quantity(
        Decimal("5"), "L", "KG", documented_factor=evidence,
        context_key="tenant-a:material-1",
    )
    assert result.quantity == Decimal("8") and result.evidence is evidence
    reverse = factor(source_unit="KG", target_unit="L", factor=Decimal("0.625"))
    assert convert_unit_quantity(
        Decimal("8"), "KG", "L", documented_factor=reverse,
        context_key="tenant-a:material-1",
    ).quantity == Decimal("5")
    with pytest.raises(ConstructionUnitContextMismatch):
        convert_unit_quantity(
            Decimal("8"), "KG", "L", documented_factor=evidence,
            context_key="tenant-a:material-1",
        )


@pytest.mark.parametrize("context", [None, "", "tenant-b:material-1", "tenant-a:material-2"])
def test_factor_cannot_be_reused_for_another_context(context):
    with pytest.raises(ConstructionUnitContextMismatch):
        convert_unit_quantity(
            Decimal("5"), "L", "KG", documented_factor=factor(), context_key=context,
        )


@pytest.mark.parametrize("source,target,multiplier", [
    ("PACKAGE", "PCS", "6"), ("ROLL", "M", "25"), ("DAY", "HOUR", "8"),
])
def test_documented_material_and_calendar_coefficients_are_explicit(source, target, multiplier):
    evidence = factor(
        source_unit=source, target_unit=target, factor=Decimal(multiplier),
        reference="calendar:working-day" if source == "DAY" else "item:packaging",
        context_key="tenant-a:calendar-1" if source == "DAY" else "tenant-a:material-1",
    )
    result = convert_unit_quantity(
        Decimal("2"), source, target, documented_factor=evidence,
        context_key=evidence.context_key,
    )
    assert result.quantity == {"PACKAGE": Decimal("12"), "ROLL": Decimal("50"), "DAY": Decimal("16")}[source]


@pytest.mark.parametrize("changes", [
    {"factor": Decimal("0")}, {"factor": Decimal("-1")}, {"factor": Decimal("NaN")},
    {"factor": 1.6}, {"version": True}, {"version": 0}, {"reference": ""},
    {"reference": "source\nother"}, {"context_key": ""}, {"source_unit": "m"},
])
def test_malformed_coefficient_snapshot_is_rejected(changes):
    with pytest.raises(ConstructionUnitConversionError):
        factor(**changes)


@pytest.mark.parametrize("value", [None, True, 1, 0.1, "0.1", Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity")])
def test_input_is_finite_decimal_only(value):
    with pytest.raises(ConstructionUnitConversionError):
        convert_unit_quantity(value, "CM", "M")


@pytest.mark.parametrize("unit", ["m", " M", "M ", "UNKNOWN", None])
def test_unit_labels_and_unknown_codes_are_not_used_as_canonical_units(unit):
    with pytest.raises(ConstructionUnitConversionError):
        convert_unit_quantity(Decimal("1"), unit, "M")


def test_documented_factor_cannot_override_a_physical_definition():
    evidence = factor(source_unit="M", target_unit="CM", factor=Decimal("10"))
    with pytest.raises(ConstructionUnitConversionError):
        convert_unit_quantity(
            Decimal("1"), "M", "CM", documented_factor=evidence,
            context_key=evidence.context_key,
        )


def test_ambient_decimal_precision_rounding_and_flags_do_not_change_conversion():
    with localcontext() as context:
        context.prec = 2
        context.Emax = 2
        context.Emin = -2
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        flags = dict(context.flags)
        result = convert_unit_quantity(Decimal("12345678901234567890.123456789"), "M", "CM")
        assert result.quantity == Decimal("1234567890123456789012.3456789")
        assert context.prec == 2 and context.rounding == ROUND_DOWN
        assert dict(context.flags) == flags


def test_unrepresentable_conversion_is_an_error_and_never_infinity_or_silent_rounding():
    with pytest.raises(ConstructionUnitArithmeticError):
        convert_unit_quantity(Decimal((0, (1,), MAX_EMAX)), "M", "MM")


def test_snapshot_is_json_safe_exact_and_keeps_source_evidence_immutable():
    evidence = factor(factor=Decimal("4.0000"))
    result = convert_unit_quantity(
        Decimal("1.2500"), "L", "KG", documented_factor=evidence,
        context_key=evidence.context_key,
    )
    snapshot = json.loads(json.dumps(result.as_snapshot(), allow_nan=False))
    assert snapshot["source_quantity"] == "1.2500"
    assert Decimal(snapshot["quantity"]) == Decimal("5")
    assert snapshot["evidence"] == evidence.as_snapshot()
    assert snapshot["evidence"]["version"] == 2
    assert snapshot["evidence"]["factor"] == "4.0000"
    snapshot["evidence"]["factor"] = "999"
    assert evidence.factor == Decimal("4.0000")
    with pytest.raises(FrozenInstanceError):
        evidence.factor = Decimal("999")
