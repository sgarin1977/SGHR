"""Company identifiers presentation; type codes remain extensible."""
from ui.construction_company_profile import LANGUAGES, tr as company_tr

LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("button", "Ідентифікатори", "Идентификаторы", "Identifiers",
      "Identificadores", "Identyfikatory", "Kennungen", "Identificaties")
label("title", "Ідентифікатори компанії", "Идентификаторы компании",
      "Company identifiers", "Identificadores da empresa",
      "Identyfikatory firmy", "Unternehmenskennungen", "Bedrijfsidentificaties")
label("empty", "Ідентифікаторів поки немає.", "Идентификаторов пока нет.",
      "No identifiers yet.", "Ainda não existem identificadores.",
      "Brak identyfikatorów.", "Noch keine Kennungen.", "Nog geen identificaties.")
label("add", "Додати ідентифікатор", "Добавить идентификатор", "Add identifier",
      "Adicionar identificador", "Dodaj identyfikator", "Kennung hinzufügen", "Identificatie toevoegen")
label("replace", "Замінити ідентифікатор", "Заменить идентификатор", "Replace identifier",
      "Substituir identificador", "Zastąp identyfikator", "Kennung ersetzen", "Identificatie vervangen")
label("delete", "Видалити ідентифікатор", "Удалить идентификатор", "Delete identifier",
      "Eliminar identificador", "Usuń identyfikator", "Kennung löschen", "Identificatie verwijderen")
label("country_code", "Країна: код ISO із двох літер, наприклад UA або PT.",
      "Страна: код ISO из двух букв, например UA или PT.",
      "Country: two-letter ISO code, for example UA or PT.",
      "País: código ISO de duas letras, por exemplo UA ou PT.",
      "Kraj: dwuliterowy kod ISO, np. UA lub PT.",
      "Land: ISO-Code mit zwei Buchstaben, zum Beispiel UA oder PT.",
      "Land: ISO-code van twee letters, bijvoorbeeld UA of PT.")
label("identifier_type", "Код типу: tax_id, vat, company_registration або інший.",
      "Код типа: tax_id, vat, company_registration или другой.",
      "Type code: tax_id, vat, company_registration or another code.",
      "Código do tipo: tax_id, vat, company_registration ou outro.",
      "Kod typu: tax_id, vat, company_registration lub inny.",
      "Typcode: tax_id, vat, company_registration oder ein anderer.",
      "Typecode: tax_id, vat, company_registration of een andere code.")
label("identifier_value", "Значення ідентифікатора", "Значение идентификатора",
      "Identifier value", "Valor do identificador", "Wartość identyfikatora",
      "Kennungswert", "Identificatiewaarde")
label("primary", "Основний для цієї країни й типу?", "Основной для этой страны и типа?",
      "Primary for this country and type?", "Principal para este país e tipo?",
      "Główny dla tego kraju i typu?", "Primär für dieses Land und diesen Typ?",
      "Primair voor dit land en type?")
label("yes", "Так", "Да", "Yes", "Sim", "Tak", "Ja", "Ja")
label("no", "Ні", "Нет", "No", "Não", "Nie", "Nein", "Nee")
label("confirm_delete", "Підтвердьте видалення ідентифікатора.",
      "Подтвердите удаление идентификатора.", "Confirm identifier deletion.",
      "Confirme a eliminação do identificador.", "Potwierdź usunięcie identyfikatora.",
      "Bestätigen Sie das Löschen der Kennung.", "Bevestig het verwijderen van de identificatie.")
label("saved", "✅ Операцію з ідентифікатором виконано.",
      "✅ Операция с идентификатором выполнена.", "✅ Identifier operation completed.",
      "✅ Operação com o identificador concluída.", "✅ Operacja na identyfikatorze zakończona.",
      "✅ Kennungsoperation abgeschlossen.", "✅ Identificatiebewerking voltooid.")
label("list", "До списку ідентифікаторів", "К списку идентификаторов", "Back to identifiers",
      "Voltar aos identificadores", "Wróć do identyfikatorów", "Zurück zu Kennungen", "Terug naar identificaties")
label("next", "Далі →", "Далее →", "Next →", "Seguinte →", "Dalej →", "Weiter →", "Volgende →")
label("previous", "← Назад", "← Назад", "← Previous", "← Anterior", "← Wstecz", "← Zurück", "← Vorige")


def tr(key, language):
    if key not in LABELS:
        return company_tr(key, language)
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])


def item_text(item, language):
    return (
        item["country_code"] + " · " + item["identifier_type"]
        + "\n" + item["identifier_value"]
        + "\n" + tr("primary", language) + " "
        + tr("yes" if item["is_primary"] else "no", language)
    )
