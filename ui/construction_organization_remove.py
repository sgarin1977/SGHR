"""Organization removal presentation; authorization belongs to Platform."""
LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "button": (
        "Видалити з організації", "Удалить из организации",
        "Remove from organization", "Remover da organização",
        "Usuń z organizacji", "Aus Organisation entfernen",
        "Uit organisatie verwijderen",
    ),
    "review": (
        "Учасник втратить членство в цій організації та весь її Construction-доступ. Його Platform-акаунт і членство в інших організаціях зберігаються. Підтвердити видалення?",
        "Участник потеряет membership в этой организации и весь её Construction-доступ. Его Platform-аккаунт и memberships других организаций сохраняются. Подтвердить удаление?",
        "The member will lose membership and all Construction access in this organization. Their Platform account and memberships in other organizations remain. Confirm removal?",
        "O membro perderá a participação e todo o acesso ao Construction nesta organização. A conta Platform e as participações noutras organizações mantêm-se. Confirmar remoção?",
        "Uczestnik utraci członkostwo i cały dostęp do Construction w tej organizacji. Konto Platform i członkostwa w innych organizacjach pozostaną. Potwierdzić usunięcie?",
        "Das Mitglied verliert die Mitgliedschaft und den gesamten Construction-Zugriff in dieser Organisation. Das Platform-Konto und andere Mitgliedschaften bleiben bestehen. Entfernung bestätigen?",
        "Het lid verliest het lidmaatschap en alle Construction-toegang in deze organisatie. Het Platform-account en andere lidmaatschappen blijven behouden. Verwijdering bevestigen?",
    ),
    "authority": (
        "Platform перевірить окреме право керування членством, межі повноважень і захист останнього власника.",
        "Platform проверит отдельное право управления membership, границы полномочий и защиту последнего владельца.",
        "Platform checks the separate membership permission, authority boundaries and protection of the last owner.",
        "A Platform verifica a permissão de participação, os limites de autoridade e a proteção do último proprietário.",
        "Platform sprawdza osobne uprawnienie do członkostwa, granice uprawnień i ochronę ostatniego właściciela.",
        "Platform prüft die separate Mitgliedschaftsberechtigung, Befugnisgrenzen und den Schutz des letzten Eigentümers.",
        "Platform controleert het afzonderlijke lidmaatschapsrecht, bevoegdheidsgrenzen en bescherming van de laatste eigenaar.",
    ),
    "self": (
        "Ви видаляєте власне членство в цій організації.",
        "Вы удаляете собственное membership в этой организации.",
        "You are removing your own membership in this organization.",
        "Está a remover a sua própria participação nesta organização.",
        "Usuwasz własne członkostwo w tej organizacji.",
        "Sie entfernen Ihre eigene Mitgliedschaft in dieser Organisation.",
        "Je verwijdert je eigen lidmaatschap in deze organisatie.",
    ),
    "confirm": (
        "Підтвердити видалення", "Подтвердить удаление", "Confirm removal",
        "Confirmar remoção", "Potwierdź usunięcie", "Entfernung bestätigen",
        "Verwijdering bevestigen",
    ),
    "cancel": (
        "Скасувати", "Отменить", "Cancel", "Cancelar",
        "Anuluj", "Abbrechen", "Annuleren",
    ),
    "pending": (
        "Результат початкового запиту ще не підтверджено. Повторіть отримання того самого результату.",
        "Результат первоначального запроса ещё не подтверждён. Повторите получение того же результата.",
        "The original request result is not confirmed yet. Retrieve that same result again.",
        "O resultado do pedido original ainda não está confirmado. Volte a obter esse mesmo resultado.",
        "Wynik pierwotnego żądania nie został potwierdzony. Pobierz ponownie ten sam wynik.",
        "Das Ergebnis der ursprünglichen Anfrage ist noch unbestätigt. Rufen Sie dasselbe Ergebnis erneut ab.",
        "Het resultaat van het oorspronkelijke verzoek is nog niet bevestigd. Vraag hetzelfde resultaat opnieuw op.",
    ),
    "retry": (
        "Отримати попередній результат", "Получить предыдущий результат",
        "Retrieve previous result", "Obter resultado anterior",
        "Pobierz poprzedni wynik", "Vorheriges Ergebnis abrufen",
        "Vorig resultaat ophalen",
    ),
    "expired": (
        "Строк безпечного повтору минув. Перевірте поточний стан команди; цей запит автоматично повторювати не можна.",
        "Срок безопасного повтора истёк. Проверьте текущее состояние команды; этот запрос автоматически повторять нельзя.",
        "The safe retry window has ended. Check the current team state; this request cannot be automatically repeated.",
        "O prazo de repetição segura terminou. Verifique a equipa atual; este pedido não pode ser repetido automaticamente.",
        "Minął czas bezpiecznego ponowienia. Sprawdź aktualny zespół; tego żądania nie można automatycznie powtórzyć.",
        "Das sichere Wiederholungsfenster ist abgelaufen. Prüfen Sie den aktuellen Teamstand; diese Anfrage kann nicht automatisch wiederholt werden.",
        "De veilige herhaaltermijn is verstreken. Controleer het huidige team; dit verzoek kan niet automatisch worden herhaald.",
    ),
    "done": (
        "Отримано успішний результат початкової операції. Оновіть команду, щоб побачити її поточний стан.",
        "Получен успешный результат первоначальной операции. Обновите команду, чтобы увидеть её текущее состояние.",
        "The original operation succeeded. Refresh the team to see its current state.",
        "A operação original foi concluída com sucesso. Atualize a equipa para ver o estado atual.",
        "Pierwotna operacja zakończyła się powodzeniem. Odśwież zespół, aby zobaczyć aktualny stan.",
        "Die ursprüngliche Operation war erfolgreich. Aktualisieren Sie das Team, um den aktuellen Stand zu sehen.",
        "De oorspronkelijke bewerking is geslaagd. Vernieuw het team om de huidige situatie te zien.",
    ),
    "denied": (
        "Видалення недоступне: Platform не підтвердила право або поточний стан учасника. Оновіть команду.",
        "Удаление недоступно: Platform не подтвердила право или текущее состояние участника. Обновите команду.",
        "Removal is unavailable: Platform did not confirm authority or the current member state. Refresh the team.",
        "A remoção não está disponível: a Platform não confirmou a autoridade ou o estado atual do membro. Atualize a equipa.",
        "Usunięcie jest niedostępne: Platform nie potwierdziła uprawnień lub aktualnego stanu uczestnika. Odśwież zespół.",
        "Die Entfernung ist nicht verfügbar: Platform hat die Befugnis oder den aktuellen Mitgliedsstatus nicht bestätigt. Aktualisieren Sie das Team.",
        "Verwijdering is niet beschikbaar: Platform heeft de bevoegdheid of huidige lidstatus niet bevestigd. Vernieuw het team.",
    ),
    "team": (
        "Оновити команду", "Обновить команду", "Refresh team",
        "Atualizar equipa", "Odśwież zespół", "Team aktualisieren",
        "Team vernieuwen",
    ),
}

def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    index = LANGUAGES.index(language) if language in LANGUAGES else 1
    return LABELS[key][index]
