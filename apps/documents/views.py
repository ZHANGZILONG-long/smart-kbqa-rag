from django.conf import settings
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from documents.models import Document
from documents.permissions import can_delete_document, visible_documents_q
from documents.serializers import DocumentSerializer, DocumentUploadSerializer
from documents.tasks import process_document_task


class DocumentListCreateView(APIView):
    """
    GET  /api/documents/          列表（按权限过滤）
    POST /api/documents/          上传（multipart）
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        qs = (
            Document.objects.filter(visible_documents_q(request.user))
            .select_related('department', 'uploader')
            .order_by('-created_at')
        )
        page = self.paginate(request, qs)
        serializer = DocumentSerializer(page, many=True)
        return Response(
            {
                'count': qs.count(),
                'results': serializer.data,
            }
        )

    def post(self, request):
        serializer = DocumentUploadSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        document = serializer.save()
        process_document_task.delay(document.id)
        document.refresh_from_db()
        return Response(
            {
                'document': DocumentSerializer(document).data,
                'async': not settings.CELERY_TASK_ALWAYS_EAGER,
            },
            status=status.HTTP_201_CREATED,
        )

    def paginate(self, request, qs):
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except ValueError:
            page = 1
        try:
            page_size = min(100, max(1, int(request.query_params.get('page_size', 20))))
        except ValueError:
            page_size = 20
        start = (page - 1) * page_size
        return qs[start:start + page_size]


class DocumentDetailView(APIView):
    """
    GET    /api/documents/{id}/
    DELETE /api/documents/{id}/
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, request, pk):
        try:
            doc = Document.objects.select_related('department', 'uploader').get(pk=pk)
        except Document.DoesNotExist:
            return None
        if not Document.objects.filter(visible_documents_q(request.user), pk=pk).exists():
            return None
        return doc

    def get(self, request, pk):
        doc = self.get_object(request, pk)
        if not doc:
            return Response({'detail': '文档不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)
        return Response(DocumentSerializer(doc).data)

    def delete(self, request, pk):
        doc = self.get_object(request, pk)
        if not doc:
            return Response({'detail': '文档不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)
        if not can_delete_document(request.user, doc):
            return Response({'detail': '无权删除该文档'}, status=status.HTTP_403_FORBIDDEN)
        from documents.services.vectorstore import delete_document_vectors

        delete_document_vectors(doc.id)
        doc.file.delete(save=False)
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DocumentReprocessView(APIView):
    """POST /api/documents/{id}/reprocess/  重新解析向量化"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            doc = Document.objects.get(pk=pk)
        except Document.DoesNotExist:
            return Response({'detail': '文档不存在'}, status=status.HTTP_404_NOT_FOUND)
        if not Document.objects.filter(visible_documents_q(request.user), pk=pk).exists():
            return Response({'detail': '无权访问'}, status=status.HTTP_403_FORBIDDEN)
        if not (request.user.is_admin or doc.uploader_id == request.user.id):
            return Response({'detail': '无权重新处理'}, status=status.HTTP_403_FORBIDDEN)

        doc.status = Document.Status.PENDING
        doc.error_message = ''
        doc.save(update_fields=['status', 'error_message', 'updated_at'])
        process_document_task.delay(doc.id)
        doc.refresh_from_db()
        return Response(
            {
                'document': DocumentSerializer(doc).data,
                'async': not settings.CELERY_TASK_ALWAYS_EAGER,
            }
        )
