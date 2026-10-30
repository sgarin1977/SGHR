"""Labels for explicit replacement of one project role."""
from ui.construction_project_revoke import tr as common

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("replace", "Змінити роль / строк", "Изменить роль / срок", "Change role / expiry",
      "Alterar função / prazo", "Zmień rolę / termin", "Rolle / Ablaufdatum ändern",
      "Rol / vervaldatum wijzigen")
label("choose_replace", "Оберіть чинну роль об’єкта для заміни.",
      "Выберите действующую роль объекта для замены.",
      "Choose the existing project role to replace.",
      "Escolha a função atual da obra a substituir.",
      "Wybierz aktualną rolę obiektu do zastąpienia.",
      "Wählen Sie die zu ersetzende Projektrolle.",
      "Kies de bestaande projectrol die u wilt vervangen.")
label("empty_replace", "Немає доступних варіантів заміни ролі.",
      "Нет доступных вариантов замены роли.",
      "No role replacements are available.",
      "Não há substituições de função disponíveis.",
      "Brak dostępnych zmian roli.",
      "Keine Rollenänderungen verfügbar.",
      "Er zijn geen rolwijzigingen beschikbaar.")
label("old_role", "Поточна роль", "Текущая роль", "Current role",
      "Função atual", "Aktualna rola", "Aktuelle Rolle", "Huidige rol")
label("new_role", "Нова роль", "Новая роль", "New role",
      "Nova função", "Nowa rola", "Neue Rolle", "Nieuwe rol")
label("review_replace", "Перевірте заміну ролі перед підтвердженням.",
      "Проверьте замену роли перед подтверждением.",
      "Review the role replacement before confirming.",
      "Verifique a alteração da função antes de confirmar.",
      "Sprawdź zmianę roli przed potwierdzeniem.",
      "Prüfen Sie die Rollenänderung vor der Bestätigung.",
      "Controleer de rolwijziging voordat u bevestigt.")
label("preserve_expiry", "Строк завершення доступу зберігається.",
      "Срок окончания доступа сохраняется.",
      "The existing access expiry is preserved.",
      "A validade atual do acesso é preservada.",
      "Dotychczasowy termin dostępu zostaje zachowany.",
      "Das bestehende Ablaufdatum bleibt erhalten.",
      "De bestaande einddatum van de toegang blijft behouden.")
label("confirm_replace", "Підтвердити заміну", "Подтвердить замену", "Confirm replacement",
      "Confirmar substituição", "Potwierdź zmianę", "Änderung bestätigen", "Wijziging bevestigen")
label("replaced", "✅ Доступ до об’єкта оновлено.", "✅ Доступ к объекту обновлён.",
      "✅ Project access updated.", "✅ Acesso à obra atualizado.",
      "✅ Dostęp do obiektu zaktualizowany.", "✅ Projektzugriff aktualisiert.",
      "✅ Projecttoegang bijgewerkt.")
label("other_replace_pending", "Спочатку з’ясуйте результат попередньої заміни в іншому об’єкті.",
      "Сначала выясните результат предыдущей замены в другом объекте.",
      "Resolve the previous replacement in the other project first.",
      "Resolva primeiro a substituição anterior na outra obra.",
      "Najpierw ustal wynik poprzedniej zmiany w innym obiekcie.",
      "Klären Sie zuerst die vorherige Änderung im anderen Projekt.",
      "Los eerst de vorige wijziging in het andere project op.")


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    if key in LABELS:
        return LABELS[key].get(language, LABELS[key]["ru"])
    return common(key, language)

# Existing project access deadline labels v1.
label("edit_expiry", "Змінити строк доступу", "Изменить срок доступа", "Change access expiry",
      "Alterar prazo de acesso", "Zmień termin dostępu", "Zugriffsfrist ändern",
      "Vervaldatum van toegang wijzigen")
label("keep_expiry", "Зберегти чинний строк", "Сохранить действующий срок", "Keep current expiry",
      "Manter prazo atual", "Zachowaj aktualny termin", "Aktuelles Ablaufdatum beibehalten",
      "Huidige vervaldatum behouden")
label("old_expiry", "Чинний строк", "Действующий срок", "Current expiry",
      "Prazo atual", "Aktualny termin", "Aktuelles Ablaufdatum", "Huidige vervaldatum")
label("new_expiry", "Новий строк", "Новый срок", "New expiry",
      "Novo prazo", "Nowy termin", "Neues Ablaufdatum", "Nieuwe vervaldatum")
label("review_access", "Перевірте зміни доступу перед підтвердженням.",
      "Проверьте изменения доступа перед подтверждением.",
      "Review the access changes before confirming.",
      "Verifique as alterações de acesso antes de confirmar.",
      "Sprawdź zmiany dostępu przed potwierdzeniem.",
      "Prüfen Sie die Zugriffsänderungen vor der Bestätigung.",
      "Controleer de toegangswijzigingen voordat u bevestigt.")
label("unchanged", "Роль і строк не змінилися. Операція не потрібна.",
      "Роль и срок не изменились. Операция не требуется.",
      "The role and expiry are unchanged. No operation is needed.",
      "A função e o prazo não mudaram. Não é necessária qualquer operação.",
      "Rola i termin nie zmieniły się. Operacja nie jest potrzebna.",
      "Rolle und Ablaufdatum sind unverändert. Es ist keine Aktion erforderlich.",
      "Rol en vervaldatum zijn ongewijzigd. Er is geen bewerking nodig.")
