import os
import uuid

from django.conf import settings
from django.db import models


def document_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f'documents/{uuid.uuid4().hex}{ext}'


class Document(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = 'public', '全员可见'
        DEPARTMENT = 'department', '部门可见'

    class Status(models.TextChoices):
        PENDING_APPROVAL = 'pending_approval', '待审批'
        REJECTED = 'rejected', '已驳回'
        PENDING = 'pending', '待处理'
        PROCESSING = 'processing', '处理中'
        READY = 'ready', '已就绪'
        FAILED = 'failed', '失败'

    title = models.CharField('标题', max_length=255)
    file = models.FileField('文件', upload_to=document_upload_to)
    original_filename = models.CharField('原始文件名', max_length=255)
    file_type = models.CharField('文件类型', max_length=16, blank=True)
    file_size = models.PositiveIntegerField('文件大小(字节)', default=0)
    visibility = models.CharField(
        '可见范围',
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.DEPARTMENT,
    )
    department = models.ForeignKey(
        'accounts.Department',
        verbose_name='所属部门',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
    )
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='上传者',
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_documents',
    )
    status = models.CharField(
        '处理状态',
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='审批人',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_documents',
    )
    reviewed_at = models.DateTimeField('审批时间', null=True, blank=True)
    review_note = models.CharField('审批备注', max_length=500, blank=True)
    chunk_count = models.PositiveIntegerField('分块数量', default=0)
    error_message = models.TextField('错误信息', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    @property
    def needs_approval(self) -> bool:
        return self.status == self.Status.PENDING_APPROVAL

    @property
    def is_searchable(self) -> bool:
        """已入库、可供检索的文档。"""
        return self.status == self.Status.READY

    class Meta:
        verbose_name = '文档'
        verbose_name_plural = '文档'
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class DocumentChunk(models.Model):
    """文档分块元数据，向量本体保存在 Chroma。"""

    document = models.ForeignKey(
        Document,
        verbose_name='文档',
        on_delete=models.CASCADE,
        related_name='chunks',
    )
    chunk_index = models.PositiveIntegerField('分块序号')
    content = models.TextField('文本内容')
    chroma_id = models.CharField('Chroma ID', max_length=64, unique=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '文档分块'
        verbose_name_plural = '文档分块'
        ordering = ['document_id', 'chunk_index']
        unique_together = [('document', 'chunk_index')]

    def __str__(self):
        return f'{self.document_id}#{self.chunk_index}'
