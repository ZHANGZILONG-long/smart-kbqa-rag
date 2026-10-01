import json
import logging
import time

from django.db import transaction
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from common.audit import write_audit
from common.models import AuditLog
from qa.models import QAMessage, QuestionSession
from qa.serializers import AskSerializer, QuestionSessionSerializer
from qa.services import run_qa

logger = logging.getLogger('smartkbqa.qa')


class AskRateThrottle(ScopedRateThrottle):
    scope = 'ask'


def _resolve_session(user, session_id, question):
    if session_id:
        session = QuestionSession.objects.filter(pk=session_id, user=user).first()
        if not session:
            return None, Response(
                {'detail': '会话不存在'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return session, None
    session = QuestionSession.objects.create(user=user, title=question[:40])
    return session, None


def _persist_qa(session, question, result):
    with transaction.atomic():
        QAMessage.objects.create(
            session=session,
            role=QAMessage.Role.USER,
            content=question,
        )
        assistant = QAMessage.objects.create(
            session=session,
            role=QAMessage.Role.ASSISTANT,
            content=result['answer'],
            sources=result['sources'],
        )
        session.updated_at = timezone.now()
        if not session.title:
            session.title = question[:40]
        session.save(update_fields=['updated_at', 'title'])
    return assistant


class AskView(APIView):
    """
    POST /api/qa/ask/
    body: {"question": "...", "session_id": null, "top_k": 5, "stream": false}
    stream=true 时返回 text/event-stream（SSE）。
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AskRateThrottle]
    throttle_scope = 'ask'

    def post(self, request):
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data['question'].strip()
        top_k = serializer.validated_data.get('top_k', 5)
        session_id = serializer.validated_data.get('session_id')
        stream = bool(
            request.data.get('stream')
            or request.query_params.get('stream') in ('1', 'true', 'yes')
        )

        session, err = _resolve_session(request.user, session_id, question)
        if err:
            return err

        if stream:
            return self._stream_response(request, session, question, top_k)

        t0 = time.perf_counter()
        result = run_qa(request.user, question, top_k=top_k)
        assistant = _persist_qa(session, question, result)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

        write_audit(
            request=request,
            user=request.user,
            action=AuditLog.Action.ASK,
            object_type='session',
            object_id=session.id,
            detail={
                'question': question[:120],
                'hit_count': result.get('hit_count'),
                'elapsed_ms': elapsed_ms,
                'stream': False,
            },
            status_code=200,
        )

        return Response(
            {
                'session_id': session.id,
                'question': question,
                'answer': result['answer'],
                'sources': result['sources'],
                'hit_count': result['hit_count'],
                'message_id': assistant.id,
                'engine': result.get('engine', 'langgraph'),
                'search_query': result.get('search_query'),
                'grade': result.get('grade'),
                'grade_reason': result.get('grade_reason'),
                'steps': result.get('steps') or [],
                'elapsed_ms': elapsed_ms,
            }
        )

    def _stream_response(self, request, session, question, top_k):
        user = request.user

        def event_stream():
            def emit(event, data):
                return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'

            yield emit('status', {'phase': 'running', 'session_id': session.id})
            t0 = time.perf_counter()
            try:
                result = run_qa(user, question, top_k=top_k)
            except Exception as exc:
                logger.exception('stream qa failed')
                yield emit('error', {'detail': str(exc)[:500]})
                return

            answer = result.get('answer') or ''
            # 按字符块模拟流式输出，便于前端展示
            chunk_size = 12
            for i in range(0, len(answer), chunk_size):
                yield emit('token', {'text': answer[i : i + chunk_size]})

            assistant = _persist_qa(session, question, result)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            payload = {
                'session_id': session.id,
                'question': question,
                'answer': answer,
                'sources': result.get('sources') or [],
                'hit_count': result.get('hit_count'),
                'message_id': assistant.id,
                'engine': result.get('engine', 'langgraph'),
                'search_query': result.get('search_query'),
                'grade': result.get('grade'),
                'grade_reason': result.get('grade_reason'),
                'steps': result.get('steps') or [],
                'elapsed_ms': elapsed_ms,
            }
            write_audit(
                request=request,
                user=user,
                action=AuditLog.Action.ASK,
                object_type='session',
                object_id=session.id,
                detail={
                    'question': question[:120],
                    'hit_count': result.get('hit_count'),
                    'elapsed_ms': elapsed_ms,
                    'stream': True,
                },
                status_code=200,
            )
            yield emit('done', payload)

        response = StreamingHttpResponse(
            event_stream(), content_type='text/event-stream; charset=utf-8'
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


class SessionListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = QuestionSession.objects.filter(user=request.user).order_by(
            '-updated_at'
        )[:50]
        data = [
            {
                'id': s.id,
                'title': s.title,
                'created_at': s.created_at,
                'updated_at': s.updated_at,
            }
            for s in qs
        ]
        return Response({'results': data})


class SessionDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        session = (
            QuestionSession.objects.filter(pk=pk, user=request.user)
            .prefetch_related('messages')
            .first()
        )
        if not session:
            return Response(
                {'detail': '会话不存在'}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(QuestionSessionSerializer(session).data)

    def delete(self, request, pk):
        session = QuestionSession.objects.filter(pk=pk, user=request.user).first()
        if not session:
            return Response(
                {'detail': '会话不存在'}, status=status.HTTP_404_NOT_FOUND
            )
        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
