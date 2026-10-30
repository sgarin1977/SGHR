"""Localized planned project activities."""
from ui.construction_activity import activity_label, display_deadline
from ui.construction_team import tr as common

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "title": (
        "Заплановані дії об’єкта", "Запланированные действия объекта",
        "Planned project activities", "Atividades planeadas da obra",
        "Zaplanowane działania obiektu", "Geplante Projektaktivitäten",
        "Geplande projectactiviteiten",
    ),
    "empty": (
        "Запланованих дій поки немає.", "Запланированных действий пока нет.",
        "No planned activities yet.", "Ainda não há atividades planeadas.",
        "Brak zaplanowanych działań.", "Noch keine geplanten Aktivitäten.",
        "Nog geen geplande activiteiten.",
    ),
    "refresh": (
        "Оновити список", "Обновить список", "Refresh list",
        "Atualizar lista", "Odśwież listę", "Liste aktualisieren", "Lijst vernieuwen",
    ),
    "first": (
        "На початок списку", "В начало списка", "First page",
        "Primeira página", "Pierwsza strona", "Erste Seite", "Eerste pagina",
    ),
    "back": (
        "До картки об’єкта", "К карточке объекта", "Project card",
        "Ficha da obra", "Karta obiektu", "Projektkarte", "Projectkaart",
    ),
}

def tr(key, language):
    if key not in LABELS:
        return common(key, language)
    language = (language or "ru").lower().split("-", 1)[0]
    index = LANGUAGES.index(language) if language in LANGUAGES else 1
    return LABELS[key][index]

def page_text(result, language):
    lines = [
        tr("title", language), str(result["organization"])[:100],
        str(result["project_name"])[:100],
    ]
    if not result["items"]:
        lines.append(tr("empty", language))
    for index, item in enumerate(result["items"], 1):
        text = (
            str(index) + ". " + activity_label(item["type"], language)
            + "\n" + display_deadline(item["due_at"], item["timezone"])
        )
        if item["comment"]:
            text += "\n" + str(item["comment"])[:160]
        lines.append(text)
    text = "\n\n".join(lines)
    if len(text) > 3900:
        raise ValueError("Project activity presentation is too large.")
    return text
