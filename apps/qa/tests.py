from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from unittest.mock import patch

from accounts.models import Department

User = get_user_model()


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class QASessionAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='qa_user',
            password='Passw0rd!',
            role=User.Role.USER,
        )
        res = self.client.post(
            '/api/auth/login/',
            {'username': 'qa_user', 'password': 'Passw0rd!'},
            format='json',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    @patch('qa.views.run_qa')
    def test_ask_creates_session(self, mock_run):
        mock_run.return_value = {
            'answer': '测试回答',
            'sources': [],
            'hit_count': 0,
            'engine': 'langgraph',
            'search_query': 'q',
            'grade': 'sufficient',
            'grade_reason': '',
            'steps': [],
        }
        res = self.client.post(
            '/api/qa/ask/',
            {'question': '请假流程是什么？', 'top_k': 3},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn('session_id', res.data)
        self.assertEqual(res.data['answer'], '测试回答')

        sessions = self.client.get('/api/qa/sessions/')
        self.assertEqual(sessions.status_code, 200)
        self.assertGreaterEqual(len(sessions.data['results']), 1)
