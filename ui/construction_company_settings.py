"""Construction-specific presentation overrides."""
from ui.construction_company_profile import LANGUAGES
from ui.construction_company_logo import tr as common_tr
from services.construction.construction_company_settings_api import FIELDS, LOGO_FIELD

LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("button", "Оформлення Construction", "Оформление Construction",
      "Construction branding", "Apresentação Construction", "Wygląd Construction",
      "Construction-Darstellung", "Construction-vormgeving")
label("note", "Окремі перевизначення для Construction. Незаповнені поля використовують успадковані або стандартні значення.",
      "Отдельные переопределения для Construction. Незаполненные поля используют унаследованные или стандартные значения.",
      "Separate Construction overrides. Unset fields use inherited or default values.",
      "Substituições próprias de Construction. Os campos não definidos usam valores herdados ou predefinidos.",
      "Osobne ustawienia Construction. Niewypełnione pola używają wartości dziedziczonych lub domyślnych.",
      "Eigene Construction-Überschreibungen. Nicht gesetzte Felder verwenden geerbte oder Standardwerte.",
      "Afzonderlijke Construction-instellingen. Niet-ingestelde velden gebruiken geërfde of standaardwaarden.")
label("inherit", "Успадковане / за замовчуванням", "Унаследованное / по умолчанию",
      "Inherited / default", "Herdado / predefinido", "Dziedziczone / domyślne",
      "Geerbt / Standard", "Geërfd / standaard")
label("reset", "Скинути перевизначення", "Сбросить переопределение", "Reset override",
      "Repor valor herdado", "Usuń nadpisanie", "Überschreibung zurücksetzen", "Overschrijving herstellen")
label("saved", "✅ Налаштування Construction збережено.", "✅ Настройки Construction сохранены.",
      "✅ Construction settings saved.", "✅ Definições Construction guardadas.",
      "✅ Ustawienia Construction zapisane.", "✅ Construction-Einstellungen gespeichert.",
      "✅ Construction-instellingen opgeslagen.")
label("unchanged", "Значення вже відповідає налаштуванню.", "Значение уже соответствует настройке.",
      "The value already matches the setting.", "O valor já corresponde à definição.",
      "Wartość już odpowiada ustawieniu.", "Der Wert entspricht bereits der Einstellung.",
      "De waarde komt al overeen met de instelling.")
label("display_name_override", "Назва для Construction", "Название для Construction",
      "Construction display name", "Nome apresentado em Construction", "Nazwa w Construction",
      "Anzeigename für Construction", "Weergavenaam voor Construction")
label("phone_override", "Телефон для Construction", "Телефон для Construction",
      "Construction phone", "Telefone de Construction", "Telefon w Construction",
      "Telefon für Construction", "Telefoon voor Construction")
label("email_override", "Email для Construction", "Email для Construction",
      "Construction email", "Email de Construction", "Email w Construction",
      "E-Mail für Construction", "E-mail voor Construction")
label("website_override", "Вебсайт для Construction", "Сайт для Construction",
      "Construction website", "Website de Construction", "Strona Construction",
      "Website für Construction", "Website voor Construction")
label("document_header_text", "Текст шапки документів", "Текст шапки документов",
      "Document header text", "Texto do cabeçalho dos documentos", "Tekst nagłówka dokumentów",
      "Dokumentkopftext", "Koptekst van documenten")
label("document_footer_text", "Текст підвалу документів", "Текст подвала документов",
      "Document footer text", "Texto do rodapé dos documentos", "Tekst stopki dokumentów",
      "Dokumentfußtext", "Voettekst van documenten")
label("accent_color", "Акцентний колір (#RRGGBB)", "Акцентный цвет (#RRGGBB)",
      "Accent color (#RRGGBB)", "Cor de destaque (#RRGGBB)", "Kolor akcentu (#RRGGBB)",
      "Akzentfarbe (#RRGGBB)", "Accentkleur (#RRGGBB)")
label("logo_file_id_override", "Окремий логотип Construction", "Отдельный логотип Construction",
      "Separate Construction logo", "Logótipo próprio de Construction", "Osobne logo Construction",
      "Eigenes Construction-Logo", "Afzonderlijk Construction-logo")
label("preview", "Переглянути окремий логотип", "Посмотреть отдельный логотип",
      "View overridden logo", "Ver logótipo próprio", "Zobacz osobne logo",
      "Eigenes Logo ansehen", "Afzonderlijk logo bekijken")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])


def settings_text(result, language):
    lines = [tr("button", language), str(result["organization"])[:120], tr("note", language)]
    item = result["item"]
    for field in FIELDS:
        value = item[field] if item is not None else None
        if value is None:
            text = tr("inherit", language)
        elif field == LOGO_FIELD:
            text = tr("configured", language)
        else:
            text = str(value)
            if len(text) > 240:
                text = text[:240] + "…"
        lines.append(tr(field, language) + ":\n" + text)
    return "\n\n".join(lines)
