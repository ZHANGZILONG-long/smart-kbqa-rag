from django.db.models import Q

from documents.models import Document


def visible_documents_q(user):
    """
    可见范围：
    - 总管理员：全部（含待审批/已驳回）
    - 其他人：全员文档 + 本部门文档，且排除他人待审批/已驳回；
      自己上传的待审批/已驳回仍可见
    """
    if not user or not user.is_authenticated:
        return Q(pk__in=[])
    if getattr(user, 'is_super_admin', False):
        return Q()

    scope = Q(visibility='public')
    if user.department_id:
        scope |= Q(visibility='department', department_id=user.department_id)

    hidden = Q(
        status__in=[
            Document.Status.PENDING_APPROVAL,
            Document.Status.REJECTED,
        ]
    )
    # 正常可见文档，或本人上传（便于查看审批进度）
    return (scope & ~hidden) | Q(uploader_id=user.id)


def can_view_document(user, document):
    if not user or not user.is_authenticated:
        return False
    if getattr(user, 'is_super_admin', False):
        return True
    if document.uploader_id == user.id:
        return True
    if document.status in (
        Document.Status.PENDING_APPROVAL,
        Document.Status.REJECTED,
    ):
        return False
    if document.visibility == 'public':
        return True
    return bool(
        document.visibility == 'department'
        and user.department_id
        and document.department_id == user.department_id
    )


def can_upload_document(user) -> bool:
    """仅总管理员、部门管理员可上传。"""
    return bool(user and user.is_authenticated and getattr(user, 'can_upload', False))


def can_delete_document(user, document) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(user, 'is_super_admin', False):
        return True
    if getattr(user, 'is_dept_admin', False):
        # 部门管理员：本部门已通过文档，或自己上传的（含待审批）
        if document.uploader_id == user.id:
            return True
        if document.status in (
            Document.Status.PENDING_APPROVAL,
            Document.Status.REJECTED,
        ):
            return False
        if (
            document.visibility == 'department'
            and user.department_id
            and document.department_id == user.department_id
        ):
            return True
    return False


def can_reprocess_document(user, document) -> bool:
    if document.status in (
        Document.Status.PENDING_APPROVAL,
        Document.Status.REJECTED,
    ):
        return False
    return can_delete_document(user, document)


def can_approve_document(user, document) -> bool:
    """仅总管理员可审批「待审批」文档。"""
    if not user or not user.is_authenticated:
        return False
    if not getattr(user, 'is_super_admin', False):
        return False
    return document.status == Document.Status.PENDING_APPROVAL


def can_reject_document(user, document) -> bool:
    return can_approve_document(user, document)


def chroma_access_filter(user):
    """
    Chroma where 过滤：
    - 总管理员：不过滤
    - 其他人：public 或本部门 department
    （未审批文档不会写入向量库，故无需在此过滤审批状态）
    """
    if not user or not user.is_authenticated:
        return {'document_id': {'$eq': -1}}
    if getattr(user, 'is_super_admin', False):
        return None
    if user.department_id:
        return {
            '$or': [
                {'visibility': {'$eq': 'public'}},
                {
                    '$and': [
                        {'visibility': {'$eq': 'department'}},
                        {'department_id': {'$eq': user.department_id}},
                    ]
                },
            ]
        }
    return {'visibility': {'$eq': 'public'}}
