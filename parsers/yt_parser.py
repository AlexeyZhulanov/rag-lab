import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from urllib.parse import urlparse, parse_qs


def extract_video_id(url):
    """Вытаскивает ID видео из ссылки"""
    # Поддерживает formats: youtube.com/watch?v=ID, youtu.be/ID
    parsed = urlparse(url)
    if "youtu.be" in parsed.netloc:
        return parsed.path[1:]
    if "youtube.com" in parsed.netloc:
        return parse_qs(parsed.query).get("v", [None])[0]
    return None


def parse_youtube(url):
    """
    Возвращает (Заголовок, Текст транскрипции)
    """
    video_id = extract_video_id(url)
    if not video_id:
        return None, "Некорректная ссылка на YouTube (не найден ID)."

    print(f"DEBUG: Пробую скачать субтитры для ID: {video_id}")

    try:
        ytt_api = YouTubeTranscriptApi()
        # 1. Получаем список ВСЕХ доступных субтитров
        transcript_list = ytt_api.list(video_id)

        # 2. Логика выбора языка:
        # Сначала ищем ручные (рус/англ), если нет - автогенерируемые (рус/англ)
        # Если ничего нет - берем вообще любые, какие есть
        try:
            transcript = transcript_list.find_transcript(['ru', 'en'])
        except NoTranscriptFound:
            # Если нет ручных, ищем автогенерируемые
            try:
                transcript = transcript_list.find_generated_transcript(['ru', 'en'])
            except NoTranscriptFound:
                # Если и таких нет, берем первый попавшийся (хоть китайский)
                print("DEBUG: Нужных языков нет, беру первый попавшийся...")
                transcript = next(iter(transcript_list))

        print(
            f"DEBUG: Нашел субтитры: {transcript.language_code} ({'Generated' if transcript.is_generated else 'Manual'})")

        # 3. Скачиваем
        text_data = transcript.fetch()
        full_text = " ".join([item.text for item in text_data])

        # Получаем заголовок (попробуем через trafilatura, если не выйдет - вернем ID)
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                metadata = trafilatura.extract_metadata(downloaded)
                title = f"YouTube: {metadata.title}"
            else:
                title = f"YouTube Video ({video_id})"
        except:
            title = f"YouTube Video ({video_id})"

        return title, full_text

    except TranscriptsDisabled:
        return None, "Субтитры у этого видео отключены автором."
    except NoTranscriptFound:
        return None, "У видео нет ни русских, ни английских субтитров."
    except Exception as e:
        # Выводим полную ошибку в консоль для отладки
        print(f"CRITICAL ERROR: {e}")
        return None, f"Ошибка YouTube API: {e}"