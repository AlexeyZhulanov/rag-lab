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