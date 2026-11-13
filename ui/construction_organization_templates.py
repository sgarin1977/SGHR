"""Organization template labels; content comes from the localized API."""
from ui.construction_stage2_settings import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Шаблони організації", "Шаблоны организации",
      "Organization templates", "Modelos da organização",
      "Szablony organizacji", "Organisationsvorlagen", "Organisatiesjablonen")
label("empty", "Опублікованих шаблонів організації поки немає.",
      "Опубликованных шаблонов организации пока нет.",
      "No published organization templates yet.",
      "Ainda não existem modelos publicados da organização.",
      "Brak opublikowanych szablonów organizacji.",
      "Es gibt noch keine veröffentlichten Organisationsvorlagen.",
      "Er zijn nog geen gepubliceerde organisatiesjablonen.")
label("active", "Увімкнений", "Включён", "Enabled", "Ativo", "Włączony", "Aktiviert", "Ingeschakeld")
label("inactive", "Вимкнений", "Выключен", "Disabled", "Inativo", "Wyłączony", "Deaktiviert", "Uitgeschakeld")
label("version", "Опублікована версія", "Опубликованная версия", "Published version",
      "Versão publicada", "Opublikowana wersja", "Veröffentlichte Version", "Gepubliceerde versie")
label("sections", "Розділів", "Разделов", "Sections", "Secções", "Sekcje", "Abschnitte", "Secties")
label("questions", "Питань", "Вопросов", "Questions", "Perguntas", "Pytania", "Fragen", "Vragen")
label("list", "До списку шаблонів", "К списку шаблонов", "Back to templates",
      "Voltar aos modelos", "Wróć do szablonów", "Zur Vorlagenliste", "Terug naar sjablonen")
label("refresh_version", "Оновити цю версію", "Обновить эту версию", "Refresh this version",
      "Atualizar esta versão", "Odśwież tę wersję", "Diese Version aktualisieren", "Deze versie vernieuwen")
label("denied", "Перегляд шаблонів недоступний для поточного доступу.",
      "Просмотр шаблонов недоступен при текущем доступе.",
      "Your current access does not allow viewing these templates.",
      "O seu acesso atual não permite consultar estes modelos.",
      "Bieżący dostęp nie pozwala na przeglądanie tych szablonów.",
      "Ihr aktueller Zugriff erlaubt keine Ansicht dieser Vorlagen.",
      "Uw huidige toegang staat het bekijken van deze sjablonen niet toe.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])
