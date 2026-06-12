import logging

from .embeddings import generate_embedding
from .models import RAGChunk

logger = logging.getLogger(__name__)


def cosine_similarity(a, b):
    """Pure-Python cosine similarity between two vectors."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def search_chunks(user, query, top_k=3, min_score=0.0):
    """Search active RAG chunks for the user by cosine similarity.

    Args:
        user: User instance.
        query: Natural language query string.
        top_k: Maximum number of results to return.
        min_score: Minimum similarity score threshold (0.0 = no filter).

    Returns:
        List of dicts: [{"content": str, "source": str, "chunk_index": int, "score": float}, ...]
    """
    if not query or not query.strip():
        return []

    query_vec = generate_embedding(query)
    if not query_vec:
        return []

    chunks = list(RAGChunk.objects.filter(user=user, is_active=True))

    if not chunks:
        return []

    scored = []
    for c in chunks:
        if not c.embedding:
            continue
        score = cosine_similarity(query_vec, c.embedding)
        if score >= min_score:
            scored.append((score, c))

    scored.sort(key=lambda x: -x[0])

    results = []
    for score, c in scored[:top_k]:
        results.append({
            "content": c.content,
            "source": c.get_source_display(),
            "chunk_index": c.chunk_index,
            "score": round(score, 4),
        })

    return results
