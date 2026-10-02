"""
会话记忆与上下文管理。

目标：让多轮问答（追问、指代、省略）能带着上下文检索与生成，同时把真正
送进 prompt 的内容控制在预算内，避免长历史挤掉本轮检索到的资料。

三层记忆：

1. 长期记忆 —— ``QuestionSession.summary``：被挤出滑动窗口的旧对话压缩成
   滚动摘要，持续更新，永不出窗口。
2. 短期记忆 —— 最近 ``MEMORY_WINDOW_TURNS`` 轮原文消息，按字符预算从新到旧
   填充，单条超长消息先截断。
3. 检索记忆 —— ``run_qa`` 返回的 ``search_query``：指代消解后的独立检索词，
   随步骤一起回传，便于排查"为什么没检索到"。

摘要更新放在回答落库之后，由 Celery 任务异步执行（``qa.tasks``），不阻塞
用户请求；LLM 不可用时退化为启发式拼接，保证功能可用。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from django.conf import settings

logger = logging.getLogger('smartkbqa.qa.memory')

ROLE_LABEL = {'user': '用户', 'assistant': '助手'}
ELLIPSIS = '…'


def _setting(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _clip(text: str, limit: int) -> str:
    """按字符上限截断文本，超长时补省略号。"""
    text = (text or '').strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + ELLIPSIS


def _squash(text: str) -> str:
    """把多行内容压成单行，便于放进摘要与检索改写提示。"""
    return re.sub(r'\s+', ' ', (text or '')).strip()


@dataclass
class MemorySnapshot:
    """一次提问实际使用的上下文，用于提示词拼装与前端可观测。"""

    enabled: bool = False
    summary: str = ''
    history: tuple[dict, ...] = ()
    total_messages: int = 0
    dropped_messages: int = 0
    history_chars: int = 0
    budget_chars: int = 0
    window_turns: int = 0

    @property
    def turns(self) -> int:
        """进入 prompt 的消息条数。"""
        return len(self.history)

    @property
    def has_context(self) -> bool:
        return bool(self.history or self.summary)

    def as_dict(self, search_query: str | None = None) -> dict:
        data = {
            'enabled': self.enabled,
            'turns': self.turns,
            'summary_used': bool(self.summary),
            'summary_chars': len(self.summary),
            'history_chars': self.history_chars,
            'budget_chars': self.budget_chars,
            'dropped_messages': self.dropped_messages,
            'window_turns': self.window_turns,
            'memory_used': self.has_context,
        }
        if search_query is not None:
            data['search_query'] = search_query
        return data

    def dialogue_text(self, max_messages: int = 6, max_chars: int = 240) -> str:
        """渲染成纯文本对话，供检索查询改写（指代消解）使用。"""
        if not self.has_context:
            return ''
        lines: list[str] = []
        if self.summary:
            lines.append(f'【更早对话摘要】{self.summary}')
        for msg in list(self.history)[-max_messages:]:
            label = ROLE_LABEL.get(msg.get('role'), '对话')
            lines.append(f'{label}：{_clip(_squash(msg.get("content") or ""), max_chars)}')
        return '\n'.join(lines)

    def llm_messages(self) -> list[dict]:
        """转成 OpenAI 风格消息列表，插在 system 之后、当前问题之前。"""
        messages: list[dict] = []
        if self.summary:
            messages.append(
                {'role': 'system', 'content': f'更早对话的摘要：{self.summary}'}
            )
        for msg in self.history:
            role = msg.get('role')
            if role not in ROLE_LABEL:
                continue
            content = (msg.get('content') or '').strip()
            if content:
                messages.append({'role': role, 'content': content})
        return messages


def _fit_budget(
    recent: Sequence[dict],
    budget: int,
    per_message: int,
) -> tuple[list[dict], int]:
    """从最新消息向前填充，直到用完字符预算或消息取完。"""
    picked: list[dict] = []
    used = 0
    for msg in reversed(list(recent)):
        content = (msg.get('content') or '').strip()
        if not content:
            continue
        if per_message and len(content) > per_message:
            content = _clip(content, per_message)
        remain = budget - used if budget else None
        if remain is not None and remain <= 0:
            break
        if remain is not None and len(content) > remain:
            piece = _clip(content, remain)
            if piece:
                picked.append({'role': msg.get('role'), 'content': piece})
                used += len(piece)
            break
        picked.append({'role': msg.get('role'), 'content': content})
        used += len(content)
    picked.reverse()
    return picked, used


def build_memory(
    session,
    *,
    enabled: bool | None = None,
    window_turns: int | None = None,
    budget_chars: int | None = None,
) -> MemorySnapshot:
    """组装本次提问要用的记忆快照。

    必须在写入本轮新消息之前调用，这样历史里天然不含当前问题。
    """
    if session is None:
        return MemorySnapshot(enabled=False)

    if enabled is None:
        enabled = bool(_setting('MEMORY_ENABLED', True))
    if not enabled:
        return MemorySnapshot(enabled=False)

    window_turns = int(window_turns or _setting('MEMORY_WINDOW_TURNS', 6) or 1)
    budget = int(budget_chars or _setting('MEMORY_MAX_HISTORY_CHARS', 4000) or 0)
    per_message = int(_setting('MEMORY_MAX_MESSAGE_CHARS', 800) or 0)

    rows = list(
        session.messages.order_by('id').values('role', 'content')
    )
    keep = max(1, window_turns) * 2
    recent = rows[-keep:]
    picked, used = _fit_budget(recent, budget, per_message)
    return MemorySnapshot(
        enabled=True,
        summary=(session.summary or '').strip(),
        history=tuple(picked),
        total_messages=len(rows),
        dropped_messages=len(rows) - len(picked),
        history_chars=used,
        budget_chars=budget,
        window_turns=window_turns,
    )


def _format_pending(messages: Iterable[tuple[str, str]]) -> str:
    lines = []
    for role, content in messages:
        text = _clip(_squash(content), 600)
        if text:
            lines.append(f'{ROLE_LABEL.get(role, "对话")}：{text}')
    return '\n'.join(lines)


def _summarize_with_llm(previous: str, transcript: str, max_chars: int) -> str:
    """调用 LLM 压缩对话；未配置 key 或调用失败时返回空串。"""
    from qa.graph import _llm_text, get_chat_llm

    llm = get_chat_llm(temperature=0)
    if llm is None:
        return ''
    prompt = (
        '你是企业知识库问答系统的记忆压缩器。请把"已有摘要"与"新增对话"合并成'
        '一段简洁的中文摘要，保留：用户关注的主题、提到的制度名/数字/时间、'
        '已经给出的结论与约束；丢弃寒暄与重复表述。'
        f'只输出摘要正文，不要标题与列表符号，不超过 {max_chars} 字。\n\n'
        f'已有摘要：\n{previous or "（无）"}\n\n新增对话：\n{transcript}'
    )
    try:
        return _llm_text(llm.invoke(prompt)).strip()
    except Exception:
        logger.exception('summary llm call failed, fallback to heuristic')
        return ''


def _heuristic_summary(
    previous: str, messages: Sequence[tuple[str, str]], max_chars: int
) -> str:
    """无 LLM 时的兜底：逐条截断拼接，超限保留最新部分。"""
    parts: list[str] = []
    if previous:
        parts.append(previous.strip())
    for role, content in messages:
        text = _clip(_squash(content), 120)
        if text:
            parts.append(f'{ROLE_LABEL.get(role, "对话")}：{text}')
    merged = '\n'.join(p for p in parts if p)
    if max_chars and len(merged) > max_chars:
        merged = merged[-max_chars:]
    return merged


def update_rolling_summary(
    session,
    *,
    window_turns: int | None = None,
    max_chars: int | None = None,
) -> str | None:
    """把滑出窗口的旧消息并入会话摘要。返回新摘要；无需更新时返回 None。"""
    if session is None or not bool(_setting('MEMORY_SUMMARY_ENABLED', True)):
        return None

    window_turns = int(window_turns or _setting('MEMORY_WINDOW_TURNS', 6) or 1)
    max_chars = int(max_chars or _setting('MEMORY_SUMMARY_MAX_CHARS', 1200) or 1200)

    rows = list(session.messages.order_by('id').values_list('role', 'content'))
    keep = max(1, window_turns) * 2
    cutoff = max(0, len(rows) - keep)
    already = int(session.summarized_message_count or 0)
    if cutoff <= already:
        return None

    pending = rows[already:cutoff]
    if not pending:
        return None

    previous = (session.summary or '').strip()
    summary = _summarize_with_llm(previous, _format_pending(pending), max_chars)
    if not summary:
        summary = _heuristic_summary(previous, pending, max_chars)
    if not summary:
        return None
    if max_chars and len(summary) > max_chars:
        summary = summary[-max_chars:]

    session.summary = summary
    session.summarized_message_count = cutoff
    session.save(update_fields=['summary', 'summarized_message_count'])
    logger.info(
        'rolling summary updated session=%s merged=%s chars=%s',
        session.pk,
        len(pending),
        len(summary),
    )
    return summary
