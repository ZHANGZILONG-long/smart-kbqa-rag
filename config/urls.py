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
        'home/',
        TemplateView.as_view(template_name='home.html'),
        name='home-page',
    ),
    path('', RedirectView.as_view(pattern_name='login-page', permanent=False)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
