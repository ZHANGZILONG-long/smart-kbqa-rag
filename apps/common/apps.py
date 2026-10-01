# apps/common - shared utilities: audit, health, logging helpers
from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'common'
    verbose_name = '公共组件'
