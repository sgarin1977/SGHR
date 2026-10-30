"""Organization grant labels; available roles come from the server."""
from ui.construction_project_replace import tr as common

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "assign": (
        "Призначити роль організації", "Назначить роль организации",
        "Assign organization role", "Atribuir função na organização",
        "Przypisz rolę organizacji", "Organisationsrolle zuweisen",
        "Organisatierol toewijzen",
    ),
    "role": (
        "Оберіть роль організації з дозволених сервером.",
        "Выберите роль организации из разрешённых сервером.",
        "Choose an organization role allowed by the server.",
        "Escolha uma função na organização permitida pelo servidor.",
        "Wybierz rolę organizacji dozwoloną przez serwer.",
        "Wählen Sie eine vom Server erlaubte Organisationsrolle.",
        "Kies een door de server toegestane organisatierol.",
    ),
    "review": (
        "Підтвердьте призначення цієї ролі в поточній організації.",
        "Подтвердите назначение этой роли в текущей организации.",
        "Confirm assigning this role in the current organization.",
        "Confirme a atribuição desta função na organização atual.",
        "Potwierdź przypisanie tej roli w bieżącej organizacji.",
        "Bestätigen Sie die Zuweisung dieser Rolle in der aktuellen Organisation.",
        "Bevestig de toewijzing van deze rol in de huidige organisatie.",
    ),
    "empty": (
        "Немає доступних нових ролей організації для цього учасника.",
        "Нет доступных новых ролей организации для этого участника.",
        "No new organization roles are available for this member.",
        "Não há novas funções na organização disponíveis para este membro.",
        "Brak nowych dostępnych ról organizacji dla tego uczestnika.",
        "Für dieses Mitglied sind keine neuen Organisationsrollen verfügbar.",
        "Er zijn geen nieuwe organisatierollen beschikbaar voor dit lid.",
    ),
    "done": (
        "Отримано успішний результат призначення. Оновіть команду для поточного стану доступу.",
        "Получен успешный результат назначения. Обновите команду для текущего состояния доступа.",
        "The assignment succeeded. Refresh the team for the current access state.",
        "A atribuição foi concluída. Atualize a equipa para ver o acesso atual.",
        "Przypisanie powiodło się. Odśwież zespół, aby zobaczyć aktualny dostęp.",
        "Die Zuweisung war erfolgreich. Aktualisieren Sie das Team für den aktuellen Zugriffsstand.",
        "De toewijzing is geslaagd. Vernieuw het team voor de huidige toegang.",
    ),
    "denied": (
        "Призначення недоступне. Оновіть команду та доступні ролі.",
        "Назначение недоступно. Обновите команду и доступные роли.",
        "Assignment is unavailable. Refresh the team and available roles.",
        "A atribuição não está disponível. Atualize a equipa e as funções disponíveis.",
        "Przypisanie jest niedostępne. Odśwież zespół i dostępne role.",
        "Die Zuweisung ist nicht verfügbar. Aktualisieren Sie Team und verfügbare Rollen.",
        "Toewijzing is niet beschikbaar. Vernieuw het team en de beschikbare rollen.",
    ),
    "expired": (
        "Строк безпечного повтору минув. Перевірте поточну команду перед новим призначенням.",
        "Срок безопасного повтора истёк. Проверьте текущую команду перед новым назначением.",
        "The safe retry window has ended. Check the current team before another assignment.",
        "O prazo de repetição segura terminou. Verifique a equipa antes de outra atribuição.",
        "Minął czas bezpiecznego ponowienia. Sprawdź zespół przed kolejnym przypisaniem.",
        "Das sichere Wiederholungsfenster ist abgelaufen. Prüfen Sie das Team vor einer neuen Zuweisung.",
        "De veilige herhaaltermijn is verstreken. Controleer het team vóór een nieuwe toewijzing.",
    ),
    "other_pending": (
        "Спочатку отримайте результат попереднього призначення в його організації.",
        "Сначала получите результат предыдущего назначения в его организации.",
        "Retrieve the previous assignment result in its organization first.",
        "Obtenha primeiro o resultado da atribuição anterior na respetiva organização.",
        "Najpierw pobierz wynik poprzedniego przypisania w jego organizacji.",
        "Rufen Sie zuerst das Ergebnis der vorherigen Zuweisung in deren Organisation ab.",
        "Haal eerst het resultaat van de vorige toewijzing in de betreffende organisatie op.",
    ),
}

def tr(key, language):
    if key not in LABELS:
        return common(key, language)
    language = (language or "ru").lower().split("-", 1)[0]
    index = LANGUAGES.index(language) if language in LANGUAGES else 1
    return LABELS[key][index]

# Organization grant replacement labels v1.
LABELS.update({
    "replace": (
        "Змінити роль / строк", "Изменить роль / срок", "Change role / expiry",
        "Alterar função / prazo", "Zmień rolę / termin",
        "Rolle / Ablaufdatum ändern", "Rol / vervaldatum wijzigen",
    ),
    "replace_done": (
        "Отримано успішний результат зміни доступу. Оновіть команду для поточного стану.",
        "Получен успешный результат изменения доступа. Обновите команду для текущего состояния.",
        "The access change succeeded. Refresh the team for its current state.",
        "A alteração de acesso foi concluída. Atualize a equipa para ver o estado atual.",
        "Zmiana dostępu powiodła się. Odśwież zespół, aby zobaczyć aktualny stan.",
        "Die Zugriffsänderung war erfolgreich. Aktualisieren Sie das Team für den aktuellen Stand.",
        "De toegangswijziging is geslaagd. Vernieuw het team voor de huidige situatie.",
    ),
    "replace_denied": (
        "Зміна доступу недоступна. Оновіть команду та права учасника.",
        "Изменение доступа недоступно. Обновите команду и права участника.",
        "The access change is unavailable. Refresh the team and member permissions.",
        "A alteração de acesso não está disponível. Atualize a equipa e as permissões do membro.",
        "Zmiana dostępu jest niedostępna. Odśwież zespół i uprawnienia uczestnika.",
        "Die Zugriffsänderung ist nicht verfügbar. Aktualisieren Sie Team und Mitgliedsrechte.",
        "De toegangswijziging is niet beschikbaar. Vernieuw het team en de rechten van het lid.",
    ),
})

# Organization grant revocation labels v1.
LABELS.update({
    "revoke": (
        "Відкликати роль організації", "Отозвать роль организации",
        "Revoke organization role", "Revogar função na organização",
        "Cofnij rolę organizacji", "Organisationsrolle widerrufen",
        "Organisatierol intrekken",
    ),
    "revoke_review": (
        "Перевірте вибрану роль перед відкликанням.",
        "Проверьте выбранную роль перед отзывом.",
        "Review the selected role before revoking it.",
        "Verifique a função selecionada antes de a revogar.",
        "Sprawdź wybraną rolę przed jej cofnięciem.",
        "Prüfen Sie die ausgewählte Rolle vor dem Widerruf.",
        "Controleer de geselecteerde rol voordat u deze intrekt.",
    ),
    "revoke_impact": (
        "Буде відкликано лише цю Construction-роль організації. Membership та інші grants зберігаються; вони можуть надавати інший доступ.",
        "Будет отозвана только эта Construction-роль организации. Membership и другие grants сохраняются; они могут предоставлять другой доступ.",
        "Only this Construction organization role will be revoked. Membership and other grants remain and may provide other access.",
        "Só esta função Construction na organização será revogada. A participação e outras concessões mantêm-se e podem dar outro acesso.",
        "Cofnięta zostanie tylko ta rola organizacji w Construction. Członkostwo i inne nadania pozostaną i mogą zapewniać inny dostęp.",
        "Nur diese Construction-Organisationsrolle wird widerrufen. Mitgliedschaft und andere Zuweisungen bleiben bestehen und können weiteren Zugriff gewähren.",
        "Alleen deze Construction-organisatierol wordt ingetrokken. Het lidmaatschap en andere toewijzingen blijven bestaan en kunnen andere toegang geven.",
    ),
    "revoke_confirm": (
        "Підтвердити відкликання", "Подтвердить отзыв", "Confirm revocation",
        "Confirmar revogação", "Potwierdź cofnięcie",
        "Widerruf bestätigen", "Intrekken bevestigen",
    ),
    "revoke_done": (
        "Отримано успішний результат відкликання. Оновіть команду для поточного стану доступу.",
        "Получен успешный результат отзыва. Обновите команду для текущего состояния доступа.",
        "The revocation succeeded. Refresh the team for the current access state.",
        "A revogação foi concluída. Atualize a equipa para ver o acesso atual.",
        "Cofnięcie powiodło się. Odśwież zespół, aby zobaczyć aktualny dostęp.",
        "Der Widerruf war erfolgreich. Aktualisieren Sie das Team für den aktuellen Zugriffsstand.",
        "Het intrekken is geslaagd. Vernieuw het team voor de huidige toegang.",
    ),
    "revoke_denied": (
        "Відкликання недоступне. Оновіть команду та права учасника.",
        "Отзыв недоступен. Обновите команду и права участника.",
        "Revocation is unavailable. Refresh the team and member permissions.",
        "A revogação não está disponível. Atualize a equipa e as permissões do membro.",
        "Cofnięcie jest niedostępne. Odśwież zespół i uprawnienia uczestnika.",
        "Der Widerruf ist nicht verfügbar. Aktualisieren Sie Team und Mitgliedsrechte.",
        "Intrekken is niet beschikbaar. Vernieuw het team en de rechten van het lid.",
    ),
    "revoke_expired": (
        "Строк безпечного повтору минув. Перевірте поточний доступ перед новою операцією.",
        "Срок безопасного повтора истёк. Проверьте текущий доступ перед новой операцией.",
        "The safe retry window has ended. Check current access before another operation.",
        "O prazo de repetição segura terminou. Verifique o acesso atual antes de outra operação.",
        "Minął czas bezpiecznego ponowienia. Sprawdź aktualny dostęp przed kolejną operacją.",
        "Das sichere Wiederholungsfenster ist abgelaufen. Prüfen Sie den aktuellen Zugriff vor einer neuen Operation.",
        "De veilige herhaaltermijn is verstreken. Controleer de huidige toegang vóór een nieuwe bewerking.",
    ),
    "revoke_other_pending": (
        "Спочатку отримайте результат попередньої операції в її організації.",
        "Сначала получите результат предыдущей операции в её организации.",
        "Retrieve the previous operation result in its organization first.",
        "Obtenha primeiro o resultado da operação anterior na respetiva organização.",
        "Najpierw pobierz wynik poprzedniej operacji w jej organizacji.",
        "Rufen Sie zuerst das vorherige Operationsergebnis in dessen Organisation ab.",
        "Haal eerst het resultaat van de vorige bewerking in de betreffende organisatie op.",
    ),
})

# Construction access block labels v1.
LABELS.update({
    "block": (
        "Заблокувати Construction-доступ", "Заблокировать Construction-доступ",
        "Block Construction access", "Bloquear acesso ao Construction",
        "Zablokuj dostęp do Construction", "Construction-Zugriff sperren",
        "Construction-toegang blokkeren",
    ),
    "block_review": (
        "Підтвердьте відкликання всіх чинних Construction-ролей цього учасника в поточній організації на момент виконання.",
        "Подтвердите отзыв всех действующих Construction-ролей этого участника в текущей организации на момент выполнения.",
        "Confirm revoking all Construction roles effective for this member in the current organization when the operation runs.",
        "Confirme a revogação de todas as funções Construction em vigor para este membro na organização atual no momento da operação.",
        "Potwierdź cofnięcie wszystkich ról Construction tego uczestnika obowiązujących w bieżącej organizacji w chwili wykonania operacji.",
        "Bestätigen Sie den Widerruf aller zum Ausführungszeitpunkt gültigen Construction-Rollen dieses Mitglieds in der aktuellen Organisation.",
        "Bevestig het intrekken van alle Construction-rollen die voor dit lid in de huidige organisatie gelden wanneer de bewerking wordt uitgevoerd.",
    ),
    "block_impact": (
        "Platform membership, обліковий запис і доступ до інших організацій зберігаються.",
        "Platform membership, учётная запись и доступ к другим организациям сохраняются.",
        "Platform membership, the account and access to other organizations remain.",
        "A participação Platform, a conta e o acesso a outras organizações mantêm-se.",
        "Członkostwo Platform, konto i dostęp do innych organizacji pozostają.",
        "Platform-Mitgliedschaft, Konto und Zugriff auf andere Organisationen bleiben erhalten.",
        "Het Platform-lidmaatschap, het account en toegang tot andere organisaties blijven behouden.",
    ),
    "block_counts": (
        "Кількість чинних ролей за областями:",
        "Количество действующих ролей по областям:",
        "Number of effective roles by scope:",
        "Número de funções em vigor por âmbito:",
        "Liczba obowiązujących ról według zakresu:",
        "Anzahl gültiger Rollen nach Geltungsbereich:",
        "Aantal geldige rollen per bereik:",
    ),
    "block_confirm": (
        "Підтвердити блокування", "Подтвердить блокировку", "Confirm block",
        "Confirmar bloqueio", "Potwierdź blokadę", "Sperrung bestätigen",
        "Blokkering bevestigen",
    ),
    "block_done": (
        "Construction-доступ оброблено. Відкликано ролей:",
        "Construction-доступ обработан. Отозвано ролей:",
        "Construction access processed. Roles revoked:",
        "Acesso ao Construction processado. Funções revogadas:",
        "Dostęp do Construction przetworzony. Cofnięte role:",
        "Construction-Zugriff verarbeitet. Widerrufene Rollen:",
        "Construction-toegang verwerkt. Ingetrokken rollen:",
    ),
    "block_uncertain": (
        "Результат запиту не підтверджено. Перевірте поточний доступ. Цей запит автоматично не повторюється.",
        "Результат запроса не подтверждён. Проверьте текущий доступ. Этот запрос автоматически не повторяется.",
        "The request result is unconfirmed. Check current access. This request is not automatically repeated.",
        "O resultado do pedido não foi confirmado. Verifique o acesso atual. Este pedido não é repetido automaticamente.",
        "Wynik żądania nie został potwierdzony. Sprawdź aktualny dostęp. To żądanie nie jest automatycznie ponawiane.",
        "Das Anfrageergebnis ist unbestätigt. Prüfen Sie den aktuellen Zugriff. Diese Anfrage wird nicht automatisch wiederholt.",
        "Het resultaat van het verzoek is niet bevestigd. Controleer de huidige toegang. Dit verzoek wordt niet automatisch herhaald.",
    ),
    "block_denied": (
        "Блокування недоступне. Оновіть команду та перевірте повноваження.",
        "Блокировка недоступна. Обновите команду и проверьте полномочия.",
        "Blocking is unavailable. Refresh the team and check authority.",
        "O bloqueio não está disponível. Atualize a equipa e verifique a autoridade.",
        "Blokada jest niedostępna. Odśwież zespół i sprawdź uprawnienia.",
        "Die Sperrung ist nicht verfügbar. Aktualisieren Sie das Team und prüfen Sie die Befugnisse.",
        "Blokkeren is niet beschikbaar. Vernieuw het team en controleer de bevoegdheid.",
    ),
    "block_other_pending": (
        "Спочатку отримайте результат незавершеної операції керування доступом або membership.",
        "Сначала получите результат незавершённой операции управления доступом или membership.",
        "Retrieve the unfinished access or membership operation result first.",
        "Obtenha primeiro o resultado da operação de acesso ou participação pendente.",
        "Najpierw pobierz wynik niedokończonej operacji dostępu lub członkostwa.",
        "Rufen Sie zuerst das Ergebnis der offenen Zugriffs- oder Mitgliedschaftsoperation ab.",
        "Haal eerst het resultaat van de openstaande toegangs- of lidmaatschapsbewerking op.",
    ),
    "block_inspect": (
        "Перевірити поточний доступ", "Проверить текущий доступ",
        "Check current access", "Verificar acesso atual",
        "Sprawdź aktualny dostęp", "Aktuellen Zugriff prüfen",
        "Huidige toegang controleren",
    ),
    "block_observed": (
        "Нижче — поточний стан у списку команди. Він не підтверджує результат попереднього запиту.",
        "Ниже — текущее состояние в списке команды. Оно не подтверждает результат предыдущего запроса.",
        "Below is the current team-list state. It does not confirm the previous request result.",
        "Segue-se o estado atual na lista da equipa. Não confirma o resultado do pedido anterior.",
        "Poniżej jest aktualny stan na liście zespołu. Nie potwierdza wyniku poprzedniego żądania.",
        "Unten steht der aktuelle Teamlistenstand. Er bestätigt nicht das Ergebnis der vorherigen Anfrage.",
        "Hieronder staat de huidige situatie in de teamlijst. Dit bevestigt niet het resultaat van het vorige verzoek.",
    ),
})
