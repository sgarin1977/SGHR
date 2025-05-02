import asyncio
from services.translator import translate

async def test():
    phrases = [
        ("Привет! Я помогу вам найти работу.", "pt"),
        ("Найти работу", "en"),
        ("Разместить вакансию", "hi"),
        ("Sou eletricista com experiência", "ru"),
    ]

    for text, lang in phrases:
        translated = await translate(text, to_lang=lang)
        print(f"\n🟢 Original: {text}\n🔵 Lang: {lang}\n🟣 Translated: {translated}")

if __name__ == "__main__":
    asyncio.run(test())

