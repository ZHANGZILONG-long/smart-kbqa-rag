"""文档切块。

优先按文档自身的结构切分：

1. **章节**：Markdown 标题（``## 一、Python``）或中文序号标题（``一、Python``、
   ``第二章 索引``）。
2. **条目**：编号条目（``1. xxx``、``1、xxx``、``（1）xxx``）。问答文档里一条
   就是「题目 + 答案」，所以按条目切块能保证答案不被长度切分截断。

切块结果带 ``section``（所属章节）与 ``topic``（章节主题词，如 ``Python``），
检索时可据此收敛主题，避免「问 Python 却把 Django/MySQL 的答案也答一遍」。

文档没有结构（纯段落）时退化到按长度递归切分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Markdown 标题：## 一、Python
_MD_HEADING_RE = re.compile(r'^\s{0,3}(#{1,6})\s*(.+?)\s*#*\s*$')
# 中文序号章节：一、Python / 第二章 索引 / 第三部分 缓存
_CN_SECTION_RE = re.compile(
    r'^\s{0,4}('
    r'第[一二三四五六七八九十百零\d]+[章节篇部分]'
    r'|[一二三四五六七八九十]+[、.．]'
    r')\s*(\S.*)$'
)
# 编号条目：1. xxx / 1、xxx / 1) xxx / （1）xxx
_ITEM_RE = re.compile(
    r'^\s{0,4}('
    r'\d{1,3}[、.．)]'
    r'|[（(]\d{1,3}[)）]'
    r')\s*(\S.*)$'
)
# 取主题词时剥离章节前缀
_TOPIC_STRIP_RE = re.compile(
    r'^(?:#{1,6}\s*'
    r'|第[一二三四五六七八九十百零\d]+[章节篇部分]\s*'
    r'|[一二三四五六七八九十]+[、.．]\s*'
    r'|\d{1,3}[、.．)]\s*)'
)


@dataclass
class Chunk:
    """一个切块及其结构元数据。"""

    content: str
    section: str = ''
    topic: str = ''
    item: str = ''

    def metadata(self) -> dict:
        return {'section': self.section, 'topic': self.topic}


def _setting_int(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    if value is None:
        value = default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=_setting_int('CHUNK_SIZE', 800),
        chunk_overlap=_setting_int('CHUNK_OVERLAP', 120),
        separators=['\n\n', '\n', '。', '！', '？', '；', ' ', ''],
    )


def _flat_split(text: str) -> list[str]:
    splitter = _text_splitter()
    return [c.strip() for c in splitter.split_text(text) if c and c.strip()]


def _section_title(line: str) -> str:
    """该行是章节标题则返回标题文本，否则返回空串（不含代码上下文判断）。"""
    match = _MD_HEADING_RE.match(line)
    if match:
        return match.group(2).strip()
    match = _CN_SECTION_RE.match(line)
    if match:
        return f'{match.group(1)}{match.group(2)}'.strip()
    return ''


def _item_title(line: str) -> str:
    """该行是编号条目则返回条目文本，否则返回空串。"""
    match = _ITEM_RE.match(line)
    if match:
        return f'{match.group(1)}{match.group(2)}'.strip()
    return ''


_PY_KEYWORD_RE = re.compile(
    r'^\s*(?:def|class|return|import|from|if|elif|else|for|while|try|except'
    r'|finally|with|yield|lambda|async|await|pass|raise|assert|print|self)\b'
)
_ASSIGN_RE = re.compile(r'^\s*[\w\.\[\]\'"]+\s*=\s*\S')
_CALL_RE = re.compile(r'^\s*[\w\.]+\s*\(.*\)\s*:?\s*$')
_FENCE_RE = re.compile(r'^\s*(?:```|~~~)')


def _looks_like_code(line: str) -> bool:
    """判断一行是否像代码，用于避免把代码注释 ``# xxx`` 当成 Markdown 标题。"""
    text = (line or '').rstrip()
    if not text.strip():
        return False
    if (
        _PY_KEYWORD_RE.match(text)
        or _ASSIGN_RE.match(text)
        or _CALL_RE.match(text)
    ):
        return True
    return bool(text[:1].isspace() and re.search(r'[=(){}\[\];]', text))


def _heading_from(line: str, prev_line: str, next_line: str) -> str:
    """带上下文判断的章节识别：紧邻代码的 ``#`` 行按代码注释处理。"""
    title = _section_title(line)
    if not title:
        return ''
    if line.lstrip().startswith('#'):
        if _looks_like_code(prev_line) or _looks_like_code(next_line):
            return ''
    return title


def _topic_of(section: str) -> str:
    """从章节标题提取主题词：``一、Python`` -> ``Python``。"""
    text = (section or '').strip()
    if not text:
        return ''
    text = _TOPIC_STRIP_RE.sub('', text).strip().strip('#').strip()
    if not text:
        return ''
    head = re.split(r'[（(【\[:：|/\\\-—\s]', text, maxsplit=1)[0].strip()
    return (head or text)[:40]


def _prev_nonblank(lines: list[str], index: int) -> str:
    for i in range(index - 1, -1, -1):
        if lines[i].strip():
            return lines[i]
    return ''


def _next_nonblank(lines: list[str], index: int) -> str:
    for i in range(index + 1, len(lines)):
        if lines[i].strip():
            return lines[i]
    return ''


def _structured_units(text: str) -> list[dict]:
    """按章节 + 条目把文本切成原子单元。"""
    units: list[dict] = []
    section = ''
    item = ''
    body: list[str] = []
    lines = text.split('\n')
    in_fence = False

    def flush() -> None:
        if not body:
            return
        content = '\n'.join(body).strip()
        if content:
            units.append({'section': section, 'item': item, 'body': content})
        body.clear()

    for index, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip():
            if body:
                body.append('')
            continue
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            body.append(line)
            continue
        if in_fence:
            body.append(line)
            continue
        heading = _heading_from(
            line, _prev_nonblank(lines, index), _next_nonblank(lines, index)
        )
        if heading:
            flush()
            section = heading
            item = ''
            continue
        title = _item_title(line)
        if title:
            flush()
            item = title
            body.append(line.strip())
            continue
        body.append(line)
    flush()
    return units


def _merge_short_units(units: list[dict], chunk_size: int, min_size: int) -> list[dict]:
    """同章节内合并过短的单元，避免切得太碎；不跨章节合并。"""
    merged: list[dict] = []
    for unit in units:
        last = merged[-1] if merged else None
        if (
            last is not None
            and unit['section'] == last['section']
            and len(last['body']) < min_size
            and len(last['body']) + len(unit['body']) + 2 <= chunk_size
        ):
            last['body'] = f"{last['body']}\n\n{unit['body']}"
        else:
            merged.append(dict(unit))
    return merged


def split_document(text: str) -> list[Chunk]:
    """把文档切成带结构元数据的块。"""
    if not (text or '').strip():
        return []
    if not bool(getattr(settings, 'CHUNK_STRUCTURED', True)):
        return [Chunk(content=piece) for piece in _flat_split(text)]

    chunk_size = _setting_int('CHUNK_SIZE', 800)
    min_size = _setting_int('CHUNK_MIN_SIZE', 200)
    units = _merge_short_units(
        _structured_units(text), chunk_size, min_size
    )

    chunks: list[Chunk] = []
    for unit in units:
        prefix = f'【{unit["section"]}】' if unit['section'] else ''
        topic = _topic_of(unit['section'])
        body = unit['body']
        pieces = (
            [body]
            if len(body) <= chunk_size
            else _flat_split(body) or [body]
        )
        for piece in pieces:
            content = f'{prefix}\n{piece}' if prefix else piece
            chunks.append(
                Chunk(
                    content=content,
                    section=unit['section'],
                    topic=topic,
                    item=unit['item'],
                )
            )
    if chunks:
        return chunks
    return [Chunk(content=piece) for piece in _flat_split(text)]


def split_text(text: str) -> list[str]:
    """只返回分块文本（向后兼容旧调用方）。"""
    return [chunk.content for chunk in split_document(text)]
