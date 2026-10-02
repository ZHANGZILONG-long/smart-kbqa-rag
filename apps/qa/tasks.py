"""问答相关的异步任务：会话记忆的滚动摘要。"""

import logging

from celery import shared_task

logger = logging.getLogger('smartkbqa.qa')


@shared_task(bind=True, max_retries=2, default_retry_delay=20)
def refresh_session_summary(self, session_id: int):
    """回答落库后，把滑出窗口的旧消息压缩进会话摘要。

    只做"锦上添花"的记忆压缩，任何失败都不应影响用户请求，因此这里不重试
    抛出，而是记录日志并返回结构化结果。
    """
    from qa.memory import update_rolling_summary
    from qa.models import QuestionSession

    session = QuestionSession.objects.filter(pk=session_id).first()
    if session is None:
        return {'ok': False, 'reason': 'not_found', 'session_id': session_id}

    try:
        summary = update_rolling_summary(session)
    except Exception as exc:  # pragma: no cover - 兜底保护
        logger.exception('rolling summary failed session=%s', session_id)
        return {'ok': False, 'session_id': session_id, 'error': str(exc)[:300]}

    if summary is None:
        return {'ok': True, 'session_id': session_id, 'updated': False}
    return {
        'ok': True,
        'session_id': session_id,
        'updated': True,
        'summary_chars': len(summary),
    }
