"""Construction settings labels in the seven supported bot languages."""
LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "⚙️ Налаштування", "⚙️ Настройки", "⚙️ Settings",
      "⚙️ Definições", "⚙️ Ustawienia", "⚙️ Einstellungen", "⚙️ Instellingen")
label("language", "Мова організації", "Язык организации", "Organization language",
      "Idioma da organização", "Język organizacji",
      "Organisationssprache", "Organisatietaal")
label("company", "Компанія й оформлення", "Компания и оформление",
      "Company and branding", "Empresa e identidade visual",
      "Firma i wygląd", "Unternehmen und Gestaltung", "Bedrijf en vormgeving")
label("explanation",
      "Мова організації використовується для її даних. Мова цього інтерфейсу береться з вашого Platform-профілю.",
      "Язык организации используется для её данных. Язык этого интерфейса берётся из вашего Platform-профиля.",
      "The organization language is used for its data. This interface uses your Platform profile language.",
      "O idioma da organização é usado nos seus dados. A interface usa o idioma do seu perfil Platform.",
      "Język organizacji jest używany dla jej danych. Interfejs korzysta z języka Twojego profilu Platform.",
      "Die Organisationssprache gilt für ihre Daten. Die Oberfläche verwendet die Sprache Ihres Platform-Profils.",
      "De organisatietaal geldt voor de gegevens. De interface gebruikt de taal van uw Platform-profiel.")
label("choose", "Оберіть мову організації.", "Выберите язык организации.",
      "Choose the organization language.", "Escolha o idioma da organização.",
      "Wybierz język organizacji.", "Wählen Sie die Organisationssprache.",
      "Kies de organisatietaal.")
label("review", "Підтвердьте зміну мови організації.",
      "Подтвердите изменение языка организации.",
      "Confirm the organization language change.",
      "Confirme a alteração do idioma da organização.",
      "Potwierdź zmianę języka organizacji.",
      "Bestätigen Sie die Änderung der Organisationssprache.",
      "Bevestig de wijziging van de organisatietaal.")
label("save", "Зберегти", "Сохранить", "Save", "Guardar",
      "Zapisz", "Speichern", "Opslaan")
label("cancel", "Скасувати", "Отменить", "Cancel", "Cancelar",
      "Anuluj", "Abbrechen", "Annuleren")
label("refresh", "Оновити налаштування", "Обновить настройки",
      "Refresh settings", "Atualizar definições", "Odśwież ustawienia",
      "Einstellungen aktualisieren", "Instellingen vernieuwen")
label("next", "Далі", "Далее", "Next", "Seguinte", "Dalej", "Weiter", "Volgende")
label("previous", "Назад", "Назад", "Previous", "Anterior",
      "Wstecz", "Zurück", "Vorige")
label("saved", "Мову організації збережено.", "Язык организации сохранён.",
      "Organization language saved.", "Idioma da organização guardado.",
      "Zapisano język organizacji.", "Organisationssprache gespeichert.",
      "Organisatietaal opgeslagen.")
label("unchanged", "Мова організації вже має це значення.",
      "Язык организации уже имеет это значение.",
      "The organization already uses this language.",
      "A organização já utiliza este idioma.",
      "Organizacja już korzysta z tego języka.",
      "Die Organisation verwendet diese Sprache bereits.",
      "De organisatie gebruikt deze taal al.")
label("conflict", "Налаштування змінилися. Оновіть їх перед збереженням.",
      "Настройки изменились. Обновите их перед сохранением.",
      "Settings changed. Refresh before saving.",
      "As definições mudaram. Atualize antes de guardar.",
      "Ustawienia uległy zmianie. Odśwież je przed zapisaniem.",
      "Die Einstellungen wurden geändert. Vor dem Speichern aktualisieren.",
      "De instellingen zijn gewijzigd. Vernieuw ze voordat u opslaat.")
label("denied", "Зміна налаштувань недоступна для поточного доступу.",
      "Изменение настроек недоступно при текущем доступе.",
      "Your current access does not allow this change.",
      "O seu acesso atual não permite esta alteração.",
      "Bieżący dostęp nie pozwala na tę zmianę.",
      "Ihr aktueller Zugriff erlaubt diese Änderung nicht.",
      "Uw huidige toegang staat deze wijziging niet toe.")
label("unavailable", "Не вдалося отримати налаштування. Спробуйте оновити.",
      "Не удалось получить настройки. Попробуйте обновить.",
      "Could not load settings. Try refreshing.",
      "Não foi possível carregar as definições. Tente atualizar.",
      "Nie udało się wczytać ustawień. Spróbuj odświeżyć.",
      "Einstellungen konnten nicht geladen werden. Bitte aktualisieren.",
      "De instellingen konden niet worden geladen. Probeer te vernieuwen.")
label("uncertain",
      "Результат збереження не підтверджено. Оновіть налаштування, щоб перевірити поточну мову, перед новою спробою.",
      "Результат сохранения не подтверждён. Обновите настройки, чтобы проверить текущий язык, перед новой попыткой.",
      "The save result is unconfirmed. Refresh settings to check the current language before trying again.",
      "O resultado não foi confirmado. Atualize as definições para verificar o idioma antes de tentar novamente.",
      "Wynik zapisu jest niepotwierdzony. Odśwież ustawienia i sprawdź język przed kolejną próbą.",
      "Das Speicherergebnis ist unbestätigt. Prüfen Sie vor einem neuen Versuch die aktualisierten Einstellungen.",
      "Het opslagresultaat is niet bevestigd. Vernieuw de instellingen en controleer de taal voordat u opnieuw probeert.")
label("readonly", "Перегляд. Прав на зміну немає.",
      "Просмотр. Прав на изменение нет.", "Read only. Editing is unavailable.",
      "Só de leitura. A edição não está disponível.",
      "Tylko odczyt. Edycja jest niedostępna.",
      "Nur Ansicht. Bearbeitung nicht verfügbar.",
      "Alleen lezen. Bewerken is niet beschikbaar.")


label("currency", "Валюта організації", "Валюта организации",
      "Organization currency", "Moeda da organização", "Waluta organizacji",
      "Organisationswährung", "Organisatievaluta")
label("currency_explanation",
      "Валюта за замовчуванням для нових об’єктів. Валюти існуючих об’єктів не змінюються.",
      "Валюта по умолчанию для новых объектов. Валюты существующих объектов не изменяются.",
      "Default currency for new projects. Existing project currencies stay unchanged.",
      "Moeda predefinida para novas obras. As moedas das obras existentes mantêm-se.",
      "Domyślna waluta nowych obiektów. Waluty istniejących obiektów pozostają bez zmian.",
      "Standardwährung für neue Projekte. Währungen bestehender Projekte bleiben unverändert.",
      "Standaardvaluta voor nieuwe projecten. Bestaande projectvaluta’s blijven ongewijzigd.")
label("currency_choose", "Оберіть валюту організації.",
      "Выберите валюту организации.", "Choose the organization currency.",
      "Escolha a moeda da organização.", "Wybierz walutę organizacji.",
      "Wählen Sie die Organisationswährung.", "Kies de organisatievaluta.")
label("currency_review", "Підтвердьте зміну валюти організації.",
      "Подтвердите изменение валюты организации.",
      "Confirm the organization currency change.",
      "Confirme a alteração da moeda da organização.",
      "Potwierdź zmianę waluty organizacji.",
      "Bestätigen Sie die Änderung der Organisationswährung.",
      "Bevestig de wijziging van de organisatievaluta.")
label("currency_saved", "Валюту організації збережено.",
      "Валюта организации сохранена.", "Organization currency saved.",
      "Moeda da organização guardada.", "Zapisano walutę organizacji.",
      "Organisationswährung gespeichert.", "Organisatievaluta opgeslagen.")
label("currency_unchanged", "Організація вже використовує цю валюту.",
      "Организация уже использует эту валюту.",
      "The organization already uses this currency.",
      "A organização já utiliza esta moeda.",
      "Organizacja już korzysta z tej waluty.",
      "Die Organisation verwendet diese Währung bereits.",
      "De organisatie gebruikt deze valuta al.")
label("currency_uncertain",
      "Результат збереження не підтверджено. Оновіть налаштування, щоб перевірити поточну валюту, перед новою спробою.",
      "Результат сохранения не подтверждён. Обновите настройки, чтобы проверить текущую валюту, перед новой попыткой.",
      "The save result is unconfirmed. Refresh settings to check the current currency before trying again.",
      "O resultado não foi confirmado. Atualize as definições para verificar a moeda antes de tentar novamente.",
      "Wynik zapisu jest niepotwierdzony. Odśwież ustawienia i sprawdź walutę przed kolejną próbą.",
      "Das Speicherergebnis ist unbestätigt. Prüfen Sie vor einem neuen Versuch die aktuelle Währung.",
      "Het opslagresultaat is niet bevestigd. Vernieuw de instellingen en controleer de valuta voordat u opnieuw probeert.")


label("currency_search", "🔎 Знайти валюту", "🔎 Найти валюту",
      "🔎 Find currency", "🔎 Procurar moeda", "🔎 Znajdź walutę",
      "🔎 Währung suchen", "🔎 Valuta zoeken")
label("currency_search_all", "Усі валюти", "Все валюты", "All currencies",
      "Todas as moedas", "Wszystkie waluty", "Alle Währungen", "Alle valuta’s")
label("currency_search_prompt",
      "Введіть код або назву валюти з каталогу, наприклад UAH, EUR або Euro.",
      "Введите код или название валюты из каталога, например UAH, EUR или Euro.",
      "Enter a currency code or catalog name, for example UAH, EUR or Euro.",
      "Introduza o código ou nome da moeda do catálogo, por exemplo UAH, EUR ou Euro.",
      "Wpisz kod lub nazwę waluty z katalogu, na przykład UAH, EUR lub Euro.",
      "Geben Sie einen Währungscode oder Katalognamen ein, zum Beispiel UAH, EUR oder Euro.",
      "Voer een valutacode of catalogusnaam in, bijvoorbeeld UAH, EUR of Euro.")
label("currency_search_empty",
      "Валют за цим запитом не знайдено. Спробуйте інший код або назву.",
      "Валют по этому запросу не найдено. Попробуйте другой код или название.",
      "No matching currencies. Try another code or name.",
      "Não foram encontradas moedas. Tente outro código ou nome.",
      "Nie znaleziono walut. Spróbuj innego kodu lub nazwy.",
      "Keine passenden Währungen gefunden. Versuchen Sie einen anderen Code oder Namen.",
      "Geen overeenkomende valuta gevonden. Probeer een andere code of naam.")
label("currency_search_invalid",
      "Введіть код або назву валюти: від 1 до 80 символів.",
      "Введите код или название валюты: от 1 до 80 символов.",
      "Enter a currency code or name using 1 to 80 characters.",
      "Introduza um código ou nome de moeda com 1 a 80 caracteres.",
      "Wpisz kod lub nazwę waluty, od 1 do 80 znaków.",
      "Geben Sie einen Währungscode oder Namen mit 1 bis 80 Zeichen ein.",
      "Voer een valutacode of naam van 1 tot 80 tekens in.")
label("currency_search_query", "Пошук", "Поиск", "Search", "Pesquisa",
      "Wyszukiwanie", "Suche", "Zoekopdracht")


def tr(key, language):
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])


def language_name(item):
    return next(
        option["label"] for option in item["languages"]
        if option["code"] == item["organization_default_language"]
    )

# CON_STAGE2_TELEGRAM_CURRENCY_V1

# CON_STAGE2_CURRENCY_SEARCH_V1
