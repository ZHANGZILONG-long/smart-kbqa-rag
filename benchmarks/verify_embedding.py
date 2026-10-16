#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
向量库自检：健康检查 + 语义检索有效性验证。

三个用途：
  1. 部署后确认向量库可读（Chroma 本地模式在多进程并发写后可能损坏，见 docs/benchmarks.md）
  2. 验证 embedding 模型能加载，且语义检索结果符合预期
  3. 走项目自身的 vectorstore 封装做一次端到端读写，确认链路通

用法：
    python benchmarks/verify_embedding.py            # 用当前 settings 的 provider
    python benchmarks/verify_embedding.py --provider hash
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("USE_LOCMEM_CACHE", "True")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# 自检样本：查询与"应当命中的文档"是有语义关系但用词不同的
SAMPLES = [
    ("员工请假需提前一天在系统提交申请，由部门管理员审批。", ["请假怎么申请", "休假要走什么流程"]),
    ("差旅报销需在出差结束后七个工作日内提交发票与行程单。", ["报销流程", "出差费用怎么报"]),
    ("服务器故障时值班工程师需在十分钟内响应并升级工单。", ["系统故障怎么处理"]),
    ("新员工入职需完成信息安全培训并通过考试。", ["入职要做什么"]),
    ("公司年会在每年一月举行，地点由行政部确定。", ["年会什么时候开"]),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="向量库自检")
    parser.add_argument("--provider", default=None, help="覆盖 settings 里的 EMBEDDING_PROVIDER")
    parser.add_argument("--isolated", action="store_true",
                        help="在临时目录里建独立向量库，不动项目的 data/chroma")
    args = parser.parse_args()

    if args.provider:
        os.environ["EMBEDDING_PROVIDER"] = args.provider

    if args.isolated:
        tmp = Path(tempfile.gettempdir()) / "smartkbqa_verify_chroma"
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ["CHROMA_PERSIST_DIR"] = str(tmp)

    import django

    django.setup()

    from django.conf import settings
    from documents.services.vectorstore import (
        clear_vector_caches,
        delete_document_vectors,
        get_collection,
        search_similar,
        upsert_chunks,
    )

    clear_vector_caches()

    print("=" * 78)
    print("向量库自检")
    print("=" * 78)
    print(f"provider   : {settings.EMBEDDING_PROVIDER}")
    print(f"model      : {settings.EMBEDDING_MODEL or '(内置)'}")
    print(f"chroma dir : {settings.CHROMA_PERSIST_DIR}")

    failures: list[str] = []

    # ---- 1. 模型加载 ----
    from documents.services.vectorstore import get_embedding_function

    t0 = time.perf_counter()
    try:
        embed_fn = get_embedding_function()
    except Exception as exc:
        print(f"\n[1] 模型加载   ❌ {type(exc).__name__}: {exc}")
        print("    提示：若报 'Unrecognized processing class'，说明 transformers 版本过高，")
        print("    需约束 transformers<5（见 requirements.txt 注释）。")
        sys.exit(1)
    load_s = time.perf_counter() - t0
    print(f"\n[1] 模型加载   ✅ {load_s:.2f}s")

    # ---- 2. 向量库可读性 ----
    t0 = time.perf_counter()
    try:
        col = get_collection()
        count = col.count()
    except Exception as exc:
        print(f"[2] 向量库读取 ❌ {type(exc).__name__}: {exc}")
        print("    提示：若报 'Error loading hnsw index'，多为多进程并发写导致索引损坏。")
        print("    可用 python manage.py reindex_documents 重建。")
        sys.exit(1)
    print(f"[2] 向量库读取 ✅ collection={col.name}  count={count}  "
          f"({time.perf_counter() - t0:.2f}s)")

    # ---- 3. 写入 + 检索正确性 ----
    docs = [s[0] for s in SAMPLES]
    ids = [f"verify-{i}" for i in range(len(docs))]
    metas = [{"document_id": 999999, "chunk_index": i, "visibility": "public",
              "department_id": 0, "title": "verify", "section": "", "topic": ""}
             for i in range(len(docs))]

    t0 = time.perf_counter()
    upsert_chunks(document_id=999999, texts=docs, ids=ids, metadatas=metas)
    print(f"[3] 向量写入   ✅ {len(docs)} 条 ({(time.perf_counter() - t0) * 1000:.1f} ms)")

    print("\n[4] 语义检索验证")
    passed = 0
    total = 0
    for expect_idx, (_, queries) in enumerate(SAMPLES):
        for query in queries:
            total += 1
            t0 = time.perf_counter()
            hits = search_similar(query, top_k=2, where={"document_id": 999999})
            dt = (time.perf_counter() - t0) * 1000
            if not hits:
                print(f"    ❌ 「{query}」无结果")
                failures.append(query)
                continue
            top = hits[0]
            ok = top["content"].startswith(SAMPLES[expect_idx][0][:10])
            passed += ok
            print(f"    {'✅' if ok else '❌'} 「{query}」→ score={top['score']:.4f}  "
                  f"{top['content'][:28]}…  ({dt:.1f} ms)")
            if not ok:
                failures.append(query)

    print(f"\n    命中 {passed}/{total}")

    # ---- 5. 清理 ----
    try:
        delete_document_vectors(999999)
        print("\n[5] 清理自检数据 ✅")
    except Exception as exc:
        print(f"\n[5] 清理自检数据 ⚠️ {exc}")

    print("\n" + "=" * 78)
    if failures:
        print(f"存在 {len(failures)} 个未命中：{failures}")
        print("检查项：① provider 是否为 sentence；② 是否约束 transformers<5；")
        print("       ③ Chroma collection 是否与当前 embedding 方案匹配（不同方案用不同 collection）。")
        sys.exit(1)
    print("自检全部通过 ✅")
    if args.isolated:
        print("（隔离模式：本次未改动项目的 data/chroma）")


if __name__ == "__main__":
    main()
