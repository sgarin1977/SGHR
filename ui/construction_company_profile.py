"""Read-only common Platform company profile presentation."""
from ui.construction_project_card import timestamp

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("button", "🏢 Компанія", "🏢 Компания", "🏢 Company", "🏢 Empresa",
      "🏢 Firma", "🏢 Unternehmen", "🏢 Bedrijf")
label("title", "🏢 Профіль компанії", "🏢 Профиль компании", "🏢 Company profile",
      "�� Perfil da empresa", "🏢 Profil firmy", "🏢 Unternehmensprofil", "🏢 Bedrijfsprofiel")
label("empty", "Профіль компанії ще не заповнено.", "Профиль компании ещё не заполнен.",
      "The company profile has not been filled in yet.",
      "O perfil da empresa ainda não foi preenchido.",
      "Profil firmy nie został jeszcze uzupełniony.",
      "Das Unternehmensprofil ist noch nicht ausgefüllt.",
      "Het bedrijfsprofiel is nog niet ingevuld.")
label("legal_name", "Юридична назва", "Юридическое название", "Legal name",
      "Denominação legal", "Nazwa prawna", "Rechtlicher Name", "Juridische naam")
label("commercial_name", "Комерційна назва", "Коммерческое название", "Trading name",
      "Nome comercial", "Nazwa handlowa", "Handelsname", "Handelsnaam")
label("registered_address_raw", "Юридична адреса", "Юридический адрес", "Registered address",
      "Morada da sede", "Adres rejestrowy", "Sitzadresse", "Vestigingsadres")
label("operational_address_raw", "Робоча адреса", "Рабочий адрес", "Operating address",
      "Morada operacional", "Adres operacyjny", "Betriebsadresse", "Bedrijfsadres")
label("phone", "Телефон", "Телефон", "Phone", "Telefone", "Telefon", "Telefon", "Telefoon")
label("email", "Email", "Email", "Email", "Email", "Email", "E-Mail", "E-mail")
label("website", "Вебсайт", "Сайт", "Website", "Website", "Strona internetowa", "Website", "Website")
label("signatory_name", "Підписант", "Подписант", "Signatory", "Signatário",
      "Osoba podpisująca", "Unterzeichner", "Ondertekenaar")
label("signatory_position", "Посада підписанта", "Должность подписанта", "Signatory position",
      "Cargo do signatário", "Stanowisko osoby podpisującej",
      "Position des Unterzeichners", "Functie ondertekenaar")
label("logo", "Логотип", "Логотип", "Logo", "Logótipo", "Logo", "Logo", "Logo")
label("configured", "Налаштовано", "Настроен", "Configured", "Configurado",
      "Skonfigurowano", "Konfiguriert", "Ingesteld")
label("not_configured", "Не налаштовано", "Не настроен", "Not configured",
      "Não configurado", "Nie skonfigurowano", "Nicht konfiguriert", "Niet ingesteld")
label("updated", "Оновлено", "Обновлено", "Updated", "Atualizado",
      "Zaktualizowano", "Aktualisiert", "Bijgewerkt")
label("refresh", "Оновити профіль", "Обновить профиль", "Refresh profile",
      "Atualizar perfil", "Odśwież profil", "Profil aktualisieren", "Profiel verversen")


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])


def profile_text(result, language):
    lines = [tr("title", language), str(result["organization"])[:120]]
    item = result["item"]
    if item is None:
        lines.append(tr("empty", language))
    else:
        limits = {
            "legal_name": 255, "commercial_name": 255,
            "registered_address_raw": 350, "operational_address_raw": 350,
            "phone": 80, "email": 150, "website": 180,
            "signatory_name": 255, "signatory_position": 180,
        }
        for field, limit in limits.items():
            value = str(item[field] or "").strip()[:limit] or "—"
            lines.append(tr(field, language) + ":\n" + value)
        lines.append(
            tr("logo", language) + ": "
            + tr("configured" if item["logo_file_id"] else "not_configured", language)
        )
        lines.append(tr("updated", language) + ": " + timestamp(item["updated_at"]))
    text = "\n\n".join(lines)
    if len(text) > 3800:
        raise ValueError("Company presentation is too large.")
    return text


# Company editor labels; common fields retain the existing translations.
label("create", "Створити профіль", "Создать профиль", "Create profile",
      "Criar perfil", "Utwórz profil", "Profil erstellen", "Profiel maken")
label("edit", "Редагувати профіль", "Редактировать профиль", "Edit profile",
      "Editar perfil", "Edytuj profil", "Profil bearbeiten", "Profiel bewerken")
label("choose_field", "Оберіть поле для редагування.",
      "Выберите поле для редактирования.", "Choose a field to edit.",
      "Escolha um campo para editar.", "Wybierz pole do edycji.",
      "Wählen Sie ein Feld zum Bearbeiten.", "Kies een veld om te bewerken.")
label("enter_value", "Надішліть нове значення повідомленням.",
      "Отправьте новое значение сообщением.", "Send the new value in a message.",
      "Envie o novo valor numa mensagem.", "Wyślij nową wartość w wiadomości.",
      "Senden Sie den neuen Wert als Nachricht.", "Stuur de nieuwe waarde in een bericht.")
label("review", "Перевірте зміну перед збереженням.",
      "Проверьте изменение перед сохранением.", "Review the change before saving.",
      "Reveja a alteração antes de guardar.", "Sprawdź zmianę przed zapisaniem.",
      "Prüfen Sie die Änderung vor dem Speichern.", "Controleer de wijziging voor het opslaan.")
label("save", "Зберегти", "Сохранить", "Save", "Guardar", "Zapisz", "Speichern", "Opslaan")
label("clear_field", "Очистити поле", "Очистить поле", "Clear field",
      "Limpar campo", "Wyczyść pole", "Feld leeren", "Veld wissen")
label("cleared_value", "Значення буде видалено.", "Значение будет удалено.",
      "The value will be removed.", "O valor será removido.",
      "Wartość zostanie usunięta.", "Der Wert wird entfernt.", "De waarde wordt verwijderd.")
label("cancel", "Скасувати", "Отменить", "Cancel", "Cancelar", "Anuluj", "Abbrechen", "Annuleren")
label("saved", "✅ Профіль компанії збережено.", "✅ Профиль компании сохранён.",
      "✅ Company profile saved.", "✅ Perfil da empresa guardado.",
      "✅ Profil firmy zapisany.", "✅ Unternehmensprofil gespeichert.", "✅ Bedrijfsprofiel opgeslagen.")
label("open_profile", "Відкрити профіль", "Открыть профиль", "Open profile",
      "Abrir perfil", "Otwórz profil", "Profil öffnen", "Profiel openen")
label("invalid_input", "Надішліть непорожній текст допустимої довжини.",
      "Отправьте непустой текст допустимой длины.",
      "Send non-empty text within the permitted length.",
      "Envie texto não vazio dentro do comprimento permitido.",
      "Wyślij niepusty tekst o dozwolonej długości.",
      "Senden Sie nicht leeren Text innerhalb der zulässigen Länge.",
      "Stuur niet-lege tekst binnen de toegestane lengte.")
label("uncertain_write",
      "Результат збереження не підтверджено. Відкрийте профіль, щоб перевірити актуальні дані. Автоматичного повтору немає.",
      "Результат сохранения не подтверждён. Откройте профиль, чтобы проверить актуальные данные. Автоматического повтора нет.",
      "Saving could not be confirmed. Open the profile to check the current data. The request will not be retried automatically.",
      "Não foi possível confirmar a gravação. Abra o perfil para verificar os dados atuais. O pedido não será repetido automaticamente.",
      "Nie potwierdzono zapisu. Otwórz profil, aby sprawdzić aktualne dane. Żądanie nie zostanie automatycznie ponowione.",
      "Das Speichern konnte nicht bestätigt werden. Öffnen Sie das Profil, um die aktuellen Daten zu prüfen. Die Anfrage wird nicht automatisch wiederholt.",
      "Opslaan kon niet worden bevestigd. Open het profiel om de actuele gegevens te controleren. Het verzoek wordt niet automatisch herhaald.")
label("conflict_write",
      "Профіль уже змінився або був створений. Перечитайте його перед новою зміною.",
      "Профиль уже изменился или был создан. Перечитайте его перед новым изменением.",
      "The profile has changed or has already been created. Reload it before making another change.",
      "O perfil foi alterado ou já foi criado. Recarregue-o antes de fazer outra alteração.",
      "Profil został zmieniony lub już utworzony. Odśwież go przed kolejną zmianą.",
      "Das Profil wurde geändert oder bereits erstellt. Laden Sie es vor einer weiteren Änderung neu.",
      "Het profiel is gewijzigd of al aangemaakt. Laad het opnieuw voordat u een nieuwe wijziging maakt.")
label("invalid_write",
      "API відхилив дані. Перечитайте профіль і перевірте значення поля.",
      "API отклонил данные. Перечитайте профиль и проверьте значение поля.",
      "The API rejected the data. Reload the profile and check the field value.",
      "A API rejeitou os dados. Recarregue o perfil e verifique o valor do campo.",
      "API odrzuciło dane. Odśwież profil i sprawdź wartość pola.",
      "Die API hat die Daten abgelehnt. Laden Sie das Profil neu und prüfen Sie den Feldwert.",
      "De API heeft de gegevens geweigerd. Laad het profiel opnieuw en controleer de veldwaarde.")
