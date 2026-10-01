from django.urls import path

from documents.views import (
    DocumentApproveView,
    DocumentDetailView,
    DocumentListCreateView,
    DocumentRejectView,
    DocumentReprocessView,
)

urlpatterns = [
    path('', DocumentListCreateView.as_view(), name='document-list-create'),
    path('<int:pk>/', DocumentDetailView.as_view(), name='document-detail'),
    path(
        '<int:pk>/reprocess/',
        DocumentReprocessView.as_view(),
        name='document-reprocess',
    ),
    path(
        '<int:pk>/approve/',
        DocumentApproveView.as_view(),
        name='document-approve',
    ),
    path(
        '<int:pk>/reject/',
        DocumentRejectView.as_view(),
        name='document-reject',
    ),
]
