import logging
import threading

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import AgentIdentity, StoreConfig, BehaviorRules, RAGChunk
from .chunking import chunk_sample_qa
from .embeddings import generate_embeddings_batch

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User, dispatch_uid="context_create_defaults")
def create_context_defaults(sender, instance, created, **kwargs):
    if not created:
        return
    AgentIdentity.objects.get_or_create(user=instance)
    StoreConfig.objects.get_or_create(user=instance)
    BehaviorRules.objects.get_or_create(user=instance)


@receiver(post_save, sender=BehaviorRules, dispatch_uid="rag_process_sample_qa")
def process_sample_qa_rag(sender, instance, **kwargs):
    """When sample_questions_answers is saved, chunk → embed → store in background."""
    text = (instance.sample_questions_answers or "").strip()
    if not text:
        RAGChunk.objects.filter(user=instance.user, source="sample_qa").update(is_active=False)
        return

    threading.Thread(
        target=_build_rag_chunks,
        args=[instance.user, text],
        daemon=True,
    ).start()


def _build_rag_chunks(user, text):
    """Chunk Q&A text, generate embeddings, replace old chunks."""
    try:
        chunks = chunk_sample_qa(text)
        if not chunks:
            RAGChunk.objects.filter(user=user, source="sample_qa").update(is_active=False)
            return

        results = generate_embeddings_batch(chunks)
        if not results:
            logger.warning("RAG embedding returned no results for user=%s", user.pk)
            return

        new_chunks = []
        for idx, (content, embedding) in enumerate(results):
            if embedding:
                new_chunks.append(RAGChunk(
                    user=user,
                    content=content,
                    embedding=embedding,
                    source="sample_qa",
                    chunk_index=idx,
                    is_active=True,
                ))

        if new_chunks:
            RAGChunk.objects.filter(user=user, source="sample_qa").update(is_active=False)
            RAGChunk.objects.bulk_create(new_chunks)
            logger.info(
                "RAG: created %d chunks for user=%s source=sample_qa",
                len(new_chunks), user.pk,
            )
    except Exception:
        logger.exception("RAG chunking/embedding failed for user=%s", user.pk)
