from django.conf import settings
from django.db import models


class QuestionSession(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='qa_sessions',
        verbose_name='用户',
    )
    title = models.CharField('会话标题', max_length=200, blank=True)
    summary = models.TextField(
        '历史摘要',
        blank=True,
        default='',
        help_text='被挤出滑动窗口的旧对话的滚动摘要，作为会话的长期记忆。',
    )
    summarized_message_count = models.PositiveIntegerField(
        '已摘要消息数',
        default=0,
        help_text='按消息顺序，已并入 summary 的消息条数，避免重复摘要。',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '问答会话'
        verbose_name_plural = '问答会话'
        ordering = ['-updated_at']

    def __str__(self):
        return self.title or f'session-{self.pk}'

    @property
    def memory_enabled(self) -> bool:
        return bool(getattr(settings, 'MEMORY_ENABLED', True))

    def reset_memory(self, *, purge_messages: bool = False) -> int:
        """清空长期记忆；purge_messages=True 时同时删除全部消息。返回删除的消息数。"""
        deleted = 0
        if purge_messages:
            deleted, _ = self.messages.all().delete()
        self.summary = ''
        self.summarized_message_count = 0
        self.save(update_fields=['summary', 'summarized_message_count'])
        return deleted


class QAMessage(models.Model):
    class Role(models.TextChoices):
        USER = 'user', '用户'
        ASSISTANT = 'assistant', '助手'

    session = models.ForeignKey(
        QuestionSession,
        on_delete=models.CASCADE,
        related_name='messages',
        verbose_name='会话',
    )
    role = models.CharField('角色', max_length=16, choices=Role.choices)
    content = models.TextField('内容')
    sources = models.JSONField('引用来源', default=list, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '问答消息'
        verbose_name_plural = '问答消息'
        ordering = ['id']

    def __str__(self):
        return f'{self.role}:{self.pk}'
