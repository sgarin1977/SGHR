"""Translated area names are display-only and remain pinned to canonical UUIDs."""
from ui.construction_client_card import clip
from ui.construction_project_translation import project_field_text, translation_notice


def translated_name(result, item, language):
    metadata = result.get("translations")
    if metadata is None:
        return None
    if (
        not isinstance(metadata, dict) or metadata.get("target_language") != language
        or result.get("translation_unavailable", False)
        or not isinstance(metadata.get("items"), list)
    ):
        raise ValueError("Area display context differs.")
    matches = [row for row in metadata["items"] if row.get("area_id") == item["id"]]
    if len(matches) != 1:
        raise ValueError("Exact area translation is unavailable.")
    row = matches[0]
    if (
        row.get("target_language") != language
        or row.get("original_text") != item["name"]
        or type(row.get("source_version")) is not int
        or row["source_version"] != item["row_version"]
    ):
        raise ValueError("Area source context differs.")
    return row


def area_name_text(result, item, language):
    row = translated_name(result, item, language)
    if row is None:
        return clip(item["name"], 180)
    return project_field_text({
        "item": {"name": item["name"], "row_version": item["row_version"]},
        "translations": {
            "target_language": language, "source_version": row["source_version"], "name": row,
        },
    }, "name", language)


def area_button_name(result, item, language):
    row = translated_name(result, item, language)
    value = item["name"] if row is None else row.get("display_text")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("An area display name is required.")
    return value[:55]


def project_heading(result, language):
    project = result.get("project_presentation")
    if project is None:
        return clip(result["project_name"], 180)
    return project_field_text(project, "name", language)


def area_translation_notice(result, language):
    notice = translation_notice(result, language)
    project = result.get("project_presentation")
    return notice or (translation_notice(project, language) if project is not None else None)
