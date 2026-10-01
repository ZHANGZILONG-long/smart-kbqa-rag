"""
权限边界与核心接口自动化测试。

运行：
  python manage.py test accounts documents qa common -v 2
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Department
from documents.models import Document
from documents.permissions import (
    can_approve_document,
    can_delete_document,
    can_upload_document,
    visible_documents_q,
)

User = get_user_model()


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class PermissionBoundaryTests(TestCase):
    def setUp(self):
        self.rd = Department.objects.create(name='研发部', code='RD')
        self.fin = Department.objects.create(name='财务部', code='FIN')

        self.super = User.objects.create_user(
            username='super',
            password='Passw0rd!',
            role=User.Role.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        self.rd_admin = User.objects.create_user(
            username='rd_admin',
            password='Passw0rd!',
            role=User.Role.DEPT_ADMIN,
            department=self.rd,
        )
        self.fin_admin = User.objects.create_user(
            username='fin_admin',
            password='Passw0rd!',
            role=User.Role.DEPT_ADMIN,
            department=self.fin,
        )
        self.rd_user = User.objects.create_user(
            username='rd_user',
            password='Passw0rd!',
            role=User.Role.USER,
            department=self.rd,
        )
        self.fin_user = User.objects.create_user(
            username='fin_user',
            password='Passw0rd!',
            role=User.Role.USER,
            department=self.fin,
        )

        self.rd_doc = Document.objects.create(
            title='研发制度',
            original_filename='rd.txt',
            file_type='txt',
            visibility=Document.Visibility.DEPARTMENT,
            department=self.rd,
            uploader=self.rd_admin,
            status=Document.Status.READY,
        )
        self.fin_doc = Document.objects.create(
            title='财务制度',
            original_filename='fin.txt',
            file_type='txt',
            visibility=Document.Visibility.DEPARTMENT,
            department=self.fin,
            uploader=self.fin_admin,
            status=Document.Status.READY,
        )
        self.public_doc = Document.objects.create(
            title='全员手册',
            original_filename='pub.txt',
            file_type='txt',
            visibility=Document.Visibility.PUBLIC,
            department=None,
            uploader=self.super,
            status=Document.Status.READY,
        )
        self.pending_doc = Document.objects.create(
            title='待审文档',
            original_filename='pending.txt',
            file_type='txt',
            visibility=Document.Visibility.DEPARTMENT,
            department=self.rd,
            uploader=self.rd_admin,
            status=Document.Status.PENDING_APPROVAL,
        )

    def test_role_flags(self):
        self.assertTrue(self.super.is_super_admin)
        self.assertTrue(self.rd_admin.is_dept_admin)
        self.assertTrue(self.rd_admin.can_upload)
        self.assertFalse(self.rd_user.can_upload)
        self.assertEqual(self.rd_admin.role_display, '研发部管理员')

    def test_upload_permission(self):
        self.assertTrue(can_upload_document(self.super))
        self.assertTrue(can_upload_document(self.rd_admin))
        self.assertFalse(can_upload_document(self.rd_user))

    def test_visible_documents_scope(self):
        rd_ids = set(
            Document.objects.filter(visible_documents_q(self.rd_user)).values_list(
                'id', flat=True
            )
        )
        self.assertIn(self.rd_doc.id, rd_ids)
        self.assertIn(self.public_doc.id, rd_ids)
        self.assertNotIn(self.fin_doc.id, rd_ids)
        # 待审批：上传者可见，普通同事不可见
        self.assertNotIn(self.pending_doc.id, rd_ids)
        uploader_ids = set(
            Document.objects.filter(
                visible_documents_q(self.rd_admin)
            ).values_list('id', flat=True)
        )
        self.assertIn(self.pending_doc.id, uploader_ids)

        super_ids = set(
            Document.objects.filter(visible_documents_q(self.super)).values_list(
                'id', flat=True
            )
        )
        self.assertEqual(
            super_ids,
            {
                self.rd_doc.id,
                self.fin_doc.id,
                self.public_doc.id,
                self.pending_doc.id,
            },
        )

    def test_only_super_can_approve(self):
        self.assertTrue(can_approve_document(self.super, self.pending_doc))
        self.assertFalse(can_approve_document(self.rd_admin, self.pending_doc))
        self.assertFalse(can_approve_document(self.rd_user, self.pending_doc))

    def test_delete_rules(self):
        self.assertTrue(can_delete_document(self.super, self.fin_doc))
        self.assertTrue(can_delete_document(self.rd_admin, self.rd_doc))
        self.assertFalse(can_delete_document(self.rd_admin, self.fin_doc))
        self.assertFalse(can_delete_document(self.rd_user, self.rd_doc))

    def test_staff_edit_rules(self):
        self.assertTrue(self.super.can_edit_staff_user(self.rd_user))
        self.assertTrue(self.rd_admin.can_edit_staff_user(self.rd_user))
        self.assertFalse(self.rd_admin.can_edit_staff_user(self.fin_user))
        self.assertFalse(self.rd_admin.can_edit_staff_user(self.fin_admin))
        self.assertFalse(self.rd_admin.can_edit_staff_user(self.super))
        self.assertEqual(
            self.rd_admin.allowed_roles_for_target(self.rd_user),
            [User.Role.USER],
        )
        self.assertIn(
            User.Role.DEPT_ADMIN,
            self.super.allowed_roles_for_target(self.rd_user),
        )


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class APIAuthAndStaffTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.dept = Department.objects.create(name='研发部', code='RD')
        self.super = User.objects.create_user(
            username='boss',
            password='Passw0rd!',
            role=User.Role.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        self.dept_admin = User.objects.create_user(
            username='lead',
            password='Passw0rd!',
            role=User.Role.DEPT_ADMIN,
            department=self.dept,
        )
        self.member = User.objects.create_user(
            username='member',
            password='Passw0rd!',
            role=User.Role.USER,
            department=self.dept,
        )

    def _login(self, username, password='Passw0rd!'):
        res = self.client.post(
            '/api/auth/login/',
            {'username': username, 'password': password},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        token = res.data['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        return res.data

    def test_register_and_me(self):
        res = self.client.post(
            '/api/auth/register/',
            {
                'username': 'newbie',
                'password': 'Passw0rd!',
                'password_confirm': 'Passw0rd!',
                'department_id': self.dept.id,
            },
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertIn('access', res.data)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')
        me = self.client.get('/api/auth/me/')
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data['role'], User.Role.USER)

    def test_staff_list_scope(self):
        self._login('lead')
        res = self.client.get('/api/auth/staff/')
        self.assertEqual(res.status_code, 200)
        names = {u['username'] for u in res.data['results']}
        self.assertIn('member', names)
        self.assertIn('lead', names)
        self.assertNotIn('boss', names)

    def test_dept_admin_cannot_promote(self):
        self._login('lead')
        res = self.client.patch(
            f'/api/auth/staff/{self.member.id}/',
            {'role': User.Role.DEPT_ADMIN, 'department_id': self.dept.id},
            format='json',
        )
        self.assertEqual(res.status_code, 400)

    def test_super_can_promote_with_department(self):
        self._login('boss')
        res = self.client.patch(
            f'/api/auth/staff/{self.member.id}/',
            {'role': User.Role.DEPT_ADMIN, 'department_id': self.dept.id},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, User.Role.DEPT_ADMIN)

    def test_member_forbidden_staff(self):
        self._login('member')
        res = self.client.get('/api/auth/staff/')
        self.assertEqual(res.status_code, 403)

    def test_change_password(self):
        self._login('member')
        res = self.client.post(
            '/api/auth/change-password/',
            {
                'old_password': 'Passw0rd!',
                'new_password': 'NewPassw0rd!',
                'new_password_confirm': 'NewPassw0rd!',
            },
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.client.credentials()
        bad = self.client.post(
            '/api/auth/login/',
            {'username': 'member', 'password': 'Passw0rd!'},
            format='json',
        )
        self.assertEqual(bad.status_code, 401)
        good = self.client.post(
            '/api/auth/login/',
            {'username': 'member', 'password': 'NewPassw0rd!'},
            format='json',
        )
        self.assertEqual(good.status_code, 200)

    def test_health(self):
        res = self.client.get('/api/health/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], 'ok')

    def test_member_cannot_upload(self):
        self._login('member')
        res = self.client.post('/api/documents/', {}, format='multipart')
        self.assertEqual(res.status_code, 403)

    def test_audit_only_super(self):
        self._login('member')
        res = self.client.get('/api/audit/')
        self.assertEqual(res.status_code, 403)
        self._login('boss')
        res = self.client.get('/api/audit/')
        self.assertEqual(res.status_code, 200)
