import asyncio
import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart

# Импорты для RAG
import ollama
import chromadb

# 1. Настройки
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
EMBED_MODEL = "nomic-embed-text"  # Модель для поиска
CHAT_MODEL = "gemma2:9b"  # Модель для ответов

# Настройка логгера
logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Глобальная переменная для базы данных
collection = None


# --- ФУНКЦИИ RAG (Логика поиска) ---

def init_db():
    """Читает файл и создает базу знаний при запуске бота"""
    global collection
    print("⏳ Начало создания базы знаний...")

    # Инициализируем ChromaDB в памяти
    client = chromadb.Client()
    # Если коллекция была, удаляем её (чтобы не дублировать данные при перезапуске)
    try:
        client.delete_collection("knowledge_base")
    except:
        pass

    collection = client.create_collection(name="knowledge_base")

    # Читаем файл
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print("❌ Ошибка: Файл knowledge.txt не найден!")
        return

    # Разбиваем на чанки (по пустым строкам)
    chunks = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]

    # Векторизуем и добавляем в базу
    for i, chunk in enumerate(chunks):
        response = ollama.embeddings(model=EMBED_MODEL, prompt=chunk)
        collection.add(
            ids=[str(i)],
            embeddings=[response["embedding"]],
            documents=[chunk]
        )

    print(f"✅ База знаний готова! Загружено {len(chunks)} фрагментов.")


def get_rag_response(user_question):
    """Ищет контекст и спрашивает LLM"""

    # 1. Ищем в базе
    response = ollama.embeddings(model=EMBED_MODEL, prompt=user_question)
    results = collection.query(
        query_embeddings=[response["embedding"]],
        n_results=1  # Берем 1 самый похожий кусок
    )

    if not results['documents'] or not results['documents'][0]:
        found_text = "Информации нет."
    else:
        found_text = results['documents'][0][0]

    # 2. Формируем промпт
    prompt = f"""
    Ты — техническая поддержка. Отвечай на вопрос пользователя, используя ТОЛЬКО предоставленный контекст ниже.
    Если в контексте нет информации для ответа, ответь фразой: "К сожалению, в моей базе знаний нет информации по этому вопросу."
    Не придумывай ничего от себя.

    Контекст:
    {found_text}

    Вопрос пользователя:
    {user_question}
    """

    # 3. Спрашиваем Gemma
    output = ollama.chat(model=CHAT_MODEL, messages=[
        {'role': 'user', 'content': prompt}
    ])

    return output['message']['content']


# --- ХЕНДЛЕРЫ TELEGRAM ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я бот техподдержки тостера Omega-3000.\n"
        "Я работаю на базе RAG (Gemma 2 + ChromaDB).\n"
        "Спроси меня про коды ошибок или режимы работы."
    )


@dp.message(F.text)
async def handle_text(message: types.Message):
    user_text = message.text

    # Показываем статус "печатает..."
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Запускаем RAG в отдельном потоке (чтобы не блокировать бота)
    # В aiogram 3 для тяжелых задач лучше использовать to_thread
    response_text = await asyncio.to_thread(get_rag_response, user_text)

    await message.answer(response_text)


# --- ЗАПУСК ---
async def main():
    # Сначала строим базу
    init_db()

    print("🚀 Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")