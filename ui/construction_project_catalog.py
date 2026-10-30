"""Localized project catalog navigation."""
from ui.construction_project_card import tr as project_tr
from ui.construction_client_card import tr as client_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "🏗 Об’єкти", "🏗 Объекты", "🏗 Projects", "🏗 Obras",
      "🏗 Obiekty", "🏗 Projekte", "🏗 Projecten")
label("choose", "Оберіть об’єкт.", "Выберите объект.", "Choose a project.",
      "Escolha uma obra.", "Wybierz obiekt.", "Wählen Sie ein Projekt.", "Kies een project.")
label("empty", "Доступних об’єктів поки немає.", "Доступных объектов пока нет.",
      "No projects are available yet.", "Ainda não há obras disponíveis.",
      "Brak dostępnych obiektów.", "Noch keine Projekte verfügbar.",
      "Er zijn nog geen projecten beschikbaar.")
label("refresh_list", "Оновити список", "Обновить список", "Refresh list",
      "Atualizar lista", "Odśwież listę", "Liste aktualisieren", "Lijst verversen")
label("first", "На початок списку", "В начало списка", "First page",
      "Primeira página", "Pierwsza strona", "Erste Seite", "Eerste pagina")
label("next", "Далі", "Далее", "Next", "Seguinte", "Dalej", "Weiter", "Volgende")
label("back", "До списку об’єктів", "К списку объектов", "Back to projects",
      "Voltar às obras", "Wróć do obiektów", "Zur Projektliste", "Terug naar projecten")


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    if key in LABELS:
        return LABELS[key].get(language, LABELS[key]["ru"])
    return project_tr(key, language)


def project_label(item, language):
    return (
        str(item["name"])[:65] + " · "
        + client_tr("project_" + item["status"], language)
    )[:110]
