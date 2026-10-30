"""Localized team presentation without raw permission codes or identity guessing."""
LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}

def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))

label("title", '👥 Команда організації', '👥 Команда организации', '👥 Organization team', '👥 Equipa da organização',
      '👥 Zespół organizacji', '👥 Organisationsteam', '👥 Organisatieteam')
label("empty", "Учасників з активним Construction-доступом поки немає.",
      "Участников с активным Construction-доступом пока нет.",
      "No members with active Construction access yet.",
      "Ainda não há membros com acesso ativo ao Construction.",
      "Brak uczestników z aktywnym dostępem do Construction.",
      "Noch keine Mitglieder mit aktivem Construction-Zugriff.",
      "Nog geen leden met actieve Construction-toegang.")
label("member", "Учасник", "Участник", "Member", "Membro",
      "Uczestnik", "Mitglied", "Lid")
label("you", "Ви", "Вы", "You", "Você", "Ty", "Sie", "Jij")
label("roles", "Ролі", "Роли", "Roles", "Funções", "Role", "Rollen", "Rollen")
label("tenant", "Організація", "Организация", "Organization", "Organização",
      "Organizacja", "Organisation", "Organisatie")
label("project", "Об’єкти", "Объекты", "Projects", "Obras",
      "Obiekty", "Projekte", "Projecten")
label("document", "Документи", "Документы", "Documents", "Documentos",
      "Dokumenty", "Dokumente", "Documenten")
label("refresh", "Оновити команду", "Обновить команду", "Refresh team",
      "Atualizar equipa", "Odśwież zespół", "Team aktualisieren", "Team verversen")
label("next", "Далі", "Далее", "Next", "Seguinte", "Dalej", "Weiter", "Volgende")
label("previous", "Назад", "Назад", "Previous", "Anterior",
      "Wstecz", "Zurück", "Vorige")
label("OWNER", "Власник", "Владелец", "Owner", "Proprietário",
      "Właściciel", "Eigentümer", "Eigenaar")
label("ADMIN", "Адміністратор", "Администратор", "Administrator",
      "Administrador", "Administrator", "Administrator", "Beheerder")
label("SURVEYOR", "Замірник", "Замерщик", "Surveyor", "Técnico de medição",
      "Pomiarowiec", "Aufmaßtechniker", "Inmeter")
label("ESTIMATOR", "Кошторисник", "Сметчик", "Estimator", "Orçamentista",
      "Kosztorysant", "Kalkulator", "Calculator")
label("PROJECT_MANAGER", "Керівник об’єкта", "Руководитель объекта",
      "Project manager", "Gestor de obra", "Kierownik projektu",
      "Projektleiter", "Projectleider")
label("EXECUTOR", "Виконавець", "Исполнитель", "Executor", "Executante",
      "Wykonawca", "Ausführender", "Uitvoerder")
label("OBSERVER", "Спостерігач", "Наблюдатель", "Observer", "Observador",
      "Obserwator", "Beobachter", "Waarnemer")

def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])

def clip(value, limit):
    return str(value or "").strip()[:limit]

def team_text(result, *, offset, language):
    items = result["items"]
    lines = [tr("title", language), clip(result["organization"], 120)]
    if not items:
        lines.append(tr("empty", language))
    else:
        end = min(offset + 5, len(items))
        lines.append(f"{offset + 1}–{end} / {len(items)}")
        for number, member in enumerate(items[offset:end], start=offset + 1):
            name = member["display_name"]
            if not name:
                name = (
                    tr("you", language)
                    if member["user_id"] == result["actor_user_id"]
                    else tr("member", language) + " " + str(number)
                )
            parts = [f"{number}. {clip(name, 80)}"]
            if member["email"]:
                parts.append(clip(member["email"], 120))
            parts.append(tr("roles", language) + ": " + ", ".join(
                tr(role, language) for role in member["roles"]
            ))
            scope_labels = []
            for scope in ("tenant", "project", "document"):
                ids = {
                    grant["scope_id"] for grant in member["grants"]
                    if grant["scope_type"] == scope
                }
                if ids:
                    scope_labels.append(
                        tr(scope, language)
                        + (": " + str(len(ids)) if scope != "tenant" else "")
                    )
            parts.append(" · ".join(scope_labels))
            lines.append("\n".join(parts))
    text = "\n\n".join(lines)
    if len(text) > 3800:
        raise ValueError("Team presentation is too large.")
    return text
