"""Client-card presentation; no queries or historical attribution."""
from datetime import datetime, timezone

from ui.construction_activity import activity_label, display_deadline
from ui.construction_lead_status import status_label

SECTIONS = ("projects", "leads", "activities", "history")
LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}

def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))

label("summary",
    "👤 Картка клієнта", "👤 Карточка клиента", "👤 Client card",
    "👤 Ficha do cliente", "👤 Karta klienta", "👤 Kundenkarte", "👤 Klantkaart")
label("projects",
    "🏗 Об’єкти", "🏗 Объекты", "🏗 Projects", "🏗 Obras",
    "🏗 Obiekty", "🏗 Projekte", "🏗 Projecten")
label("leads",
    '\U0001f4cb \u0417\u0432\u0435\u0440\u043d\u0435\u043d\u043d\u044f',
    '\U0001f4cb \u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f',
    '\U0001f4cb Leads',
    '\U0001f4cb Contactos',
    '\U0001f4cb Zg\u0142oszenia',
    '\U0001f4cb Anfragen',
    '\U0001f4cb Aanvragen')
label("activities",
    "📅 Заплановані дії", "📅 Запланированные действия", "📅 Planned activities",
    "📅 Atividades planeadas", "📅 Zaplanowane działania",
    "📅 Geplante Aktivitäten", "📅 Geplande activiteiten")
label("history",
    "🕘 Історія", "🕘 История", "🕘 History", "🕘 Histórico",
    "🕘 Historia", "🕘 Verlauf", "🕘 Geschiedenis")
label("phone",
    "Телефон", "Телефон", "Phone", "Telefone", "Telefon", "Telefon", "Telefoon")
label("email",
    "Email", "Email", "Email", "Email", "Email", "E-Mail", "E-mail")
label("notes",
    "Нотатки", "Заметки", "Notes", "Notas", "Notatki", "Notizen", "Notities")
label("person",
    "Фізична особа", "Физическое лицо", "Individual", "Pessoa singular",
    "Osoba fizyczna", "Privatperson", "Particulier")
label("company",
    "Компанія", "Компания", "Company", "Empresa",
    "Firma", "Unternehmen", "Bedrijf")
label("refresh",
    "Оновити картку", "Обновить карточку", "Refresh card", "Atualizar ficha",
    "Odśwież kartę", "Karte aktualisieren", "Kaart verversen")
label("first",
    "На початок цього списку", "В начало этого списка", "First page of this list",
    "Primeira página desta lista", "Pierwsza strona tej listy",
    "Erste Seite dieser Liste", "Eerste pagina van deze lijst")
label("empty",
    "На цій сторінці немає записів.", "На этой странице нет записей.",
    "No records on this page.", "Não há registos nesta página.",
    "Brak wpisów na tej stronie.", "Keine Einträge auf dieser Seite.",
    "Geen gegevens op deze pagina.")
label("project_draft",
    "Чернетка", "Черновик", "Draft", "Rascunho", "Szkic", "Entwurf", "Concept")
label("project_planning",
    "Планування", "Планирование", "Planning", "Planeamento",
    "Planowanie", "Planung", "Planning")
label("project_active",
    "Активний", "Активный", "Active", "Ativo", "Aktywny", "Aktiv", "Actief")
label("project_completed",
    "Завершений", "Завершён", "Completed", "Concluído",
    "Zakończony", "Abgeschlossen", "Afgerond")
label("project_cancelled",
    "Скасований", "Отменён", "Cancelled", "Cancelado",
    "Anulowany", "Storniert", "Geannuleerd")
label("entity_client",
    "Клієнт", "Клиент", "Client", "Cliente", "Klient", "Kunde", "Klant")
label("entity_project",
    "Об’єкт", "Объект", "Project", "Obra", "Obiekt", "Projekt", "Project")
label("entity_lead",
    "Звернення", "Обращение", "Lead", "Contacto", "Zgłoszenie", "Anfrage", "Aanvraag")
label("entity_activity",
    "Дія", "Действие", "Activity", "Atividade", "Działanie", "Aktivität", "Activiteit")
label("op_created",
    "Створено", "Создано", "Created", "Criado", "Utworzono", "Erstellt", "Aangemaakt")
label("op_updated",
    "Оновлено", "Обновлено", "Updated", "Atualizado", "Zaktualizowano",
    "Aktualisiert", "Bijgewerkt")
label("op_archived",
    "Архівовано", "Архивировано", "Archived", "Arquivado", "Zarchiwizowano",
    "Archiviert", "Gearchiveerd")
label("op_status",
    "Змінено статус", "Изменён статус", "Status changed", "Estado alterado",
    "Zmieniono status", "Status geändert", "Status gewijzigd")
label("op_converted",
    "Перетворено на клієнта й об’єкт", "Преобразовано в клиента и объект",
    "Converted to client and project", "Convertido em cliente e obra",
    "Przekształcono w klienta i obiekt", "In Kunde und Projekt umgewandelt",
    "Omgezet naar klant en project")
label("op_completed",
    "Виконано", "Выполнено", "Completed", "Concluído", "Wykonano",
    "Abgeschlossen", "Afgerond")
label("op_cancelled",
    "Скасовано", "Отменено", "Cancelled", "Cancelado", "Anulowano",
    "Storniert", "Geannuleerd")

EVENTS = {
    "construction_client_created": ("client", "created"),
    "construction_client_updated": ("client", "updated"),
    "construction_client_archived": ("client", "archived"),
    "construction_project_created": ("project", "created"),
    "construction_project_updated": ("project", "updated"),
    "construction_project_status_changed": ("project", "status"),
    "construction_lead_created": ("lead", "created"),
    "construction_lead_status_changed": ("lead", "status"),
    "construction_lead_converted": ("lead", "converted"),
    "construction_activity_created": ("activity", "created"),
    "construction_activity_completed": ("activity", "completed"),
    "construction_activity_cancelled": ("activity", "cancelled"),
}

def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])

def clip(value, limit=160):
    text = str(value or "—")
    return text if len(text) <= limit else text[:limit - 1] + "…"

def card_text(result, section, language):
    item = result["item"]
    lines = [
        tr("summary", language),
        clip(result["organization"], 100),
        clip(item["display_name"], 180),
    ]
    if section == "summary":
        lines.extend([
            tr(item["client_type"], language),
            tr("phone", language) + ": " + clip(item["phone"]),
            tr("email", language) + ": " + clip(item["email"]),
            tr("notes", language) + ":\n" + clip(item["notes"], 600),
        ])
    else:
        lines.append(tr(section, language))
        if not item[section]:
            lines.append(tr("empty", language))
        for record in item[section]:
            if section == "projects":
                text = "\n".join([
                    clip(record["name"], 100),
                    tr("project_" + record["status"], language),
                    clip(record["address_raw"], 160),
                ])
            elif section == "leads":
                text = (
                    clip(record["name"], 140)
                    + "\n" + status_label(record["status"], language)
                )
            elif section == "activities":
                text = "\n".join([
                    activity_label(record["type"], language),
                    display_deadline(record["due_at"], record["timezone"])
                        + " (" + clip(record["timezone"], 60) + ")",
                    clip(record["comment"], 160),
                ])
            else:
                entity, operation = EVENTS[record["event_type"]]
                at = datetime.fromisoformat(
                    record["created_at"].replace("Z", "+00:00")
                )
                if at.tzinfo is None:
                    raise ValueError("Aware history timestamp required.")
                text = (
                    at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
                    + "\n" + tr("entity_" + entity, language)
                    + " · " + tr("op_" + operation, language)
                )
                reason = record["payload"].get("reason")
                if reason:
                    text += "\n" + clip(reason, 160)
            lines.append(text)
    result_text = "\n\n".join(lines)
    if len(result_text) > 3500:
        raise ValueError("Client card presentation is too large.")
    return result_text

# Client self identity presentation v1.
label("identity", "Зв’язок з моїм акаунтом", "Связь с моим аккаунтом", "Link to my account",
      "Ligação à minha conta", "Powiązanie z moim kontem", "Verknüpfung mit meinem Konto",
      "Koppeling met mijn account")
label("identity_none", "Картку ще не пов’язано з Platform-акаунтом.",
      "Карточка ещё не связана с Platform-аккаунтом.",
      "This card is not linked to a Platform account.",
      "Esta ficha ainda não está ligada a uma conta Platform.",
      "Ta karta nie jest jeszcze powiązana z kontem Platform.",
      "Diese Karte ist noch mit keinem Platform-Konto verknüpft.",
      "Deze kaart is nog niet gekoppeld aan een Platform-account.")
label("identity_mine", "Картку пов’язано з вашим акаунтом.",
      "Карточка связана с вашим аккаунтом.", "This card is linked to your account.",
      "Esta ficha está ligada à sua conta.", "Ta karta jest powiązana z Twoim kontem.",
      "Diese Karte ist mit Ihrem Konto verknüpft.", "Deze kaart is gekoppeld aan uw account.")
label("identity_other", "Картку пов’язано з іншим Platform-користувачем.",
      "Карточка связана с другим Platform-пользователем.",
      "This card is linked to another Platform user.",
      "Esta ficha está ligada a outro utilizador Platform.",
      "Ta karta jest powiązana z innym użytkownikiem Platform.",
      "Diese Karte ist mit einem anderen Platform-Benutzer verknüpft.",
      "Deze kaart is gekoppeld aan een andere Platform-gebruiker.")
label("identity_link", "Прив’язати до мого акаунта", "Привязать к моему аккаунту",
      "Link to my account", "Ligar à minha conta", "Powiąż z moim kontem",
      "Mit meinem Konto verknüpfen", "Aan mijn account koppelen")
label("identity_replace", "Замінити зв’язок на мій акаунт", "Заменить связь на мой аккаунт",
      "Replace link with my account", "Substituir ligação pela minha conta",
      "Zastąp powiązanie moim kontem", "Verknüpfung durch mein Konto ersetzen",
      "Koppeling vervangen door mijn account")
label("identity_review", "Підтвердьте прив’язку цієї картки до власного акаунта.",
      "Подтвердите привязку этой карточки к собственному аккаунту.",
      "Confirm linking this card to your own account.",
      "Confirme a ligação desta ficha à sua própria conta.",
      "Potwierdź powiązanie tej karty ze swoim kontem.",
      "Bestätigen Sie die Verknüpfung dieser Karte mit Ihrem eigenen Konto.",
      "Bevestig de koppeling van deze kaart aan uw eigen account.")
label("identity_replace_review",
      "Увага: чинний зв’язок з іншим користувачем буде замінено зв’язком з вашим акаунтом. Підтвердьте цю заміну.",
      "Внимание: действующая связь с другим пользователем будет заменена связью с вашим аккаунтом. Подтвердите эту замену.",
      "Warning: the existing link to another user will be replaced with a link to your account. Confirm this replacement.",
      "Atenção: a ligação atual a outro utilizador será substituída por uma ligação à sua conta. Confirme esta substituição.",
      "Uwaga: aktualne powiązanie z innym użytkownikiem zostanie zastąpione powiązaniem z Twoim kontem. Potwierdź tę zmianę.",
      "Achtung: Die bestehende Verknüpfung mit einem anderen Benutzer wird durch Ihr Konto ersetzt. Bestätigen Sie diesen Austausch.",
      "Let op: de bestaande koppeling met een andere gebruiker wordt vervangen door uw account. Bevestig deze vervanging.")
label("identity_boundary",
      "Це лише зв’язок облікових записів. Він не додає вас до команди організації та не надає доступу до її даних.",
      "Это только связь учётных записей. Она не добавляет вас в команду организации и не предоставляет доступ к её данным.",
      "This is an identity link only. It does not add you to the organization team or grant access to its data.",
      "Esta é apenas uma ligação de identidade. Não o adiciona à equipa da organização nem concede acesso aos seus dados.",
      "To wyłącznie powiązanie tożsamości. Nie dodaje Cię do zespołu organizacji ani nie daje dostępu do jej danych.",
      "Dies ist nur eine Identitätsverknüpfung. Sie fügt Sie nicht zum Organisationsteam hinzu und gewährt keinen Datenzugriff.",
      "Dit is alleen een identiteitskoppeling. U wordt niet aan het organisatieteam toegevoegd en krijgt geen toegang tot de gegevens.")
label("identity_done", "✅ Зв’язок із вашим акаунтом збережено.",
      "✅ Связь с вашим аккаунтом сохранена.", "✅ Link to your account saved.",
      "✅ Ligação à sua conta guardada.", "✅ Powiązanie z Twoim kontem zapisane.",
      "✅ Verknüpfung mit Ihrem Konto gespeichert.", "✅ Koppeling met uw account opgeslagen.")
label("identity_conflict", "Картка змінилася. Оновіть її та перевірте зв’язок перед новим підтвердженням.",
      "Карточка изменилась. Обновите её и проверьте связь перед новым подтверждением.",
      "The card has changed. Refresh it and check the link before another confirmation.",
      "A ficha mudou. Atualize-a e verifique a ligação antes de confirmar novamente.",
      "Karta uległa zmianie. Odśwież ją i sprawdź powiązanie przed ponownym potwierdzeniem.",
      "Die Karte wurde geändert. Aktualisieren und prüfen Sie die Verknüpfung vor einer neuen Bestätigung.",
      "De kaart is gewijzigd. Vernieuw deze en controleer de koppeling voordat u opnieuw bevestigt.")
label("identity_uncertain", "Результат операції невідомий. Оновіть стан зв’язку. Автоматичного повтору не буде.",
      "Результат операции неизвестен. Обновите состояние связи. Автоматического повтора не будет.",
      "The operation result is unknown. Refresh the link status. It will not be retried automatically.",
      "O resultado da operação é desconhecido. Atualize o estado da ligação. Não haverá repetição automática.",
      "Wynik operacji jest nieznany. Odśwież stan powiązania. Operacja nie zostanie automatycznie ponowiona.",
      "Das Ergebnis ist unbekannt. Aktualisieren Sie den Verknüpfungsstatus. Es erfolgt keine automatische Wiederholung.",
      "Het resultaat is onbekend. Vernieuw de koppelingsstatus. De bewerking wordt niet automatisch herhaald.")

# Client unlink presentation v1.
label("identity_unlink", "Відв’язати акаунт", "Отвязать аккаунт", "Unlink account",
      "Desassociar conta", "Odłącz konto", "Konto-Verknüpfung lösen", "Account ontkoppelen")
label("identity_unlink_confirm", "Підтвердити відв’язку", "Подтвердить отвязку", "Confirm unlink",
      "Confirmar desassociação", "Potwierdź odłączenie", "Trennung bestätigen", "Ontkoppeling bevestigen")
label("identity_unlink_own_review",
      "Підтвердьте видалення зв’язку цієї картки з вашим акаунтом.",
      "Подтвердите удаление связи этой карточки с вашим аккаунтом.",
      "Confirm removing the link between this card and your account.",
      "Confirme a remoção da ligação entre esta ficha e a sua conta.",
      "Potwierdź usunięcie powiązania tej karty ze swoim kontem.",
      "Bestätigen Sie das Entfernen der Verknüpfung dieser Karte mit Ihrem Konto.",
      "Bevestig het verwijderen van de koppeling tussen deze kaart en uw account.")
label("identity_unlink_editor_review",
      "Підтвердьте видалення чинного зв’язку картки з іншим акаунтом. Сервер перевірить ваші права редагування.",
      "Подтвердите удаление действующей связи карточки с другим аккаунтом. Сервер проверит ваши права редактирования.",
      "Confirm removing the card's current link to another account. The server will check your editing authority.",
      "Confirme a remoção da ligação atual da ficha a outra conta. O servidor verificará a sua autorização de edição.",
      "Potwierdź usunięcie aktualnego powiązania karty z innym kontem. Serwer sprawdzi uprawnienia do edycji.",
      "Bestätigen Sie das Entfernen der bestehenden Verknüpfung mit einem anderen Konto. Der Server prüft Ihre Bearbeitungsrechte.",
      "Bevestig het verwijderen van de huidige koppeling met een ander account. De server controleert uw bewerkingsrechten.")
label("identity_unlink_boundary",
      "Відв’язка не видаляє акаунт, участь в організації, права доступу чи прив’язки Telegram.",
      "Отвязка не удаляет аккаунт, участие в организации, права доступа или привязки Telegram.",
      "Unlinking does not delete the account, organization membership, access rights or Telegram links.",
      "A desassociação não elimina a conta, a participação na organização, os direitos de acesso ou as ligações Telegram.",
      "Odłączenie nie usuwa konta, członkostwa w organizacji, uprawnień ani powiązań Telegram.",
      "Die Trennung löscht weder das Konto noch Organisationsmitgliedschaften, Zugriffsrechte oder Telegram-Verknüpfungen.",
      "Ontkoppelen verwijdert geen account, organisatielidmaatschap, toegangsrechten of Telegram-koppelingen.")
label("identity_unlink_done", "✅ Зв’язок акаунта з карткою видалено.",
      "✅ Связь аккаунта с карточкой удалена.", "✅ Account link removed from the card.",
      "✅ Ligação da conta à ficha removida.", "✅ Powiązanie konta z kartą usunięte.",
      "✅ Konto-Verknüpfung zur Karte entfernt.", "✅ Accountkoppeling met de kaart verwijderd.")
label("identity_own_refresh", "Перевірити мій зв’язок", "Проверить мою связь", "Check my link",
      "Verificar a minha ligação", "Sprawdź moje powiązanie", "Meine Verknüpfung prüfen",
      "Mijn koppeling controleren")
label("identity_own_unavailable",
      "Власний зв’язок із цією карткою недоступний. Його могло бути видалено або змінено.",
      "Собственная связь с этой карточкой недоступна. Она могла быть удалена или изменена.",
      "Your link to this card is unavailable. It may have been removed or changed.",
      "A sua ligação a esta ficha não está disponível. Pode ter sido removida ou alterada.",
      "Twoje powiązanie z tą kartą jest niedostępne. Mogło zostać usunięte lub zmienione.",
      "Ihre Verknüpfung mit dieser Karte ist nicht verfügbar. Sie wurde möglicherweise entfernt oder geändert.",
      "Uw koppeling met deze kaart is niet beschikbaar. Deze kan zijn verwijderd of gewijzigd.")

# Direct client activity button v1.
label("plan_activity", "Запланувати дію", "Запланировать действие", "Plan an activity",
      "Planear atividade", "Zaplanuj działanie", "Aktivität planen", "Activiteit plannen")

# Client activity terminal labels v1.
label("activity_action", "Запланована дія", "Запланированное действие", "Planned activity",
      "Atividade planeada", "Zaplanowane działanie", "Geplante Aktivität", "Geplande activiteit")
label("activity_choose", "Оберіть: виконати чи скасувати цю дію.",
      "Выберите: выполнить или отменить это действие.",
      "Choose whether to complete or cancel this activity.",
      "Escolha concluir ou cancelar esta atividade.",
      "Wybierz wykonanie lub anulowanie tego działania.",
      "Wählen Sie, ob Sie diese Aktivität abschließen oder abbrechen möchten.",
      "Kies of u deze activiteit wilt voltooien of annuleren.")
label("activity_resume", "Незавершений запит щодо дії", "Незавершённый запрос по действию",
      "Unresolved activity request", "Pedido de atividade por resolver",
      "Nierozstrzygnięte żądanie działania", "Offene Aktivitätsanfrage",
      "Onbevestigd activiteitsverzoek")
