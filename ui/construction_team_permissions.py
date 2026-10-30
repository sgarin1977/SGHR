"""Read-only presentation of API-supplied permissions per grant."""
from ui.construction_team import LANGUAGES, tr as team_tr

LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("rights", "Права учасника", "Права участника", "Member permissions",
      "Permissões do membro", "Uprawnienia uczestnika", "Mitgliederrechte", "Lidrechten")
label("scopes", "Області доступу", "Области доступа", "Access scopes",
      "Âmbitos de acesso", "Zakresy dostępu", "Zugriffsbereiche", "Toegangsbereiken")
label("readonly", "Права визначаються роллю. Цей список доступний лише для перегляду.",
      "Права определяются ролью. Этот список доступен только для просмотра.",
      "Permissions are defined by the role. This list is read-only.",
      "As permissões são definidas pela função. Esta lista é apenas de leitura.",
      "Uprawnienia wynikają z roli. Ta lista jest tylko do odczytu.",
      "Die Rechte werden durch die Rolle bestimmt. Diese Liste ist schreibgeschützt.",
      "Rechten worden door de rol bepaald. Deze lijst is alleen-lezen.")
label("scope_note", "Нижче — права лише для обраного доступу, без об’єднання з іншими областями.",
      "Ниже — права только для выбранного доступа, без объединения с другими областями.",
      "These permissions apply only to the selected grant, without merging other scopes.",
      "Estas permissões aplicam-se apenas ao acesso selecionado, sem juntar outros âmbitos.",
      "Te uprawnienia dotyczą tylko wybranego dostępu, bez łączenia innych zakresów.",
      "Diese Rechte gelten nur für den gewählten Zugriff, ohne andere Bereiche zusammenzufassen.",
      "Deze rechten gelden alleen voor de geselecteerde toegang, zonder andere bereiken samen te voegen.")
label("team", "До команди", "К команде", "Back to team",
      "Voltar à equipa", "Wróć do zespołu", "Zurück zum Team", "Terug naar team")
label("refresh", "Оновити права", "Обновить права", "Refresh permissions",
      "Atualizar permissões", "Odśwież uprawnienia", "Rechte aktualisieren", "Rechten vernieuwen")
label("expiry", "Доступ чинний до", "Доступ действует до", "Access expires",
      "Acesso válido até", "Dostęp ważny do", "Zugriff gültig bis", "Toegang verloopt")
label("unlimited", "Без строку завершення", "Без срока окончания", "No expiry",
      "Sem prazo", "Bez terminu", "Ohne Ablaufdatum", "Geen vervaldatum")
label("empty", "У цьому scope немає наданих прав.", "В этом scope нет предоставленных прав.",
      "No permissions are granted in this scope.", "Não há permissões concedidas neste âmbito.",
      "W tym zakresie nie przyznano uprawnień.", "In diesem Bereich wurden keine Rechte erteilt.",
      "Binnen dit bereik zijn geen rechten verleend.")

FAMILIES = {
    "projects": ("Об’єкти", "Объекты", "Projects", "Obras", "Obiekty", "Projekte", "Projecten"),
    "measurements": ("Заміри", "Замеры", "Measurements", "Medições", "Pomiary", "Aufmaße", "Metingen"),
    "estimates": ("Кошториси", "Сметы", "Estimates", "Orçamentos", "Kosztorysy", "Kalkulationen", "Begrotingen"),
    "pricing": ("Ціни", "Цены", "Pricing", "Preços", "Ceny", "Preise", "Prijzen"),
    "costs": ("Витрати", "Затраты", "Costs", "Custos", "Koszty", "Kosten", "Kosten"),
    "materials": ("Матеріали", "Материалы", "Materials", "Materiais", "Materiały", "Materialien", "Materialen"),
    "tax": ("Податки", "Налоги", "Taxes", "Impostos", "Podatki", "Steuern", "Belastingen"),
    "documents": ("Документи", "Документы", "Documents", "Documentos", "Dokumenty", "Dokumente", "Documenten"),
    "exports": ("Експорт", "Экспорт", "Exports", "Exportações", "Eksport", "Exporte", "Exports"),
    "team": ("Команда", "Команда", "Team", "Equipa", "Zespół", "Team", "Team"),
    "access": ("Права доступу", "Права доступа", "Access rights", "Direitos de acesso", "Uprawnienia", "Zugriffsrechte", "Toegangsrechten"),
    "logistics": ("Логістика", "Логистика", "Logistics", "Logística", "Logistyka", "Logistik", "Logistiek"),
    "planning": ("Планування", "Планирование", "Planning", "Planeamento", "Planowanie", "Planung", "Planning"),
    "workforce": ("Персонал", "Персонал", "Workforce", "Pessoal", "Personel", "Personal", "Personeel"),
    "workforce.crews": ("Бригади", "Бригады", "Crews", "Equipas de trabalho", "Brygady", "Arbeitskolonnen", "Werkploegen"),
    "workforce.cv": ("Резюме", "Резюме", "CVs", "Currículos", "CV", "Lebensläufe", "Cv's"),
    "workforce.profile": ("Професійні профілі", "Профессиональные профили", "Professional profiles", "Perfis profissionais", "Profile zawodowe", "Berufsprofile", "Beroepsprofielen"),
    "execution": ("Виконання робіт", "Выполнение работ", "Work execution", "Execução dos trabalhos", "Wykonanie prac", "Arbeitsausführung", "Werkuitvoering"),
    "project_documents": ("Документи об’єкта", "Документы объекта", "Project documents", "Documentos da obra", "Dokumenty obiektu", "Projektdokumente", "Projectdocumenten"),
    "contracts": ("Договори", "Договоры", "Contracts", "Contratos", "Umowy", "Verträge", "Contracten"),
    "settings": ("Налаштування", "Настройки", "Settings", "Definições", "Ustawienia", "Einstellungen", "Instellingen"),
}

ACTIONS = {
    "read": ("Перегляд", "Просмотр", "View", "Consultar", "Podgląd", "Ansehen", "Bekijken"),
    "create": ("Створення", "Создание", "Create", "Criar", "Tworzenie", "Erstellen", "Aanmaken"),
    "edit": ("Редагування", "Редактирование", "Edit", "Editar", "Edycja", "Bearbeiten", "Bewerken"),
    "submit": ("Подання", "Отправка на проверку", "Submit", "Submeter", "Przekazanie do weryfikacji", "Einreichen", "Indienen"),
    "verify": ("Перевірка", "Проверка", "Verify", "Verificar", "Weryfikacja", "Prüfen", "Verifiëren"),
    "approve": ("Затвердження", "Утверждение", "Approve", "Aprovar", "Zatwierdzanie", "Genehmigen", "Goedkeuren"),
    "generate": ("Формування", "Формирование", "Generate", "Gerar", "Generowanie", "Generieren", "Genereren"),
    "internal": ("Внутрішній", "Внутренний", "Internal", "Interno", "Wewnętrzny", "Intern", "Intern"),
    "client": ("Для клієнта", "Для клиента", "For client", "Para o cliente", "Dla klienta", "Für Kunden", "Voor klant"),
    "procurement": ("Для закупівель", "Для закупок", "For procurement", "Para compras", "Dla zakupów", "Für Beschaffung", "Voor inkoop"),
    "manage": ("Керування", "Управление", "Manage", "Gerir", "Zarządzanie", "Verwalten", "Beheren"),
    "assign": ("Призначення", "Назначение", "Assign", "Atribuir", "Przydzielanie", "Zuweisen", "Toewijzen"),
    "import": ("Імпорт", "Импорт", "Import", "Importar", "Importowanie", "Importieren", "Importeren"),
    "update": ("Оновлення", "Обновление", "Update", "Atualizar", "Aktualizacja", "Aktualisieren", "Bijwerken"),
    "activate": ("Активація", "Активация", "Activate", "Ativar", "Aktywacja", "Aktivieren", "Activeren"),
}


def language_index(language):
    language = (language or "ru").lower().split("-", 1)[0]
    return LANGUAGES.index(language) if language in LANGUAGES else LANGUAGES.index("ru")


def tr(key, language):
    if key not in LABELS:
        return team_tr(key, language)
    return LABELS[key][LANGUAGES[language_index(language)]]


def permission_label(code, language):
    if not isinstance(code, str) or not code.startswith("construction."):
        raise ValueError("Invalid permission code.")
    family, separator, action = code[len("construction."):].rpartition(".")
    if not separator or family not in FAMILIES or action not in ACTIONS:
        return code
    index = language_index(language)
    return FAMILIES[family][index] + " — " + ACTIONS[action][index]


def member_name(member, number, language):
    return str(
        member.get("display_name") or member.get("email")
        or tr("member", language) + " " + str(number)
    )[:65]


def member_button(member, number, language):
    return str(number) + ". " + tr("rights", language) + ": " + member_name(member, number, language)


def scope_text(grant, language, *, names=None, organization=None):
    kind = grant["scope_type"]
    if kind == "tenant":
        name = str(organization or "").strip()
        return tr(kind, language) + (": " + name[:150] if name else "")
    if kind == "project":
        name = (names or {}).get(grant["scope_id"])
        if isinstance(name, str) and name.strip():
            return tr(kind, language) + ": " + " ".join(name.split())[:150]
        return tr("project_unavailable", language)
    if kind == "document":
        return tr("document_unavailable", language)
    raise ValueError("Unknown permission scope.")


def scope_projection(member, grant):
    found = [
        item for item in member["scoped_permissions"] if item["grant_id"] == grant["id"]
    ]
    if len(found) != 1:
        raise ValueError("Permission scope is unavailable.")
    item = found[0]
    if any(item[key] != grant[key] for key in ("role", "scope_type", "scope_id")):
        raise ValueError("Permission scope differs from its grant.")
    permissions = item["permissions"]
    if (
        not isinstance(permissions, list) or len(set(permissions)) != len(permissions)
        or any(not isinstance(code, str) or not code.startswith("construction.") for code in permissions)
    ):
        raise ValueError("Invalid scoped permission projection.")
    return sorted(permissions)

label("project_unavailable",
      "Об’єкт: назва недоступна", "Объект: название недоступно",
      "Project: name unavailable", "Obra: nome indisponível",
      "Obiekt: nazwa niedostępna", "Objekt: Name nicht verfügbar",
      "Project: naam niet beschikbaar")
label("document_unavailable",
      "Документ: назва недоступна", "Документ: название недоступно",
      "Document: name unavailable", "Documento: nome indisponível",
      "Dokument: nazwa niedostępna", "Dokument: Name nicht verfügbar",
      "Document: naam niet beschikbaar")

# construction_scope_names_v1
