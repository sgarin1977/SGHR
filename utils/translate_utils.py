import json
from pathlib import Path

_translation_cache = {}

def get_lang(event) -> str:
    return getattr(event.from_user, "language_code", "ru") or "ru"

def load_translations(lang: str) -> dict:
    if lang not in _translation_cache:
        # Путь к locales строго из корня проекта
        path = Path(__file__).parent.parent / "locales" / f"{lang}.json"
        print("⛏️ Загрузка перевода:", lang)
        print("📂 Путь к JSON:", path)
        print("📄 Существует?", path.exists())

        if path.exists():
            _translation_cache[lang] = json.loads(path.read_text(encoding="utf-8"))
        else:
            _translation_cache[lang] = {}
    return _translation_cache[lang]

def tr(key: str, lang: str = "ru") -> str:
    translations = load_translations(lang)
    return translations.get(key, key)

# Перевод через внешнее API (не используется при старте)
import aiohttp
from logger import log

async def translate(text: str, target_lang: str) -> str:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post("https://libretranslate.de/translate", json={
                "q": text,
                "source": "auto",
                "target": target_lang,
                "format": "text"
            }) as resp:
                result = await resp.json()
                return result.get("translatedText", text)
        except Exception as e:
            log.error(f"[TRANSLATOR] Ошибка перевода: {e}")
            return text

