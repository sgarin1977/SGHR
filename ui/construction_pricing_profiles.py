"""Organization pricing presentation without invented commercial values."""
from decimal import Decimal
from ui.construction_stage2_settings import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Цінові профілі", "Ценовые профили", "Pricing profiles",
      "Perfis de preços", "Profile cenowe", "Preisprofile", "Prijsprofielen")
label("ECONOMY", "Економний", "Экономный", "Economy", "Económico",
      "Ekonomiczny", "Economy", "Economy")
label("STANDARD", "Стандартний", "Стандартный", "Standard", "Standard",
      "Standardowy", "Standard", "Standaard")
label("PREMIUM", "Преміальний", "Премиальный", "Premium", "Premium",
      "Premium", "Premium", "Premium")
label("code", "Рівень", "Уровень", "Level", "Nível", "Poziom", "Stufe", "Niveau")
label("configure", "Налаштувати", "Настроить", "Configure", "Configurar",
      "Skonfiguruj", "Einrichten", "Instellen")
label("not_configured", "Не налаштовано", "Не настроено", "Not configured",
      "Não configurado", "Nie skonfigurowano", "Nicht eingerichtet", "Niet ingesteld")
label("work_coefficient", "Коефіцієнт робіт", "Коэффициент работ", "Work coefficient",
      "Coeficiente dos trabalhos", "Współczynnik robót",
      "Arbeitskoeffizient", "Werkcoëfficiënt")
label("material_markup_percent", "Націнка матеріалів, %", "Наценка материалов, %",
      "Material markup, %", "Margem dos materiais, %", "Narzut materiałów, %",
      "Materialaufschlag, %", "Materiaalopslag, %")
label("enter_work", "Введіть коефіцієнт робіт, більший за 0.",
      "Введите коэффициент работ больше 0.", "Enter a work coefficient greater than 0.",
      "Introduza um coeficiente dos trabalhos superior a 0.",
      "Wpisz współczynnik robót większy od 0.",
      "Geben Sie einen Arbeitskoeffizienten größer als 0 ein.",
      "Voer een werkcoëfficiënt groter dan 0 in.")
label("enter_markup", "Введіть націнку матеріалів від 0 до 100.",
      "Введите наценку материалов от 0 до 100.",
      "Enter a material markup from 0 to 100.",
      "Introduza uma margem dos materiais de 0 a 100.",
      "Wpisz narzut materiałów od 0 do 100.",
      "Geben Sie einen Materialaufschlag von 0 bis 100 ein.",
      "Voer een materiaalopslag van 0 tot 100 in.")
label("note",
      "Націнка матеріалів застосовується лише в режимі «Собівартість + надбавка».",
      "Наценка материалов применяется только в режиме «Себестоимость + надбавка».",
      "Material markup applies only in Cost plus markup mode.",
      "A margem dos materiais aplica-se apenas no modo Custo mais margem.",
      "Narzut materiałów działa tylko w trybie Koszt plus narzut.",
      "Der Materialaufschlag gilt nur im Modus Kosten plus Aufschlag.",
      "Materiaalopslag geldt alleen in de modus Kostprijs plus opslag.")
label("review", "Перевірте ціновий профіль перед збереженням.",
      "Проверьте ценовой профиль перед сохранением.",
      "Review the pricing profile before saving.",
      "Reveja o perfil de preços antes de guardar.",
      "Sprawdź profil cenowy przed zapisaniem.",
      "Prüfen Sie das Preisprofil vor dem Speichern.",
      "Controleer het prijsprofiel voordat u opslaat.")
label("saved", "Ціновий профіль збережено.", "Ценовой профиль сохранён.",
      "Pricing profile saved.", "Perfil de preços guardado.",
      "Zapisano profil cenowy.", "Preisprofil gespeichert.", "Prijsprofiel opgeslagen.")
label("edit", "Редагувати", "Редактировать", "Edit", "Editar",
      "Edytuj", "Bearbeiten", "Bewerken")
label("list", "До цінових профілів", "К ценовым профилям",
      "Back to pricing profiles", "Voltar aos perfis de preços",
      "Wróć do profili cenowych", "Zu den Preisprofilen", "Terug naar prijsprofielen")
label("pending",
      "Результат збереження не підтверджено. Повтор використає початковий запит.",
      "Результат сохранения не подтверждён. Повтор использует исходный запрос.",
      "The save result is unconfirmed. Retrying uses the original request.",
      "O resultado não foi confirmado. A repetição usa o pedido original.",
      "Wynik zapisu jest niepotwierdzony. Ponowienie użyje pierwotnego żądania.",
      "Das Speicherergebnis ist unbestätigt. Erneutes Senden verwendet die ursprüngliche Anfrage.",
      "Het resultaat is niet bevestigd. Opnieuw proberen gebruikt het oorspronkelijke verzoek.")
label("retry", "Отримати результат", "Получить результат", "Retrieve result",
      "Obter resultado", "Pobierz wynik", "Ergebnis abrufen", "Resultaat ophalen")
label("exists", "Цей рівень уже налаштовано. Оновіть список.",
      "Этот уровень уже настроен. Обновите список.",
      "This level is already configured. Refresh the list.",
      "Este nível já está configurado. Atualize a lista.",
      "Ten poziom jest już skonfigurowany. Odśwież listę.",
      "Diese Stufe ist bereits eingerichtet. Aktualisieren Sie die Liste.",
      "Dit niveau is al ingesteld. Vernieuw de lijst.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])


def fields_text(fields, language):
    return "\n".join(
        tr(key, language) + ": " + (
            tr(value, language) if key == "code"
            else format(Decimal(value), "f")
        )
        for key, value in fields.items()
    )
