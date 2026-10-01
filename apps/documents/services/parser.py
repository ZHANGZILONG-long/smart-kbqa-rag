from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader


SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.txt', '.md'}


def detect_file_type(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip('.')


def extract_text(file_path: str, file_type: str | None = None) -> str:
    path = Path(file_path)
    ext = (file_type or path.suffix.lstrip('.')).lower()
    if ext == 'pdf':
        return _extract_pdf(path)
    if ext == 'docx':
        return _extract_docx(path)
    if ext in {'txt', 'md'}:
        return _extract_plain(path)
    raise ValueError(f'不支持的文件类型: {ext}')


def _extract_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ''
        if text.strip():
            parts.append(text.strip())
    return '\n\n'.join(parts).strip()


def _extract_docx(path: Path) -> str:
    doc = DocxDocument(str(path))
    parts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    return '\n\n'.join(parts).strip()


def _extract_plain(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ('utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin-1'):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='ignore').strip()
