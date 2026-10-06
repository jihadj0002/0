import hashlib
import logging
import os

from openai import OpenAI
from django.core.cache import cache

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL = "text-embedding-3-small"


def _client():
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )


def generate_embedding(text):
    """Generate a vector embedding for a single text string.

    Returns a list of floats, or an empty list on failure.
    """
    if not text or not text.strip():
        return []

    cache_key = f"embed:{hashlib.md5(text.strip().encode('utf-8')).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        response = _client().embeddings.create(
            model=EMBEDDING_MODEL,
            input=text.strip(),
        )
        embedding = response.data[0].embedding
        cache.set(cache_key, embedding, 86400)
        return embedding
    except Exception:
        logger.exception("Embedding generation failed")
        return []


def generate_embeddings_batch(texts):
    """Generate embeddings for a list of texts in a single API call.

    Returns a list of (text, embedding) tuples. Failed embeddings return empty list.
    """
    if not texts:
        return []

    valid = [(i, t.strip()) for i, t in enumerate(texts) if t and t.strip()]
    if not valid:
        return []

    try:
        inputs = [t for _, t in valid]
        response = _client().embeddings.create(
            model=EMBEDDING_MODEL,
            input=inputs,
        )
        embeddings = [d.embedding for d in response.data]
        results = [None] * len(texts)
        for (idx, text), emb in zip(valid, embeddings):
            results[idx] = (text, emb)
        return results
    except Exception:
        logger.exception("Batch embedding generation failed")
        return []
