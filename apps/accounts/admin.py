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
        'is_staff',
        'is_active',
        'date_joined',
    )
    list_filter = ('is_staff', 'is_active', 'department')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    fieldsets = DjangoUserAdmin.fieldsets + (
        ('组织信息', {'fields': ('department',)}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ('组织信息', {'fields': ('department',)}),
    )
