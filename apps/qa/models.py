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
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '问答会话'
        verbose_name_plural = '问答会话'
        ordering = ['-updated_at']

    def __str__(self):
        return self.title or f'session-{self.pk}'


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
