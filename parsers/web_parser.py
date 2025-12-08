import trafilatura

def parse_web_page(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            return None, "Не удалось скачать страницу."

        text = trafilatura.extract(downloaded)
        metadata = trafilatura.extract_metadata(downloaded)
        title = metadata.title if metadata and metadata.title else "Веб-статья"

        if not text:
            return None, "Текст не найден."

        return title, text
    except Exception as e:
        return None, f"Ошибка веб-парсера: {e}"