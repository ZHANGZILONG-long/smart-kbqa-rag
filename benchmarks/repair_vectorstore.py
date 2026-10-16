#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
向量库维护：健康检查、重建、清理孤儿向量。

为什么需要这个脚本：
    Chroma 的 persistent 本地模式曾在部署过程中出现过索引不可读
    （报 "Error loading hnsw index" 或读取挂住）。无论是哪种原因，
    恢复手段都是同一套：清掉本地索引目录，从数据库里的源文件重建。

    另外，向量库与关系库是两个存储，没有跨两者的原子提交，
    因此需要能定期比对两边是否一致（本脚本的 audit 功能）。

用法：
    python benchmarks/repair_vectorstore.py --check     # 只做健康检查与一致性审计
    python benchmarks/repair_vectorstore.py --rebuild   # 备份并清空索引目录，全量重建
    python benchmarks/repair_vectorstore.py --audit     # 审计孤儿向量（不删除）

注意：--rebuild 会重建向量库。运行前请确认没有其他进程
（runserver / celery worker）正在使用同一个索引目录。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "True")
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def check(argv) -> int:
    """健康检查：能否打开 collection、能否读取。带超时保护，避免损坏时无限挂住。"""
    import django

    django.setup()

    from documents.services.vectorstore import clear_vector_caches, get_chroma_client

    clear_vector_caches()
    client = get_chroma_client()
    names = [c if isinstance(c, str) else c.name for c in client.list_collections()]
    if not names:
        print("（向量库为空，尚未创建 collection）")
        return 0

    print(f"发现 {len(names)} 个 collection\n")
    failed = []
    for name in names:
        result: dict = {}

        def probe() -> None:
            try:
                col = client.get_collection(name=name)
                result["count"] = col.count()
                result["sample"] = col.get(limit=1)["ids"]
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"{type(exc).__name__}: {exc}"

        t = threading.Thread(target=probe, daemon=True)
        t.start()
        t.join(30)

        if t.is_alive():
            print(f"  ❌ {name}\n     读取超时 30s（索引可能损坏）")
            failed.append(name)
        elif "error" in result:
            print(f"  ❌ {name}\n     {result['error'][:160]}")
            failed.append(name)
        else:
            print(f"  ✅ {name}   count={result['count']}   sample={result['sample']}")

    if failed:
        print(f"\n有 {len(failed)} 个 collection 不可读。")
        print("执行 `python benchmarks/repair_vectorstore.py --rebuild` 重建。")
        return 1
    print("\n向量库健康 ✅")
    return 0


def audit(argv) -> int:
    """审计：向量库里的 document_id 是否都能在数据库里找到对应文档。"""
    import django

    django.setup()
    from documents.models import Document, DocumentChunk
    from documents.services.vectorstore import clear_vector_caches, get_collection

    clear_vector_caches()
    col = get_collection()
    total = col.count()
    print(f"collection={col.name}  向量数={total}")

    data = col.get(include=["metadatas"])
    doc_ids = Counter(
        m["document_id"] for m in data["metadatas"]
        if m and "document_id" in m
    )

    live = set(Document.objects.values_list("id", flat=True))
    print(f"数据库文档 id: {sorted(live)}\n")
    orphans = []
    for did, n in sorted(doc_ids.items()):
        ok = did in live
        print(f"  document_id={did:<8} {n:>5} 条  {'✅' if ok else '❌ 孤儿'}")
        if not ok:
            orphans.append(did)

    print(f"\n数据库分块记录: {DocumentChunk.objects.count()} 行")
    print(f"向量库向量数  : {total}")
    print(f"孤儿 document_id: {orphans}")

    if orphans and getattr(argv, "delete_orphans", False):
        for did in orphans:
            col.delete(where={"document_id": did})
        print(f"已删除 {len(orphans)} 组孤儿向量，剩余 {col.count()} 条")
    elif orphans:
        print("（加 --delete-orphans 可清理）")
    return 0


def rebuild(argv) -> int:
    """备份并清空索引目录，然后从数据库源文件全量重建。"""
    import django

    django.setup()
    from django.conf import settings
    from documents.models import Document, DocumentChunk
    from documents.services.vectorstore import clear_vector_caches, get_collection
    from documents.tasks import process_document_task

    chroma_dir = Path(str(settings.CHROMA_PERSIST_DIR))
    backup = chroma_dir.parent / f"{chroma_dir.name}_backup_{time.strftime('%Y%m%d_%H%M%S')}"

    print("=" * 78)
    print("重建向量库")
    print("=" * 78)

    docs = list(Document.objects.filter(status=Document.Status.READY).order_by("id"))
    if not docs:
        print("没有 status=ready 的文档，无需重建。")
        return 0
    print(f"\n将重建 {len(docs)} 篇文档")

    if not argv.yes:
        answer = input(f"确认清空 {chroma_dir} 并重建？[y/N] ").strip().lower()
        if answer != "y":
            print("已取消")
            return 1

    # 1. 备份并清空索引目录（必须在没有客户端持有句柄时进行）
    clear_vector_caches()
    if chroma_dir.exists():
        shutil.move(str(chroma_dir), str(backup))
        print(f"\n[1] 原索引已备份到 {backup}")
    chroma_dir.mkdir(parents=True, exist_ok=True)
    clear_vector_caches()
    print("    已创建空目录")

    # 2. 清空分块记录（向量要重建，记录也要重建）
    n = DocumentChunk.objects.count()
    DocumentChunk.objects.all().delete()
    print(f"\n[2] 清空分块记录 {n} 行")

    # 3. 逐篇重建（单进程串行，避免并发写）
    print(f"\n[3] 重建中")
    col = get_collection()
    ok_count = 0
    t_all = time.perf_counter()
    for doc in docs:
        t0 = time.perf_counter()
        result = process_document_task(doc.id)
        doc.refresh_from_db()
        flag = "✅" if result.get("ok") else "❌"
        ok_count += bool(result.get("ok"))
        print(f"    {flag} id={doc.id} 《{doc.title}》 "
              f"{(time.perf_counter() - t0) * 1000:>7.0f} ms  chunks={doc.chunk_count}")
        if not result.get("ok"):
            print(f"        错误: {result}")

    print(f"\n[4] 校验")
    print(f"    文档数            : {Document.objects.count()}")
    print(f"    DocumentChunk 行数: {DocumentChunk.objects.count()}")
    print(f"    向量库向量数      : {col.count()}")
    print(f"    重建耗时          : {time.perf_counter() - t_all:.1f}s")

    consistent = DocumentChunk.objects.count() == col.count()
    print(f"\n{'✅ 重建完成，数据一致' if consistent else '⚠️ 行数与向量数不一致，请检查'}")
    print(f"原索引备份: {backup}")
    return 0 if consistent else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="向量库维护工具")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="健康检查")
    group.add_argument("--audit", action="store_true", help="审计孤儿向量")
    group.add_argument("--rebuild", action="store_true", help="备份并全量重建")
    parser.add_argument("--delete-orphans", action="store_true", help="审计时顺带清理孤儿向量")
    parser.add_argument("--yes", action="store_true", help="重建时跳过确认")
    args = parser.parse_args()

    if args.check:
        sys.exit(check(args))
    if args.audit:
        sys.exit(audit(args))
    sys.exit(rebuild(args))


if __name__ == "__main__":
    main()
