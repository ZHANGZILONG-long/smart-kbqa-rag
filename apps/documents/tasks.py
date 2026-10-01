from celery import shared_task
from django.db import transaction
from django.utils import timezone


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def process_document_task(self, document_id: int):
    from documents.models import Document, DocumentChunk
    from documents.services.chunking import split_text
    from documents.services.parser import extract_text
    from documents.services.vectorstore import delete_document_vectors, upsert_chunks

    try:
        document = Document.objects.select_related('department', 'uploader').get(
            pk=document_id
        )
    except Document.DoesNotExist:
        return {'ok': False, 'reason': 'not_found'}

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

        chunks = split_text(text)
        if not chunks:
            raise ValueError('文本分块结果为空')

        delete_document_vectors(document.id)
        DocumentChunk.objects.filter(document=document).delete()

        ids = []
        metadatas = []
        chunk_rows = []
        for idx, content in enumerate(chunks):
            chroma_id = f'doc-{document.id}-chunk-{idx}'
            ids.append(chroma_id)
            metadatas.append(
                {
                    'document_id': document.id,
                    'chunk_index': idx,
                    'visibility': document.visibility,
                    'department_id': document.department_id or 0,
                    'title': document.title[:200],
                }
            )
            chunk_rows.append(
                DocumentChunk(
                    document=document,
                    chunk_index=idx,
                    content=content,
                    chroma_id=chroma_id,
                )
            )

        upsert_chunks(
            document_id=document.id,
            texts=chunks,
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
        return {'ok': True, 'document_id': document.id, 'chunk_count': len(chunks)}
    except Exception as exc:
        Document.objects.filter(pk=document_id).update(
            status=Document.Status.FAILED,
            error_message=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        # 不向上抛：上传接口应返回文档记录，由 status=failed 表达结果
        return {'ok': False, 'document_id': document_id, 'error': str(exc)[:500]}
