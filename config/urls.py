from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/documents/', include('documents.urls')),
    path('api/qa/', include('qa.urls')),
    path('api/', include('common.urls')),
    path(
        'login/',
        TemplateView.as_view(template_name='login.html'),
        name='login-page',
    ),
    path(
        'register/',
        TemplateView.as_view(template_name='register.html'),
        name='register-page',
    ),
    path(
        'qa/',
        TemplateView.as_view(template_name='qa.html'),
        name='qa-page',
    ),
    path(
        'profile/',
        TemplateView.as_view(template_name='profile.html'),
        name='profile-page',
    ),
    path(
        'documents/',
        TemplateView.as_view(template_name='documents.html'),
        name='documents-page',
    ),
    path(
        'upload/',
        TemplateView.as_view(template_name='upload.html'),
        name='upload-page',
    ),
    path(
        'staff/',
        TemplateView.as_view(template_name='staff.html'),
        name='staff-page',
    ),
    # 兼容旧入口
    path(
        'home/',
        RedirectView.as_view(pattern_name='qa-page', permanent=False),
        name='home-page',
    ),
    path('', RedirectView.as_view(pattern_name='login-page', permanent=False)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
