from django.urls import path

from documents.views import (
    DocumentDetailView,
    DocumentListCreateView,
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
]
