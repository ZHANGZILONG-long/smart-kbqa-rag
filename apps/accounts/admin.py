from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Department, User


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'code', 'created_at')
    search_fields = ('name', 'code')
    ordering = ('code',)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        'username',
        'email',
        'department',
        'role',
        'is_staff',
        'is_superuser',
        'is_active',
        'date_joined',
    )
    list_filter = ('role', 'is_staff', 'is_superuser', 'is_active', 'department')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            '组织与角色',
            {
                'fields': ('department', 'role'),
                'description': (
                    '部门管理员必须选择所属部门，例如研发部、财务部；'
                    '角色显示为「研发部管理员」这类名称。'
                ),
            },
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ('组织与角色', {'fields': ('department', 'role')}),
    )
