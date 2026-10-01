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
    角色约定：
    - super_admin  总管理员：全公司范围
    - dept_admin   某部门管理员（如研发部管理员、财务部管理员）：
                   必须绑定具体部门；只管本部门文档/员工
    - user         普通员工：按所属部门检索，不能上传/管人
    """

    class Role(models.TextChoices):
        USER = 'user', '普通员工'
        DEPT_ADMIN = 'dept_admin', '部门管理员'
        SUPER_ADMIN = 'super_admin', '总管理员'

    department = models.ForeignKey(
        Department,
        verbose_name='所属部门',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        help_text='部门管理员必须绑定部门，例如研发部、财务部',
    )
    role = models.CharField(
        '角色',
        max_length=20,
        choices=Role.choices,
        default=Role.USER,
        db_index=True,
    )

    class Meta:
        verbose_name = '用户'
        verbose_name_plural = '用户'

    def __str__(self):
        return self.username

    def save(self, *args, **kwargs):
        # 保持 Django 后台 is_superuser/is_staff 与业务角色大致同步
        if self.is_superuser:
            self.role = self.Role.SUPER_ADMIN
            self.is_staff = True
        elif self.role == self.Role.SUPER_ADMIN:
            self.is_staff = True
        elif self.role == self.Role.DEPT_ADMIN:
            # 部门管理员不进 Django admin，除非显式给 is_staff
            pass
        super().save(*args, **kwargs)

    @property
    def is_super_admin(self):
        return bool(
            self.is_superuser or self.role == self.Role.SUPER_ADMIN
        )

    @property
    def is_dept_admin(self):
        """是否为「某部门」管理员（须已绑定部门，如研发部管理员）。"""
        return bool(
            self.role == self.Role.DEPT_ADMIN
            and not self.is_super_admin
            and self.department_id
        )

    @property
    def is_admin(self):
        """是否具备任一管理权限（总管理或某部门管理）。"""
        return bool(self.is_super_admin or self.is_dept_admin)

    @property
    def can_upload(self):
        return bool(self.is_super_admin or self.is_dept_admin)

    @property
    def role_display(self):
        if self.is_super_admin:
            return '总管理员'
        if self.role == self.Role.DEPT_ADMIN:
            if self.department_id and self.department:
                return f'{self.department.name}管理员'
            return '部门管理员（未绑定部门）'
        return '普通员工'

    def can_manage_department(self, department_id) -> bool:
        if self.is_super_admin:
            return True
        if self.is_dept_admin and self.department_id and department_id:
            return int(self.department_id) == int(department_id)
        return False

    @property
    def can_manage_staff(self) -> bool:
        """是否可进入员工管理（总管理员或某部门管理员）。"""
        return bool(self.is_admin)

    def can_view_staff_user(self, target) -> bool:
        if not self.is_authenticated or not target:
            return False
        if self.is_super_admin:
            return True
        if self.is_dept_admin and self.department_id:
            return bool(target.department_id == self.department_id)
        return False

    def can_edit_staff_user(self, target) -> bool:
        """
        编辑权限：
        - 总管理员：可编辑任意员工（含设为某部门管理员）
        - 某部门管理员（如研发部）：仅可编辑本部门普通员工
        """
        if not self.is_authenticated or not target:
            return False
        if self.is_super_admin:
            return True
        if self.is_dept_admin and self.department_id:
            return bool(
                target.department_id == self.department_id
                and not target.is_admin
                and target.role == self.Role.USER
            )
        return False

    def allowed_roles_for_target(self, target) -> list[str]:
        """当前操作者可给目标用户设置的角色列表。"""
        if self.is_super_admin:
            return [
                self.Role.USER,
                self.Role.DEPT_ADMIN,
                self.Role.SUPER_ADMIN,
            ]
        if self.can_edit_staff_user(target):
            # 部门管理员不能任命管理员
            return [self.Role.USER]
        return []
