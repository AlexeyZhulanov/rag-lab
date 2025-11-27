import asyncio
import json
import logging
import os
import datetime
import ollama
import chromadb
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
    waiting_for_article_choice = State() # Выбор статьи
    waiting_for_count_choice = State()   # Выбор количества вопросов
    waiting_for_answer = State()         # Сама игра

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def generate_summary(text):
    """Просит LLM сделать краткую выжимку статьи"""
    # Лимит в 25000 символов (примерно под завязку контекста 8k)
    # Если текст больше, берем начало, чтобы не сломать запрос.
    # В идеале для супер-длинных текстов нужны сложные алгоритмы (Map-Reduce).
    safe_text = text[:25000]

    prompt = f"""
    Прочитай текст статьи ниже.
    1. Напиши краткое содержание (Summary) в 2-3 предложениях.
    2. Выдели 3 главных тега (через запятую).

    Формат ответа строго такой:
    Саммари: [Текст]
    Теги: [Тег1, Тег2, Тег3]

    Текст статьи:
    {safe_text} 
    """
    # Ограничиваем текст 4000 символов, чтобы не забить контекст

    response = ollama.chat(model=CHAT_MODEL, messages=[{'role': 'user', 'content': prompt}],
        options={
            'temperature': 0.3,  # Небольшая свобода для красивого слога
            'num_ctx': 8192  # Чтобы влезла вся статья целиком
        }
    )
    return response['message']['content']


def generate_quiz_json(text, num_questions):
    """
    Генерирует вопросы по тексту и возвращает их как Python-список.
    """
    # Тоже увеличиваем лимит до максимума контекста
    safe_text = text[:25000]

    # Жесткий промпт, чтобы получить чистый JSON
    prompt = f"""
        Проанализируй текст и создай ровно {num_questions} вопросов для викторины с вариантами ответов.
        Каждый вопрос должен начинаться с заглавной буквы.
        Ты должен вернуть ТОЛЬКО валидный JSON массив.

        Формат JSON:
        [
          {{
            "question": "Текст вопроса?",
            "options": ["А", "Б", "В", "Г"],
            "correct_index": 0 
          }}
        ]

        Текст:
        {safe_text} 
        """

    response = ollama.chat(model=CHAT_MODEL, messages=[{'role': 'user', 'content': prompt}],
        options={
            'temperature': 0.6,  # Немного креатива, чтобы вопросы не повторялись
            'num_ctx': 8192 # Больше памяти
        }
    )

    raw_content = response['message']['content']

    # Очистка от мусора (иногда LLM добавляет ```json в начале)
    cleaned_json = raw_content.replace("```json", "").replace("```", "").strip()

    try:
        quiz_data = json.loads(cleaned_json)
        return quiz_data
    except json.JSONDecodeError:
        print(f"Ошибка парсинга JSON. LLM выдала:\n{raw_content}")
        return None


def split_text(text, chunk_size=1000, overlap=100):
    """Режет текст на куски по chunk_size символов с перекрытием"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        # overlap нужен, чтобы не разрезать важную мысль посередине
        start += (chunk_size - overlap)
    return chunks


def save_article_to_db(url, title, text, summary_block):
    """Сохраняет статью и её векторы в базу"""
    # 1. Режем текст
    chunks = split_text(text)

    print(f"Сохраняю {len(chunks)} фрагментов для: {title}")

    # 2. Сохраняем каждый кусок отдельно
    for i, chunk in enumerate(chunks):
        # Генерируем вектор для КУСКА, а не всего текста
        emb_response = ollama.embeddings(model=EMBED_MODEL, prompt=chunk)

        collection.upsert(
            ids=[f"{url}_{i}"],  # Уникальный ID для куска
            documents=[chunk],
            embeddings=[emb_response["embedding"]],
            metadatas=[{
                "title": title,
                "url": url,
                "summary": summary_block,  # Саммари у всех кусков одинаковое
                "chunk_id": i,
                "date_added": datetime.datetime.now().strftime("%Y-%m-%d")
            }]
        )


def get_unique_articles():
    """Возвращает список уникальных статей (title, url)"""
    data = collection.get(limit=100, include=['metadatas'])
    unique = {}
    if data['metadatas']:
        for meta in data['metadatas']:
            url = meta.get('url')
            if url and url not in unique:
                unique[url] = meta.get('title', 'Без названия')
    return unique # Словарь {url: title}


def search_in_db(query):
    """Ищет ответ в базе данных"""
    # Векторизуем вопрос
    query_emb = ollama.embeddings(model=EMBED_MODEL, prompt=query)["embedding"]

    # Берем ТОП-5 результатов
    results = collection.query(
        query_embeddings=[query_emb],
        n_results=5
    )

    if not results['documents'] or not results['documents'][0]:
        return None, None

    # Собираем тексты всех 5-х найденных кусков в одну строку
    found_texts = results['documents'][0] # Это список ['текст1', 'текст2', 'текст3']
    metadatas = results['metadatas'][0]

    # Возвращаем склеенный текст и метаданные первого (самого релевантного) источника
    combined_text = "\n---\n".join(found_texts)
    return combined_text, metadatas[0]


def get_full_text_by_url(target_url):
    """Собирает полный текст статьи из всех её чанков"""
    # Ищем все записи с этим URL
    data = collection.get(where={"url": target_url})
    if not data['documents']:
        return ""

    # Сортируем документы по chunk_id
    sorted_docs = [doc for _, doc in sorted(zip(data['metadatas'], data['documents']), key=lambda pair: pair[0].get('chunk_id', 0))]

    full_text = "\n".join(sorted_docs)
    return full_text

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

    report_text = "📊 **Отчет по сохраненным знаниям:**\n\n"

    # Перебираем уникальные записи
    for url, meta in unique_sources.items():
        title = meta.get('title', 'Без названия')
        date = meta.get('date_added', '?')
        report_text += f"🔹 **{title}**\n📅 Дата: {date}\n🔗 {url}\n\n"

    await message.answer(report_text, parse_mode="None")  # parse_mode=None чтобы ссылки не ломали разметку


# 1. Запуск: Показываем список статей
@dp.message(Command("quiz"))
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
@dp.callback_query(QuizState.waiting_for_article_choice, F.data.startswith("q_art_"))
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
@dp.callback_query(QuizState.waiting_for_count_choice, F.data.startswith("q_cnt_"))
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
        await ask_question(callback.message, quiz_data[next_q_index], next_q_index, data['selected_title'])
    else:
        # Конец викторины
        await callback.message.answer(f"🏁 **Викторина завершена!**\n\nТвой результат: {score} из {len(quiz_data)}.")
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


def expand_query(user_query):
    """Превращает короткий запрос в развернутый для лучшего поиска"""
    prompt = f"""
    Ты — поисковый оптимизатор. Твоя задача — переформулировать запрос пользователя так, чтобы по нему было легче найти информацию в базе знаний.
    Добавь контекст, синонимы, но не меняй смысл.

    Запрос пользователя: "{user_query}"

    Верни ТОЛЬКО переформулированный запрос. Никаких вступлений.
    """

    response = ollama.chat(model=CHAT_MODEL, messages=[{'role': 'user', 'content': prompt}],
        options={
            'temperature': 0.0,  # Максимальная точность и детерминизм
            'num_ctx': 2048 # Стандартное значение, тут много не надо
        }
    )
    return str.strip(response['message']['content'])


# Хендлер для обычных вопросов (RAG)
@dp.message(F.text)
async def handle_question(message: types.Message):
    user_text = message.text
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # 1. Расширяем запрос (делаем это в потоке, чтобы не блокировать бота)
    expanded_query = await asyncio.to_thread(expand_query, user_text)
    print(f"DEBUG: Оригинал: '{user_text}' -> Расширенный: '{expanded_query}'")

    # 2. Ищем в базе уже по РАСШИРЕННОМУ запросу
    found_text, meta = await asyncio.to_thread(search_in_db, expanded_query)

    if not found_text:
        await message.answer("🤷‍♂️ Я пока не знаю ответа. Попробуй скинуть мне статью на эту тему.")
        return

    # 3. Формируем ответ (подаем оригинальный вопрос для контекста)
    prompt = f"""
    Ты — аналитик данных. Твоя задача — ответить на вопрос, опираясь ИСКЛЮЧИТЕЛЬНО на приведенный ниже контекст.
    Контекст может содержать несколько отрывков из разных источников.

    Инструкция:
    1. Сначала найди в тексте цитаты, подтверждающие ответ.
    2. Если информации нет, честно напиши: "В базе знаний нет информации".
    3. Сформулируй краткий и четкий ответ на основе найденных фактов.

    Контекст:
    {found_text}

    Вопрос: {user_text}
    """

    response = ollama.chat(model=CHAT_MODEL, messages=[{'role': 'user', 'content': prompt}],
        options={
            'temperature': 0.1,  # Минимум фантазии
            'num_ctx': 8192  # Больше памяти
        }
    )

    answer = response['message']['content']

    # Проверка: если бот отказался отвечать
    refusal_phrases = ["нет информации", "не знаю", "не найдено", "затрудняюсь ответить"]
    # Проверяем, есть ли стоп-фраза в начале ответа (в нижнем регистре)
    is_refusal = any(phrase in answer.lower() for phrase in refusal_phrases)

    if is_refusal:
        # Если бот не знает, просто отправляем текст без ссылки
        await message.answer(answer)
    else:
        # Если ответил по делу - добавляем источник
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