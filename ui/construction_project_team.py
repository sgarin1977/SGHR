"""Project team presentation; authorization stays in the API."""
from ui.construction_team import tr as team_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "👥 Команда об’єкта", "👥 Команда объекта", "👥 Project team",
      "👥 Equipa da obra", "👥 Zespół obiektu", "👥 Projektteam", "👥 Projectteam")
label("participation", "Участь в об’єкті", "Участие в объекте", "Project participation",
      "Participação na obra", "Udział w obiekcie", "Projektteilnahme", "Projectdeelname")
label("organization_access", "Доступ організації", "Доступ организации", "Organization access",
      "Acesso da organização", "Dostęp organizacji", "Organisationszugriff", "Organisatietoegang")
label("empty", "Учасників об’єкта поки немає.", "Участников объекта пока нет.",
      "No project participants yet.", "Ainda não há participantes na obra.",
      "Brak uczestników obiektu.", "Noch keine Projektteilnehmer.",
      "Nog geen projectdeelnemers.")
label("add", "Додати учасника", "Добавить участника", "Add participant",
      "Adicionar participante", "Dodaj uczestnika", "Teilnehmer hinzufügen", "Deelnemer toevoegen")
label("choose", "Оберіть користувача з наявної Construction-команди.",
      "Выберите пользователя из существующей Construction-команды.",
      "Choose a user from the existing Construction team.",
      "Escolha um utilizador da equipa Construction existente.",
      "Wybierz użytkownika z istniejącego zespołu Construction.",
      "Wählen Sie einen Benutzer aus dem bestehenden Construction-Team.",
      "Kies een gebruiker uit het bestaande Construction-team.")
label("role", "Оберіть роль на цьому об’єкті.", "Выберите роль на этом объекте.",
      "Choose a role for this project.", "Escolha uma função nesta obra.",
      "Wybierz rolę w tym obiekcie.", "Wählen Sie eine Rolle für dieses Projekt.",
      "Kies een rol voor dit project.")
label("expiry", "Підтвердіть строк дії доступу.", "Подтвердите срок действия доступа.",
      "Confirm the access duration.", "Confirme a duração do acesso.",
      "Potwierdź okres dostępu.", "Bestätigen Sie die Zugriffsdauer.",
      "Bevestig de toegangsduur.")
label("no_expiry", "Без строку завершення", "Без срока окончания", "No expiry",
      "Sem data de validade", "Bez terminu końcowego", "Ohne Ablaufdatum", "Geen einddatum")
label("review", "Підтвердьте додавання ролі на цей об’єкт.",
      "Подтвердите добавление роли на этот объект.",
      "Confirm adding this role to the project.",
      "Confirme a atribuição desta função na obra.",
      "Potwierdź dodanie tej roli do obiektu.",
      "Bestätigen Sie das Hinzufügen dieser Projektrolle.",
      "Bevestig het toevoegen van deze projectrol.")
label("confirm", "Підтвердити додавання", "Подтвердить добавление", "Confirm addition",
      "Confirmar adição", "Potwierdź dodanie", "Hinzufügen bestätigen", "Toevoegen bevestigen")
label("pending", "Запит уже надсилався. Повтор поверне результат тієї самої операції.",
      "Запрос уже отправлялся. Повтор вернёт результат той же операции.",
      "The request was sent. Retry retrieves the same operation's result.",
      "O pedido foi enviado. Repetir recupera o resultado da mesma operação.",
      "Żądanie wysłano. Ponowienie pobiera wynik tej samej operacji.",
      "Die Anfrage wurde gesendet. Wiederholen ruft dasselbe Ergebnis ab.",
      "Het verzoek is verzonden. Opnieuw proberen haalt hetzelfde resultaat op.")
label("retry", "Повторити запит", "Повторить запрос", "Retry request",
      "Repetir pedido", "Ponów żądanie", "Anfrage wiederholen", "Verzoek opnieuw proberen")
label("expired", "Вікно безпечного повтору минуло. Перевірте актуальну команду об’єкта.",
      "Окно безопасного повтора истекло. Проверьте текущую команду объекта.",
      "The safe retry window has ended. Check the current project team.",
      "O prazo de repetição segura terminou. Verifique a equipa atual.",
      "Okno bezpiecznego ponowienia minęło. Sprawdź aktualny zespół.",
      "Das sichere Wiederholungsfenster ist abgelaufen. Prüfen Sie das aktuelle Team.",
      "De veilige herhaaltermijn is verstreken. Controleer het huidige team.")
label("other_pending", "Спочатку з’ясуйте результат попереднього додавання в іншому об’єкті.",
      "Сначала выясните результат предыдущего добавления в другом объекте.",
      "Resolve the previous addition in the other project first.",
      "Resolva primeiro a adição anterior na outra obra.",
      "Najpierw ustal wynik poprzedniego dodania w innym obiekcie.",
      "Klären Sie zuerst das vorherige Hinzufügen im anderen Projekt.",
      "Los eerst de vorige toevoeging in het andere project op.")
label("done", "✅ Роль на об’єкті додано.", "✅ Роль на объекте добавлена.",
      "✅ Project role added.", "✅ Função na obra adicionada.",
      "✅ Rola w obiekcie dodana.", "✅ Projektrolle hinzugefügt.", "✅ Projectrol toegevoegd.")
label("list", "До команди об’єкта", "К команде объекта", "Project team",
      "Equipa da obra", "Zespół obiektu", "Zum Projektteam", "Naar projectteam")
label("back", "До картки об’єкта", "К карточке объекта", "Project card",
      "Ficha da obra", "Karta obiektu", "Zur Projektkarte", "Naar projectkaart")


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    if key in LABELS:
        return LABELS[key].get(language, LABELS[key]["ru"])
    return team_tr(key, language)


def clip(value, limit=100):
    return str(value or "").strip()[:limit]


def member_name(member, number, language):
    return clip(
        member.get("display_name") or member.get("email")
        or f'{tr("member", language)} {number}', 80,
    )


def project_team_text(result, *, offset, language):
    lines = [
        tr("title", language), clip(result["organization"]),
        clip(result["project_name"]),
    ]
    members = result["items"]
    if not any(
        grant["scope_type"] == "project"
        for member in members for grant in member["grants"]
    ):
        lines.append(tr("empty", language))
    if members:
        lines.append(f"{offset + 1}–{min(offset + 5, len(members))} / {len(members)}")
    for number, member in enumerate(members[offset:offset + 5], offset + 1):
        parts = [f"{number}. {member_name(member, number, language)}"]
        for scope, key in (
            ("project", "participation"), ("tenant", "organization_access"),
        ):
            roles = sorted({
                grant["role"] for grant in member["grants"]
                if grant["scope_type"] == scope
            })
            if roles:
                parts.append(tr(key, language) + ": " + ", ".join(
                    tr(role, language) for role in roles
                ))
        lines.append("\n".join(parts))
    return "\n\n".join(lines)

# Project participant deadline presentation v1.
label("date", "Задати дату завершення", "Задать дату окончания", "Set expiry date",
      "Definir data de fim", "Ustaw datę zakończenia", "Ablaufdatum festlegen",
      "Vervaldatum instellen")
label("until", "Доступ чинний до", "Доступ действует до", "Access expires",
      "Acesso válido até", "Dostęp ważny do", "Zugriff gültig bis", "Toegang verloopt")
label("date_prompt",
      "Надішліть майбутню дату завершення доступу з часовим поясом.\nФормат: YYYY-MM-DD HH:MM+HH:MM\nПриклад: 2030-12-31 18:00+02:00",
      "Отправьте будущую дату окончания доступа с часовым поясом.\nФормат: YYYY-MM-DD HH:MM+HH:MM\nПример: 2030-12-31 18:00+02:00",
      "Send a future access expiry with a time zone.\nFormat: YYYY-MM-DD HH:MM+HH:MM\nExample: 2030-12-31 18:00+02:00",
      "Envie uma data futura de fim de acesso com fuso horário.\nFormato: YYYY-MM-DD HH:MM+HH:MM\nExemplo: 2030-12-31 18:00+02:00",
      "Wyślij przyszłą datę końca dostępu ze strefą czasową.\nFormat: YYYY-MM-DD HH:MM+HH:MM\nPrzykład: 2030-12-31 18:00+02:00",
      "Senden Sie ein zukünftiges Zugriffsende mit Zeitzone.\nFormat: YYYY-MM-DD HH:MM+HH:MM\nBeispiel: 2030-12-31 18:00+02:00",
      "Stuur een toekomstige vervaldatum voor toegang met tijdzone.\nFormaat: YYYY-MM-DD HH:MM+HH:MM\nVoorbeeld: 2030-12-31 18:00+02:00")
