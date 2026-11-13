"""Localized project pricing adjustments."""
from ui.construction_tax_profiles import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Поправки ціноутворення", "Поправки ценообразования",
      "Pricing adjustments", "Ajustes de preços", "Korekty cen",
      "Preisanpassungen", "Prijsaanpassingen")
label("new", "Додати поправку", "Добавить поправку", "Add adjustment",
      "Adicionar ajuste", "Dodaj korektę", "Anpassung hinzufügen", "Aanpassing toevoegen")
label("empty", "Поправок поки немає.", "Поправок пока нет.", "No adjustments yet.",
      "Ainda não há ajustes.", "Brak korekt.", "Noch keine Anpassungen.", "Nog geen aanpassingen.")
label("name", "Назва", "Название", "Name", "Nome", "Nazwa", "Name", "Naam")
label("coefficient", "Коефіцієнт", "Коэффициент", "Coefficient",
      "Coeficiente", "Współczynnik", "Koeffizient", "Coëfficiënt")
label("enabled", "Стан", "Состояние", "State", "Estado", "Stan", "Status", "Status")
label("on", "Увімкнено", "Включено", "Enabled", "Ativo", "Włączona", "Aktiviert", "Ingeschakeld")
label("off", "Вимкнено", "Выключено", "Disabled", "Inativo", "Wyłączona", "Deaktiviert", "Uitgeschakeld")
label("enter_name", "Введіть назву поправки.", "Введите название поправки.",
      "Enter the adjustment name.", "Introduza o nome do ajuste.",
      "Wpisz nazwę korekty.", "Geben Sie den Namen der Anpassung ein.",
      "Voer de naam van de aanpassing in.")
label("enter_coefficient", "Введіть коефіцієнт, більший за 0.",
      "Введите коэффициент больше 0.", "Enter a coefficient greater than 0.",
      "Introduza um coeficiente superior a 0.", "Wpisz współczynnik większy od 0.",
      "Geben Sie einen Koeffizienten größer als 0 ein.", "Voer een coëfficiënt groter dan 0 in.")
label("invalid_name", "Назва має містити від 1 до 255 символів.",
      "Название должно содержать от 1 до 255 символов.",
      "The name must contain 1 to 255 characters.",
      "O nome deve ter 1 a 255 caracteres.", "Nazwa musi mieć od 1 do 255 znaków.",
      "Der Name muss 1 bis 255 Zeichen enthalten.", "De naam moet 1 tot 255 tekens bevatten.")
label("review", "Перевірте поправку перед збереженням.",
      "Проверьте поправку перед сохранением.", "Review the adjustment before saving.",
      "Reveja o ajuste antes de guardar.", "Sprawdź korektę przed zapisaniem.",
      "Prüfen Sie die Anpassung vor dem Speichern.", "Controleer de aanpassing voordat u opslaat.")
label("saved", "Поправку збережено.", "Поправка сохранена.", "Adjustment saved.",
      "Ajuste guardado.", "Zapisano korektę.", "Anpassung gespeichert.", "Aanpassing opgeslagen.")
label("list", "До списку поправок", "К списку поправок", "Back to adjustments",
      "Voltar aos ajustes", "Wróć do korekt", "Zur Anpassungsliste", "Terug naar aanpassingen")
label("project", "До картки об’єкта", "К карточке объекта", "Back to project",
      "Voltar à obra", "Wróć do obiektu", "Zur Projektkarte", "Terug naar project")
label("note", "Застосовуються лише ввімкнені явні поправки.",
      "Применяются только включённые явные поправки.",
      "Only explicitly enabled adjustments apply.",
      "Aplicam-se apenas os ajustes explicitamente ativados.",
      "Obowiązują tylko jawnie włączone korekty.",
      "Es gelten nur ausdrücklich aktivierte Anpassungen.",
      "Alleen expliciet ingeschakelde aanpassingen zijn van toepassing.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])


def fields_text(fields, language):
    return "\n".join(
        tr(key, language) + ": " + (
            tr("on" if value else "off", language) if key == "enabled" else value
        )
        for key, value in fields.items()
    )
