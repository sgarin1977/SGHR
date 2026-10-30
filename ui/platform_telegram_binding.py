"""Own Telegram binding UI in the seven existing interface languages."""
LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "title": ("Мій Telegram", "Мой Telegram", "My Telegram", "O meu Telegram", "Mój Telegram", "Mein Telegram", "Mijn Telegram"),
    "new": ("Створити посилання", "Создать ссылку", "Create link", "Criar ligação", "Utwórz link", "Link erstellen", "Link maken"),
    "confirm": ("Підтвердити", "Подтвердить", "Confirm", "Confirmar", "Potwierdź", "Bestätigen", "Bevestigen"),
    "cancel": ("Скасувати", "Отменить", "Cancel", "Cancelar", "Anuluj", "Abbrechen", "Annuleren"),
    "accounts": ("Мої Telegram-зв’язки", "Мои Telegram-связи", "My Telegram accounts", "As minhas contas Telegram", "Moje konta Telegram", "Meine Telegram-Konten", "Mijn Telegram-accounts"),
    "empty": ("Зв’язків на цій сторінці немає.", "Связей на этой странице нет.", "No accounts on this page.", "Sem contas nesta página.", "Brak kont na tej stronie.", "Keine Konten auf dieser Seite.", "Geen accounts op deze pagina."),
    "active": ("Активний", "Активный", "Active", "Ativa", "Aktywne", "Aktiv", "Actief"),
    "unlinked": ("Від’єднаний", "Отвязан", "Unlinked", "Desassociada", "Odłączone", "Getrennt", "Ontkoppeld"),
    "restore": ("Отримати попередній результат", "Получить предыдущий результат", "Get previous result", "Obter resultado anterior", "Odbierz poprzedni wynik", "Vorheriges Ergebnis abrufen", "Vorig resultaat ophalen"),
    "open": ("Відкрити в потрібному Telegram-акаунті", "Открыть в нужном Telegram-аккаунте", "Open in the intended Telegram account", "Abrir na conta Telegram pretendida", "Otwórz na właściwym koncie Telegram", "Im gewünschten Telegram-Konto öffnen", "Openen in het gewenste Telegram-account"),
    "resend": ("Замінити посилання", "Заменить ссылку", "Replace link", "Substituir ligação", "Zastąp link", "Link ersetzen", "Link vervangen"),
    "revoke": ("Відкликати посилання", "Отозвать ссылку", "Revoke link", "Revogar ligação", "Unieważnij link", "Link widerrufen", "Link intrekken"),
    "revoked": ("Посилання відкликано.", "Ссылка отозвана.", "Link revoked.", "Ligação revogada.", "Link unieważniony.", "Link widerrufen.", "Link ingetrokken."),
    "revoke_review": ("Відкликати це одноразове посилання?", "Отозвать эту одноразовую ссылку?", "Revoke this one-time link?", "Revogar esta ligação de utilização única?", "Unieważnić ten jednorazowy link?", "Diesen einmaligen Link widerrufen?", "Deze eenmalige link intrekken?"),
    "expires": ("Діє до", "Действует до", "Valid until", "Válida até", "Ważny do", "Gültig bis", "Geldig tot"),
    "expired": ("Строк посилання минув. Для нової спроби створіть нове посилання.", "Срок ссылки истёк. Для новой попытки создайте новую ссылку.", "The link has expired. Create a new link to try again.", "A ligação expirou. Crie uma nova para tentar novamente.", "Link wygasł. Utwórz nowy, aby spróbować ponownie.", "Der Link ist abgelaufen. Für einen neuen Versuch einen neuen Link erstellen.", "De link is verlopen. Maak een nieuwe link om opnieuw te proberen."),
    "pending": ("Результат запиту не підтверджено. Повторіть отримання того самого результату.", "Результат запроса не подтверждён. Повторите получение того же результата.", "The result is unconfirmed. Retrieve the same result again.", "O resultado não foi confirmado. Volte a obter o mesmo resultado.", "Wynik nie został potwierdzony. Pobierz ponownie ten sam wynik.", "Ergebnis nicht bestätigt. Dasselbe Ergebnis erneut abrufen.", "Het resultaat is niet bevestigd. Haal hetzelfde resultaat opnieuw op."),
    "instructions": (
        "Посилання одноразове, діє 15 хвилин. Відкрийте його в Telegram-акаунті, який хочете пов’язати зі своїм Platform-акаунтом. Не передавайте посилання іншим. Заміна робить попереднє посилання недійсним.",
        "Ссылка одноразовая, действует 15 минут. Откройте её в Telegram-аккаунте, который хотите связать со своим Platform-аккаунтом. Не передавайте ссылку другим. Замена делает предыдущую ссылку недействительной.",
        "The link is single-use and valid for 15 minutes. Open it in the Telegram account you want to link to your Platform account. Do not share it. Replacing it invalidates the previous link.",
        "A ligação é de utilização única e válida por 15 minutos. Abra-a na conta Telegram que pretende associar à sua conta Platform. Não a partilhe. A substituição invalida a ligação anterior.",
        "Link jest jednorazowy i ważny przez 15 minut. Otwórz go na koncie Telegram, które chcesz połączyć ze swoim kontem Platform. Nie udostępniaj go. Zastąpienie unieważnia poprzedni link.",
        "Der Link ist einmalig und 15 Minuten gültig. Im Telegram-Konto öffnen, das mit Ihrem Platform-Konto verbunden werden soll. Nicht weitergeben. Das Ersetzen macht den vorherigen Link ungültig.",
        "De link is eenmalig en 15 minuten geldig. Open deze in het Telegram-account dat u aan uw Platform-account wilt koppelen. Deel de link niet. Vervangen maakt de vorige link ongeldig.",
    ),
}

LABELS.update({
    "unlink_account": (
        "Відв’язати Telegram", "Отвязать Telegram", "Unlink Telegram",
        "Desassociar Telegram", "Odłącz Telegram", "Telegram trennen", "Telegram ontkoppelen",
    ),
    "unlink_review": (
        "Відв’язати цей Telegram від вашого Platform-акаунта? Новий вхід через нього стане недоступним у всіх організаціях. Інші Telegram-зв’язки збережуться.",
        "Отвязать этот Telegram от вашего Platform-аккаунта? Новый вход через него станет недоступен во всех организациях. Другие Telegram-связи сохранятся.",
        "Unlink this Telegram account from your Platform account? New sign-ins through it will be unavailable across all organizations. Other Telegram links will remain.",
        "Desassociar esta conta Telegram da sua conta Platform? Novos acessos através dela ficarão indisponíveis em todas as organizações. As outras ligações Telegram serão mantidas.",
        "Odłączyć to konto Telegram od konta Platform? Nowe logowanie przez nie będzie niedostępne we wszystkich organizacjach. Pozostałe powiązania Telegram zostaną zachowane.",
        "Dieses Telegram-Konto vom Platform-Konto trennen? Neue Anmeldungen darüber sind dann in allen Organisationen nicht mehr möglich. Andere Telegram-Verknüpfungen bleiben erhalten.",
        "Dit Telegram-account van uw Platform-account ontkoppelen? Nieuwe aanmeldingen ermee zijn dan niet meer mogelijk voor alle organisaties. Andere Telegram-koppelingen blijven behouden.",
    ),
    "unlink_current": (
        "Це Telegram-акаунт, з якого ви зараз працюєте. Для нового входу використайте інший підтверджений спосіб входу в Platform.",
        "Это Telegram-аккаунт, из которого вы сейчас работаете. Для нового входа используйте другой подтверждённый способ входа в Platform.",
        "You are currently using this Telegram account. For a new sign-in, use another verified Platform sign-in method.",
        "Está a utilizar esta conta Telegram. Para um novo acesso, utilize outro método verificado de acesso à Platform.",
        "Obecnie korzystasz z tego konta Telegram. Do nowego logowania użyj innej potwierdzonej metody dostępu do Platform.",
        "Sie verwenden gerade dieses Telegram-Konto. Für eine neue Anmeldung eine andere bestätigte Platform-Anmeldemethode verwenden.",
        "U gebruikt momenteel dit Telegram-account. Gebruik voor een nieuwe aanmelding een andere geverifieerde Platform-inlogmethode.",
    ),
    "unlink_done": (
        "Запит відв’язування виконано. Оновіть список, щоб побачити актуальний стан зв’язків.",
        "Запрос отвязки выполнен. Обновите список, чтобы увидеть актуальное состояние связей.",
        "The unlink request completed. Refresh the list to see the current account state.",
        "O pedido de desassociação foi concluído. Atualize a lista para ver o estado atual.",
        "Żądanie odłączenia zostało wykonane. Odśwież listę, aby zobaczyć aktualny stan.",
        "Die Trennungsanfrage wurde ausgeführt. Liste für den aktuellen Kontostatus aktualisieren.",
        "Het ontkoppelverzoek is uitgevoerd. Vernieuw de lijst voor de huidige accountstatus.",
    ),
    "unlink_conflict": (
        "Стан зв’язку змінився. Оновіть список і підтвердьте операцію заново.",
        "Состояние связи изменилось. Обновите список и подтвердите операцию заново.",
        "The account state changed. Refresh the list and confirm a new operation.",
        "O estado da conta mudou. Atualize a lista e confirme uma nova operação.",
        "Stan powiązania się zmienił. Odśwież listę i ponownie potwierdź operację.",
        "Der Kontostatus wurde geändert. Liste aktualisieren und neue Aktion bestätigen.",
        "De accountstatus is gewijzigd. Vernieuw de lijst en bevestig een nieuwe actie.",
    ),
    "unlink_pending": (
        "Результат відв’язування не підтверджено. Отримайте результат того самого запиту.",
        "Результат отвязки не подтверждён. Получите результат того же запроса.",
        "The unlink result is unconfirmed. Retrieve the result of the same request.",
        "O resultado da desassociação não foi confirmado. Obtenha o resultado do mesmo pedido.",
        "Wynik odłączenia nie został potwierdzony. Pobierz wynik tego samego żądania.",
        "Das Ergebnis ist nicht bestätigt. Ergebnis derselben Anfrage abrufen.",
        "Het ontkoppelresultaat is niet bevestigd. Haal het resultaat van hetzelfde verzoek op.",
    ),
    "unlink_expired": (
        "Період безпечного повтору минув. Оновіть список перед новою операцією.",
        "Период безопасного повтора истёк. Обновите список перед новой операцией.",
        "The retry period has expired. Refresh the list before a new operation.",
        "O período de repetição expirou. Atualize a lista antes de uma nova operação.",
        "Okres ponawiania wygasł. Odśwież listę przed nową operacją.",
        "Der Wiederholungszeitraum ist abgelaufen. Liste vor einer neuen Aktion aktualisieren.",
        "De herhaalperiode is verlopen. Vernieuw de lijst voordat u een nieuwe actie start.",
    ),
})

def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key][LANGUAGES.index(language) if language in LANGUAGES else 1]
