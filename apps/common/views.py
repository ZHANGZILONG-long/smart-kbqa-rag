import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import AuditLog

logger = logging.getLogger('smartkbqa.health')


class HealthView(APIView):
    """
    GET /api/health/  存活检查（公开）
    GET /api/health/ready/  就绪：DB/缓存
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                'status': 'ok',
                'service': 'smartkbqa',
                'time': timezone.now().isoformat(),
                'debug': bool(settings.DEBUG),
            }
        )


class ReadyView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        checks = {}
        ok = True

        t0 = time.perf_counter()
        try:
            connection.ensure_connection()
            checks['database'] = {
                'ok': True,
                'ms': round((time.perf_counter() - t0) * 1000, 2),
            }
        except Exception as exc:
            ok = False
            checks['database'] = {'ok': False, 'error': str(exc)[:200]}
            logger.exception('health database failed')

        t0 = time.perf_counter()
        try:
            cache.set('health_ping', '1', 10)
            val = cache.get('health_ping')
            checks['cache'] = {
                'ok': val == '1',
                'ms': round((time.perf_counter() - t0) * 1000, 2),
            }
            if val != '1':
                ok = False
        except Exception as exc:
            ok = False
            checks['cache'] = {'ok': False, 'error': str(exc)[:200]}
            logger.exception('health cache failed')

        body = {
            'status': 'ok' if ok else 'degraded',
            'checks': checks,
            'time': timezone.now().isoformat(),
        }
        return JsonResponse(body, status=200 if ok else 503)


class AuditLogListView(APIView):
    """GET /api/audit/  仅总管理员可查看。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not getattr(user, 'is_super_admin', False):
            return Response({'detail': '仅总管理员可查看审计日志'}, status=403)
        qs = AuditLog.objects.all().order_by('-created_at')
        action = (request.query_params.get('action') or '').strip()
        if action:
            qs = qs.filter(action=action)
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except ValueError:
            page = 1
        page_size = 50
        start = (page - 1) * page_size
        rows = qs[start : start + page_size]
        data = [
            {
                'id': r.id,
                'created_at': r.created_at,
                'action': r.action,
                'action_display': r.get_action_display(),
                'username': r.username,
                'object_type': r.object_type,
                'object_id': r.object_id,
                'detail': r.detail,
                'ip': r.ip,
                'path': r.path,
                'success': r.success,
                'status_code': r.status_code,
            }
            for r in rows
        ]
        return Response({'count': qs.count(), 'results': data, 'page': page})
