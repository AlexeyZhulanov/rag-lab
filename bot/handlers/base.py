from aiogram import types, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import collection

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я твой персональный агрегатор знаний.\n\n"
        "1. **Отправь мне ссылку на Habr**, и я прочитаю, сокращу и запомню статью.\n"
        "2. **Задай вопрос**, и я найду ответ в сохраненных статьях.\n"
        "3. Напиши **/report**, чтобы увидеть, что я уже запомнил."
        "4. Напиши **/quiz** — Проверь свои знания по сохраненным статьям!"
        , parse_mode="Markdown")


# Хендлер для команды /report
@router.message(Command("report"))
async def cmd_report(message: types.Message):
    # В реальном продукте тут должна быть пагинация, а пока limit=100
    data = collection.get(limit=100, include=['metadatas'])

    if not data['metadatas']:
        await message.answer("📭 База знаний пока пуста. Пришли мне ссылку на статью!")
        return

    # Словарь для уникальных записей: {url: metadata}
    unique_sources = {}

    for meta in data['metadatas']:
        url = meta.get('url')
        # Если такого URL еще не было в нашем словаре, добавляем
        if url not in unique_sources:
            unique_sources[url] = meta

    # Перебираем уникальные записи
    for idx, (url, meta) in enumerate(unique_sources.items()):
        title = meta.get('title', 'Без названия')
        date = meta.get('date_added', '?')
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_{idx}")
            ]
        ])
        text = (
            f"🔹 <b>{title}</b>\n"
            f"📅 Дата: {date}\n"
            f"🔗 {url}"
        )
        # Отправляем каждую статью отдельным сообщением
        await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data.startswith("del_"))
async def delete_article(callback: types.CallbackQuery):
    url_index = int(callback.data.split("_")[-1])

    data = collection.get(limit=100, include=['metadatas'])
    if not data['metadatas']:
        return

    unique_urls = []
    for meta in data['metadatas']:
        url = meta.get('url')
        if url and url not in unique_urls:
            unique_urls.append(url)

    target_url = unique_urls[url_index]

    collection.delete(where={"url": target_url})
    await callback.message.answer(f"✅ Статья **{target_url}** — удалена из базы.", parse_mode="Markdown")