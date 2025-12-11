from aiogram import types, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import collection
from bot.states import ReportState

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


def build_report_keyboard(articles: list[tuple[str,str]]):
    """
    articles: список кортежей (url, title)
    возвращает InlineKeyboardMarkup с кнопками удаления + кнопку Close
    """
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for idx, (url, title) in enumerate(articles):
        btn_text = title if len(title) <= 40 else title[:37] + "..."
        # callback_data — только короткий индекс
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ {btn_text}", callback_data=f"del_{idx}")])
    # кнопка закрыть
    kb.inline_keyboard.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="report_close")])
    return kb


# Хендлер для команды /report
@router.message(Command("report"))
async def cmd_report(message: types.Message, state: FSMContext):
    # В реальном продукте тут должна быть пагинация, а пока limit=100
    data = collection.get(limit=100, include=['metadatas'])
    metadatas = data.get('metadatas') or []

    if not metadatas:
        await message.answer("📭 База знаний пока пуста. Пришли мне ссылку на статью!")
        return

    # Собираем уникальные URL -> title (в порядке появления)
    seen = set()
    articles = []  # список (url, title)
    for meta in metadatas:
        url = meta.get('url')
        if not url or url in seen:
            continue
        seen.add(url)
        title = meta.get('title') or "Без названия"
        date = meta.get('date_added') or "?"
        articles.append((url, f"{title} — {date}"))

    if not articles:
        await message.answer("📭 Нет доступных статей для отображения.")
        return

    # Сохраняем список в FSM (он хранится для этого конкретного пользователя/чата)
    await state.set_state(ReportState.showing_report)
    await state.update_data(articles=articles)

    # Создаём клавиатуру и отправляем единым сообщением
    kb = build_report_keyboard(articles)
    text_lines = []
    for idx, (url, title) in enumerate(articles):
        text_lines.append(f"{idx + 1}. <b>{title}</b>\n🔗 {url}")
    message_text = "📚 <b>Список сохранённых статей:</b>\n\n" + "\n\n".join(text_lines)

    await message.answer(message_text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data == "report_close", StateFilter(ReportState.showing_report))
async def close_report(callback, state: FSMContext):
    # Просто удалим сообщение с отчётом и выйдем из состояния
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.clear()
    await callback.answer()  # убирать "часики"


@router.callback_query(F.data.startswith("del_"), StateFilter(ReportState.showing_report))
async def delete_article(callback, state: FSMContext):
    # idx из callback_data
    try:
        idx = int(callback.data.split("_", 1)[1])
    except Exception:
        await callback.answer("Неверные данные кнопки.", show_alert=True)
        return

    data = await state.get_data()
    articles = data.get('articles') or []

    if idx < 0 or idx >= len(articles):
        await callback.answer("Эта статья уже удалена или недоступна.", show_alert=True)
        return

    target_url, target_title = articles[idx]

    # Удаляем из коллекции
    try:
        collection.delete(where={"url": target_url})
    except Exception as e:
        # логгируем ошибку, но не ломаем UX
        print("Ошибка при удалении из collection:", e)

    # Удалим элемент из локального списка и обновим state
    articles.pop(idx)
    await state.update_data(articles=articles)

    # Подтверждение пользователю
    await callback.answer("✅ Статья удалена.", show_alert=False)

    # Если список пуст — удаляем сообщение и очищаем состояние
    if not articles:
        try:
            await callback.message.edit_text("📭 База знаний пуста.", reply_markup=None)
        except Exception:
            pass
        await state.clear()
        return

    # Иначе перестраиваем клавиатуру и редактируем сообщение
    kb = build_report_keyboard(articles)
    # Пересобираем текст
    text_lines = []
    for i, (url, title) in enumerate(articles):
        text_lines.append(f"{i+1}. <b>{title}</b>\n🔗 {url}")
    message_text = "📚 <b>Список сохранённых статей:</b>\n\n" + "\n\n".join(text_lines)

    try:
        await callback.message.edit_text(message_text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        print("Ошибка при редактировании отчёта:", e)