import asyncio
from aiogram import types, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.states import QuizState
from rag.chroma import get_unique_articles, get_full_text_by_url
from rag.llm import generate_quiz_json

router = Router()

# 1. Запуск: Показываем список статей
@router.message(Command("quiz"))
async def start_quiz_selection(message: types.Message, state: FSMContext):
    articles = await asyncio.to_thread(get_unique_articles)

    if not articles:
        await message.answer("📭 База знаний пуста. Сначала скинь ссылку!")
        return

    # Сохраняем словарь статей в состояние, чтобы потом найти URL по индексу
    # (В кнопках нельзя передавать длинные URL)
    articles_list = list(articles.items())  # [ (url1, title1), (url2, title2) ]
    await state.set_state(QuizState.waiting_for_article_choice)
    await state.update_data(articles_list=articles_list)

    # Создаем кнопки
    builder = InlineKeyboardBuilder()
    for i, (url, title) in enumerate(articles_list):
        # Обрезаем название, если слишком длинное
        btn_text = title[:40] + "..." if len(title) > 40 else title
        builder.button(text=btn_text, callback_data=f"q_art_{i}")

    builder.adjust(1)  # По 1 кнопке в ряд
    await message.answer("📚 Выбери материал для теста:", reply_markup=builder.as_markup())


# 2. Обработка выбора статьи -> Показ выбора количества
@router.callback_query(QuizState.waiting_for_article_choice, F.data.startswith("q_art_"))
async def quiz_article_chosen(callback: types.CallbackQuery, state: FSMContext):
    # Получаем индекс выбранной статьи
    index = int(callback.data.split("_")[-1])
    data = await state.get_data()
    articles_list = data['articles_list']

    selected_url, selected_title = articles_list[index]

    # Сохраняем выбор
    await state.update_data(selected_url=selected_url, selected_title=selected_title)

    # Рисуем кнопки кол-ва вопросов
    builder = InlineKeyboardBuilder()
    builder.button(text="3 вопроса", callback_data="q_cnt_3")
    builder.button(text="5 вопросов", callback_data="q_cnt_5")
    builder.button(text="7 вопросов", callback_data="q_cnt_7")

    await state.set_state(QuizState.waiting_for_count_choice)
    await callback.message.edit_text(f"Выбрано: **{selected_title}**\nСколько вопросов задать?", parse_mode="Markdown", reply_markup=builder.as_markup())


# 3. Обработка количества -> Генерация -> Старт
@router.callback_query(QuizState.waiting_for_count_choice, F.data.startswith("q_cnt_"))
async def quiz_count_chosen(callback: types.CallbackQuery, state: FSMContext):
    num_questions = int(callback.data.split("_")[-1])

    data = await state.get_data()
    url = data['selected_url']
    title = data['selected_title']

    await callback.message.edit_text(f"🎲 Генерирую {num_questions} вопросов по теме \"{title}\"...\n(Жди, читаю базу...)")

    # Собираем текст и генерируем (в потоке)
    full_text = await asyncio.to_thread(get_full_text_by_url, url)
    quiz_data = await asyncio.to_thread(generate_quiz_json, full_text, num_questions)

    if not quiz_data:
        await callback.message.edit_text("❌ Ошибка генерации. LLM подвела. Попробуй еще раз.")
        await state.clear()
        return

    # Настраиваем игру
    await state.set_state(QuizState.waiting_for_answer)
    await state.update_data(quiz_data=quiz_data, current_q=0, score=0)

    # Задаем первый вопрос
    await ask_question(callback.message, quiz_data[0], 0, title)


async def ask_question(message, question_item, index, title):
    """Отправляет сообщение с кнопками"""
    text = f"📚 Статья: *{title}*\n\n❓ **Вопрос {index + 1}:**\n{question_item['question']}"

    # Создаем клавиатуру
    buttons = []
    for i, option in enumerate(question_item['options']):
        # В callback_data передаем индекс ответа, который выбрал юзер
        buttons.append([InlineKeyboardButton(text=option, callback_data=f"quiz_ans_{i}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


# Обработка нажатия на кнопку
@router.callback_query(QuizState.waiting_for_answer, F.data.startswith("quiz_ans_"))
async def quiz_answer_handler(callback: types.CallbackQuery, state: FSMContext):
    # Получаем данные из хранилища
    data = await state.get_data()
    quiz_data = data['quiz_data']
    current_q_index = data['current_q']
    score = data['score']

    # Какой ответ выбрал юзер (число из callback_data)
    user_choice = int(callback.data.split("_")[-1])
    correct_choice = quiz_data[current_q_index]['correct_index']

    # Проверяем
    if user_choice == correct_choice:
        score += 1
        result_text = "✅ **Верно!**"
    else:
        correct_text = quiz_data[current_q_index]['options'][correct_choice]
        result_text = f"❌ **Ошибка.** Правильный ответ:\n{correct_text}"

    # Удаляем кнопки у старого сообщения, чтобы не нажал дважды
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(result_text, parse_mode="Markdown")

    # Переходим к следующему вопросу
    next_q_index = current_q_index + 1

    if next_q_index < len(quiz_data):
        # Если есть еще вопросы
        await state.update_data(current_q=next_q_index, score=score)
        await ask_question(callback.message, quiz_data[next_q_index], next_q_index, data['selected_title'])
    else:
        # Конец викторины
        await callback.message.answer(f"🏁 **Викторина завершена!**\n\nТвой результат: {score} из {len(quiz_data)}.")
        await state.clear()  # Сбрасываем состояние

    await callback.answer()  # Чтобы часики на кнопке пропали