"""Client editing labels in the seven existing interface languages."""
from ui.construction_client_card import tr as card_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "edit": (
        "Редагувати клієнта", "Редактировать клиента", "Edit client",
        "Editar cliente", "Edytuj klienta", "Kunden bearbeiten", "Klant bewerken",
    ),
    "choose": (
        "Оберіть поле.", "Выберите поле.", "Choose a field.",
        "Escolha um campo.", "Wybierz pole.", "Feld auswählen.", "Kies een veld.",
    ),
    "display_name": (
        "Назва клієнта", "Название клиента", "Client name",
        "Nome do cliente", "Nazwa klienta", "Kundenname", "Klantnaam",
    ),
    "client_type": (
        "Тип клієнта", "Тип клиента", "Client type",
        "Tipo de cliente", "Typ klienta", "Kundentyp", "Klanttype",
    ),
    "enter": (
        "Введіть нове значення.", "Введите новое значение.", "Enter the new value.",
        "Introduza o novo valor.", "Wpisz nową wartość.",
        "Neuen Wert eingeben.", "Voer de nieuwe waarde in.",
    ),
    "review": (
        "Перевірте зміну перед збереженням.",
        "Проверьте изменение перед сохранением.",
        "Review the change before saving.",
        "Verifique a alteração antes de guardar.",
        "Sprawdź zmianę przed zapisaniem.",
        "Änderung vor dem Speichern prüfen.",
        "Controleer de wijziging voordat u opslaat.",
    ),
    "save": (
        "Зберегти зміну", "Сохранить изменение", "Save change",
        "Guardar alteração", "Zapisz zmianę", "Änderung speichern", "Wijziging opslaan",
    ),
    "clear": (
        "Очистити поле", "Очистить поле", "Clear field",
        "Limpar campo", "Wyczyść pole", "Feld leeren", "Veld leegmaken",
    ),
    "cancel": (
        "Скасувати", "Отменить", "Cancel", "Cancelar",
        "Anuluj", "Abbrechen", "Annuleren",
    ),
    "cancelled": (
        "Редагування скасовано.", "Редактирование отменено.", "Editing cancelled.",
        "Edição cancelada.", "Edycja anulowana.",
        "Bearbeitung abgebrochen.", "Bewerken geannuleerd.",
    ),
    "saved": (
        "✅ Дані клієнта збережено.", "✅ Данные клиента сохранены.",
        "✅ Client data saved.", "✅ Dados do cliente guardados.",
        "✅ Dane klienta zapisane.", "✅ Kundendaten gespeichert.",
        "✅ Klantgegevens opgeslagen.",
    ),
    "conflict": (
        "Картка вже змінилася. Перечитайте її перед новим редагуванням.",
        "Карточка уже изменилась. Перечитайте её перед новым редактированием.",
        "The card has changed. Reload it before editing again.",
        "A ficha foi alterada. Recarregue-a antes de voltar a editar.",
        "Karta została zmieniona. Odśwież ją przed kolejną edycją.",
        "Die Karte wurde geändert. Vor der nächsten Bearbeitung neu laden.",
        "De kaart is gewijzigd. Laad deze opnieuw voordat u weer bewerkt.",
    ),
    "uncertain": (
        "Результат збереження не підтверджено. Перечитайте картку. Автоматичного повтору немає.",
        "Результат сохранения не подтверждён. Перечитайте карточку. Автоматического повтора нет.",
        "Saving could not be confirmed. Reload the card. There is no automatic retry.",
        "Não foi possível confirmar a gravação. Recarregue a ficha. Não há repetição automática.",
        "Nie potwierdzono zapisu. Odśwież kartę. Żądanie nie zostanie automatycznie ponowione.",
        "Speichern konnte nicht bestätigt werden. Karte neu laden. Keine automatische Wiederholung.",
        "Opslaan kon niet worden bevestigd. Laad de kaart opnieuw. Geen automatische herhaling.",
    ),
    "blocked": (
        "Зміна недоступна. Перечитайте картку та перевірте свої права.",
        "Изменение недоступно. Перечитайте карточку и проверьте свои права.",
        "Editing is unavailable. Reload the card and check your access.",
        "A edição não está disponível. Recarregue a ficha e verifique o acesso.",
        "Edycja jest niedostępna. Odśwież kartę i sprawdź uprawnienia.",
        "Bearbeitung nicht verfügbar. Karte neu laden und Zugriff prüfen.",
        "Bewerken is niet beschikbaar. Laad de kaart opnieuw en controleer uw toegang.",
    ),
    "invalid": (
        "Введіть непорожнє текстове значення або скористайтеся очищенням поля.",
        "Введите непустое текстовое значение или воспользуйтесь очисткой поля.",
        "Enter a non-empty text value or use Clear field.",
        "Introduza um texto não vazio ou use Limpar campo.",
        "Wpisz niepusty tekst lub użyj Wyczyść pole.",
        "Nichtleeren Text eingeben oder Feld leeren verwenden.",
        "Voer tekst in of gebruik Veld leegmaken.",
    ),
}

LABELS.update({
    "archive": (
        "Архівувати клієнта", "Архивировать клиента", "Archive client",
        "Arquivar cliente", "Archiwizuj klienta", "Kunden archivieren", "Klant archiveren",
    ),
    "archive_review": (
        "Архівувати цього клієнта?\n\nВін зникне зі списку доступних клієнтів. Об’єкти, історія та наявні зв’язки збережуться.",
        "Архивировать этого клиента?\n\nОн исчезнет из списка доступных клиентов. Объекты, история и существующие связи сохранятся.",
        "Archive this client?\n\nThey will leave the available clients list. Projects, history and existing links will be preserved.",
        "Arquivar este cliente?\n\nDeixará a lista de clientes disponíveis. As obras, o histórico e as ligações existentes serão preservados.",
        "Zarchiwizować tego klienta?\n\nZniknie z listy dostępnych klientów. Obiekty, historia i istniejące powiązania zostaną zachowane.",
        "Diesen Kunden archivieren?\n\nEr verschwindet aus der Liste verfügbarer Kunden. Projekte, Verlauf und bestehende Verknüpfungen bleiben erhalten.",
        "Deze klant archiveren?\n\nDe klant verdwijnt uit de lijst met beschikbare klanten. Projecten, geschiedenis en bestaande koppelingen blijven behouden.",
    ),
    "archive_confirm": (
        "Підтвердити архівацію", "Подтвердить архивацию", "Confirm archive",
        "Confirmar arquivo", "Potwierdź archiwizację",
        "Archivierung bestätigen", "Archiveren bevestigen",
    ),
    "archived": (
        "✅ Клієнта архівовано.", "✅ Клиент архивирован.", "✅ Client archived.",
        "✅ Cliente arquivado.", "✅ Klient zarchiwizowany.",
        "✅ Kunde archiviert.", "✅ Klant gearchiveerd.",
    ),
})

def tr(key, language):
    if key not in LABELS:
        return card_tr(key, language)
    language = (language or "ru").lower().split("-", 1)[0]
    index = LANGUAGES.index(language) if language in LANGUAGES else 1
    return LABELS[key][index]
