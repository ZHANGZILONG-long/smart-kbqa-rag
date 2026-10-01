from django.urls import path

from .views import (
    ChangePasswordView,
    DepartmentListView,
    LoginView,
    LogoutView,
    MeView,
    RefreshTokenView,
    RegisterView,
    StaffDetailView,
    StaffListView,
)

urlpatterns = [
    path('login/', LoginView.as_view(), name='auth-login'),
    path('register/', RegisterView.as_view(), name='auth-register'),
    path('departments/', DepartmentListView.as_view(), name='auth-departments'),
    path('refresh/', RefreshTokenView.as_view(), name='auth-refresh'),
    path('logout/', LogoutView.as_view(), name='auth-logout'),
    path('me/', MeView.as_view(), name='auth-me'),
    path(
        'change-password/',
        ChangePasswordView.as_view(),
        name='auth-change-password',
    ),
    path('staff/', StaffListView.as_view(), name='auth-staff-list'),
    path('staff/<int:pk>/', StaffDetailView.as_view(), name='auth-staff-detail'),
]

