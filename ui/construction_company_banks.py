"""Company bank account presentation."""
from ui.construction_company_profile import LANGUAGES
from ui.construction_company_identifiers import tr as common_tr

LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("button", "Банківські рахунки", "Банковские счета", "Bank accounts",
      "Contas bancárias", "Rachunki bankowe", "Bankkonten", "Bankrekeningen")
label("title", "Банківські рахунки компанії", "Банковские счета компании",
      "Company bank accounts", "Contas bancárias da empresa",
      "Rachunki bankowe firmy", "Unternehmensbankkonten", "Bankrekeningen van het bedrijf")
label("empty", "Рахунків поки немає.", "Счетов пока нет.", "No bank accounts yet.",
      "Ainda não existem contas bancárias.", "Brak rachunków bankowych.",
      "Noch keine Bankkonten.", "Nog geen bankrekeningen.")
label("add", "Додати рахунок", "Добавить счёт", "Add account", "Adicionar conta",
      "Dodaj rachunek", "Konto hinzufügen", "Rekening toevoegen")
label("edit", "Редагувати рахунок", "Редактировать счёт", "Edit account", "Editar conta",
      "Edytuj rachunek", "Konto bearbeiten", "Rekening bewerken")
label("deactivate", "Деактивувати рахунок", "Деактивировать счёт", "Deactivate account",
      "Desativar conta", "Dezaktywuj rachunek", "Konto deaktivieren", "Rekening deactiveren")
label("list", "До списку рахунків", "К списку счетов", "Back to accounts",
      "Voltar às contas", "Wróć do rachunków", "Zurück zu Konten", "Terug naar rekeningen")
label("label", "Назва рахунку", "Название счёта", "Account label", "Designação da conta",
      "Nazwa rachunku", "Kontobezeichnung", "Rekeningnaam")
label("account_holder_name", "Власник рахунку", "Владелец счёта", "Account holder",
      "Titular da conta", "Posiadacz rachunku", "Kontoinhaber", "Rekeninghouder")
label("bank_name", "Назва банку", "Название банка", "Bank name", "Nome do banco",
      "Nazwa banku", "Bankname", "Banknaam")
label("country_code", "Країна банку: дволітерний ISO-код", "Страна банка: двухбуквенный ISO-код",
      "Bank country: two-letter ISO code", "País do banco: código ISO de duas letras",
      "Kraj banku: dwuliterowy kod ISO", "Bankland: ISO-Code mit zwei Buchstaben",
      "Land van de bank: ISO-code van twee letters")
label("iban", "IBAN", "IBAN", "IBAN", "IBAN", "IBAN", "IBAN", "IBAN")
label("bic_swift", "BIC / SWIFT", "BIC / SWIFT", "BIC / SWIFT", "BIC / SWIFT",
      "BIC / SWIFT", "BIC / SWIFT", "BIC / SWIFT")
label("account_number", "Локальний номер рахунку", "Локальный номер счёта",
      "Local account number", "Número de conta local", "Lokalny numer rachunku",
      "Lokale Kontonummer", "Lokaal rekeningnummer")
label("currency", "Валюта: трилітерний ISO-код, наприклад EUR або UAH",
      "Валюта: трёхбуквенный ISO-код, например EUR или UAH",
      "Currency: three-letter ISO code, for example EUR or UAH",
      "Moeda: código ISO de três letras, por exemplo EUR ou UAH",
      "Waluta: trzyliterowy kod ISO, np. EUR lub UAH",
      "Währung: ISO-Code mit drei Buchstaben, zum Beispiel EUR oder UAH",
      "Valuta: ISO-code van drie letters, bijvoorbeeld EUR of UAH")
label("is_primary", "Основний рахунок для цієї валюти?", "Основной счёт для этой валюты?",
      "Primary account for this currency?", "Conta principal para esta moeda?",
      "Główny rachunek dla tej waluty?", "Primärkonto für diese Währung?",
      "Primaire rekening voor deze valuta?")
label("show_in_documents", "Показувати в документах?", "Показывать в документах?",
      "Show in documents?", "Mostrar nos documentos?", "Pokazywać w dokumentach?",
      "In Dokumenten anzeigen?", "In documenten tonen?")
label("active", "Активний", "Активный", "Active", "Ativa", "Aktywny", "Aktiv", "Actief")
label("inactive", "Неактивний", "Неактивный", "Inactive", "Inativa", "Nieaktywny", "Inaktiv", "Inactief")
label("kind", "Оберіть формат рахунку.", "Выберите формат счёта.",
      "Choose the account format.", "Escolha o formato da conta.",
      "Wybierz format rachunku.", "Wählen Sie das Kontoformat.", "Kies het rekeningformaat.")
label("choose", "Оберіть поле для редагування.", "Выберите поле для редактирования.",
      "Choose a field to edit.", "Escolha um campo para editar.",
      "Wybierz pole do edycji.", "Wählen Sie ein Feld zum Bearbeiten.", "Kies een veld om te bewerken.")
label("create_note", "Інші поля можна заповнити після створення рахунку.",
      "Остальные поля можно заполнить после создания счёта.",
      "Other fields can be filled in after creating the account.",
      "Os restantes campos podem ser preenchidos após criar a conta.",
      "Pozostałe pola można uzupełnić po utworzeniu rachunku.",
      "Weitere Felder können nach dem Erstellen des Kontos ausgefüllt werden.",
      "Andere velden kunnen na het aanmaken van de rekening worden ingevuld.")
label("confirm_deactivate", "Підтвердьте деактивацію. Запис рахунку залишиться в БД.",
      "Подтвердите деактивацию. Запись счёта останется в БД.",
      "Confirm deactivation. The account record will be retained.",
      "Confirme a desativação. O registo da conta será mantido.",
      "Potwierdź dezaktywację. Zapis rachunku zostanie zachowany.",
      "Bestätigen Sie die Deaktivierung. Der Kontodatensatz bleibt erhalten.",
      "Bevestig de deactivering. Het rekeningrecord blijft bewaard.")
label("saved", "✅ Операцію з рахунком виконано.", "✅ Операция со счётом выполнена.",
      "✅ Bank account operation completed.", "✅ Operação com a conta concluída.",
      "✅ Operacja na rachunku zakończona.", "✅ Kontooperation abgeschlossen.",
      "✅ Rekeningbewerking voltooid.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])


TEXT_FIELDS = (
    "label", "account_holder_name", "bank_name", "country_code",
    "iban", "bic_swift", "account_number", "currency",
)
FLAGS = ("is_primary", "show_in_documents")
EDIT_FIELDS = TEXT_FIELDS + FLAGS


def value_text(value, language):
    if type(value) is bool:
        return tr("yes" if value else "no", language)
    return str(value) if value is not None else "—"


def account_text(item, language):
    lines = [
        tr("title", language),
        tr("active" if item["is_active"] else "inactive", language),
    ]
    for field in EDIT_FIELDS:
        lines.append(tr(field, language) + ":\n" + value_text(item[field], language))
    return "\n\n".join(lines)
