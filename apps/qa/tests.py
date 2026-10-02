from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from qa.graph import run_qa
from qa.memory import build_memory, update_rolling_summary
from qa.models import QAMessage, QuestionSession

User = get_user_model()


class FakeLLM:
    """按调用顺序返回预设内容的假 LLM，用于断言提示词拼装。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return SimpleNamespace(content=self.replies[index])


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class QASessionAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        patcher = patch('qa.views.refresh_session_summary')
        patcher.start()
        self.addCleanup(patcher.stop)
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


@override_settings(
    MEMORY_ENABLED=True,
    MEMORY_WINDOW_TURNS=2,
    MEMORY_MAX_HISTORY_CHARS=1000,
    MEMORY_MAX_MESSAGE_CHARS=200,
    MEMORY_SUMMARY_ENABLED=True,
    MEMORY_SUMMARY_MAX_CHARS=300,
)
class MemoryBuilderTests(TestCase):
    """会话记忆的窗口裁剪、预算控制与滚动摘要。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='mem_user', password='Passw0rd!'
        )
        self.session = QuestionSession.objects.create(
            user=self.user, title='记忆测试'
        )

    def _say(self, role, content):
        return QAMessage.objects.create(
            session=self.session, role=role, content=content
        )

    def _fill_turns(self, count):
        for i in range(count):
            self._say(QAMessage.Role.USER, f'第{i}个问题')
            self._say(QAMessage.Role.ASSISTANT, f'第{i}个回答')

    def test_window_keeps_only_recent_turns(self):
        self._fill_turns(3)
        snap = build_memory(self.session)
        self.assertTrue(snap.enabled)
        self.assertEqual(snap.window_turns, 2)
        self.assertEqual(snap.turns, 4)
        self.assertEqual(snap.history[0]['content'], '第1个问题')
        self.assertEqual(snap.dropped_messages, 2)
        self.assertTrue(snap.has_context)

    def test_empty_session_has_no_context(self):
        snap = build_memory(self.session)
        self.assertTrue(snap.enabled)
        self.assertFalse(snap.has_context)
        self.assertFalse(snap.as_dict()['memory_used'])

    @override_settings(MEMORY_MAX_HISTORY_CHARS=5)
    def test_budget_keeps_latest_messages(self):
        self._say(QAMessage.Role.USER, '甲' * 5)
        self._say(QAMessage.Role.ASSISTANT, '乙' * 5)
        snap = build_memory(self.session)
        self.assertEqual(snap.turns, 1)
        self.assertEqual(snap.history[0]['content'], '乙' * 5)
        self.assertLessEqual(snap.history_chars, 5)

    @override_settings(MEMORY_MAX_MESSAGE_CHARS=5)
    def test_long_message_is_truncated(self):
        self._say(QAMessage.Role.USER, '很长的用户问题内容')
        snap = build_memory(self.session)
        self.assertEqual(snap.history[0]['content'], '很长的用户…')

    def test_memory_can_be_disabled(self):
        self._fill_turns(1)
        snap = build_memory(self.session, enabled=False)
        self.assertFalse(snap.enabled)
        self.assertEqual(snap.turns, 0)
        self.assertFalse(snap.as_dict()['memory_used'])

    def test_dialogue_text_renders_roles_and_summary(self):
        self._fill_turns(1)
        snap = build_memory(self.session)
        text = snap.dialogue_text()
        self.assertIn('用户：第0个问题', text)
        self.assertIn('助手：第0个回答', text)

    @patch('qa.graph.get_chat_llm', return_value=None)
    def test_rolling_summary_absorbs_messages_outside_window(self, _mock_llm):
        self._fill_turns(3)  # 窗口 2 轮，最老的 1 轮应被摘要
        summary = update_rolling_summary(self.session)
        self.session.refresh_from_db()
        self.assertTrue(summary)
        self.assertIn('第0个问题', self.session.summary)
        self.assertEqual(self.session.summarized_message_count, 2)
        # 幂等：没有新消息滑出窗口时不再重复摘要
        self.assertIsNone(update_rolling_summary(self.session))

    @patch('qa.graph.get_chat_llm', return_value=None)
    def test_summary_is_used_as_long_term_memory(self, _mock_llm):
        self._fill_turns(3)
        update_rolling_summary(self.session)
        self.session.refresh_from_db()
        snap = build_memory(self.session)
        self.assertTrue(snap.summary)
        self.assertIn('更早对话摘要', snap.dialogue_text())
        self.assertEqual(snap.as_dict()['summary_used'], True)

    @patch('qa.graph.get_chat_llm', return_value=None)
    def test_summary_can_be_purged(self, _mock_llm):
        self._fill_turns(3)
        update_rolling_summary(self.session)
        deleted = self.session.reset_memory(purge_messages=True)
        self.session.refresh_from_db()
        self.assertEqual(deleted, 6)
        self.assertEqual(self.session.summary, '')
        self.assertEqual(self.session.summarized_message_count, 0)
        self.assertEqual(self.session.messages.count(), 0)


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    MEMORY_ENABLED=True,
    MEMORY_WINDOW_TURNS=2,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class AskMemoryAPITests(TestCase):
    """问答接口是否真的把记忆交给流水线，并暴露记忆管理入口。"""

    def setUp(self):
        self.client = APIClient()
        patcher = patch('qa.views.refresh_session_summary')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user(
            username='mem_api', password='Passw0rd!'
        )
        res = self.client.post(
            '/api/auth/login/',
            {'username': 'mem_api', 'password': 'Passw0rd!'},
            format='json',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')
        self.session = QuestionSession.objects.create(
            user=self.user, title='多轮会话'
        )
        QAMessage.objects.create(
            session=self.session, role=QAMessage.Role.USER, content='请假流程是什么'
        )
        QAMessage.objects.create(
            session=self.session,
            role=QAMessage.Role.ASSISTANT,
            content='请假需要主管审批',
        )

    @staticmethod
    def _result():
        return {
            'answer': 'ok',
            'sources': [],
            'hit_count': 0,
            'engine': 'langgraph',
            'search_query': '请假审批时效',
            'grade': 'sufficient',
            'grade_reason': '',
            'steps': [],
            'memory_used': True,
        }

    @patch('qa.views.run_qa')
    def test_ask_passes_memory_snapshot(self, mock_run):
        mock_run.return_value = self._result()
        res = self.client.post(
            '/api/qa/ask/',
            {'question': '审批要多久', 'session_id': self.session.id},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        snapshot = mock_run.call_args.kwargs['memory']
        self.assertTrue(snapshot.enabled)
        self.assertEqual(snapshot.turns, 2)
        self.assertEqual(res.data['memory']['turns'], 2)
        self.assertEqual(res.data['memory']['history_chars'], len('请假流程是什么') + len('请假需要主管审批'))
        self.assertTrue(res.data['memory']['memory_used'])
        self.assertEqual(res.data['memory']['search_query'], '请假审批时效')

    @patch('qa.views.run_qa')
    def test_use_memory_false_disables_context(self, mock_run):
        mock_run.return_value = self._result()
        res = self.client.post(
            '/api/qa/ask/',
            {
                'question': '审批要多久',
                'session_id': self.session.id,
                'use_memory': False,
            },
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        snapshot = mock_run.call_args.kwargs['memory']
        self.assertFalse(snapshot.enabled)
        self.assertEqual(snapshot.turns, 0)
        self.assertFalse(res.data['memory']['memory_used'])

    @patch('qa.views.refresh_session_summary')
    @patch('qa.views.run_qa')
    def test_summary_task_scheduled_after_answer(self, mock_run, mock_summary):
        mock_run.return_value = self._result()
        self.client.post(
            '/api/qa/ask/',
            {'question': '审批要多久', 'session_id': self.session.id},
            format='json',
        )
        mock_summary.delay.assert_called_once_with(self.session.id)

    def test_memory_endpoint_reports_and_resets(self):
        get_res = self.client.get(f'/api/qa/sessions/{self.session.id}/memory/')
        self.assertEqual(get_res.status_code, 200, get_res.content)
        self.assertEqual(get_res.data['message_count'], 2)
        self.assertEqual(get_res.data['turns'], 2)

        QuestionSession.objects.filter(pk=self.session.pk).update(
            summary='旧摘要', summarized_message_count=2
        )
        del_res = self.client.delete(f'/api/qa/sessions/{self.session.id}/memory/')
        self.assertEqual(del_res.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.summary, '')
        self.assertEqual(self.session.messages.count(), 2)

        purge_res = self.client.delete(
            f'/api/qa/sessions/{self.session.id}/memory/?purge_messages=1'
        )
        self.assertEqual(purge_res.data['purged_messages'], 2)
        self.assertEqual(self.session.messages.count(), 0)

    def test_memory_endpoint_rejects_other_users_session(self):
        other = User.objects.create_user(username='other_mem', password='Passw0rd!')
        other_session = QuestionSession.objects.create(user=other, title='别人的')
        res = self.client.get(f'/api/qa/sessions/{other_session.id}/memory/')
        self.assertEqual(res.status_code, 404)


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    MEMORY_ENABLED=True,
    MEMORY_WINDOW_TURNS=4,
    MEMORY_HISTORY_IN_REWRITE=True,
)
class GraphMemoryTests(TestCase):
    """LangGraph 流水线是否带着记忆改写检索词并生成回答。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='graph_user', password='Passw0rd!'
        )
        self.session = QuestionSession.objects.create(
            user=self.user, title='图测试'
        )
        QAMessage.objects.create(
            session=self.session, role=QAMessage.Role.USER, content='请假流程是什么'
        )
        QAMessage.objects.create(
            session=self.session,
            role=QAMessage.Role.ASSISTANT,
            content='请假需要主管审批',
        )

    @staticmethod
    def _hits():
        return [
            {
                'content': '请假需主管审批，审批时效 3 个工作日。',
                'score': 0.92,
                'metadata': {
                    'title': '考勤制度',
                    'document_id': 1,
                    'chunk_index': 0,
                },
            }
        ]

    @patch('documents.services.vectorstore.search_similar')
    @patch('qa.graph.get_chat_llm')
    def test_generate_receives_history_messages(self, mock_llm, mock_search):
        mock_search.return_value = self._hits()
        fake = FakeLLM(
            [
                '请假审批时效',
                '{"grade": "sufficient", "reason": "资料充分"}',
                '审批时效为 3 个工作日。',
            ]
        )
        mock_llm.return_value = fake

        snap = build_memory(self.session)
        result = run_qa(self.user, '那要多久', top_k=3, memory=snap)

        self.assertEqual(result['answer'], '审批时效为 3 个工作日。')
        self.assertTrue(result['memory_used'])
        self.assertEqual(result['search_query'], '请假审批时效')

        generate_prompt = fake.calls[-1]
        self.assertEqual(generate_prompt[0]['role'], 'system')
        roles = [m['role'] for m in generate_prompt]
        self.assertIn('assistant', roles)
        self.assertTrue(any('请假需要主管审批' in m['content'] for m in generate_prompt))
        step_nodes = [s['node'] for s in result['steps']]
        self.assertEqual(step_nodes, ['rewrite', 'retrieve', 'grade', 'generate'])

    @patch('documents.services.vectorstore.search_similar')
    @patch('qa.graph.get_chat_llm')
    def test_rewrite_uses_history_for_reference_resolution(
        self, mock_llm, mock_search
    ):
        mock_search.return_value = self._hits()
        fake = FakeLLM(
            [
                '请假流程 审批时效',
                '{"grade": "sufficient", "reason": "资料充分"}',
                '需要主管审批，时效 3 个工作日。',
            ]
        )
        mock_llm.return_value = fake

        snap = build_memory(self.session)
        result = run_qa(self.user, '那要多久', top_k=3, memory=snap)

        rewrite_prompt = fake.calls[0]
        self.assertIn('对话历史', rewrite_prompt)
        self.assertIn('请假流程是什么', rewrite_prompt)
        self.assertTrue(result['steps'][0]['history_used'])

    @patch('documents.services.vectorstore.search_similar', return_value=[])
    @patch('qa.graph.get_chat_llm', return_value=None)
    def test_rewrite_falls_back_without_llm(self, _mock_llm, _mock_search):
        snap = build_memory(self.session)
        result = run_qa(self.user, '那要多久', top_k=3, memory=snap)

        rewrite_step = result['steps'][0]
        self.assertEqual(rewrite_step['node'], 'rewrite')
        self.assertIn('请假流程是什么', rewrite_step['search_query'])
        self.assertIn('那要多久', rewrite_step['search_query'])

    @patch('documents.services.vectorstore.search_similar')
    @patch('qa.graph.get_chat_llm')
    def test_disabled_memory_sends_no_history(self, mock_llm, mock_search):
        mock_search.return_value = self._hits()
        fake = FakeLLM(
            [
                '请假审批时效',
                '{"grade": "sufficient", "reason": "资料充分"}',
                '审批时效为 3 个工作日。',
            ]
        )
        mock_llm.return_value = fake

        snap = build_memory(self.session, enabled=False)
        result = run_qa(self.user, '那要多久', top_k=3, memory=snap)

        self.assertFalse(result['memory_used'])
        generate_prompt = fake.calls[-1]
        self.assertFalse(
            any('请假需要主管审批' in m['content'] for m in generate_prompt)
        )
