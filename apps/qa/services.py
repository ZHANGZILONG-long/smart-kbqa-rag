from django.conf import settings


def get_chat_llm():
    from langchain_openai import ChatOpenAI

    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        return None
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=api_key,
        base_url=settings.DEEPSEEK_BASE_URL,
        temperature=0.2,
        timeout=60,
    )


def build_context(hits: list[dict]) -> str:
    blocks = []
    for i, hit in enumerate(hits, start=1):
        meta = hit.get('metadata') or {}
        title = meta.get('title') or f"文档{meta.get('document_id', '')}"
        content = (hit.get('content') or '').strip()
        blocks.append(f'[{i}] 来源《{title}》\n{content}')
    return '\n\n'.join(blocks)


def answer_with_rag(question: str, hits: list[dict]) -> str:
    """
    有 DeepSeek Key 时调用 LLM；否则返回基于检索片段的本地摘要答案，保证联调可用。
    """
    context = build_context(hits)
    if not hits:
        return '知识库中没有检索到与问题相关的内容。请先上传并完成向量化的文档后再提问。'

    llm = get_chat_llm()
    if llm is None:
        snippets = []
        for i, hit in enumerate(hits[:3], start=1):
            meta = hit.get('metadata') or {}
            title = meta.get('title') or '未知文档'
            text = (hit.get('content') or '')[:300]
            snippets.append(f'{i}. 《{title}》：{text}')
        return (
            '（当前未配置 DEEPSEEK_API_KEY，返回检索摘要）\n'
            f'问题：{question}\n\n相关片段：\n' + '\n'.join(snippets)
        )

    system = (
        '你是企业内部知识库助手。请仅依据给定资料回答用户问题。'
        '若资料不足，请明确说明不知道，不要编造。'
        '回答使用简体中文，必要时引用资料编号如 [1]。'
    )
    user_prompt = f'资料：\n{context}\n\n用户问题：{question}\n\n请给出回答：'
    response = llm.invoke(
        [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_prompt},
        ]
    )
    return getattr(response, 'content', None) or str(response)


def run_qa(user, question: str, top_k: int = 5) -> dict:
    from documents.permissions import chroma_access_filter
    from documents.services.vectorstore import search_similar

    where = chroma_access_filter(user)
    hits = search_similar(question, top_k=top_k, where=where)
    answer = answer_with_rag(question, hits)
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
    return {'answer': answer, 'sources': sources, 'hit_count': len(hits)}
