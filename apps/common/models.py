from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """关键操作审计记录。"""

    class Action(models.TextChoices):
        LOGIN = 'login', '登录'
        LOGOUT = 'logout', '退出'
        REGISTER = 'register', '注册'
        CHANGE_PASSWORD = 'change_password', '修改密码'
        UPLOAD_DOC = 'upload_doc', '上传文档'
        APPROVE_DOC = 'approve_doc', '审批通过'
        REJECT_DOC = 'reject_doc', '审批驳回'
        DELETE_DOC = 'delete_doc', '删除文档'
        REPROCESS_DOC = 'reprocess_doc', '重处理文档'
        ASK = 'ask', '知识问答'
        MEMORY_RESET = 'memory_reset', '重置会话记忆'
        STAFF_UPDATE = 'staff_update', '更新员工'
        OTHER = 'other', '其他'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        verbose_name='操作者',
    )
    username = models.CharField('用户名快照', max_length=150, blank=True)
    action = models.CharField('动作', max_length=32, choices=Action.choices, db_index=True)
    object_type = models.CharField('对象类型', max_length=64, blank=True)
    object_id = models.CharField('对象ID', max_length=64, blank=True)
    detail = models.JSONField('详情', default=dict, blank=True)
    ip = models.GenericIPAddressField('IP', null=True, blank=True)
    path = models.CharField('路径', max_length=255, blank=True)
    method = models.CharField('方法', max_length=16, blank=True)
    status_code = models.PositiveSmallIntegerField('状态码', null=True, blank=True)
    success = models.BooleanField('是否成功', default=True)
    created_at = models.DateTimeField('时间', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = '审计日志'
        verbose_name_plural = '审计日志'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.action} by {self.username or "-"} @ {self.created_at}'
