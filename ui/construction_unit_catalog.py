"""Navigation for server-localized unit labels."""
LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Одиниці вимірювання", "Единицы измерения", "Units of measurement",
      "Unidades de medida", "Jednostki miary", "Maßeinheiten", "Maateenheden")
label("description", "Системний каталог одиниць вимірювання.",
      "Системный каталог единиц измерения.", "System unit catalog.",
      "Catálogo de unidades do sistema.", "Systemowy katalog jednostek.",
      "Systemkatalog der Maßeinheiten.", "Systeemcatalogus van maateenheden.")
label("refresh", "Оновити каталог", "Обновить каталог", "Refresh catalog",
      "Atualizar catálogo", "Odśwież katalog", "Katalog aktualisieren",
      "Catalogus vernieuwen")
label("settings", "До налаштувань", "К настройкам", "Back to settings",
      "Voltar às definições", "Wróć do ustawień", "Zu den Einstellungen",
      "Terug naar instellingen")
label("next", "Далі", "Далее", "Next", "Seguinte", "Dalej", "Weiter", "Volgende")
label("previous", "Назад", "Назад", "Previous", "Anterior",
      "Wstecz", "Zurück", "Vorige")


def tr(key, language):
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])


def unit_text(unit):
    text = unit["label"]
    symbol = unit["symbol"]
    return text + (" — " + symbol if symbol else "")
