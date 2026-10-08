from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
import logging

logger = logging.getLogger('smartkbqa.documents')


def _send_alert(title: str, detail: dict):
    url = getattr(settings, 'ALERT_WEBHOOK_URL', '') or ''
    if not url:
        logger.error('ALERT %s %s', title, detail)
        return
    try:
        import requests

        requests.post(
            url,
            json={'title': title, 'detail': detail, 'service': 'smartkbqa'},
            timeout=5,
        )
    except Exception:
        logger.exception('alert webhook failed')


@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def process_document_task(self, document_id: int):
    from documents.models import Document, DocumentChunk
    from documents.services.chunking import split_document
    from documents.services.parser import extract_text
    from documents.services.vectorstore import delete_document_vectors, upsert_chunks

    try:
        document = Document.objects.select_related('department', 'uploader').get(
            pk=document_id
        )
    except Document.DoesNotExist:
        return {'ok': False, 'reason': 'not_found'}

    if document.status in (
        Document.Status.PENDING_APPROVAL,
        Document.Status.REJECTED,
    ):
        return {
            'ok': False,
            'reason': 'not_approved',
            'status': document.status,
        }

    Document.objects.filter(pk=document_id).update(
        status=Document.Status.PROCESSING,
        error_message='',
        updated_at=timezone.now(),
    )

    try:
        file_path = document.file.path
        text = extract_text(file_path, document.file_type)
        if not text:
            raise ValueError('未能从文件中提取到有效文本')

        chunks = split_document(text)
        if not chunks:
            raise ValueError('文本分块结果为空')

        delete_document_vectors(document.id)
        DocumentChunk.objects.filter(document=document).delete()

        ids = []
        metadatas = []
        chunk_rows = []
        for idx, chunk in enumerate(chunks):
            chroma_id = f'doc-{document.id}-chunk-{idx}'
            ids.append(chroma_id)
            metadatas.append(
                {
                    'document_id': document.id,
                    'chunk_index': idx,
                    'visibility': document.visibility,
                    'department_id': document.department_id or 0,
                    'title': document.title[:200],
                    'section': chunk.section,
                    'topic': chunk.topic,
                }
            )
            chunk_rows.append(
                DocumentChunk(
                    document=document,
                    chunk_index=idx,
                    content=chunk.content,
                    chroma_id=chroma_id,
                )
            )

        upsert_chunks(
            document_id=document.id,
            texts=[chunk.content for chunk in chunks],
            ids=ids,
            metadatas=metadatas,
        )

        with transaction.atomic():
            DocumentChunk.objects.bulk_create(chunk_rows, batch_size=200)
            Document.objects.filter(pk=document.id).update(
                status=Document.Status.READY,
                chunk_count=len(chunks),
                error_message='',
                updated_at=timezone.now(),
            )
        logger.info(
            'document processed id=%s chunks=%s', document.id, len(chunks)
        )
        return {'ok': True, 'document_id': document.id, 'chunk_count': len(chunks)}
    except Exception as exc:
        retries = getattr(self.request, 'retries', 0)
        max_retries = self.max_retries or 3
        if retries < max_retries and not settings.CELERY_TASK_ALWAYS_EAGER:
            logger.warning(
                'document process retry id=%s attempt=%s err=%s',
                document_id,
                retries + 1,
                exc,
            )
            raise self.retry(exc=exc, countdown=15 * (retries + 1))

        Document.objects.filter(pk=document_id).update(
            status=Document.Status.FAILED,
            error_message=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        _send_alert(
            'document_process_failed',
            {
                'document_id': document_id,
                'error': str(exc)[:500],
                'retries': retries,
            },
        )
        logger.exception('document process failed id=%s', document_id)
        return {'ok': False, 'document_id': document_id, 'error': str(exc)[:500]}
