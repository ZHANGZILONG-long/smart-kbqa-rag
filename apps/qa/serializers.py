from rest_framework import serializers

from qa.models import QAMessage, QuestionSession


class AskSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=2000)
    session_id = serializers.IntegerField(required=False, allow_null=True)
    top_k = serializers.IntegerField(required=False, min_value=1, max_value=10, default=5)


class QAMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = QAMessage
        fields = ('id', 'role', 'content', 'sources', 'created_at')
        read_only_fields = fields


class QuestionSessionSerializer(serializers.ModelSerializer):
    messages = QAMessageSerializer(many=True, read_only=True)

    class Meta:
        model = QuestionSession
        fields = ('id', 'title', 'created_at', 'updated_at', 'messages')
        read_only_fields = fields
