"""Universal project-area list presentation."""
from ui.construction_area_translation import (
    area_name_text, project_heading, area_translation_notice,
)
LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}

def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))

label("title", "Області об’єкта", "Области объекта", "Project areas",
      "Áreas da obra", "Obszary obiektu", "Projektbereiche", "Projectgebieden")
label("empty", "Областей об’єкта поки немає.", "Областей объекта пока нет.",
      "No project areas yet.", "Ainda não há áreas nesta obra.",
      "Brak obszarów obiektu.", "Noch keine Projektbereiche.",
      "Nog geen projectgebieden.")
label("refresh", "Оновити список", "Обновить список", "Refresh list",
      "Atualizar lista", "Odśwież listę", "Liste aktualisieren", "Lijst verversen")
label("next", "Далі", "Далее", "Next", "Seguinte", "Dalej", "Weiter", "Volgende")
label("previous", "Назад", "Назад", "Previous", "Anterior", "Wstecz", "Zurück", "Vorige")
label("back", "До картки об’єкта", "К карточке объекта", "Back to project",
      "Voltar à obra", "Wróć do obiektu", "Zur Projektkarte", "Terug naar project")
label("ROOM", "Приміщення", "Помещение", "Room", "Divisão",
      "Pomieszczenie", "Raum", "Ruimte")
label("PROJECT_GENERAL", "Об’єкт загалом", "Объект в целом", "Whole project",
      "Obra em geral", "Cały obiekt", "Gesamtprojekt", "Gehele project")
label("EXTERIOR", "Зовнішня зона", "Наружная зона", "Exterior",
      "Exterior", "Obszar zewnętrzny", "Außenbereich", "Buitengebied")
label("OTHER", "Інша область", "Другая область", "Other area",
      "Outra área", "Inny obszar", "Anderer Bereich", "Ander gebied")
label("draft", "Чернетка", "Черновик", "Draft", "Rascunho",
      "Wersja robocza", "Entwurf", "Concept")
label("measured", "Обміряно", "Обмерено", "Measured", "Medido",
      "Zmierzone", "Aufgemessen", "Ingemeten")
label("confirmed", "Підтверджено", "Подтверждено", "Confirmed", "Confirmado",
      "Potwierdzone", "Bestätigt", "Bevestigd")

label("create", "Створити область", "Создать область", "Create area",
      "Criar área", "Utwórz obszar", "Bereich erstellen", "Gebied aanmaken")

def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])

def areas_text(result, *, offset, language):
    lines = [
        tr("title", language),
        str(result["organization"])[:120],
        project_heading(result, language),
    ]
    items = result["items"]
    if not items:
        lines.append(tr("empty", language))
    else:
        lines.append(f"{offset + 1}–{min(offset + 5, len(items))} / {len(items)}")
        for index, item in enumerate(items[offset:offset + 5], start=offset + 1):
            lines.append(
                str(index) + ". " + area_name_text(result, item, language) + "\n"
                + tr(item["area_type"], language) + " · "
                + tr(item["status"], language)
            )
    notice = area_translation_notice(result, language)
    if notice:
        lines.append(notice)
    text = "\n\n".join(lines)
    if len(text) > 3800:
        raise ValueError("Area presentation is too large.")
    return text
