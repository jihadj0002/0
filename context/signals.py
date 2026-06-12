import hashlib
import logging
import threading

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import AgentIdentity, StoreConfig, BehaviorRules, RAGChunk
from .chunking import chunk_sample_qa, chunk_text
from .embeddings import generate_embeddings_batch

logger = logging.getLogger(__name__)

_RAG_CONTENT_CACHE = {}


@receiver(post_save, sender=User, dispatch_uid="context_create_defaults")
def create_context_defaults(sender, instance, created, **kwargs):
    if not created:
        return
    AgentIdentity.objects.get_or_create(user=instance)
    StoreConfig.objects.get_or_create(user=instance)
    BehaviorRules.objects.get_or_create(user=instance)


@receiver(post_save, sender=BehaviorRules, dispatch_uid="rag_process_both")
def process_rag_sources(sender, instance, **kwargs):
    """When BehaviorRules is saved, process both sample_qa and knowledge_base in background."""
    _maybe_process_source(instance, "sample_qa", instance.sample_questions_answers)
    _maybe_process_source(instance, "knowledge_base", instance.knowledge_base)


def _maybe_process_source(instance, source, raw_text):
    """Re-chunk + re-embed a source only if its content hash changed."""
    text = (raw_text or "").strip()
    cache_key = (instance.user_id, source)
    content_hash = hashlib.md5(text.encode("utf-8")).hexdigest() if text else ""

    if not text:
        RAGChunk.objects.filter(user=instance.user, source=source).update(is_active=False)
        _RAG_CONTENT_CACHE.pop(cache_key, None)
        return

    prev_hash = _RAG_CONTENT_CACHE.get(cache_key)
    if prev_hash == content_hash:
        return

    _RAG_CONTENT_CACHE[cache_key] = content_hash

    threading.Thread(
        target=_build_rag_chunks,
        args=[instance.user, text, source],
        daemon=True,
    ).start()


def _build_rag_chunks(user, text, source):
    """Chunk text, generate embeddings, replace old chunks for a given source."""
    try:
        if source == "sample_qa":
            chunks = chunk_sample_qa(text)
        else:
            chunks = chunk_text(text, chunk_size=600, overlap=120)

        if not chunks:
            RAGChunk.objects.filter(user=user, source=source).update(is_active=False)
            return

        results = generate_embeddings_batch(chunks)
        if not results:
            logger.warning("RAG embedding returned no results for user=%s source=%s", user.pk, source)
            return

        new_chunks = []
        for idx, (content, embedding) in enumerate(results):
            if embedding:
                new_chunks.append(RAGChunk(
                    user=user,
                    content=content,
                    embedding=embedding,
                    source=source,
                    chunk_index=idx,
                    is_active=True,
                ))

        if new_chunks:
            RAGChunk.objects.filter(user=user, source=source).update(is_active=False)
            RAGChunk.objects.bulk_create(new_chunks)
            logger.info(
                "RAG: created %d chunks for user=%s source=%s",
                len(new_chunks), user.pk, source,
            )
    except Exception:
        logger.exception("RAG chunking/embedding failed for user=%s source=%s", user.pk, source)
