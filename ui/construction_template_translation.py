"""Original-preserving presentation of server-validated template texts."""
from html import escape

from ui.construction_project_translation import (
    LANGUAGES, tr as project_translation_label,
)

LABELS = {
    "PUBLISHED_LABEL": (
        "Переклад із шаблону",
        "Перевод из шаблона",
        "Translation supplied with the template",
        "Tradução incluída no modelo",
        "Tłumaczenie zawarte w szablonie",
        "Mit der Vorlage bereitgestellte Übersetzung",
        "Vertaling uit de sjabloon",
    ),
}


def tr(key, language):
    if key not in LABELS:
        return project_translation_label(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    index = LANGUAGES.index(code) if code in LANGUAGES else LANGUAGES.index("ru")
    return LABELS[key][index]


def escaped_clip(value, limit):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Template display text must be a string.")
    parts, size = [], 0
    for char in value:
        token = escape(char, quote=False)
        if size + len(token) > limit - 1:
            return "".join(parts) + "…"
        parts.append(token)
        size += len(token)
    return "".join(parts)


def field_text(item, field, language):
    if field not in ("title", "description"):
        raise ValueError("Unknown template display field.")
    original = item[field]
    if original is not None and not isinstance(original, str):
        raise ValueError("Original template text differs.")
    if field == "title" and (original is None or not original.strip()):
        raise ValueError("Original template title is required.")
    limit = 200 if field == "title" else 800
    fields = item.get("text_presentations")
    if fields is None:
        # Older presentation objects carry no verified translation context.
        return escaped_clip(original, limit)
    if type(fields) is not dict or set(fields) != {"title", "description"}:
        raise ValueError("Template translation fields differ.")
    row = fields[field]
    if not isinstance(row, dict):
        raise ValueError("Template translation metadata differs.")
    if (
        row.get("field_name") != field
        or row.get("target_language") != language
        or row.get("template_id") != item["template_id"]
        or row.get("version_id") != item["version_id"]
        or row.get("original_text") != original
        or row.get("display_text") != item["display_" + field]
    ):
        raise ValueError("Template translation context differs.")
    if original is None:
        if (
            row.get("source_version") is not None
            or row.get("source_language") is not None
            or row.get("display_text") is not None
            or row.get("origin") != "ORIGINAL"
            or row.get("translation_status") is not None
        ):
            raise ValueError("Absent template description metadata differs.")
        return ""
    if (
        type(row.get("source_version")) is not int
        or row["source_version"] != item["version"]
        or row.get("source_language") != item["source_language"]
    ):
        raise ValueError("Template source version differs.")
    display = row.get("display_text")
    if not isinstance(display, str):
        raise ValueError("Template display text is missing.")
    origin, status = row.get("origin"), row.get("translation_status")
    if origin == "ORIGINAL":
        if display != original or status is not None:
            raise ValueError("Original-only template presentation differs.")
        return escaped_clip(original, limit)
    if not display.strip():
        raise ValueError("Translated template text is empty.")
    if origin == "PUBLISHED_LABEL":
        if status is not None or row["source_language"] == language:
            raise ValueError("Published label cannot claim a verification status.")
        label = tr("PUBLISHED_LABEL", language)
    elif (
        origin == "CACHE" and status in ("VERIFIED", "AUTO_TRANSLATED")
        or origin == "GENERATED" and status == "AUTO_TRANSLATED"
    ):
        if row["source_language"] == language:
            raise ValueError("Translation source and target languages agree.")
        label = tr(status, language)
    else:
        raise ValueError("A current usable template translation is required.")
    text = escaped_clip(display, limit) + "\n" + label
    if display != original:
        text += "\n" + tr("original", language) + ": " + escaped_clip(
            original, 180 if field == "title" else 450,
        )
    return text


def template_heading(item, language):
    title = field_text(item, "title", language)
    description = field_text(item, "description", language)
    text = title + ("\n\n" + description if description else "")
    if len(text) > 2600:
        raise ValueError("Template heading is too large.")
    return text
