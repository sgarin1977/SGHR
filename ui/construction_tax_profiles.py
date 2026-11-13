"""Tax profile labels; technical mode codes are not shown to the user."""
from ui.construction_stage2_settings import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Податкові профілі", "Налоговые профили", "Tax profiles",
      "Perfis fiscais", "Profile podatkowe", "Steuerprofile", "Belastingprofielen")
label("new", "Створити профіль", "Создать профиль", "Create profile",
      "Criar perfil", "Utwórz profil", "Profil erstellen", "Profiel maken")
label("list", "До списку профілів", "К списку профилей", "Back to profiles",
      "Voltar aos perfis", "Wróć do profili", "Zur Profilliste", "Terug naar profielen")
label("empty", "Податкових профілів поки немає.", "Налоговых профилей пока нет.",
      "No tax profiles yet.", "Ainda não há perfis fiscais.",
      "Brak profili podatkowych.", "Noch keine Steuerprofile.", "Nog geen belastingprofielen.")
label("name", "Назва профілю", "Название профиля", "Profile name",
      "Nome do perfil", "Nazwa profilu", "Profilname", "Profielnaam")
label("client_price_tax_mode", "Клієнтські ціни", "Клиентские цены", "Client prices",
      "Preços ao cliente", "Ceny dla klienta", "Kundenpreise", "Klantprijzen")
label("material_cost_basis", "Вартість матеріалів", "Стоимость материалов",
      "Material cost", "Custo dos materiais", "Koszt materiałów",
      "Materialkosten", "Materiaalkosten")
label("TAX_EXCLUSIVE", "Ціни без податку", "Цены без налога", "Prices exclude tax",
      "Preços sem imposto", "Ceny bez podatku", "Preise ohne Steuer", "Prijzen exclusief belasting")
label("TAX_INCLUSIVE", "Ціни з податком", "Цены с налогом", "Prices include tax",
      "Preços com imposto", "Ceny z podatkiem", "Preise inklusive Steuer", "Prijzen inclusief belasting")
label("NET", "Матеріали без податку", "Материалы без налога", "Material cost excludes tax",
      "Custo dos materiais sem imposto", "Koszt materiałów bez podatku",
      "Materialkosten ohne Steuer", "Materiaalkosten exclusief belasting")
label("GROSS", "Матеріали з податком", "Материалы с налогом", "Material cost includes tax",
      "Custo dos materiais com imposto", "Koszt materiałów z podatkiem",
      "Materialkosten inklusive Steuer", "Materiaalkosten inclusief belasting")
label("edit", "Редагувати", "Редактировать", "Edit", "Editar",
      "Edytuj", "Bearbeiten", "Bewerken")
label("enter_name", "Введіть назву профілю.", "Введите название профиля.",
      "Enter the profile name.", "Introduza o nome do perfil.",
      "Wpisz nazwę profilu.", "Geben Sie den Profilnamen ein.", "Voer de profielnaam in.")
label("invalid", "Введіть назву від 1 до 255 символів.",
      "Введите название от 1 до 255 символов.",
      "Enter a name using 1 to 255 characters.",
      "Introduza um nome com 1 a 255 caracteres.",
      "Wpisz nazwę od 1 do 255 znaków.",
      "Geben Sie einen Namen mit 1 bis 255 Zeichen ein.",
      "Voer een naam van 1 tot 255 tekens in.")
label("review", "Перевірте профіль перед збереженням.",
      "Проверьте профиль перед сохранением.", "Review the profile before saving.",
      "Reveja o perfil antes de guardar.", "Sprawdź profil przed zapisaniem.",
      "Prüfen Sie das Profil vor dem Speichern.", "Controleer het profiel voordat u opslaat.")
label("saved", "Податковий профіль збережено.", "Налоговый профиль сохранён.",
      "Tax profile saved.", "Perfil fiscal guardado.", "Zapisano profil podatkowy.",
      "Steuerprofil gespeichert.", "Belastingprofiel opgeslagen.")
label("retry", "Отримати результат операції", "Получить результат операции",
      "Retrieve operation result", "Obter resultado da operação",
      "Pobierz wynik operacji", "Operationsergebnis abrufen", "Resultaat ophalen")
label("pending", "Результат операції не підтверджено. Повтор використає той самий запит.",
      "Результат операции не подтверждён. Повтор использует тот же запрос.",
      "The operation result is unconfirmed. Retrying uses the same request.",
      "O resultado não foi confirmado. A repetição usa o mesmo pedido.",
      "Wynik operacji jest niepotwierdzony. Ponowienie użyje tego samego żądania.",
      "Das Operationsergebnis ist unbestätigt. Ein erneuter Versuch verwendet dieselbe Anfrage.",
      "Het resultaat is niet bevestigd. Opnieuw proberen gebruikt hetzelfde verzoek.")
label("note", "Ставки налаштовуються окремо. Профіль не обирається автоматично.",
      "Ставки настраиваются отдельно. Профиль не выбирается автоматически.",
      "Rates are configured separately. This profile is not selected automatically.",
      "As taxas são configuradas separadamente. O perfil não é selecionado automaticamente.",
      "Stawki ustawia się osobno. Profil nie jest wybierany automatycznie.",
      "Steuersätze werden separat festgelegt. Das Profil wird nicht automatisch ausgewählt.",
      "Tarieven worden apart ingesteld. Het profiel wordt niet automatisch geselecteerd.")


label("rules", "Податкові правила", "Налоговые правила", "Tax rules",
      "Regras fiscais", "Reguły podatkowe", "Steuerregeln", "Belastingregels")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])


def fields_text(fields, language):
    return "\n".join(
        tr(name, language) + ": " + (
            value if name == "name" else tr(value, language)
        )
        for name, value in fields.items()
    )


def profile_text(item, language):
    return fields_text({
        name: item[name] for name in (
            "name", "client_price_tax_mode", "material_cost_basis",
        )
    }, language)
