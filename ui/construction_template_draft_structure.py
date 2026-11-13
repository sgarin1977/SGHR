"""Editor controls are localized; original authoring text stays explicit."""
from ui.construction_template_draft_header import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("structure", "Структура оригіналу", "Структура оригинала", "Original structure",
      "Estrutura original", "Struktura oryginału", "Originalstruktur", "Oorspronkelijke structuur")
label("sections_title", "Розділи чернетки", "Разделы черновика", "Draft sections",
      "Secções do rascunho", "Sekcje wersji roboczej", "Entwurfsabschnitte", "Conceptsecties")
label("questions_title", "Питання розділу", "Вопросы раздела", "Section questions",
      "Perguntas da secção", "Pytania sekcji", "Fragen des Abschnitts", "Sectievragen")
label("type", "Тип питання", "Тип вопроса", "Question type", "Tipo de pergunta",
      "Typ pytania", "Fragetyp", "Vraagtype")
label("required", "Обов’язкове", "Обязательный", "Required", "Obrigatória",
      "Wymagane", "Erforderlich", "Verplicht")
label("unit", "Одиниця вимірювання", "Единица измерения", "Unit",
      "Unidade", "Jednostka", "Einheit", "Eenheid")
label("condition", "Умова показу задана", "Условие показа задано", "Display condition configured",
      "Condição de apresentação definida", "Ustawiono warunek wyświetlania",
      "Anzeigebedingung festgelegt", "Weergavevoorwaarde ingesteld")
label("options", "Варіантів відповіді", "Вариантов ответа", "Answer options",
      "Opções de resposta", "Opcje odpowiedzi", "Antwortoptionen", "Antwoordopties")
label("yes", "Так", "Да", "Yes", "Sim", "Tak", "Ja", "Ja")
label("no", "Ні", "Нет", "No", "Não", "Nie", "Nein", "Nee")
label("refresh_structure", "Оновити структуру", "Обновить структуру", "Refresh structure",
      "Atualizar estrutura", "Odśwież strukturę", "Struktur aktualisieren", "Structuur vernieuwen")
label("back_sections", "До розділів", "К разделам", "Back to sections",
      "Voltar às secções", "Wróć do sekcji", "Zu den Abschnitten", "Terug naar secties")
label("back_questions", "До питань розділу", "К вопросам раздела", "Back to section questions",
      "Voltar às perguntas", "Wróć do pytań sekcji", "Zu den Abschnittsfragen", "Terug naar sectievragen")
label("back_draft", "До чернетки", "К черновику", "Back to draft",
      "Voltar ao rascunho", "Wróć do wersji roboczej", "Zum Entwurf", "Terug naar concept")
label("conflict", "Чернетка змінилася. Оновіть структуру перед продовженням.",
      "Черновик изменился. Обновите структуру перед продолжением.",
      "The draft changed. Refresh the structure before continuing.",
      "O rascunho mudou. Atualize a estrutura antes de continuar.",
      "Wersja robocza uległa zmianie. Odśwież strukturę przed kontynuowaniem.",
      "Der Entwurf wurde geändert. Aktualisieren Sie die Struktur, bevor Sie fortfahren.",
      "Het concept is gewijzigd. Vernieuw de structuur voordat u verdergaat.")
label("BOOLEAN", "Так / ні", "Да / нет", "Yes / no", "Sim / não", "Tak / nie", "Ja / nein", "Ja / nee")
label("NUMBER", "Число", "Число", "Number", "Número", "Liczba", "Zahl", "Getal")
label("TEXT", "Текст", "Текст", "Text", "Texto", "Tekst", "Text", "Tekst")
label("SINGLE_CHOICE", "Один варіант", "Один вариант", "Single choice",
      "Escolha única", "Jeden wybór", "Einzelauswahl", "Enkele keuze")
label("MULTI_CHOICE", "Кілька варіантів", "Несколько вариантов", "Multiple choice",
      "Escolha múltipla", "Wielokrotny wybór", "Mehrfachauswahl", "Meerdere keuzes")
label("DATE", "Дата", "Дата", "Date", "Data", "Data", "Datum", "Datum")
label("PHOTO", "Фото", "Фото", "Photo", "Fotografia", "Zdjęcie", "Foto", "Foto")
label("ELEMENT", "Елемент", "Элемент", "Element", "Elemento", "Element", "Element", "Element")
label("CALCULATED", "Розрахункове значення", "Расчётное значение", "Calculated value",
      "Valor calculado", "Wartość obliczana", "Berechneter Wert", "Berekende waarde")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])
