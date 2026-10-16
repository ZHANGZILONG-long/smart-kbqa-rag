#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
入库链路基准测试：分解「解析 → 切块 → 向量编码 → 向量写入」各阶段耗时。

为什么按阶段测：只有拆开量，才知道该优化哪里。
实测结论是向量编码与写入占了 97% 以上，解析和切块不到 4%——
所以优化方向在 embedding 和向量库选型，不在文本处理。

用法：
    python benchmarks/bench_ingest.py --provider sentence   # 真实 BGE 模型（慢，但真实）
    python benchmarks/bench_ingest.py --provider hash       # 确定性假向量，快速跑通链路

说明：
    --provider hash 使用内置的 HashEmbeddingFunction（sha256 派生），
    不加载任何模型，用于在不具备模型环境时验证链路本身；
    它的耗时**不代表**真实语义检索性能，报告中的数据均为 sentence 模式。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("USE_LOCMEM_CACHE", "True")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "True")
os.environ.setdefault("MEMORY_SUMMARY_ENABLED", "False")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

RESULT_DIR = Path(__file__).resolve().parent / "results"


def ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms" if seconds < 1 else f"{seconds:.2f} s"


def build_sample_text(sections: int) -> str:
    """构造一份有结构的中文长文档：章节 + 编号条目，贴近真实制度文件。"""
    lines = []
    for i in range(1, sections + 1):
        lines.append(f"## 第{i}章 管理规定")
        lines.append(
            f"{i}. 第 {i} 条：申请人在系统提交表单后，由部门管理员在一个工作日内完成审批，"
            f"审批通过后进入处理队列，处理时限为 {i} 小时，责任人为部门管理员。"
        )
        lines.append(
            f"{i}. 第 {i} 条补充：如遇特殊情况需延期，须在到期前提交说明并由总管理员确认。"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="入库链路分阶段基准")
    parser.add_argument("--provider", default="hash", choices=["hash", "sentence", "openai"])
    parser.add_argument("--sections", type=int, default=180, help="生成文档的段落数")
    parser.add_argument("--repeat", type=int, default=3, help="每阶段重复轮数，取中位数")
    parser.add_argument("--rounds", type=int, default=3, help="端到端任务的执行次数")
    args = parser.parse_args()

    os.environ["EMBEDDING_PROVIDER"] = args.provider

    import django

    django.setup()

    from django.conf import settings
    from django.core.files.base import ContentFile

    from accounts.models import Department, User
    from documents.models import Document
    from documents.services.chunking import split_document
    from documents.services.parser import extract_text
    from documents.services.vectorstore import (
        clear_vector_caches,
        get_collection,
        search_similar,
        upsert_chunks,
    )
    from documents.tasks import process_document_task

    clear_vector_caches()

    print("=" * 78)
    print(f"入库链路基准   provider={args.provider}   dim={settings.EMBEDDING_DIM}")
    print(f"DB={settings.DATABASES['default']['ENGINE'].split('.')[-1]}   "
          f"chunk_size={settings.CHUNK_SIZE}   overlap={settings.CHUNK_OVERLAP}")
    print("=" * 78)

    report: dict = {"provider": args.provider, "sections": args.sections}

    # ---- 1. 冷启动 ----
    t0 = time.perf_counter()
    get_collection()
    cold = time.perf_counter() - t0
    print(f"\n[冷启动] 首次 get_collection（含模型加载）: {ms(cold)}")
    report["cold_start_s"] = round(cold, 3)

    # ---- 2. 解析 + 切块 ----
    text = build_sample_text(args.sections)
    raw = text.encode("utf-8")
    tmp_path = BASE_DIR / "media" / "_bench_sample.txt"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(text, encoding="utf-8")

    parse_times, chunk_times, chunk_counts = [], [], []
    for _ in range(args.repeat):
        t0 = time.perf_counter()
        parsed = extract_text(str(tmp_path), "txt")
        parse_times.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        chunks = split_document(parsed)
        chunk_times.append(time.perf_counter() - t0)
        chunk_counts.append(len(chunks))

    print(f"\n测试文档：{len(text):,} 字符 / {len(raw) / 1024:.1f} KB → {chunk_counts[0]} 个分块")
    print(f"[1] 解析 extract_text  : 中位数 {ms(statistics.median(parse_times))}")
    print(f"[2] 切块 split_document: 中位数 {ms(statistics.median(chunk_times))}")
    report["parse_ms"] = round(statistics.median(parse_times) * 1000, 1)
    report["chunk_ms"] = round(statistics.median(chunk_times) * 1000, 1)
    report["chunks"] = chunk_counts[0]
    report["text_chars"] = len(text)

    # ---- 3. 向量编码 ----
    from documents.services.vectorstore import get_embedding_function

    embed_fn = get_embedding_function()
    texts = [c.content for c in chunks]
    embed_times = []
    for _ in range(args.repeat):
        t0 = time.perf_counter()
        embed_fn(texts)
        embed_times.append(time.perf_counter() - t0)
    med_embed = statistics.median(embed_times)
    print(f"[3] 向量编码            : 中位数 {ms(med_embed)}"
          f"   ({med_embed / len(chunks) * 1000:.1f} ms/块)")
    report["embed_ms"] = round(med_embed * 1000, 1)
    report["ms_per_chunk_embed"] = round(med_embed / len(chunks) * 1000, 1)

    # ---- 4. 向量写入 ----
    upsert_times = []
    for r in range(args.repeat):
        ids = [f"bench-{r}-{i}" for i in range(len(chunks))]
        metas = [
            {"document_id": 888000 + r, "chunk_index": i, "visibility": "public",
             "department_id": 0, "title": "bench", "section": "", "topic": ""}
            for i in range(len(chunks))
        ]
        t0 = time.perf_counter()
        upsert_chunks(document_id=888000 + r, texts=texts, ids=ids, metadatas=metas)
        upsert_times.append(time.perf_counter() - t0)
    med_upsert = statistics.median(upsert_times)
    print(f"[4] 向量写入 upsert     : 中位数 {ms(med_upsert)}"
          f"   ({med_upsert / len(chunks) * 1000:.1f} ms/块)")
    report["upsert_ms"] = round(med_upsert * 1000, 1)

    # ---- 5. 端到端任务 ----
    dept, _ = Department.objects.get_or_create(code="bench", defaults={"name": "基准测试部"})
    user, _ = User.objects.get_or_create(
        username="bench_user", defaults={"role": "super_admin", "is_staff": True}
    )

    e2e_times = []
    created_ids = []
    for i in range(args.rounds):
        doc = Document.objects.create(
            title=f"基准文档 {i}", uploader=user, department=dept,
            original_filename=f"_bench_{i}.txt", visibility="public",
            file_type="txt", file_size=len(raw), status=Document.Status.READY,
        )
        doc.file.save(f"_bench_{i}.txt", ContentFile(raw), save=True)
        t0 = time.perf_counter()
        result = process_document_task(doc.id)
        e2e_times.append(time.perf_counter() - t0)
        doc.refresh_from_db()
        created_ids.append(doc.id)
        print(f"[5] 端到端 doc={doc.id} status={doc.status} "
              f"chunks={doc.chunk_count} ok={result.get('ok')}  {ms(e2e_times[-1])}")

    med_e2e = statistics.median(e2e_times)
    print(f"\n    端到端中位数 {ms(med_e2e)}   "
          f"最小 {ms(min(e2e_times))}  最大 {ms(max(e2e_times))}")
    report["e2e_ms"] = round(med_e2e * 1000, 1)
    report["e2e_min_ms"] = round(min(e2e_times) * 1000, 1)
    report["e2e_max_ms"] = round(max(e2e_times) * 1000, 1)
    report["e2e_ms_per_chunk"] = round(med_e2e / len(chunks) * 1000, 1)

    # 各阶段占比（用于判断优化方向）
    total = report["parse_ms"] + report["chunk_ms"] + report["embed_ms"] + report["upsert_ms"]
    if total > 0:
        report["share"] = {
            "parse": round(report["parse_ms"] / total * 100, 1),
            "chunk": round(report["chunk_ms"] / total * 100, 1),
            "embed": round(report["embed_ms"] / total * 100, 1),
            "upsert": round(report["upsert_ms"] / total * 100, 1),
        }
        print(f"\n    阶段占比: 解析 {report['share']['parse']}%  "
              f"切块 {report['share']['chunk']}%  "
              f"编码 {report['share']['embed']}%  "
              f"写入 {report['share']['upsert']}%")

    # ---- 6. 检索延迟 ----
    queries = ["请假怎么申请", "报销流程是什么", "新员工入职要做什么", "系统故障怎么处理"]
    latencies = []
    print(f"\n[6] 检索延迟（库内 {get_collection().count()} 条，top_k=20）")
    for q in queries:
        t0 = time.perf_counter()
        hits = search_similar(q, top_k=20)
        dt = (time.perf_counter() - t0) * 1000
        latencies.append(dt)
        top = hits[0]["score"] if hits else None
        print(f"    {q:<14} {dt:>7.1f} ms  命中 {len(hits):>2} 条  "
              f"top1={round(top, 4) if top is not None else None}")
    latencies.sort()
    report["search_ms"] = {
        "p50": round(latencies[len(latencies) // 2], 1),
        "max": round(latencies[-1], 1),
    }
    print(f"    P50 {report['search_ms']['p50']} ms   max {report['search_ms']['max']} ms")

    # ---- 清理 ----
    try:
        col = get_collection()
        col.delete(where={"document_id": {"$gte": 888000}})
        for did in created_ids:
            col.delete(where={"document_id": did})
        Document.objects.filter(id__in=created_ids).delete()
    except Exception as exc:  # pragma: no cover
        print(f"    清理失败：{exc}")
    try:
        tmp_path.unlink()
    except OSError:
        pass

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    # 带时间戳存档，避免覆盖仓库里已提交的正式基准数据（如 result_ingest_sentence.json）
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = RESULT_DIR / f"result_ingest_{args.provider}_{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n结果已写入 {out.relative_to(BASE_DIR)}")
    print("（仓库中 docs/benchmarks.md 引用的正式数据为 result_ingest_sentence.json / result_async.json）")


if __name__ == "__main__":
    main()
