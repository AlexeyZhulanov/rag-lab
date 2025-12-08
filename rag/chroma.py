import datetime
import ollama
from rag.utils import split_text
from config import EMBED_MODEL, collection


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