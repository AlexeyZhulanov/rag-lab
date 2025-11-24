import asyncio
import logging
import os
import datetime
import ollama
import chromadb
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from habr_parser import parse_habr

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


# Хендлер для ссылок (простая проверка, есть ли 'habr' в тексте)
@dp.message(F.text.contains("habr.com"))
async def handle_link(message: types.Message):
    url = message.text.strip()
    await message.answer("🕵️‍♂️ Вижу ссылку! Начинаю читать статью...")
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # 1. Парсинг (запускаем в отдельном потоке, чтобы бот не завис)
    try:
        title, text = await asyncio.to_thread(parse_habr, url)
    except Exception as _:
        await message.answer(f"❌ Не удалось скачать статью. Ошибка внутри парсера.")
        return

    if not title:
        await message.answer(f"❌ Ошибка: {text}")  # text тут содержит текст ошибки
        return

    await message.answer(f"✅ Статья скачана: **{title}**\n🧠 Читаю и анализирую...")
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # 2. Генерация саммари через LLM
    try:
        summary = await asyncio.to_thread(generate_summary, text)
    except Exception as e:
        await message.answer(f"❌ Ошибка LLM: {e}")
        return

    # 3. Сохранение в базу
    await asyncio.to_thread(save_article_to_db, url, title, text, summary)

    await message.answer(
        f"💾 **Сохранено в базу знаний!**\n\n{summary}",
        parse_mode="Markdown"
    )


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