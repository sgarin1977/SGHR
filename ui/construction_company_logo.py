"""Organization logo labels."""
from ui.construction_company_profile import LANGUAGES, tr as common_tr

LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("button", "Логотип організації", "Логотип организации", "Organization logo",
      "Logótipo da organização", "Logo organizacji", "Organisationslogo", "Organisatielogo")
label("upload", "Завантажити / замінити", "Загрузить / заменить", "Upload / replace",
      "Carregar / substituir", "Prześlij / zastąp", "Hochladen / ersetzen", "Uploaden / vervangen")
label("preview", "Переглянути логотип", "Посмотреть логотип", "View logo",
      "Ver logótipo", "Zobacz logo", "Logo ansehen", "Logo bekijken")
label("remove", "Прибрати логотип", "Убрать логотип", "Remove logo",
      "Remover logótipo", "Usuń logo", "Logo entfernen", "Logo verwijderen")
label("send_image", "Надішліть зображення як фото або файл.",
      "Отправьте изображение как фото или файл.", "Send an image as a photo or file.",
      "Envie uma imagem como fotografia ou ficheiro.", "Wyślij obraz jako zdjęcie lub plik.",
      "Senden Sie ein Bild als Foto oder Datei.", "Stuur een afbeelding als foto of bestand.")
label("maximum", "Максимальний розмір, байтів", "Максимальный размер, байт",
      "Maximum size in bytes", "Tamanho máximo em bytes", "Maksymalny rozmiar w bajtach",
      "Maximale Größe in Bytes", "Maximale grootte in bytes")
label("legal_name_needed",
      "Файл завантажено. Для створення профілю надішліть юридичну назву організації.",
      "Файл загружен. Для создания профиля отправьте юридическое название организации.",
      "File uploaded. Send the organization's legal name to create its profile.",
      "Ficheiro carregado. Envie a denominação legal da organização para criar o perfil.",
      "Plik przesłany. Wyślij nazwę prawną organizacji, aby utworzyć profil.",
      "Datei hochgeladen. Senden Sie den rechtlichen Namen der Organisation, um das Profil zu erstellen.",
      "Bestand geüpload. Stuur de juridische naam van de organisatie om het profiel aan te maken.")
label("review_logo", "Підтвердьте збереження завантаженого логотипа.",
      "Подтвердите сохранение загруженного логотипа.", "Confirm saving the uploaded logo.",
      "Confirme a gravação do logótipo carregado.", "Potwierdź zapis przesłanego logo.",
      "Bestätigen Sie das Speichern des hochgeladenen Logos.", "Bevestig het opslaan van het geüploade logo.")
label("confirm_remove",
      "Прибрати логотип із профілю? Сам файл залишиться у сховищі.",
      "Убрать логотип из профиля? Сам файл останется в хранилище.",
      "Remove the logo from the profile? The file itself will remain in storage.",
      "Remover o logótipo do perfil? O ficheiro será mantido no armazenamento.",
      "Usunąć logo z profilu? Sam plik pozostanie w magazynie.",
      "Logo aus dem Profil entfernen? Die Datei bleibt im Speicher.",
      "Het logo uit het profiel verwijderen? Het bestand blijft opgeslagen.")
label("saved", "✅ Логотип у профілі оновлено.", "✅ Логотип в профиле обновлён.",
      "✅ Profile logo updated.", "✅ Logótipo do perfil atualizado.",
      "✅ Logo profilu zaktualizowane.", "✅ Profillogo aktualisiert.", "✅ Profiellogo bijgewerkt.")
label("invalid_image", "Непідтримуване зображення або перевищено допустимий розмір.",
      "Неподдерживаемое изображение или превышен допустимый размер.",
      "Unsupported image or maximum size exceeded.",
      "Imagem não suportada ou tamanho máximo excedido.",
      "Nieobsługiwany obraz lub przekroczony maksymalny rozmiar.",
      "Nicht unterstütztes Bild oder maximale Größe überschritten.",
      "Niet-ondersteunde afbeelding of maximale grootte overschreden.")
label("storage_unavailable", "Файловий обмін недоступний. Перевірте налаштування Platform Files.",
      "Файловый обмен недоступен. Проверьте настройки Platform Files.",
      "File transfer is unavailable. Check the Platform Files configuration.",
      "A transferência de ficheiros está indisponível. Verifique a configuração de Platform Files.",
      "Przesyłanie plików jest niedostępne. Sprawdź konfigurację Platform Files.",
      "Dateiübertragung ist nicht verfügbar. Prüfen Sie die Konfiguration von Platform Files.",
      "Bestandsoverdracht is niet beschikbaar. Controleer de configuratie van Platform Files.")
label("uncertain_upload",
      "Результат завантаження не підтверджено. Профіль автоматично не змінювався. Перечитайте його перед новою дією.",
      "Результат загрузки не подтверждён. Профиль автоматически не изменялся. Перечитайте его перед новым действием.",
      "Upload could not be confirmed. The profile was not changed automatically. Reload it before another action.",
      "Não foi possível confirmar o carregamento. O perfil não foi alterado automaticamente. Recarregue-o antes de outra ação.",
      "Nie potwierdzono przesłania. Profil nie został automatycznie zmieniony. Odśwież go przed kolejną czynnością.",
      "Das Hochladen konnte nicht bestätigt werden. Das Profil wurde nicht automatisch geändert. Laden Sie es vor einer weiteren Aktion neu.",
      "Uploaden kon niet worden bevestigd. Het profiel is niet automatisch gewijzigd. Laad het opnieuw voor een volgende actie.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])
