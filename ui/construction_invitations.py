"""Invitation sender presentation without tokens or delivery claims."""
from ui.construction_team import LANGUAGES, tr as team_tr
from ui.construction_company_profile import tr as common_tr

LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("button", "Запрошення", "Приглашения", "Invitations", "Convites",
      "Zaproszenia", "Einladungen", "Uitnodigingen")
label("empty", "Запрошень поки немає.", "Приглашений пока нет.", "No invitations yet.",
      "Ainda não existem convites.", "Brak zaproszeń.", "Noch keine Einladungen.", "Nog geen uitnodigingen.")
label("create", "Створити запрошення", "Создать приглашение", "Create invitation",
      "Criar convite", "Utwórz zaproszenie", "Einladung erstellen", "Uitnodiging maken")
label("email", "Надішліть email запрошуваного користувача.",
      "Отправьте email приглашаемого пользователя.", "Send the invited user's email.",
      "Envie o email do utilizador convidado.", "Wyślij email zapraszanej osoby.",
      "Senden Sie die E-Mail-Adresse der eingeladenen Person.", "Stuur het e-mailadres van de uitgenodigde gebruiker.")
label("role", "Оберіть роль із дозволених сервером.", "Выберите роль из разрешённых сервером.",
      "Choose a role allowed by the server.", "Escolha uma função permitida pelo servidor.",
      "Wybierz rolę dozwoloną przez serwer.", "Wählen Sie eine vom Server erlaubte Rolle.",
      "Kies een rol die door de server is toegestaan.")
label("resend", "Повторити доставку", "Повторить доставку", "Request delivery again",
      "Pedir novo envio", "Zleć ponowne dostarczenie", "Erneute Zustellung anfordern", "Opnieuw laten bezorgen")
label("revoke", "Відкликати запрошення", "Отозвать приглашение", "Revoke invitation",
      "Revogar convite", "Odwołaj zaproszenie", "Einladung widerrufen", "Uitnodiging intrekken")
label("created", "✅ Запрошення створено.", "✅ Приглашение создано.", "✅ Invitation created.",
      "✅ Convite criado.", "✅ Zaproszenie utworzone.", "✅ Einladung erstellt.", "✅ Uitnodiging aangemaakt.")
label("resent", "✅ Запрошення оновлено для повторної доставки.",
      "✅ Приглашение обновлено для повторной доставки.", "✅ Invitation updated for delivery again.",
      "✅ Convite atualizado para novo envio.", "✅ Zaproszenie zaktualizowane do ponownego dostarczenia.",
      "✅ Einladung für erneute Zustellung aktualisiert.", "✅ Uitnodiging bijgewerkt voor nieuwe bezorging.")
label("revoked_notice", "✅ Запрошення відкликано.", "✅ Приглашение отозвано.", "✅ Invitation revoked.",
      "✅ Convite revogado.", "✅ Zaproszenie odwołane.", "✅ Einladung widerrufen.", "✅ Uitnodiging ingetrokken.")
label("pending", "Очікує прийняття", "Ожидает принятия", "Awaiting acceptance",
      "A aguardar aceitação", "Oczekuje na przyjęcie", "Wartet auf Annahme", "Wacht op acceptatie")
label("accepted", "Прийнято", "Принято", "Accepted", "Aceite", "Przyjęte", "Angenommen", "Geaccepteerd")
label("declined", "Відхилено", "Отклонено", "Declined", "Recusado", "Odrzucone", "Abgelehnt", "Afgewezen")
label("expired", "Строк минув", "Срок истёк", "Expired", "Expirado", "Wygasłe", "Abgelaufen", "Verlopen")
label("revoked", "Відкликано", "Отозвано", "Revoked", "Revogado", "Odwołane", "Widerrufen", "Ingetrokken")
label("expires", "Запрошення чинне до", "Приглашение действует до", "Invitation expires",
      "Convite válido até", "Zaproszenie ważne do", "Einladung gültig bis", "Uitnodiging verloopt")
label("grant_expires", "Доступ чинний до", "Доступ действует до", "Access expires",
      "Acesso válido até", "Dostęp ważny do", "Zugriff gültig bis", "Toegang verloopt")
label("unlimited", "Без окремого строку", "Без отдельного срока", "No separate expiry",
      "Sem prazo adicional", "Bez osobnego terminu", "Ohne separates Ablaufdatum", "Geen afzonderlijke vervaldatum")
label("scope", "Обсяг доступу", "Область доступа", "Access scope", "Âmbito de acesso",
      "Zakres dostępu", "Zugriffsbereich", "Toegangsbereik")
label("before_accept", "До прийняття запрошення membership і доступ не надаються.",
      "До принятия приглашения membership и доступ не предоставляются.",
      "Membership and access are granted only after acceptance.",
      "A adesão e o acesso só são concedidos após a aceitação.",
      "Członkostwo i dostęp są nadawane dopiero po przyjęciu.",
      "Mitgliedschaft und Zugriff werden erst nach Annahme gewährt.",
      "Lidmaatschap en toegang worden pas na acceptatie verleend.")
label("resend_note", "Підтвердьте повторну доставку. Попередній одноразовий token стане недійсним.",
      "Подтвердите повторную доставку. Предыдущий одноразовый token станет недействительным.",
      "Confirm delivery again. The previous one-time token will be invalidated.",
      "Confirme o novo envio. O token anterior será invalidado.",
      "Potwierdź ponowne dostarczenie. Poprzedni token straci ważność.",
      "Bestätigen Sie die erneute Zustellung. Der vorherige Token wird ungültig.",
      "Bevestig nieuwe bezorging. Het vorige eenmalige token wordt ongeldig.")
label("revoke_note", "Підтвердьте відкликання цього запрошення.",
      "Подтвердите отзыв этого приглашения.", "Confirm revoking this invitation.",
      "Confirme a revogação deste convite.", "Potwierdź odwołanie tego zaproszenia.",
      "Bestätigen Sie den Widerruf dieser Einladung.", "Bevestig het intrekken van deze uitnodiging.")
label("list", "До списку запрошень", "К списку приглашений", "Back to invitations",
      "Voltar aos convites", "Wróć do zaproszeń", "Zurück zu Einladungen", "Terug naar uitnodigingen")
label("refresh", "Оновити запрошення", "Обновить приглашения", "Refresh invitations",
      "Atualizar convites", "Odśwież zaproszenia", "Einladungen aktualisieren", "Uitnodigingen vernieuwen")
label("retry", "Повторити той самий запит", "Повторить тот же запрос", "Retry the same request",
      "Repetir o mesmo pedido", "Ponów to samo żądanie", "Dieselbe Anfrage wiederholen", "Hetzelfde verzoek opnieuw proberen")
label("uncertain", "Результат запиту не підтверджено. Перевірте список або повторіть той самий запит.",
      "Результат запроса не подтверждён. Проверьте список или повторите тот же запрос.",
      "The request result is unconfirmed. Check the list or retry the same request.",
      "O resultado não foi confirmado. Verifique a lista ou repita o mesmo pedido.",
      "Wynik żądania jest niepotwierdzony. Sprawdź listę lub ponów to samo żądanie.",
      "Das Anfrageergebnis ist unbestätigt. Prüfen Sie die Liste oder wiederholen Sie dieselbe Anfrage.",
      "Het resultaat is niet bevestigd. Controleer de lijst of probeer hetzelfde verzoek opnieuw.")
label("finish", "Завершити без повтору", "Завершить без повтора", "Finish without retry",
      "Terminar sem repetir", "Zakończ bez ponawiania", "Ohne Wiederholung beenden", "Afronden zonder opnieuw te proberen")
label("finish_note", "Це прибере лише локальну спробу повтору. Запрошення на сервері не скасовується. Спочатку перевірте список.",
      "Это уберёт только локальную попытку повтора. Приглашение на сервере не отменяется. Сначала проверьте список.",
      "This only clears the local retry attempt. It does not revoke the server invitation. Check the list first.",
      "Isto apenas limpa a tentativa local. Não revoga o convite no servidor. Verifique primeiro a lista.",
      "Usuwa to tylko lokalną próbę ponowienia. Nie odwołuje zaproszenia na serwerze. Najpierw sprawdź listę.",
      "Dies entfernt nur den lokalen Wiederholungsversuch. Die Servereinladung wird nicht widerrufen. Prüfen Sie zuerst die Liste.",
      "Dit wist alleen de lokale herhaalpoging. De uitnodiging op de server wordt niet ingetrokken. Controleer eerst de lijst.")
label("window_expired", "Строк безпечного повтору минув. Перевірте список перед новою операцією.",
      "Срок безопасного повтора истёк. Проверьте список перед новой операцией.",
      "The safe retry window has expired. Check the list before another operation.",
      "O prazo seguro para repetir terminou. Verifique a lista antes de outra operação.",
      "Upłynął czas bezpiecznego ponowienia. Sprawdź listę przed kolejną operacją.",
      "Das sichere Wiederholungsfenster ist abgelaufen. Prüfen Sie die Liste vor einer weiteren Aktion.",
      "Het veilige herhaalvenster is verlopen. Controleer de lijst voor een nieuwe bewerking.")
label("other_pending", "Є незавершена спроба запрошення в іншому контексті. Поверніться до неї перед новим записом.",
      "Есть незавершённая попытка приглашения в другом контексте. Вернитесь к ней перед новой записью.",
      "There is an unfinished invitation attempt in another context. Return to it before another write.",
      "Existe uma tentativa de convite pendente noutro contexto. Volte a ela antes de outra alteração.",
      "Istnieje niedokończona próba zaproszenia w innym kontekście. Wróć do niej przed kolejnym zapisem.",
      "In einem anderen Kontext gibt es einen offenen Einladungsversuch. Kehren Sie vor einem weiteren Schreibvorgang dorthin zurück.",
      "Er staat een uitnodigingspoging open in een andere context. Ga terug voordat u een nieuwe wijziging doet.")


def tr(key, language):
    if key in LABELS:
        language = (language or "ru").lower().split("-", 1)[0]
        return LABELS[key].get(language, LABELS[key]["ru"])
    if key in ("OWNER", "ADMIN", "SURVEYOR", "ESTIMATOR", "PROJECT_MANAGER", "EXECUTOR", "OBSERVER",
               "tenant", "project", "next", "previous"):
        return team_tr(key, language)
    return common_tr(key, language)


def detail(item, language):
    scope_types = sorted({scope["scope_type"] for scope in item["scopes"]})
    return "\n\n".join([
        item["email"], tr(item["role"], language), tr(item["status"], language),
        tr("scope", language) + ": " + ", ".join(tr(kind, language) for kind in scope_types),
        tr("expires", language) + ": " + item["expires_at"],
        tr("grant_expires", language) + ": " + (
            item["grant_expires_at"] or tr("unlimited", language)
        ),
    ])

# Invitation grant deadline UI v1.
label("grant_date", "Задати строк доступу", "Задать срок доступа", "Set access expiry",
      "Definir prazo de acesso", "Ustaw termin dostępu", "Zugriffsfrist festlegen",
      "Vervaldatum voor toegang instellen")
label("grant_none", "Без обмеження строку", "Без ограничения срока", "No expiry",
      "Sem prazo", "Bez terminu", "Ohne Ablaufdatum", "Geen vervaldatum")
label("grant_back", "Назад до підтвердження", "Назад к подтверждению", "Back to review",
      "Voltar à confirmação", "Wróć do potwierdzenia", "Zurück zur Bestätigung",
      "Terug naar controle")
label("grant_prompt",
      "Надішліть майбутню дату завершення доступу з часовим поясом.\nФормат: YYYY-MM-DD HH:MM+HH:MM\nПриклад: 2030-12-31 18:00+02:00\nЦе строк доступу після прийняття, а не строк самого запрошення.",
      "Отправьте будущую дату окончания доступа с часовым поясом.\nФормат: YYYY-MM-DD HH:MM+HH:MM\nПример: 2030-12-31 18:00+02:00\nЭто срок доступа после принятия, а не срок самого приглашения.",
      "Send a future access expiry with a time zone.\nFormat: YYYY-MM-DD HH:MM+HH:MM\nExample: 2030-12-31 18:00+02:00\nThis limits access after acceptance, not the invitation lifetime.",
      "Envie uma data futura de fim de acesso com fuso horário.\nFormato: YYYY-MM-DD HH:MM+HH:MM\nExemplo: 2030-12-31 18:00+02:00\nEste prazo limita o acesso após a aceitação, não a validade do convite.",
      "Wyślij przyszłą datę końca dostępu ze strefą czasową.\nFormat: YYYY-MM-DD HH:MM+HH:MM\nPrzykład: 2030-12-31 18:00+02:00\nTo termin dostępu po przyjęciu, a nie ważności zaproszenia.",
      "Senden Sie ein zukünftiges Zugriffsende mit Zeitzone.\nFormat: YYYY-MM-DD HH:MM+HH:MM\nBeispiel: 2030-12-31 18:00+02:00\nDies begrenzt den Zugriff nach Annahme, nicht die Gültigkeit der Einladung.",
      "Stuur een toekomstige vervaldatum voor toegang met tijdzone.\nFormaat: YYYY-MM-DD HH:MM+HH:MM\nVoorbeeld: 2030-12-31 18:00+02:00\nDit beperkt toegang na acceptatie, niet de geldigheid van de uitnodiging.")
label("grant_invalid",
      "Потрібна коректна майбутня дата з часовим поясом, наприклад 2030-12-31 18:00+02:00.",
      "Нужна корректная будущая дата с часовым поясом, например 2030-12-31 18:00+02:00.",
      "Enter a valid future date with a time zone, for example 2030-12-31 18:00+02:00.",
      "Introduza uma data futura válida com fuso horário, por exemplo 2030-12-31 18:00+02:00.",
      "Wpisz poprawną przyszłą datę ze strefą czasową, np. 2030-12-31 18:00+02:00.",
      "Geben Sie ein gültiges zukünftiges Datum mit Zeitzone ein, z. B. 2030-12-31 18:00+02:00.",
      "Voer een geldige toekomstige datum met tijdzone in, bijvoorbeeld 2030-12-31 18:00+02:00.")


def parse_grant_expiry(value, *, now=None):
    """Parse explicit offset input; keep only a canonical UTC string in FSM."""
    from datetime import datetime, timezone
    import re

    if not isinstance(value, str):
        raise ValueError("Access expiry must be text.")
    value = value.strip()
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}"
        r"(?::[0-9]{2})?(?:Z|[+-][0-9]{2}:[0-9]{2})",
        value,
    ):
        raise ValueError("Explicit time zone is required.")
    try:
        deadline = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("Invalid access expiry.") from None
    if now is None:
        now = datetime.now(timezone.utc)
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
        or deadline <= now
    ):
        raise ValueError("Access expiry must be future.")
    return deadline.isoformat(timespec="seconds").replace("+00:00", "Z")
