"""Tax-rule labels and exact rate display."""
from ui.construction_tax_profiles import tr as profile_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Податкові правила", "Налоговые правила", "Tax rules",
      "Regras fiscais", "Reguły podatkowe", "Steuerregeln", "Belastingregels")
label("new_rule", "Додати правило", "Добавить правило", "Add rule",
      "Adicionar regra", "Dodaj regułę", "Regel hinzufügen", "Regel toevoegen")
label("empty_rules", "Ставки ще не задані.", "Ставки ещё не заданы.",
      "No rates configured yet.", "Ainda não há taxas definidas.",
      "Nie określono jeszcze stawek.", "Noch keine Steuersätze festgelegt.",
      "Nog geen tarieven ingesteld.")
label("category", "Категорія", "Категория", "Category", "Categoria",
      "Kategoria", "Kategorie", "Categorie")
label("rate_percent", "Ставка, %", "Ставка, %", "Rate, %", "Taxa, %",
      "Stawka, %", "Steuersatz, %", "Tarief, %")
label("WORK", "Роботи", "Работы", "Work", "Trabalhos", "Prace", "Arbeiten", "Werkzaamheden")
label("MATERIAL", "Матеріали", "Материалы", "Materials", "Materiais",
      "Materiały", "Materialien", "Materialen")
label("SERVICE", "Послуги", "Услуги", "Services", "Serviços",
      "Usługi", "Dienstleistungen", "Diensten")
label("EQUIPMENT", "Обладнання", "Оборудование", "Equipment", "Equipamento",
      "Sprzęt", "Ausrüstung", "Apparatuur")
label("OTHER", "Інше", "Прочее", "Other", "Outros", "Inne", "Sonstiges", "Overig")
label("enter_rate", "Введіть ставку від 0 до 100, наприклад 20 або 20,5.",
      "Введите ставку от 0 до 100, например 20 или 20,5.",
      "Enter a rate from 0 to 100, for example 20 or 20.5.",
      "Introduza uma taxa de 0 a 100, por exemplo 20 ou 20,5.",
      "Wpisz stawkę od 0 do 100, na przykład 20 lub 20,5.",
      "Geben Sie einen Steuersatz von 0 bis 100 ein, zum Beispiel 20 oder 20,5.",
      "Voer een tarief van 0 tot 100 in, bijvoorbeeld 20 of 20,5.")
label("invalid_rate", "Потрібне десяткове число від 0 до 100.",
      "Требуется десятичное число от 0 до 100.",
      "Enter a decimal number from 0 to 100.",
      "Introduza um número decimal de 0 a 100.",
      "Wpisz liczbę dziesiętną od 0 do 100.",
      "Geben Sie eine Dezimalzahl von 0 bis 100 ein.",
      "Voer een decimaal getal van 0 tot 100 in.")
label("category_exists", "Для цієї категорії вже є правило. Оновіть список.",
      "Для этой категории уже есть правило. Обновите список.",
      "A rule already exists for this category. Refresh the list.",
      "Já existe uma regra para esta categoria. Atualize a lista.",
      "Reguła dla tej kategorii już istnieje. Odśwież listę.",
      "Für diese Kategorie besteht bereits eine Regel. Aktualisieren Sie die Liste.",
      "Er bestaat al een regel voor deze categorie. Vernieuw de lijst.")
label("profile", "До податкового профілю", "К налоговому профилю", "Back to tax profile",
      "Voltar ao perfil fiscal", "Wróć do profilu podatkowego",
      "Zum Steuerprofil", "Terug naar belastingprofiel")
label("rule_saved", "Податкове правило збережено.", "Налоговое правило сохранено.",
      "Tax rule saved.", "Regra fiscal guardada.", "Zapisano regułę podatkową.",
      "Steuerregel gespeichert.", "Belastingregel opgeslagen.")


def tr(key, language):
    if key not in LABELS:
        return profile_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])


def fields_text(fields, language):
    return "\n".join(
        tr(name, language) + ": "
        + (tr(value, language) if name == "category" else value + "%")
        for name, value in fields.items()
    )


def rule_text(item, language):
    return fields_text({
        "category": item["category"], "rate_percent": item["rate_percent"],
    }, language)
