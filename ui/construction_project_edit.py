"""Project edit labels; shared generic form wording uses existing translations."""
from ui.construction_client_edit import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "edit": (
        "Редагувати об’єкт", "Редактировать объект", "Edit project",
        "Editar obra", "Edytuj obiekt", "Projekt bearbeiten", "Project bewerken",
    ),
    "name": (
        "Назва об’єкта", "Название объекта", "Project name",
        "Nome da obra", "Nazwa obiektu", "Projektname", "Projectnaam",
    ),
    "address_raw": (
        "Адреса об’єкта", "Адрес объекта", "Project address",
        "Morada da obra", "Adres obiektu", "Projektadresse", "Projectadres",
    ),
    "comment": (
        "Коментар", "Комментарий", "Comment", "Comentário",
        "Komentarz", "Kommentar", "Opmerking",
    ),
    "back": (
        "Картка об’єкта", "Карточка объекта", "Project card",
        "Ficha da obra", "Karta obiektu", "Projektkarte", "Projectkaart",
    ),
    "saved": (
        "✅ Дані об’єкта збережено.", "✅ Данные объекта сохранены.",
        "✅ Project data saved.", "✅ Dados da obra guardados.",
        "✅ Dane obiektu zapisane.", "✅ Projektdaten gespeichert.",
        "✅ Projectgegevens opgeslagen.",
    ),
    "address_notice": (
        "Після зміни адреси її попередню перевірку буде скинуто.",
        "После изменения адреса потребуется проверить заново.",
        "Changing the address resets its previous verification.",
        "Alterar a morada repõe a verificação anterior.",
        "Zmiana adresu resetuje jego poprzednią weryfikację.",
        "Eine Adressänderung setzt die bisherige Prüfung zurück.",
        "Een adreswijziging maakt de eerdere verificatie ongedaan.",
    ),
}

def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    language = (language or "ru").lower().split("-", 1)[0]
    index = LANGUAGES.index(language) if language in LANGUAGES else 1
    return LABELS[key][index]
