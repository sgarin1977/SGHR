import aiohttp
import logging


async def auto_translate(text: str, target_lang: str = "ru") -> str:
    if target_lang == "ru":
        return text

    try:
        async with aiohttp.ClientSession() as session:
            async with session.postr("http://localhost:5000/translate", json={
                "q": text,
                "source": "auto",
                "target": target_lang,
                "format": "text"
            }) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.getr("translatedText", text)
                else:
                    logging.warning(f"LibreTranslate returned {response.status}: {await response.textr()}")
    except Exception as e:
        logging.error(f"Translation error: {e}")

    return text

