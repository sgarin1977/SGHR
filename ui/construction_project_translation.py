"""Display translations separately; canonical project fields stay unchanged."""
from ui.construction_client_card import clip

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "original": (
        "Оригінал", "Оригинал", "Original", "Original",
        "Oryginał", "Original", "Origineel",
    ),
    "AUTO_TRANSLATED": (
        "Автоматичний переклад", "Автоматический перевод", "Automatic translation",
        "Tradução automática", "Tłumaczenie automatyczne",
        "Automatische Übersetzung", "Automatische vertaling",
    ),
    "VERIFIED": (
        "Перевірений переклад", "Проверенный перевод", "Verified translation",
        "Tradução verificada", "Zweryfikowane tłumaczenie",
        "Geprüfte Übersetzung", "Geverifieerde vertaling",
    ),
    "unavailable": (
        "Переклад тимчасово недоступний. Показано оригінал.",
        "Перевод временно недоступен. Показан оригинал.",
        "Translation is temporarily unavailable. The original is shown.",
        "A tradução está temporariamente indisponível. É apresentado o original.",
        "Tłumaczenie jest tymczasowo niedostępne. Wyświetlono oryginał.",
        "Die Übersetzung ist vorübergehend nicht verfügbar. Das Original wird angezeigt.",
        "De vertaling is tijdelijk niet beschikbaar. Het origineel wordt getoond.",
    ),
}


def tr(key, language):
    code = (language or "ru").lower().split("-", 1)[0]
    index = LANGUAGES.index(code) if code in LANGUAGES else LANGUAGES.index("ru")
    return LABELS[key][index]


def translation_notice(result, language):
    unavailable = result.get("translation_unavailable", False)
    if type(unavailable) is not bool:
        raise ValueError("Invalid translation availability.")
    if unavailable and result.get("translations") is not None:
        raise ValueError("Unavailable translation cannot claim translated content.")
    return tr("unavailable", language) if unavailable else None


def project_field_text(result, field, language):
    if field not in ("name", "comment"):
        raise ValueError("Unknown display field.")
    original = result["item"][field]
    limit = 180 if field == "name" else 600
    metadata = result.get("translations")
    if metadata is None:
        return clip(original, limit)
    if (
        not isinstance(metadata, dict)
        or metadata.get("target_language") != language
        or metadata.get("source_version") != result["item"]["row_version"]
        or type(metadata.get("source_version")) is not int
        or result.get("translation_unavailable", False)
    ):
        raise ValueError("Project display context differs.")
    text = metadata[field]
    if not isinstance(text, dict) or text.get("original_text") != original:
        raise ValueError("Original project text differs.")
    status, display = text.get("translation_status"), text.get("display_text")
    if status is None:
        if display != original:
            raise ValueError("Original-only presentation differs.")
        return clip(original, limit)
    if status not in ("VERIFIED", "AUTO_TRANSLATED") or not isinstance(display, str) or not display.strip():
        raise ValueError("A usable translation is required.")
    rendered = clip(display, limit) + "\n" + tr(status, language)
    if display != original:
        rendered += "\n" + tr("original", language) + ": " + clip(
            original, 180 if field == "name" else 300,
        )
    return rendered
