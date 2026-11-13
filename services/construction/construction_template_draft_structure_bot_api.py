"""Data-driven original structure from the existing exact-draft GET."""
from services.construction.construction_template_draft_bot_api import TemplateDraftBotApi
from services.construction.construction_unit_catalog_bot_api import ConstructionUnitCatalogBotApi
from services.construction.construction_language_bot_api import problem


class TemplateDraftStructureBotApi(TemplateDraftBotApi):
    async def read_structure(self, *, template_id, version_id, **binding):
        result = await self.read_draft(
            template_id=template_id, version_id=version_id,
            include_content=True, **binding,
        )
        item = result["item"]
        try:
            payload = item["definition"]
            labels = payload["labels"][item["source_language"]]
            references = item["section_references"]
            if len(references) != len(payload["sections"]):
                raise ValueError("Section projection differs.")
            sections = []
            for body, reference in zip(payload["sections"], references):
                if body["code"] != reference["code"] or len(body["questions"]) != len(reference["questions"]):
                    raise ValueError("Section references differ.")
                questions = []
                for question, child in zip(body["questions"], reference["questions"]):
                    if question["code"] != child["code"]:
                        raise ValueError("Question reference differs.")
                    questions.append({
                        "id": child["id"], "row_version": child["row_version"],
                        "label": labels[question["label_key"]],
                        "question_type": question["question_type"],
                        "active": question["active"], "required": question["required"],
                        "unit_code": question["unit_code"],
                        "has_condition": question["show_if"] is not None,
                        "option_count": len(question["options"]),
                    })
                sections.append({
                    "id": reference["id"], "row_version": reference["row_version"],
                    "label": labels[body["label_key"]], "active": body["active"],
                    "questions": questions,
                })
            metadata = {
                key: value for key, value in item.items()
                if key not in ("definition", "section_references", "title", "description")
            }
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        return {**result, "item": {**metadata, "sections": sections}}

    async def unit_option(self, *, code, expected_language, **binding):
        result = await ConstructionUnitCatalogBotApi(self.entry).read(**binding)
        if result["language"] != expected_language:
            raise problem(409, "LANGUAGE_CONTEXT_CHANGED")
        item = next(
            (unit for unit in result["item"]["units"] if unit["code"] == code),
            None,
        )
        if item is None:
            raise problem()
        return item
