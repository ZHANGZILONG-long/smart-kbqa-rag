"""运维接口与审计相关测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from common.audit import write_audit
from common.models import AuditLog

User = get_user_model()


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class HealthAndAuditTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.super = User.objects.create_user(
            username='ops_boss',
            password='Passw0rd!',
            role=User.Role.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        self.member = User.objects.create_user(
            username='ops_user',
            password='Passw0rd!',
            role=User.Role.USER,
        )

    def test_health_liveness(self):
        res = self.client.get('/api/health/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], 'ok')
        self.assertIn('time', res.data)

    def test_health_ready(self):
        res = self.client.get('/api/health/ready/')
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['status'], 'ok')
        self.assertTrue(body['checks']['database']['ok'])
        self.assertTrue(body['checks']['cache']['ok'])

    def test_write_audit_and_filter(self):
        write_audit(
            user=self.super,
            action=AuditLog.Action.OTHER,
            object_type='system',
            object_id='1',
            detail={'note': 'unit'},
            success=True,
            status_code=200,
        )
        self.assertEqual(AuditLog.objects.count(), 1)

        self.client.force_authenticate(user=self.super)
        res = self.client.get('/api/audit/', {'action': AuditLog.Action.OTHER})
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['action'], AuditLog.Action.OTHER)

        self.client.force_authenticate(user=self.member)
        denied = self.client.get('/api/audit/')
        self.assertEqual(denied.status_code, 403)
