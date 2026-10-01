from django.db.models import Q


def visible_documents_q(user):
    """普通用户：全员文档 + 本部门文档；管理员：全部。"""
    if not user or not user.is_authenticated:
        return Q(pk__in=[])
    if getattr(user, 'is_admin', False):
        return Q()
    q = Q(visibility='public')
    if user.department_id:
        q |= Q(visibility='department', department_id=user.department_id)
    return q


def can_view_document(user, document):
    if not user or not user.is_authenticated:
        return False
    if getattr(user, 'is_admin', False):
        return True
    if document.visibility == 'public':
        return True
    return bool(
        document.visibility == 'department'
        and user.department_id
        and document.department_id == user.department_id
    )


def can_delete_document(user, document):
    if not user or not user.is_authenticated:
        return False
    if getattr(user, 'is_admin', False):
        return True
    return document.uploader_id == user.id


def chroma_access_filter(user):
    """
    构造 Chroma where 过滤条件。
    管理员不传过滤；普通用户只能看到 public 或本部门 department 文档。
    """
    if not user or not user.is_authenticated:
        return {'document_id': {'$eq': -1}}
    if getattr(user, 'is_admin', False):
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
