from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from common.audit import write_audit
from common.models import AuditLog
from documents.models import Document
from documents.permissions import (
    can_approve_document,
    can_delete_document,
    can_reject_document,
    can_reprocess_document,
    can_upload_document,
    visible_documents_q,
)
from documents.serializers import (
    DocumentRejectSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
)
from documents.tasks import process_document_task


class UploadRateThrottle(ScopedRateThrottle):
    scope = 'upload'


class DocumentListCreateView(APIView):
    """
    GET  /api/documents/          列表（按权限过滤）
    POST /api/documents/          上传（仅总管理员/部门管理员）
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [UploadRateThrottle]
    throttle_scope = 'upload'

    def get_throttles(self):
        if self.request.method == 'GET':
            return []
        return super().get_throttles()

    def get(self, request):
        qs = (
            Document.objects.filter(visible_documents_q(request.user))
            .select_related('department', 'uploader', 'reviewed_by')
            .order_by('-created_at')
        )
        only_pending = request.query_params.get('pending_approval')
        if only_pending in ('1', 'true', 'yes') and getattr(
            request.user, 'is_super_admin', False
        ):
            qs = qs.filter(status=Document.Status.PENDING_APPROVAL)

        page = self.paginate(request, qs)
        serializer = DocumentSerializer(
            page, many=True, context={'request': request}
        )
        return Response(
            {
                'count': qs.count(),
                'results': serializer.data,
            }
        )

    def post(self, request):
        if not can_upload_document(request.user):
            return Response(
                {'detail': '仅总管理员或部门管理员可以上传文档'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = DocumentUploadSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        document = serializer.save()

        started = False
        if document.status == Document.Status.PENDING:
            process_document_task.delay(document.id)
            started = True

        document.refresh_from_db()
        write_audit(
            request=request,
            user=request.user,
            action=AuditLog.Action.UPLOAD_DOC,
            object_type='document',
            object_id=document.id,
            detail={
                'title': document.title,
                'status': document.status,
                'awaiting_approval': document.status
                == Document.Status.PENDING_APPROVAL,
            },
            status_code=201,
        )
        return Response(
            {
                'document': DocumentSerializer(
                    document, context={'request': request}
                ).data,
                'async': started and not settings.CELERY_TASK_ALWAYS_EAGER,
                'awaiting_approval': document.status
                == Document.Status.PENDING_APPROVAL,
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
        return qs[start : start + page_size]


class DocumentDetailView(APIView):
    """
    GET    /api/documents/{id}/
    DELETE /api/documents/{id}/
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, request, pk):
        try:
            doc = Document.objects.select_related(
                'department', 'uploader', 'reviewed_by'
            ).get(pk=pk)
        except Document.DoesNotExist:
            return None
        if not Document.objects.filter(
            visible_documents_q(request.user), pk=pk
        ).exists():
            return None
        return doc

    def get(self, request, pk):
        doc = self.get_object(request, pk)
        if not doc:
            return Response(
                {'detail': '文档不存在或无权访问'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            DocumentSerializer(doc, context={'request': request}).data
        )

    def delete(self, request, pk):
        doc = self.get_object(request, pk)
        if not doc:
            return Response(
                {'detail': '文档不存在或无权访问'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not can_delete_document(request.user, doc):
            return Response(
                {'detail': '无权删除该文档'},
                status=status.HTTP_403_FORBIDDEN,
            )
        from documents.services.vectorstore import delete_document_vectors

        delete_document_vectors(doc.id)
        doc.file.delete(save=False)
        doc_id = doc.id
        doc.delete()
        write_audit(
            request=request,
            user=request.user,
            action=AuditLog.Action.DELETE_DOC,
            object_type='document',
            object_id=doc_id,
            status_code=204,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class DocumentReprocessView(APIView):
    """POST /api/documents/{id}/reprocess/  重新解析向量化"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            doc = Document.objects.get(pk=pk)
        except Document.DoesNotExist:
            return Response(
                {'detail': '文档不存在'}, status=status.HTTP_404_NOT_FOUND
            )
        if not Document.objects.filter(
            visible_documents_q(request.user), pk=pk
        ).exists():
            return Response(
                {'detail': '无权访问'}, status=status.HTTP_403_FORBIDDEN
            )
        if not can_reprocess_document(request.user, doc):
            return Response(
                {'detail': '无权重新处理'}, status=status.HTTP_403_FORBIDDEN
            )
        if doc.status in (
            Document.Status.PENDING_APPROVAL,
            Document.Status.REJECTED,
        ):
            return Response(
                {'detail': '待审批或已驳回的文档不能重新处理，请先审批通过'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        doc.status = Document.Status.PENDING
        doc.error_message = ''
        doc.save(update_fields=['status', 'error_message', 'updated_at'])
        process_document_task.delay(doc.id)
        doc.refresh_from_db()
        write_audit(
            request=request,
            user=request.user,
            action=AuditLog.Action.REPROCESS_DOC,
            object_type='document',
            object_id=doc.id,
            status_code=200,
        )
        return Response(
            {
                'document': DocumentSerializer(
                    doc, context={'request': request}
                ).data,
                'async': not settings.CELERY_TASK_ALWAYS_EAGER,
            }
        )


class DocumentApproveView(APIView):
    """POST /api/documents/{id}/approve/  总管理员通过审批并开始向量化"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            doc = Document.objects.select_related(
                'department', 'uploader', 'reviewed_by'
            ).get(pk=pk)
        except Document.DoesNotExist:
            return Response(
                {'detail': '文档不存在'}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_approve_document(request.user, doc):
            return Response(
                {'detail': '无权审批或文档不在待审批状态'},
                status=status.HTTP_403_FORBIDDEN,
            )

        doc.status = Document.Status.PENDING
        doc.reviewed_by = request.user
        doc.reviewed_at = timezone.now()
        doc.review_note = ''
        doc.error_message = ''
        doc.save(
            update_fields=[
                'status',
                'reviewed_by',
                'reviewed_at',
                'review_note',
                'error_message',
                'updated_at',
            ]
        )
        process_document_task.delay(doc.id)
        doc.refresh_from_db()
        write_audit(
            request=request,
            user=request.user,
            action=AuditLog.Action.APPROVE_DOC,
            object_type='document',
            object_id=doc.id,
            status_code=200,
        )
        return Response(
            {
                'document': DocumentSerializer(
                    doc, context={'request': request}
                ).data,
                'async': not settings.CELERY_TASK_ALWAYS_EAGER,
                'detail': '已通过审批，开始处理文档',
            }
        )


class DocumentRejectView(APIView):
    """POST /api/documents/{id}/reject/  总管理员驳回"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            doc = Document.objects.select_related(
                'department', 'uploader', 'reviewed_by'
            ).get(pk=pk)
        except Document.DoesNotExist:
            return Response(
                {'detail': '文档不存在'}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_reject_document(request.user, doc):
            return Response(
                {'detail': '无权驳回或文档不在待审批状态'},
                status=status.HTTP_403_FORBIDDEN,
            )

        ser = DocumentRejectSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        reason = (ser.validated_data.get('reason') or '').strip()

        doc.status = Document.Status.REJECTED
        doc.reviewed_by = request.user
        doc.reviewed_at = timezone.now()
        doc.review_note = reason
        doc.error_message = reason or '总管理员已驳回'
        doc.save(
            update_fields=[
                'status',
                'reviewed_by',
                'reviewed_at',
                'review_note',
                'error_message',
                'updated_at',
            ]
        )
        write_audit(
            request=request,
            user=request.user,
            action=AuditLog.Action.REJECT_DOC,
            object_type='document',
            object_id=doc.id,
            detail={'reason': reason},
            status_code=200,
        )
        return Response(
            {
                'document': DocumentSerializer(
                    doc, context={'request': request}
                ).data,
                'detail': '已驳回',
            }
        )
