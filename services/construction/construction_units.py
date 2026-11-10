"""CON-013 canonical unit catalog from rev.12 section 10.1."""
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class UnitCode(str, Enum):
    MM = "MM"
    CM = "CM"
    M = "M"
    M2 = "M2"
    M3 = "M3"
    ML = "ML"
    L = "L"
    G = "G"
    KG = "KG"
    PCS = "PCS"
    PACKAGE = "PACKAGE"
    ROLL = "ROLL"
    BAG = "BAG"
    SET = "SET"
    HOUR = "HOUR"
    DAY = "DAY"


class UnitDimension(str, Enum):
    LENGTH = "length"
    AREA = "area"
    VOLUME = "volume"
    LIQUID_VOLUME = "liquid_volume"
    MASS = "mass"
    COUNT = "count"
    TIME = "time"


class ConstructionUnitValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ConstructionUnit:
    code: UnitCode
    dimension: UnitDimension
    symbol: str | None

    @property
    def label_key(self):
        return "construction.unit." + self.code.value.lower()


# Categories group the catalog; they do not authorize a conversion.
# Packaging, density and working-day coefficients are not invented here.
_UNITS = (
    ConstructionUnit(UnitCode.MM, UnitDimension.LENGTH, "mm"),
    ConstructionUnit(UnitCode.CM, UnitDimension.LENGTH, "cm"),
    ConstructionUnit(UnitCode.M, UnitDimension.LENGTH, "m"),
    ConstructionUnit(UnitCode.M2, UnitDimension.AREA, "m²"),
    ConstructionUnit(UnitCode.M3, UnitDimension.VOLUME, "m³"),
    ConstructionUnit(UnitCode.ML, UnitDimension.LIQUID_VOLUME, "ml"),
    ConstructionUnit(UnitCode.L, UnitDimension.LIQUID_VOLUME, "l"),
    ConstructionUnit(UnitCode.G, UnitDimension.MASS, "g"),
    ConstructionUnit(UnitCode.KG, UnitDimension.MASS, "kg"),
    ConstructionUnit(UnitCode.PCS, UnitDimension.COUNT, None),
    ConstructionUnit(UnitCode.PACKAGE, UnitDimension.COUNT, None),
    ConstructionUnit(UnitCode.ROLL, UnitDimension.COUNT, None),
    ConstructionUnit(UnitCode.BAG, UnitDimension.COUNT, None),
    ConstructionUnit(UnitCode.SET, UnitDimension.COUNT, None),
    ConstructionUnit(UnitCode.HOUR, UnitDimension.TIME, None),
    ConstructionUnit(UnitCode.DAY, UnitDimension.TIME, None),
)
_BY_CODE = MappingProxyType({unit.code.value: unit for unit in _UNITS})


def list_construction_units():
    return _UNITS


def require_construction_unit(code):
    if not isinstance(code, str) or code not in _BY_CODE:
        raise ConstructionUnitValidationError("Canonical unit code is required.")
    return _BY_CODE[code]
