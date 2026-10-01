from django.urls import path

from qa.views import AskView, SessionDetailView, SessionListView

urlpatterns = [
    path('ask/', AskView.as_view(), name='qa-ask'),
    path('sessions/', SessionListView.as_view(), name='qa-sessions'),
    path('sessions/<int:pk>/', SessionDetailView.as_view(), name='qa-session-detail'),
]
