"""Region settings labels in the seven supported interface languages."""
from ui.construction_stage2_settings import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Країна й часовий пояс", "Страна и часовой пояс",
      "Country and timezone", "País e fuso horário", "Kraj i strefa czasowa",
      "Land und Zeitzone", "Land en tijdzone")
label("country", "Країна", "Страна", "Country", "País", "Kraj", "Land", "Land")
label("timezone", "Часовий пояс", "Часовой пояс", "Timezone", "Fuso horário",
      "Strefa czasowa", "Zeitzone", "Tijdzone")
label("empty", "Регіон організації ще не налаштовано.",
      "Регион организации ещё не настроен.", "Organization region is not configured.",
      "A região da organização ainda não está configurada.",
      "Region organizacji nie jest jeszcze skonfigurowany.",
      "Die Organisationsregion ist noch nicht konfiguriert.",
      "De organisatieregio is nog niet ingesteld.")
label("edit", "Налаштувати регіон", "Настроить регион", "Configure region",
      "Configurar região", "Ustaw region", "Region einstellen", "Regio instellen")
label("country_prompt",
      "Введіть дволітерний код країни, наприклад UA або PT. Доступність перевірить Platform.",
      "Введите двухбуквенный код страны, например UA или PT. Доступность проверит Platform.",
      "Enter a two-letter country code, such as UA or PT. Platform checks availability.",
      "Introduza o código do país de duas letras, como UA ou PT. A Platform verifica a disponibilidade.",
      "Wpisz dwuliterowy kod kraju, np. UA lub PT. Platform sprawdzi dostępność.",
      "Geben Sie den zweistelligen Ländercode ein, etwa UA oder PT. Platform prüft die Verfügbarkeit.",
      "Voer de tweeletterige landcode in, zoals UA of PT. Platform controleert de beschikbaarheid.")
label("timezone_prompt",
      "Введіть часовий пояс IANA, наприклад Europe/Kyiv або Europe/Lisbon.",
      "Введите часовой пояс IANA, например Europe/Kyiv или Europe/Lisbon.",
      "Enter an IANA timezone, such as Europe/Kyiv or Europe/Lisbon.",
      "Introduza um fuso horário IANA, como Europe/Kyiv ou Europe/Lisbon.",
      "Wpisz strefę czasową IANA, np. Europe/Kyiv lub Europe/Lisbon.",
      "Geben Sie eine IANA-Zeitzone ein, etwa Europe/Kyiv oder Europe/Lisbon.",
      "Voer een IANA-tijdzone in, zoals Europe/Kyiv of Europe/Lisbon.")
label("review", "Перевірте країну й часовий пояс перед збереженням.",
      "Проверьте страну и часовой пояс перед сохранением.",
      "Review the country and timezone before saving.",
      "Reveja o país e o fuso horário antes de guardar.",
      "Sprawdź kraj i strefę czasową przed zapisaniem.",
      "Prüfen Sie Land und Zeitzone vor dem Speichern.",
      "Controleer land en tijdzone voordat u opslaat.")
label("saved", "Регіон організації збережено.", "Регион организации сохранён.",
      "Organization region saved.", "Região da organização guardada.",
      "Zapisano region organizacji.", "Organisationsregion gespeichert.",
      "Organisatieregio opgeslagen.")
label("unchanged", "Організація вже має ці налаштування.",
      "Организация уже использует эти настройки.",
      "The organization already uses these settings.",
      "A organização já utiliza estas definições.",
      "Organizacja już używa tych ustawień.",
      "Die Organisation verwendet diese Einstellungen bereits.",
      "De organisatie gebruikt deze instellingen al.")
label("invalid_region",
      "Країна недоступна або регіон некоректний. Оновіть налаштування та введіть значення заново.",
      "Страна недоступна или регион некорректен. Обновите настройки и введите значения заново.",
      "The country is unavailable or the region is invalid. Refresh settings and enter the values again.",
      "O país está indisponível ou a região é inválida. Atualize e introduza os valores novamente.",
      "Kraj jest niedostępny lub region jest nieprawidłowy. Odśwież ustawienia i wpisz wartości ponownie.",
      "Das Land ist nicht verfügbar oder die Region ist ungültig. Aktualisieren Sie die Einstellungen und geben Sie die Werte erneut ein.",
      "Het land is niet beschikbaar of de regio is ongeldig. Vernieuw de instellingen en voer de waarden opnieuw in.")
label("uncertain",
      "Результат збереження не підтверджено. Спочатку оновіть налаштування й перевірте поточний регіон.",
      "Результат сохранения не подтверждён. Сначала обновите настройки и проверьте текущий регион.",
      "The save result is unconfirmed. Refresh settings and check the current region first.",
      "O resultado não foi confirmado. Atualize as definições e verifique primeiro a região atual.",
      "Wynik zapisu jest niepotwierdzony. Najpierw odśwież ustawienia i sprawdź bieżący region.",
      "Das Speicherergebnis ist unbestätigt. Aktualisieren Sie zuerst die Einstellungen und prüfen Sie die aktuelle Region.",
      "Het opslagresultaat is niet bevestigd. Vernieuw eerst de instellingen en controleer de huidige regio.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])


def region_text(item, language):
    if not item["configured"]:
        return tr("empty", language)
    return (
        tr("country", language) + ": " + item["country_code"]
        + "\n" + tr("timezone", language) + ": " + item["timezone"]
    )
