"""Embedding 与 Chroma 向量库封装。"""

from __future__ import annotations

from functools import lru_cache

from django.conf import settings


class HashEmbeddingFunction:
    """
    本地确定性向量：不依赖外网，仅作兜底联调。
    正式检索请使用 sentence-transformers / openai。
    """

    def __init__(self, dim: int = 384):
        self.dim = dim

    @staticmethod
    def name() -> str:
        return 'hash-embedding'

    def get_config(self) -> dict:
        return {'dim': self.dim}

    def __call__(self, input) -> list[list[float]]:
        texts = input if isinstance(input, list) else [input]
        return [self._embed_one(text) for text in texts]

    def embed_query(self, input=None, **kwargs) -> list[list[float]]:
        if input is None and 'input' in kwargs:
            input = kwargs['input']
        return self.__call__(input)

    def embed_documents(self, input=None, **kwargs) -> list[list[float]]:
        if input is None and 'input' in kwargs:
            input = kwargs['input']
        return self.__call__(input)

    def _embed_one(self, text: str) -> list[float]:
        import hashlib
        import math
        import struct

        digest = hashlib.sha256((text or '').encode('utf-8')).digest()
        values = []
        seed = digest
        while len(values) < self.dim:
            for i in range(0, len(seed), 4):
                if len(values) >= self.dim:
                    break
                chunk = seed[i:i + 4]
                if len(chunk) < 4:
                    chunk = chunk.ljust(4, b'\0')
                num = struct.unpack('>I', chunk)[0]
                values.append((num / 0xFFFFFFFF) * 2.0 - 1.0)
            seed = hashlib.sha256(seed + digest).digest()
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]


def _collection_name() -> str:
    """
    不同 embedding 方案使用不同 collection，避免维度/语义空间混用。
    """
    base = settings.CHROMA_COLLECTION
    provider = (settings.EMBEDDING_PROVIDER or 'hash').lower()
    if provider == 'sentence':
        model = (settings.EMBEDDING_MODEL or 'BAAI/bge-small-zh-v1.5').replace('/', '_')
        return f'{base}__sentence__{model}'
    if provider == 'openai':
        model = (settings.EMBEDDING_MODEL or 'text-embedding-3-small').replace('/', '_')
        return f'{base}__openai__{model}'
    return f'{base}__hash'


@lru_cache(maxsize=1)
def get_embedding_function():
    provider = (settings.EMBEDDING_PROVIDER or 'hash').lower()

    if provider == 'openai':
        from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

        api_key = settings.EMBEDDING_API_KEY or settings.DEEPSEEK_API_KEY
        if not api_key:
            raise RuntimeError('使用 openai embedding 时需要配置 EMBEDDING_API_KEY')
        kwargs = {
            'api_key': api_key,
            'model_name': settings.EMBEDDING_MODEL or 'text-embedding-3-small',
        }
        if settings.EMBEDDING_BASE_URL:
            kwargs['api_base'] = settings.EMBEDDING_BASE_URL
        return OpenAIEmbeddingFunction(**kwargs)

    if provider in {'sentence', 'local', 'bge'}:
        from chromadb.utils.embedding_functions import (
            SentenceTransformerEmbeddingFunction,
        )

        model = settings.EMBEDDING_MODEL or 'BAAI/bge-small-zh-v1.5'
        return SentenceTransformerEmbeddingFunction(model_name=model)

    return HashEmbeddingFunction(dim=settings.EMBEDDING_DIM)


def clear_vector_caches():
    get_embedding_function.cache_clear()
    get_chroma_client.cache_clear()


@lru_cache(maxsize=1)
def get_chroma_client():
    import chromadb

    mode = (settings.CHROMA_MODE or 'persistent').lower()
    if mode == 'http':
        return chromadb.HttpClient(
            host=settings.CHROMA_HOST,
            port=settings.CHROMA_PORT,
        )
    path = str(settings.CHROMA_PERSIST_DIR)
    settings.CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=path)


def get_collection():
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=_collection_name(),
        embedding_function=get_embedding_function(),
        metadata={
            'hnsw:space': 'cosine',
            'embedding_provider': settings.EMBEDDING_PROVIDER,
            'embedding_model': settings.EMBEDDING_MODEL or '',
        },
    )


def upsert_chunks(
    *,
    document_id: int,
    texts: list[str],
    ids: list[str],
    metadatas: list[dict],
) -> None:
    if not texts:
        return
    collection = get_collection()
    collection.upsert(ids=ids, documents=texts, metadatas=metadatas)


def delete_document_vectors(document_id: int) -> None:
    collection = get_collection()
    try:
        collection.delete(where={'document_id': document_id})
    except Exception:
        pass


def search_similar(
    query: str,
    *,
    top_k: int = 5,
    where: dict | None = None,
) -> list[dict]:
    collection = get_collection()
    kwargs = {
        'query_texts': [query],
        'n_results': max(1, top_k),
        'include': ['documents', 'metadatas', 'distances'],
    }
    if where:
        kwargs['where'] = where
    result = collection.query(**kwargs)

    items = []
    ids = (result.get('ids') or [[]])[0]
    docs = (result.get('documents') or [[]])[0]
    metas = (result.get('metadatas') or [[]])[0]
    dists = (result.get('distances') or [[]])[0]
    for i, doc_id in enumerate(ids):
        distance = dists[i] if i < len(dists) else None
        score = None
        if distance is not None:
            score = max(0.0, 1.0 - float(distance))
        items.append(
            {
                'id': doc_id,
                'content': docs[i] if i < len(docs) else '',
                'metadata': metas[i] if i < len(metas) else {},
                'distance': distance,
                'score': score,
            }
        )
    return items
