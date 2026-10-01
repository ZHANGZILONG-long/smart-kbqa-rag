from django.urls import path

from common.views import AuditLogListView, HealthView, ReadyView

urlpatterns = [
    path('health/', HealthView.as_view(), name='health'),
    path('health/ready/', ReadyView.as_view(), name='health-ready'),
    path('audit/', AuditLogListView.as_view(), name='audit-list'),
]
