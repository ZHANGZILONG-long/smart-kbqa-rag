from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from qa.models import QAMessage, QuestionSession
from qa.serializers import AskSerializer, QuestionSessionSerializer
from qa.services import run_qa


class AskView(APIView):
    """
    POST /api/qa/ask/
    body: {"question": "...", "session_id": null, "top_k": 5}
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data['question'].strip()
        top_k = serializer.validated_data.get('top_k', 5)
        session_id = serializer.validated_data.get('session_id')

        session = None
        if session_id:
            session = QuestionSession.objects.filter(
                pk=session_id, user=request.user
            ).first()
            if not session:
                return Response(
                    {'detail': '会话不存在'},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            session = QuestionSession.objects.create(
                user=request.user,
                title=question[:40],
            )

        result = run_qa(request.user, question, top_k=top_k)

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

        return Response(
            {
                'session_id': session.id,
                'question': question,
                'answer': result['answer'],
                'sources': result['sources'],
                'hit_count': result['hit_count'],
                'message_id': assistant.id,
            }
        )


class SessionListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = QuestionSession.objects.filter(user=request.user).order_by('-updated_at')[:50]
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
            return Response({'detail': '会话不存在'}, status=status.HTTP_404_NOT_FOUND)
        return Response(QuestionSessionSerializer(session).data)
