import aiohttp
from logger import log

API_URL = "http://localhost:5000/translate"

async def translate(text: str, to_lang: str = "en", from_lang: str = "auto") -> str:
    if not text:
        return ""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json={
                "q": text,
                "source": from_lang,
                "target": to_lang,
                "format": "text"
            }) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("translatedText", text)
                else:
                    log.warning(f"[TRANSLATOR] Ошибка {response.status}: {await response.text()}")
    except Exception as e:
        log.error(f"[TRANSLATOR] Ошибка перевода: {e}")

    return text  # fallback

