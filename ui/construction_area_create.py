"""Universal area creation, seven interface languages."""
from ui.construction_project_areas import tr as area_tr
from ui.texts import t

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "create": (
        "Створити область", "Создать область", "Create area", "Criar área",
        "Utwórz obszar", "Bereich erstellen", "Gebied aanmaken",
    ),
    "title": (
        "Нова область об’єкта", "Новая область объекта", "New project area",
        "Nova área da obra", "Nowy obszar obiektu", "Neuer Projektbereich",
        "Nieuw projectgebied",
    ),
    "type": (
        "Обери тип області.", "Выберите тип области.", "Choose an area type.",
        "Escolha o tipo de área.", "Wybierz typ obszaru.",
        "Bereichstyp wählen.", "Kies een gebiedstype.",
    ),
    "name": (
        "Введи назву області.", "Введите название области.", "Enter the area name.",
        "Introduza o nome da área.", "Wpisz nazwę obszaru.",
        "Bereichsnamen eingeben.", "Voer de gebiedsnaam in.",
    ),
    "created": (
        "✅ Область створено.", "✅ Область создана.", "✅ Area created.",
        "✅ Área criada.", "✅ Obszar utworzony.", "✅ Bereich erstellt.",
        "✅ Gebied aangemaakt.",
    ),
    "list": (
        "До списку областей", "К списку областей", "Back to areas",
        "Voltar às áreas", "Wróć do obszarów", "Zur Bereichsliste",
        "Terug naar gebieden",
    ),
    "expired": (
        "Строк безпечного повтору минув. Перевір список областей перед новим створенням.",
        "Срок безопасного повтора истёк. Проверьте список областей перед новым созданием.",
        "The safe retry window has expired. Check the area list before creating again.",
        "O prazo de repetição segura expirou. Verifique as áreas antes de criar novamente.",
        "Okres bezpiecznego ponawiania minął. Sprawdź obszary przed ponownym utworzeniem.",
        "Der sichere Wiederholungszeitraum ist abgelaufen. Vor erneutem Erstellen die Bereiche prüfen.",
        "De veilige herhaalperiode is verstreken. Controleer de gebieden voordat je opnieuw aanmaakt.",
    ),
    "pending_other": (
        "Спочатку перевір результат попереднього створення області.",
        "Сначала проверьте результат предыдущего создания области.",
        "Check the previous area creation result first.",
        "Verifique primeiro o resultado da criação anterior.",
        "Najpierw sprawdź wynik poprzedniego tworzenia obszaru.",
        "Zuerst das Ergebnis der vorherigen Bereichserstellung prüfen.",
        "Controleer eerst het resultaat van de vorige gebiedsaanmaak.",
    ),
}


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    if key in LABELS:
        return LABELS[key][LANGUAGES.index(language) if language in LANGUAGES else 1]
    if key in ("ROOM", "PROJECT_GENERAL", "EXTERIOR", "OTHER"):
        return area_tr(key, language)
    return t("construction_lead_form_" + key, language)
