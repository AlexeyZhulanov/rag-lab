import asyncio
from aiogram import types, F, Router
from bot import bot
from parsers.web_parser import parse_web_page
from parsers.yt_parser import parse_youtube
from rag.llm import generate_summary
from rag.chroma import save_article_to_db

router = Router()

# Хендлер для ссылок
@router.message(F.text.regexp(r'http[s]?://')) # Ловим ЛЮБУЮ ссылку
async def handle_link(message: types.Message):
    url = message.text.strip()
    await message.answer("🕵️‍♂️ Вижу ссылку! Определяю тип контента...")
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Логика маршрутизации
    is_youtube = "youtube.com" in url or "youtu.be" in url

    # 1. Парсинг (запускаем в отдельном потоке, чтобы бот не завис)
    try:
        if is_youtube:
            await message.answer("📺 Это YouTube! Пробую достать субтитры...")
            # Запускаем парсер видео
            title, text = await asyncio.to_thread(parse_youtube, url)
        else:
            await message.answer("🌍 Это веб-страница. Парсим текст...")
            # Запускаем универсальный парсер
            title, text = await asyncio.to_thread(parse_web_page, url)
    except Exception as e:
        await message.answer(f"❌ Критическая ошибка парсера: {e}")
        return

    if not title:
        await message.answer(f"❌ Не удалось обработать ссылку:\n{text}")  # вывод ошибки
        return

    await message.answer(f"✅ Успех!\n**{title}**\n\n🧠 Читаю и анализирую (это может занять время)...", parse_mode="Markdown")
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # 2. Генерация саммари через LLM
    try:
        summary = await asyncio.to_thread(generate_summary, text)
        await asyncio.to_thread(save_article_to_db, url, title, text, summary)

        await message.answer(
            f"💾 **Сохранено в базу знаний!**\n\n{summary}\n\n"
            f"Теперь можешь задавать вопросы или запустить /quiz!",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при работе с AI: {e}")