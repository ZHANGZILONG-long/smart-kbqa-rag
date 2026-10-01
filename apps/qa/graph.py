"""
LangGraph 多步知识库问答。

流程：
  rewrite  →  retrieve  →  grade  ─┬─(不足且未超限)→ expand_retrieve → grade ...
                                   └─(足够/放弃)──→ generate → END
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

from django.conf import settings
from langgraph.graph import END, START, StateGraph


class QAState(TypedDict, total=False):
    question: str
    search_query: str
    top_k: int
    user_id: int
    is_super_admin: bool
    department_id: int | None
    hits: list[dict]
    grade: str
    grade_reason: str
    attempt: int
    max_attempts: int
    answer: str
    sources: list[dict]
    steps: list[dict]


def _append_step(steps: list[dict] | None, step: dict) -> list[dict]:
    out = list(steps or [])
    out.append(step)
    return out


def get_chat_llm(temperature: float = 0.2):
    from langchain_openai import ChatOpenAI

    api_key = settings.DEEPSEEK_API_KEY
    if not api_key or str(api_key).startswith('sk-your'):
        return None
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=api_key,
        base_url=settings.DEEPSEEK_BASE_URL,
        temperature=temperature,
        timeout=60,
    )


def _llm_text(response) -> str:
    content = getattr(response, 'content', None)
    if content is None:
        return str(response)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and 'text' in item:
                parts.append(item['text'])
            else:
                parts.append(str(item))
        return ''.join(parts)
    return str(content)


def _parse_json_object(text: str) -> dict:
    text = (text or '').strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def build_context(hits: list[dict]) -> str:
    blocks = []
    for i, hit in enumerate(hits, start=1):
        meta = hit.get('metadata') or {}
        title = meta.get('title') or f"文档{meta.get('document_id', '')}"
        content = (hit.get('content') or '').strip()
        blocks.append(f'[{i}] 来源《{title}》\n{content}')
    return '\n\n'.join(blocks)


def hits_to_sources(hits: list[dict]) -> list[dict]:
    sources = []
    for hit in hits:
        meta = hit.get('metadata') or {}
        sources.append(
            {
                'document_id': meta.get('document_id'),
                'title': meta.get('title'),
                'chunk_index': meta.get('chunk_index'),
                'score': hit.get('score'),
                'snippet': (hit.get('content') or '')[:240],
            }
        )
    return sources


def _access_where(state: QAState) -> dict | None:
    """与 documents.permissions.chroma_access_filter 一致：仅总管理员不过滤。"""
    if state.get('is_super_admin'):
        return None
    department_id = state.get('department_id')
    if department_id:
        return {
            '$or': [
                {'visibility': {'$eq': 'public'}},
                {
                    '$and': [
                        {'visibility': {'$eq': 'department'}},
                        {'department_id': {'$eq': int(department_id)}},
                    ]
                },
            ]
        }
    return {'visibility': {'$eq': 'public'}}


def rewrite_node(state: QAState) -> dict:
    """把口语问题改写成更利于检索的查询。"""
    question = (state.get('question') or '').strip()
    llm = get_chat_llm(temperature=0)
    search_query = question
    detail = '使用原问题作为检索词'

    if llm is not None and question:
        prompt = (
            '你是检索查询改写助手。把用户问题改写成适合企业知识库向量检索的简短中文查询。'
            '保留关键实体、制度名、数字；不要回答问题；只输出查询文本本身。\n'
            f'用户问题：{question}'
        )
        try:
            rewritten = _llm_text(llm.invoke(prompt)).strip().strip('"\'')
            if rewritten:
                search_query = rewritten[:300]
                detail = 'LLM 改写检索词'
        except Exception as exc:
            detail = f'LLM 改写失败，回退原问题：{exc}'

    return {
        'search_query': search_query,
        'attempt': int(state.get('attempt') or 0),
        'max_attempts': int(state.get('max_attempts') or 2),
        'steps': _append_step(
            state.get('steps'),
            {
                'node': 'rewrite',
                'detail': detail,
                'search_query': search_query,
            },
        ),
    }


def retrieve_node(state: QAState) -> dict:
    from documents.services.vectorstore import search_similar

    query = (state.get('search_query') or state.get('question') or '').strip()
    top_k = int(state.get('top_k') or 5)
    attempt = int(state.get('attempt') or 0)
    # 扩检索时适当加大 top_k
    n = top_k if attempt == 0 else min(10, top_k + 3)
    where = _access_where(state)
    hits = search_similar(query, top_k=n, where=where) if query else []
    return {
        'hits': hits,
        'attempt': attempt + 1,
        'steps': _append_step(
            state.get('steps'),
            {
                'node': 'retrieve',
                'detail': f'检索到 {len(hits)} 条',
                'search_query': query,
                'top_k': n,
                'best_score': hits[0].get('score') if hits else None,
            },
        ),
    }


def grade_node(state: QAState) -> dict:
    """判断检索结果是否足以回答问题。"""
    question = (state.get('question') or '').strip()
    hits = list(state.get('hits') or [])
    llm = get_chat_llm(temperature=0)

    if not hits:
        return {
            'grade': 'insufficient',
            'grade_reason': '没有检索到任何片段',
            'steps': _append_step(
                state.get('steps'),
                {
                    'node': 'grade',
                    'detail': 'insufficient',
                    'reason': '没有检索到任何片段',
                },
            ),
        }

    best = hits[0].get('score')
    # 启发式：最高分过低直接判定不足
    if best is not None and float(best) < 0.25:
        return {
            'grade': 'insufficient',
            'grade_reason': f'最高相似度过低({best:.3f})',
            'steps': _append_step(
                state.get('steps'),
                {
                    'node': 'grade',
                    'detail': 'insufficient',
                    'reason': f'最高相似度过低({float(best):.3f})',
                    'best_score': best,
                },
            ),
        }

    grade = 'sufficient'
    reason = '检索结果可用'
    if llm is not None:
        context = build_context(hits[:5])
        prompt = (
            '你是 RAG 检索结果评审员。判断给定资料是否包含回答用户问题所需的关键信息。'
            '只输出 JSON：{"grade":"sufficient|insufficient","reason":"一句话原因"}。\n'
            f'用户问题：{question}\n\n资料：\n{context}'
        )
        try:
            data = _parse_json_object(_llm_text(llm.invoke(prompt)))
            g = str(data.get('grade') or '').lower()
            if g in {'sufficient', 'insufficient'}:
                grade = g
            reason = str(data.get('reason') or reason)[:200]
        except Exception as exc:
            reason = f'LLM 评审失败，按启发式放行：{exc}'

    return {
        'grade': grade,
        'grade_reason': reason,
        'steps': _append_step(
            state.get('steps'),
            {
                'node': 'grade',
                'detail': grade,
                'reason': reason,
                'best_score': best,
            },
        ),
    }


def route_after_grade(state: QAState) -> Literal['expand_retrieve', 'generate']:
    grade = (state.get('grade') or 'sufficient').lower()
    attempt = int(state.get('attempt') or 0)
    max_attempts = int(state.get('max_attempts') or 2)
    if grade == 'insufficient' and attempt < max_attempts:
        return 'expand_retrieve'
    return 'generate'


def expand_retrieve_node(state: QAState) -> dict:
    """检索不足时，扩展/简化查询后再检索。"""
    question = (state.get('question') or '').strip()
    prev = (state.get('search_query') or question).strip()
    llm = get_chat_llm(temperature=0)
    search_query = question

    if llm is not None:
        prompt = (
            '上一轮知识库检索结果不足。请给出另一个更宽泛或换个说法的中文检索查询，'
            '便于召回相关制度/流程文档。只输出查询文本。\n'
            f'原问题：{question}\n上一轮查询：{prev}'
        )
        try:
            rewritten = _llm_text(llm.invoke(prompt)).strip().strip('"\'')
            if rewritten:
                search_query = rewritten[:300]
        except Exception:
            # 回退：拼接关键词式扩展
            search_query = f'{question} 制度 流程 规定'
    else:
        search_query = f'{question} 制度 流程 规定'

    return {
        'search_query': search_query,
        'steps': _append_step(
            state.get('steps'),
            {
                'node': 'expand_retrieve',
                'detail': '扩展检索词后再次检索',
                'search_query': search_query,
            },
        ),
    }


def generate_node(state: QAState) -> dict:
    question = (state.get('question') or '').strip()
    hits = list(state.get('hits') or [])
    sources = hits_to_sources(hits)

    if not hits:
        answer = (
            '知识库中没有检索到与问题相关的内容。'
            '请先上传并完成向量化的文档后再提问。'
        )
        return {
            'answer': answer,
            'sources': sources,
            'steps': _append_step(
                state.get('steps'),
                {'node': 'generate', 'detail': '无命中，返回空结果提示'},
            ),
        }

    llm = get_chat_llm(temperature=0.2)
    if llm is None:
        snippets = []
        for i, hit in enumerate(hits[:3], start=1):
            meta = hit.get('metadata') or {}
            title = meta.get('title') or '未知文档'
            text = (hit.get('content') or '')[:300]
            snippets.append(f'{i}. 《{title}》：{text}')
        answer = (
            '（当前未配置有效 DEEPSEEK_API_KEY，返回检索摘要）\n'
            f'问题：{question}\n\n相关片段：\n' + '\n'.join(snippets)
        )
        return {
            'answer': answer,
            'sources': sources,
            'steps': _append_step(
                state.get('steps'),
                {'node': 'generate', 'detail': '无 LLM，返回摘要'},
            ),
        }

    context = build_context(hits)
    grade_note = ''
    if (state.get('grade') or '') == 'insufficient':
        grade_note = (
            '注意：检索评审认为资料可能不完整；若无法从资料确认，请明确说明资料不足。\n'
        )
    system = (
        '你是企业内部知识库助手。请仅依据给定资料回答用户问题。'
        '若资料不足，请明确说明不知道，不要编造。'
        '回答使用简体中文，必要时引用资料编号如 [1]。'
    )
    user_prompt = (
        f'{grade_note}资料：\n{context}\n\n用户问题：{question}\n\n请给出回答：'
    )
    try:
        answer = _llm_text(
            llm.invoke(
                [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user_prompt},
                ]
            )
        ).strip()
        detail = 'DeepSeek 生成回答'
    except Exception as exc:
        answer = f'生成回答失败：{exc}'
        detail = 'LLM 调用失败'

    return {
        'answer': answer,
        'sources': sources,
        'steps': _append_step(
            state.get('steps'),
            {'node': 'generate', 'detail': detail},
        ),
    }


def build_qa_graph():
    graph = StateGraph(QAState)
    graph.add_node('rewrite', rewrite_node)
    graph.add_node('retrieve', retrieve_node)
    graph.add_node('grade', grade_node)
    graph.add_node('expand_retrieve', expand_retrieve_node)
    graph.add_node('generate', generate_node)

    graph.add_edge(START, 'rewrite')
    graph.add_edge('rewrite', 'retrieve')
    graph.add_edge('retrieve', 'grade')
    graph.add_conditional_edges(
        'grade',
        route_after_grade,
        {
            'expand_retrieve': 'expand_retrieve',
            'generate': 'generate',
        },
    )
    graph.add_edge('expand_retrieve', 'retrieve')
    graph.add_edge('generate', END)
    return graph.compile()


_QA_GRAPH = None


def get_qa_graph():
    global _QA_GRAPH
    if _QA_GRAPH is None:
        _QA_GRAPH = build_qa_graph()
    return _QA_GRAPH


def run_qa(user, question: str, top_k: int = 5) -> dict:
    """对外入口：执行 LangGraph 多步问答。"""
    graph = get_qa_graph()
    initial: QAState = {
        'question': (question or '').strip(),
        'top_k': int(top_k or 5),
        'user_id': getattr(user, 'id', 0) or 0,
        'is_super_admin': bool(getattr(user, 'is_super_admin', False)),
        'department_id': getattr(user, 'department_id', None),
        'hits': [],
        'attempt': 0,
        'max_attempts': 2,
        'steps': [],
        'answer': '',
        'sources': [],
    }
    final: dict[str, Any] = graph.invoke(initial)
    hits = list(final.get('hits') or [])
    sources = list(final.get('sources') or hits_to_sources(hits))
    return {
        'answer': final.get('answer') or '',
        'sources': sources,
        'hit_count': len(hits),
        'search_query': final.get('search_query') or question,
        'grade': final.get('grade') or '',
        'grade_reason': final.get('grade_reason') or '',
        'steps': list(final.get('steps') or []),
        'engine': 'langgraph',
    }
