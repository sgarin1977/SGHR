"""Source-versioned unit display names; canonical codes/symbols stay unchanged."""
import json
from pathlib import Path

from services.construction.construction_units import UnitCode
from services.platform_catalog_localization import label_text


def unit_label_source():
    path = Path(__file__).resolve().parents[2] / "locales" / "construction_units.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    codes, version, languages = data["codes"], data["version"], data["labels"]
    if (
        type(version) is not int or version <= 0
        or not isinstance(codes, list) or len(codes) != len(UnitCode)
        or set(codes) != {unit.value for unit in UnitCode}
        or not isinstance(languages, dict) or "en" not in languages
    ):
        raise ValueError("Unit localization source is invalid.")
    translations = {}
    for language, values in languages.items():
        if (
            not isinstance(language, str) or not 1 <= len(language) <= 10
            or language != language.strip()
            or not isinstance(values, list) or len(values) != len(codes)
        ):
            raise ValueError("Unit localization language is invalid.")
        translations[language] = dict(zip(codes, (label_text(value) for value in values)))
    return version, translations["en"], translations
