"""Organizational area edits in seven interface languages."""
from ui.construction_area_create import tr as create_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "title": (
        "Редагування області об’єкта", "Редактирование области объекта",
        "Edit project area", "Editar área da obra", "Edytuj obszar obiektu",
        "Projektbereich bearbeiten", "Projectgebied bewerken",
    ),
    "edit": (
        "Редагувати", "Редактировать", "Edit", "Editar",
        "Edytuj", "Bearbeiten", "Bewerken",
    ),
    "name": (
        "Назва області", "Название области", "Area name", "Nome da área",
        "Nazwa obszaru", "Bereichsname", "Gebiedsnaam",
    ),
    "order": (
        "Порядок у списку", "Порядок в списке", "List order",
        "Ordem na lista", "Kolejność na liście", "Listenreihenfolge",
        "Lijstvolgorde",
    ),
    "choose": (
        "Обери поле для зміни.", "Выберите поле для изменения.",
        "Choose a field to change.", "Escolha o campo a alterar.",
        "Wybierz pole do zmiany.", "Zu änderndes Feld wählen.",
        "Kies een veld om te wijzigen.",
    ),
    "enter_name": (
        "Введи нову назву області.", "Введите новое название области.",
        "Enter the new area name.", "Introduza o novo nome da área.",
        "Wpisz nową nazwę obszaru.", "Neuen Bereichsnamen eingeben.",
        "Voer de nieuwe gebiedsnaam in.",
    ),
    "enter_order": (
        "Введи ціле число від −2147483648 до 2147483647. Менші значення відображаються раніше; порожній порядок — наприкінці.",
        "Введите целое число от −2147483648 до 2147483647. Меньшие значения отображаются раньше; пустой порядок — в конце.",
        "Enter an integer from −2147483648 to 2147483647. Lower values appear first; an unset order appears last.",
        "Introduza um inteiro de −2147483648 a 2147483647. Valores menores aparecem primeiro; sem ordem aparece no fim.",
        "Wpisz liczbę całkowitą od −2147483648 do 2147483647. Mniejsze wartości są wcześniej; brak kolejności jest na końcu.",
        "Ganze Zahl von −2147483648 bis 2147483647 eingeben. Kleinere Werte stehen zuerst; ohne Reihenfolge am Ende.",
        "Voer een geheel getal van −2147483648 tot 2147483647 in. Lagere waarden staan eerst; zonder volgorde achteraan.",
    ),
    "clear": (
        "Без заданого порядку", "Без заданного порядка", "Unset order",
        "Sem ordem definida", "Bez ustalonej kolejności",
        "Ohne feste Reihenfolge", "Zonder vaste volgorde",
    ),
    "unset": (
        "Не задано", "Не задан", "Unset", "Não definida",
        "Nie ustawiono", "Nicht festgelegt", "Niet ingesteld",
    ),
    "review": (
        "Перевір зміну перед збереженням.", "Проверьте изменение перед сохранением.",
        "Review the change before saving.", "Reveja a alteração antes de guardar.",
        "Sprawdź zmianę przed zapisaniem.", "Änderung vor dem Speichern prüfen.",
        "Controleer de wijziging voordat je opslaat.",
    ),
    "save": (
        "Зберегти зміну", "Сохранить изменение", "Save change",
        "Guardar alteração", "Zapisz zmianę", "Änderung speichern",
        "Wijziging opslaan",
    ),
    "fields": (
        "Обрати інше поле", "Выбрать другое поле", "Choose another field",
        "Escolher outro campo", "Wybierz inne pole",
        "Anderes Feld wählen", "Kies een ander veld",
    ),
    "saved": (
        "✅ Зміну збережено.", "✅ Изменение сохранено.", "✅ Change saved.",
        "✅ Alteração guardada.", "✅ Zmiana zapisana.",
        "✅ Änderung gespeichert.", "✅ Wijziging opgeslagen.",
    ),
    "conflict": (
        "Область змінилася. Онови список і відкрий редагування заново.",
        "Область изменилась. Обновите список и откройте редактирование заново.",
        "The area changed. Refresh the list and open the editor again.",
        "A área foi alterada. Atualize a lista e abra novamente o editor.",
        "Obszar się zmienił. Odśwież listę i otwórz edytor ponownie.",
        "Der Bereich wurde geändert. Liste aktualisieren und Editor erneut öffnen.",
        "Het gebied is gewijzigd. Ververs de lijst en open de editor opnieuw.",
    ),
    "uncertain": (
        "Результат збереження не підтверджено. Онови список і перевір поточні дані перед новою зміною.",
        "Результат сохранения не подтверждён. Обновите список и проверьте текущие данные перед новым изменением.",
        "The save result is unconfirmed. Refresh the list and check the current data before making another change.",
        "O resultado não foi confirmado. Atualize a lista e verifique os dados antes de outra alteração.",
        "Wynik zapisu nie jest potwierdzony. Odśwież listę i sprawdź dane przed kolejną zmianą.",
        "Das Speicherergebnis ist unbestätigt. Liste aktualisieren und aktuelle Daten vor einer weiteren Änderung prüfen.",
        "Het opslagresultaat is onbevestigd. Ververs de lijst en controleer de gegevens voordat je opnieuw wijzigt.",
    ),
}

def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    if key not in LABELS:
        return create_tr(key, language)
    return LABELS[key][LANGUAGES.index(language) if language in LANGUAGES else 1]

# universal_area_editor_v1
