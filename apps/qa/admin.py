from django.contrib import admin

from qa.models import QAMessage, QuestionSession


class QAMessageInline(admin.TabularInline):
    model = QAMessage
    extra = 0
    readonly_fields = ('role', 'content', 'sources', 'created_at')


@admin.register(QuestionSession)
class QuestionSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'title', 'created_at', 'updated_at')
    search_fields = ('title', 'user__username')
    inlines = [QAMessageInline]
