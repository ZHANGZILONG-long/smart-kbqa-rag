from django.contrib.auth.models import AbstractUser
from django.db import models


class Department(models.Model):
    """公司部门，用于后续文档权限隔离。"""

    name = models.CharField('部门名称', max_length=64, unique=True)
    code = models.CharField('部门编码', max_length=32, unique=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '部门'
        verbose_name_plural = '部门'
        ordering = ['code']

    def __str__(self):
        return f'{self.name}({self.code})'


class User(AbstractUser):
    """
    自定义用户：在 Django 自带账号字段上增加部门归属。

    角色约定（个人项目模拟企业场景）：
    - is_staff=True  视为管理员，可管理用户、部门与全部文档
    - is_staff=False 视为普通用户，只能访问全员文档与本部门文档
    """

    department = models.ForeignKey(
        Department,
        verbose_name='所属部门',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
    )

    class Meta:
        verbose_name = '用户'
        verbose_name_plural = '用户'

    def __str__(self):
        return self.username

    @property
    def is_admin(self):
        """业务层统一的管理员判断。"""
        return bool(self.is_staff or self.is_superuser)
