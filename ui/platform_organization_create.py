"""Organization creation presentation; seven interface languages."""
from ui.texts import t
from ui.construction_project_areas import tr as navigation_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "button": (
        "Створити організацію", "Создать организацию", "Create organization",
        "Criar organização", "Utwórz organizację", "Organisation erstellen",
        "Organisatie aanmaken",
    ),
    "title": (
        "Нова організація", "Новая организация", "New organization",
        "Nova organização", "Nowa organizacja", "Neue Organisation",
        "Nieuwe organisatie",
    ),
    "name": (
        "Введи назву організації.", "Введите название организации.",
        "Enter the organization name.", "Introduza o nome da organização.",
        "Wpisz nazwę organizacji.", "Organisationsnamen eingeben.",
        "Voer de organisatienaam in.",
    ),
    "language": (
        "Обери основну мову організації.", "Выберите основной язык организации.",
        "Choose the organization's default language.",
        "Escolha o idioma principal da organização.",
        "Wybierz domyślny język organizacji.",
        "Standardsprache der Organisation wählen.",
        "Kies de standaardtaal van de organisatie.",
    ),
    "currency": (
        "Введи код валюти з трьох літер, наприклад EUR або UAH.",
        "Введите код валюты из трёх букв, например EUR или UAH.",
        "Enter a three-letter currency code, for example EUR or UAH.",
        "Introduza o código de moeda de três letras, por exemplo EUR ou UAH.",
        "Wpisz trzyliterowy kod waluty, np. EUR lub UAH.",
        "Dreistelligen Währungscode eingeben, zum Beispiel EUR oder UAH.",
        "Voer een valutacode van drie letters in, bijvoorbeeld EUR of UAH.",
    ),
    "name_label": (
        "Назва", "Название", "Name", "Nome", "Nazwa", "Name", "Naam",
    ),
    "language_label": (
        "Мова", "Язык", "Language", "Idioma", "Język", "Sprache", "Taal",
    ),
    "currency_label": (
        "Валюта", "Валюта", "Currency", "Moeda", "Waluta", "Währung", "Valuta",
    ),
    "created": (
        "✅ Організацію створено.", "✅ Организация создана.",
        "✅ Organization created.", "✅ Organização criada.",
        "✅ Organizacja utworzona.", "✅ Organisation erstellt.",
        "✅ Organisatie aangemaakt.",
    ),
    "open": (
        "Відкрити Construction", "Открыть Construction", "Open Construction",
        "Abrir Construction", "Otwórz Construction", "Construction öffnen",
        "Construction openen",
    ),
    "organizations": (
        "До списку організацій", "К списку организаций", "Back to organizations",
        "Voltar às organizações", "Wróć do organizacji", "Zur Organisationsliste",
        "Terug naar organisaties",
    ),
    "rejected": (
        "Запит відхилено. Перевір дані та введи їх заново.",
        "Запрос отклонён. Проверьте данные и введите их заново.",
        "The request was rejected. Check the details and enter them again.",
        "O pedido foi rejeitado. Verifique os dados e introduza-os novamente.",
        "Żądanie odrzucono. Sprawdź dane i wprowadź je ponownie.",
        "Die Anfrage wurde abgelehnt. Angaben prüfen und erneut eingeben.",
        "Het verzoek is afgewezen. Controleer de gegevens en voer ze opnieuw in.",
    ),
    "expired": (
        "Строк безпечного повтору минув. Перевір список організацій перед новим створенням.",
        "Срок безопасного повтора истёк. Проверьте список организаций перед новым созданием.",
        "The safe retry window has expired. Check the organization list before creating again.",
        "O prazo de repetição segura expirou. Verifique as organizações antes de criar novamente.",
        "Okres bezpiecznego ponawiania minął. Sprawdź organizacje przed ponownym utworzeniem.",
        "Der sichere Wiederholungszeitraum ist abgelaufen. Vor erneutem Erstellen die Organisationen prüfen.",
        "De veilige herhaalperiode is verstreken. Controleer de organisaties voordat je opnieuw aanmaakt.",
    ),
    "finish": (
        "Завершити спробу", "Завершить попытку", "Finish this attempt",
        "Terminar esta tentativa", "Zakończ tę próbę", "Versuch beenden",
        "Deze poging beëindigen",
    ),
}
LANGUAGE_NAMES = {
    "uk": "Українська", "ru": "Русский", "en": "English",
    "pt": "Português", "pl": "Polski", "de": "Deutsch", "nl": "Nederlands",
    "es": "Español", "fr": "Français", "it": "Italiano",
}


def language_name(code):
    return LANGUAGE_NAMES.get(code, code)


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    if key in LABELS:
        index = LANGUAGES.index(language) if language in LANGUAGES else 1
        return LABELS[key][index]
    if key in ("previous", "next"):
        return navigation_tr(key, language)
    return t("construction_lead_form_" + key, language)
