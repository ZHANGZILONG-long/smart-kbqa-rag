from django.contrib import admin

from common.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'created_at',
        'action',
        'username',
        'object_type',
        'object_id',
        'success',
        'ip',
        'status_code',
    )
    list_filter = ('action', 'success', 'created_at')
    search_fields = ('username', 'object_id', 'path', 'ip')
    readonly_fields = (
        'user',
        'username',
        'action',
        'object_type',
        'object_id',
        'detail',
        'ip',
        'path',
        'method',
        'status_code',
        'success',
        'created_at',
    )
    ordering = ('-created_at',)
