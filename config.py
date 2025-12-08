import os

import chromadb
from dotenv import load_dotenv

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "gemma2:9b"

# Инициализируем "Вечную" базу данных
# Данные будут сохраняться в папку ./rag_db
chroma_client = chromadb.PersistentClient(path="./rag_db")
collection = chroma_client.get_or_create_collection(name="articles_knowledge")