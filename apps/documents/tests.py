from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient
from unittest.mock import patch

from accounts.models import Department
from documents.models import Document
from documents.services.chunking import split_text

User = get_user_model()


@override_settings(CHUNK_SIZE=40, CHUNK_OVERLAP=5)
class ChunkingTests(SimpleTestCase):
    def test_split_basic(self):
        text = '甲' * 50 + '乙' * 50 + '丙' * 50
        parts = split_text(text)
        self.assertTrue(len(parts) >= 2)
        self.assertTrue(all(p for p in parts))


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class DocumentApprovalAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.dept = Department.objects.create(name='研发部', code='RD')
        self.super = User.objects.create_user(
            username='doc_boss',
            password='Passw0rd!',
            role=User.Role.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        self.dept_admin = User.objects.create_user(
            username='doc_lead',
            password='Passw0rd!',
            role=User.Role.DEPT_ADMIN,
            department=self.dept,
        )
        self.pending = Document.objects.create(
            title='待审',
            original_filename='p.txt',
            file_type='txt',
            visibility=Document.Visibility.DEPARTMENT,
            department=self.dept,
            uploader=self.dept_admin,
            status=Document.Status.PENDING_APPROVAL,
        )

    def _login(self, username):
        res = self.client.post(
            '/api/auth/login/',
            {'username': username, 'password': 'Passw0rd!'},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def test_dept_admin_cannot_approve(self):
        self._login('doc_lead')
        res = self.client.post(f'/api/documents/{self.pending.id}/approve/')
        self.assertEqual(res.status_code, 403)

    @patch('documents.views.process_document_task.delay')
    def test_super_can_approve(self, mock_delay):
        self._login('doc_boss')
        res = self.client.post(f'/api/documents/{self.pending.id}/approve/')
        self.assertEqual(res.status_code, 200, res.content)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Document.Status.PENDING)
        mock_delay.assert_called_once_with(self.pending.id)

    def test_super_can_reject(self):
        self._login('doc_boss')
        res = self.client.post(
            f'/api/documents/{self.pending.id}/reject/',
            {'reason': '内容不完整'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Document.Status.REJECTED)

    @patch('documents.views.process_document_task.delay')
    def test_dept_admin_upload_pending_approval(self, mock_delay):
        self._login('doc_lead')
        upload = SimpleUploadedFile(
            'note.txt', b'hello knowledge base', content_type='text/plain'
        )
        res = self.client.post(
            '/api/documents/',
            {
                'file': upload,
                'title': '部门文档',
                'visibility': Document.Visibility.DEPARTMENT,
            },
            format='multipart',
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(res.data.get('awaiting_approval'))
        doc = Document.objects.get(pk=res.data['document']['id'])
        self.assertEqual(doc.status, Document.Status.PENDING_APPROVAL)
        mock_delay.assert_not_called()
