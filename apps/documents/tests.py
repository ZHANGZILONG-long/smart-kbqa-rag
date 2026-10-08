from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient
from unittest.mock import patch

from accounts.models import Department
from documents.models import Document
from documents.services.chunking import split_document, split_text

User = get_user_model()


@override_settings(CHUNK_SIZE=40, CHUNK_OVERLAP=5)
class ChunkingTests(SimpleTestCase):
    def test_split_basic(self):
        text = '甲' * 50 + '乙' * 50 + '丙' * 50
        parts = split_text(text)
        self.assertTrue(len(parts) >= 2)
        self.assertTrue(all(p for p in parts))


@override_settings(
    CHUNK_SIZE=800,
    CHUNK_OVERLAP=100,
    CHUNK_STRUCTURED=True,
    CHUNK_MIN_SIZE=0,
)
class StructuredChunkingTests(SimpleTestCase):
    """按章节 + 条目切块：题目与答案同块，并带主题元数据。"""

    TEXT = (
        '一、Python\n\n'
        '1. 列表和元组的区别？\n\n答案要点：\n\n列表可变，元组不可变。\n\n'
        '2. 装饰器是什么？\n\n答案要点：\n\n接收函数并返回新函数。\n\n'
        '二、Django\n\n'
        '1. MTV 是什么？\n\n答案要点：\n\n模型、模板、视图。\n'
    )

    def test_question_and_answer_stay_in_one_chunk(self):
        chunks = split_document(self.TEXT)
        self.assertEqual([c.topic for c in chunks], ['Python', 'Python', 'Django'])
        self.assertEqual(chunks[0].section, '一、Python')
        self.assertIn('列表和元组的区别', chunks[0].content)
        self.assertIn('列表可变', chunks[0].content)
        self.assertTrue(chunks[0].content.startswith('【一、Python】'))
        self.assertIn('列表和元组的区别', chunks[0].item)

    def test_markdown_heading_is_section(self):
        chunks = split_document('## 一、Python\n\n1. 问题A？\n\n答案A。\n')
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].topic, 'Python')

    def test_code_comment_is_not_a_section(self):
        text = (
            '一、Python\n\n'
            '1. 如何实现单例？\n\n答案要点：\n\npython\nclass Singleton:\n'
            'def __new__(cls):\n# 可以修改dct\nreturn super().__new__(cls)\n'
        )
        chunks = split_document(text)
        self.assertTrue(chunks)
        self.assertTrue(all(c.topic == 'Python' for c in chunks))

    def test_fenced_code_heading_is_ignored(self):
        text = (
            '## 一、Python\n\n'
            '1. 装饰器是什么？\n\n答案要点：\n\n```python\n# 这不是标题\nx = 1\n```\n'
        )
        chunks = split_document(text)
        self.assertEqual([c.topic for c in chunks], ['Python'])

    def test_short_items_merge_within_same_section(self):
        with override_settings(CHUNK_MIN_SIZE=400):
            chunks = split_document(self.TEXT)
        # Python 章节的两条短条目合并成一块，且不跨到 Django
        self.assertEqual([c.topic for c in chunks], ['Python', 'Django'])
        self.assertIn('装饰器', chunks[0].content)

    def test_plain_text_has_no_topic(self):
        with override_settings(CHUNK_SIZE=50, CHUNK_OVERLAP=0):
            chunks = split_document('甲' * 120 + '\n\n' + '乙' * 120)
        self.assertTrue(len(chunks) >= 2)
        self.assertTrue(all(c.content for c in chunks))
        self.assertTrue(all(c.topic == '' for c in chunks))

    def test_unstructured_setting_falls_back(self):
        with override_settings(CHUNK_STRUCTURED=False, CHUNK_SIZE=50, CHUNK_OVERLAP=0):
            chunks = split_document(self.TEXT)
        self.assertTrue(all(c.section == '' and c.topic == '' for c in chunks))


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
