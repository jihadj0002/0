import re


def chunk_sample_qa(text):
    """Parse sample_questions_answers text into individual Q&A chunks.

    Expected format:
        Q: What is your return policy?
        A: We offer a 7-day return policy...

    Returns a list of strings, one per Q&A pair.
    """
    if not text or not text.strip():
        return []

    lines = text.strip().split("\n")
    chunks = []
    current = []

    for line in lines:
        stripped = line.strip()
        if re.match(r"^Q\d*[:.)]\s*", stripped, re.IGNORECASE):
            if current:
                chunks.append("\n".join(current).strip())
            current = [stripped]
        elif re.match(r"^A\d*[:.)]\s*", stripped, re.IGNORECASE):
            current.append(stripped)
        elif current:
            current.append(stripped)

    if current:
        chunks.append("\n".join(current).strip())

    if not chunks and text.strip():
        chunks = chunk_text(text, chunk_size=600, overlap=120)

    return [c for c in chunks if len(c) > 20]


def chunk_text(text, chunk_size=600, overlap=120):
    """Generic recursive text splitter.

    Splits on paragraph breaks first, then sentences, then words.
    Useful for future sources like knowledge_base or uploaded files.
    """
    if not text or not text.strip():
        return []

    def _split(text, separators):
        if not text or len(text) <= chunk_size:
            return [text] if text else []

        sep = separators[0] if separators else None
        if not sep:
            return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size - overlap)]

        parts = text.split(sep)
        chunks = []
        current = ""

        for part in parts:
            candidate = (current + sep + part).strip() if current else part.strip()
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                if len(part) > chunk_size:
                    sub = _split(part, separators[1:])
                    chunks.extend(sub)
                    current = ""
                else:
                    current = part.strip()

        if current:
            chunks.append(current)

        return chunks

    separators = ["\n\n", "\n", ". ", " ", ""]
    return _split(text, separators)
