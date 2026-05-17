import os
import json
import aiohttp
from pathlib import Path
from logger import log

# Папка с JSON-файлами переводов
LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
SUPPORTED_LANGS = ["ru", "pt", "en"]
DEFAULT_LANG = "ru"
TRANSLATE_API_URL = "http://localhost:5000/translate"  # LibreTranslate

_cache = {}

def load_locale(lang: str) -> dict:
    """Загрузка словаря переводов из JSON"""
    path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_locale(lang: str, data: dict):
    """Сохранение словаря переводов в JSON"""
    path = LOCALES_DIR / f"{lang}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_translation_dict(lang: str) -> dict:
    """Получение кешированного словаря переводов"""
    if lang not in _cache:
        _cache[lang] = load_locale(lang)
    return _cache[lang]

async def auto_translate(text: str, target_lang: str) -> str:
    """Автоматический перевод через локальный LibreTranslate"""
    if target_lang == DEFAULT_LANG:
        return text
    try:
        async with aiohttp.ClientSession() as session:
            response = await session.post(TRANSLATE_API_URL, json={
                "q": text,
                "source": DEFAULT_LANG,
                "target": target_lang,
                "format": "text"
            })
            if response.status == 200:
                data = await response.json()
                return data.get("translatedText", text)
            else:
                log.warning(f"[TRANSLATE] Ответ {response.status}: {await response.text()}")
    except Exception as e:
        log.error(f"[TRANSLATE] Ошибка: {e}")
    return text

def tr(key: str, lang: str) -> str:
    """Получение перевода по ключу с автозаполнением fallback"""
    lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    d = get_translation_dict(lang)
    if key in d:
        return d[key]
    # fallback: добавляем ключ в словарь и возвращаем оригинал
    d[key] = key
    _cache[lang][key] = key
    save_locale(lang, d)
    return key

