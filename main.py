import asyncio
import json
import logging
import os
import datetime
import ollama
import chromadb
import random
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from parsers import parse_web_page, parse_youtube


# --- КОНФИГУРАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "gemma2:9b"

# Настройка логгера
logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Инициализируем "Вечную" базу данных
# Данные будут сохраняться в папку ./rag_db
chroma_client = chromadb.PersistentClient(path="./rag_db")
collection = chroma_client.get_or_create_collection(name="articles_knowledge")

# --- МАШИНА СОСТОЯНИЙ (FSM) ---
class QuizState(StatesGroup):
    waiting_for_answer = State() # Ждем, пока юзер нажмет кнопку

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def generate_summary(text):
    """Просит LLM сделать краткую выжимку статьи"""
    prompt = f"""
    Прочитай текст статьи ниже.
    1. Напиши краткое содержание (Summary) в 2-3 предложениях.
    2. Выдели 3 главных тега (через запятую).

    Формат ответа строго такой:
    Саммари: [Текст]
    Теги: [Тег1, Тег2, Тег3]

    Текст статьи:
    {text[:4000]} 
    """
    # Ограничиваем текст 4000 символов, чтобы не забить контекст

    response = ollama.chat(model=CHAT_MODEL, messages=[
        {'role': 'user', 'content': prompt}
    ])
    return response['message']['content']


def generate_quiz_json(text):
    """
    Генерирует вопросы по тексту и возвращает их как Python-список.
    """
    # Жесткий промпт, чтобы получить чистый JSON
    prompt = f"""
    Проанализируй текст и создай 3 вопроса для викторины с вариантами ответов.
    Ты должен вернуть ТОЛЬКО валидный JSON массив, без лишнего текста, без markdown (```json).

    Формат JSON:
    [
      {{
        "question": "Текст вопроса 1?",
        "options": ["Вариант А", "Вариант Б", "Вариант В"],
        "correct_index": 0 
      }},
      ...
    ]

    Примечание: correct_index - это номер правильного ответа в массиве options (начиная с 0).

    Текст статьи:
    {text[:4000]}
    """

    response = ollama.chat(model=CHAT_MODEL, messages=[
        {'role': 'user', 'content': prompt}
    ])

    raw_content = response['message']['content']

    # Очистка от мусора (иногда LLM добавляет ```json в начале)
    cleaned_json = raw_content.replace("```json", "").replace("```", "").strip()

    try:
        quiz_data = json.loads(cleaned_json)
        return quiz_data
    except json.JSONDecodeError:
        print(f"Ошибка парсинга JSON. LLM выдала:\n{raw_content}")
        return None


def save_article_to_db(url, title, text, summary_block):
    """Сохраняет статью и её векторы в базу"""

    # Генерируем вектор для поиска
    # Важно: мы векторизуем ПОЛНЫЙ текст, чтобы искать по смыслу внутри статьи
    emb_response = ollama.embeddings(model=EMBED_MODEL, prompt=text)

    # Сохраняем в ChromaDB
    # ID документа будет его URL (чтобы не сохранять дважды одно и то же)
    collection.upsert(
        ids=[url],
        documents=[text],
        embeddings=[emb_response["embedding"]],
        metadatas=[{
            "title": title,
            "url": url,
            "summary": summary_block,
            "date_added": datetime.datetime.now().strftime("%Y-%m-%d")
        }]
    )


def get_random_article():
    """Берет случайную статью из базы"""
    data = collection.get()
    if not data['documents']:
        return None, None

    # Выбираем случайный индекс
    idx = random.randint(0, len(data['documents']) - 1)
    text = data['documents'][idx]
    title = data['metadatas'][idx]['title']
    return title, text


def search_in_db(query):
    """Ищет ответ в базе данных"""
    # 1. Векторизуем вопрос
    query_emb = ollama.embeddings(model=EMBED_MODEL, prompt=query)["embedding"]

    # 2. Ищем 3 самых похожих куска
    results = collection.query(
        query_embeddings=[query_emb],
        n_results=1
    )

    if not results['documents'] or not results['documents'][0]:
        return None, None

    found_text = results['documents'][0][0]
    metadata = results['metadatas'][0][0]
    return found_text, metadata


# --- ХЕНДЛЕРЫ TELEGRAM ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я твой персональный агрегатор знаний.\n\n"
        "1. **Отправь мне ссылку на Habr**, и я прочитаю, сокращу и запомню статью.\n"
        "2. **Задай вопрос**, и я найду ответ в сохраненных статьях.\n"
        "3. Напиши **/report**, чтобы увидеть, что я уже запомнил."
        "4. Напиши **/quiz** — Проверь свои знания по сохраненным статьям!"
        , parse_mode="Markdown")


# Хендлер для команды /report
@dp.message(Command("report"))
async def cmd_report(message: types.Message):
    # Получаем все метаданные из базы (limit=10, чтобы не спамить)
    # В ChromaDB .get() без embeddings работает быстро
    data = collection.get(limit=10, include=['metadatas'])

    if not data['metadatas']:
        await message.answer("📭 База знаний пока пуста. Пришли мне ссылку на статью!")
        return

    report_text = "📊 **Отчет по сохраненным знаниям:**\n\n"

    for meta in data['metadatas']:
        # meta - это словарь с нашими полями
        title = meta.get('title', 'Без названия')
        date = meta.get('date_added', '?')
        url = meta.get('url', '#')

        report_text += f"🔹 **{title}**\n📅 {date}\n🔗 {url}\n\n"

    await message.answer(report_text, parse_mode="None")  # parse_mode=None чтобы ссылки не ломали разметку


@dp.message(Command("quiz"))
async def start_quiz(message: types.Message, state: FSMContext):
    """Начинает викторину"""
    await message.answer("🎲 Ищу статью и генерирую вопросы... (это займет секунд 10)")
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # 1. Берем статью
    title, text = await asyncio.to_thread(get_random_article)
    if not title:
        await message.answer("Сначала сохрани хотя бы одну статью!")
        return

    # 2. Генерируем вопросы через LLM
    quiz_data = await asyncio.to_thread(generate_quiz_json, text)

    if not quiz_data:
        await message.answer("❌ Не удалось сгенерировать вопросы. Попробуй еще раз.")
        return

    # 3. Сохраняем состояние (текущие вопросы, счетчик)
    await state.set_state(QuizState.waiting_for_answer)
    await state.update_data(
        quiz_data=quiz_data,
        current_q=0,
        score=0,
        article_title=title
    )

    # 4. Задаем первый вопрос
    await ask_question(message, quiz_data[0], 0, title)


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
@dp.callback_query(QuizState.waiting_for_answer, F.data.startswith("quiz_ans_"))
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
        await ask_question(callback.message, quiz_data[next_q_index], next_q_index, data['article_title'])
    else:
        # Конец викторины
        await callback.message.answer(
            f"🏁 **Викторина завершена!**\n\nТвой результат: {score} из {len(quiz_data)}."
        )
        await state.clear()  # Сбрасываем состояние

    await callback.answer()  # Чтобы часики на кнопке пропали

# --- ОБРАБОТКА ССЫЛОК И ВОПРОСОВ ---------

# Хендлер для ссылок
@dp.message(F.text.regexp(r'http[s]?://')) # Ловим ЛЮБУЮ ссылку
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


# Хендлер для обычных вопросов (RAG)
@dp.message(F.text)
async def handle_question(message: types.Message):
    user_text = message.text
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # 1. Ищем в базе
    found_text, meta = await asyncio.to_thread(search_in_db, user_text)

    if not found_text:
        await message.answer("🤷‍♂️ Я пока не знаю ответа. Попробуй скинуть мне статью на эту тему.")
        return

    # 2. Формируем ответ через LLM
    prompt = f"""
    Используй контекст статьи, чтобы ответить на вопрос.
    Статья: "{meta['title']}"

    Контекст:
    {found_text[:3000]}

    Вопрос: {user_text}
    """

    response = ollama.chat(model=CHAT_MODEL, messages=[
        {'role': 'user', 'content': prompt}
    ])

    answer = response['message']['content']

    # Добавляем ссылку на источник
    full_answer = f"{answer}\n\n📚 *Источник:* [{meta['title']}]({meta['url']})"

    await message.answer(full_answer, parse_mode="Markdown")


# --- ЗАПУСК ---
async def main():
    print("🚀 Бот запущен (База данных: ./rag_db)")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")