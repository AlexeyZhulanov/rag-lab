import requests
from bs4 import BeautifulSoup


def parse_habr(url):
    """
    Принимает URL статьи на Хабре.
    Возвращает кортеж: (Заголовок, Чистый текст статьи)
    Или (None, Ошибка), если что-то пошло не так.
    """
    # Притворяемся обычным браузером Chrome
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Если сайт вернул 404 или 500, тут вылетит ошибка

        soup = BeautifulSoup(response.text, 'html.parser')

        # 1. Ищем заголовок (обычно это тег <h1> с классом tm-title)
        title_tag = soup.find('h1', class_='tm-title')
        if not title_tag:
            # Запасной вариант, если верстка чуть другая
            title_tag = soup.find('h1')

        title = title_tag.get_text(strip=True) if title_tag else "Без названия"

        # 2. Ищем тело статьи
        # На Хабре основной текст лежит в блоке с id="post-content-body" или классом tm-article-body
        content_div = soup.find('div', id='post-content-body')
        if not content_div:
            content_div = soup.find('div', class_='tm-article-body')

        if not content_div:
            return None, "Не удалось найти текст статьи. Возможно, это не статья с Хабра?"

        # 3. Чистим текст
        # get_text(separator='\n') заменит все <br> и </p> на перенос строки
        text = content_div.get_text(separator='\n', strip=True)

        return title, text

    except Exception as e:
        return None, f"Ошибка при скачивании: {e}"


# --- ТЕСТОВЫЙ ЗАПУСК ---
if __name__ == "__main__":
    # Ссылка для теста (можешь поменять на любую другую статью с Хабра)
    test_url = "https://habr.com/ru/articles/775686/"

    print(f"🔄 Скачиваю: {test_url}")
    title, content = parse_habr(test_url)

    if title:
        print(f"\n✅ УСПЕХ!\nЗаголовок: {title}")
        print("-" * 20)
        # Выведем первые 500 символов текста, чтобы не засорять консоль
        print(f"Текст (начало):\n{content[:500]}...")
        print("-" * 20)
        print(f"Всего символов: {len(content)}")
    else:
        print(f"❌ ОШИБКА: {content}")