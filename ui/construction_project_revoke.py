"""Labels for explicit revocation of one project-scoped role."""
from ui.construction_project_team import tr as common

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("revoke", "Відкликати роль", "Отозвать роль", "Revoke role",
      "Revogar função", "Cofnij rolę", "Rolle widerrufen", "Rol intrekken")
label("choose_revoke", "Оберіть конкретну роль об’єкта для відкликання.",
      "Выберите конкретную роль объекта для отзыва.",
      "Choose the project role to revoke.",
      "Escolha a função da obra a revogar.",
      "Wybierz rolę w obiekcie do cofnięcia.",
      "Wählen Sie die zu widerrufende Projektrolle.",
      "Kies de projectrol die u wilt intrekken.")
label("empty_revoke", "Немає доступних для відкликання ролей цього об’єкта.",
      "Нет доступных для отзыва ролей этого объекта.",
      "No project roles are available for revocation.",
      "Não há funções da obra disponíveis para revogar.",
      "Brak ról obiektu dostępnych do cofnięcia.",
      "Keine Projektrollen zum Widerruf verfügbar.",
      "Er zijn geen projectrollen beschikbaar om in te trekken.")
label("review_revoke", "Перевірте роль перед відкликанням.",
      "Проверьте роль перед отзывом.", "Review the role before revoking it.",
      "Verifique a função antes de a revogar.", "Sprawdź rolę przed cofnięciem.",
      "Prüfen Sie die Rolle vor dem Widerruf.", "Controleer de rol voordat u deze intrekt.")
label("impact", "Буде відкликано лише цю роль на цьому об’єкті. Інші чинні ролі можуть зберігати доступ.",
      "Будет отозвана только эта роль на этом объекте. Другие действующие роли могут сохранять доступ.",
      "Only this role on this project will be revoked. Other active roles may retain access.",
      "Só esta função nesta obra será revogada. Outras funções ativas podem manter o acesso.",
      "Cofnięta zostanie tylko ta rola w tym obiekcie. Inne aktywne role mogą zachować dostęp.",
      "Nur diese Rolle in diesem Projekt wird widerrufen. Andere aktive Rollen können Zugriff gewähren.",
      "Alleen deze rol voor dit project wordt ingetrokken. Andere actieve rollen kunnen toegang behouden.")
label("confirm_revoke", "Підтвердити відкликання", "Подтвердить отзыв", "Confirm revocation",
      "Confirmar revogação", "Potwierdź cofnięcie", "Widerruf bestätigen", "Intrekken bevestigen")
label("revoked", "✅ Роль на об’єкті відкликано.", "✅ Роль на объекте отозвана.",
      "✅ Project role revoked.", "✅ Função na obra revogada.",
      "✅ Rola w obiekcie cofnięta.", "✅ Projektrolle widerrufen.", "✅ Projectrol ingetrokken.")
label("deadline", "Строк завершення", "Срок окончания", "Expiry",
      "Data de validade", "Termin końcowy", "Ablaufdatum", "Einddatum")
label("other_revoke_pending", "Спочатку з’ясуйте результат попереднього відкликання в іншому об’єкті.",
      "Сначала выясните результат предыдущего отзыва в другом объекте.",
      "Resolve the previous revocation in the other project first.",
      "Resolva primeiro a revogação anterior na outra obra.",
      "Najpierw ustal wynik poprzedniego cofnięcia w innym obiekcie.",
      "Klären Sie zuerst den vorherigen Widerruf im anderen Projekt.",
      "Los eerst de vorige intrekking in het andere project op.")


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    if key in LABELS:
        return LABELS[key].get(language, LABELS[key]["ru"])
    return common(key, language)
