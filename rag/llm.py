import json
import ollama
from config import CHAT_MODEL


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