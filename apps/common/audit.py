import logging

from common.models import AuditLog

logger = logging.getLogger('smartkbqa.audit')


def client_ip(request):
    if not request:
        return None
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def write_audit(
    *,
    request=None,
    user=None,
    action: str,
    object_type: str = '',
    object_id: str = '',
    detail=None,
    success: bool = True,
    status_code: int | None = None,
):
    """写入审计日志；失败不影响主流程。"""
    try:
        actor = user
        if actor is None and request is not None:
            actor = getattr(request, 'user', None)
            if actor is not None and not getattr(actor, 'is_authenticated', False):
                actor = None
        username = ''
        if actor is not None:
            username = getattr(actor, 'username', '') or ''
        path = ''
        method = ''
        ip = None
        if request is not None:
            path = getattr(request, 'path', '') or ''
            method = getattr(request, 'method', '') or ''
            ip = client_ip(request)
        AuditLog.objects.create(
            user=actor if actor and getattr(actor, 'pk', None) else None,
            username=username,
            action=action,
            object_type=object_type or '',
            object_id=str(object_id) if object_id not in (None, '') else '',
            detail=detail or {},
            ip=ip,
            path=path[:255],
            method=method[:16],
            status_code=status_code,
            success=success,
        )
        logger.info(
            'audit action=%s user=%s object=%s:%s success=%s',
            action,
            username or '-',
            object_type or '-',
            object_id or '-',
            success,
        )
    except Exception:
        logger.exception('failed to write audit log action=%s', action)
