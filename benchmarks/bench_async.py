#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
异步化收益测量：对比「接口响应时间」与「Worker 后台处理时间」。

要回答的问题：把入库改成 Celery 异步之后，用户实际少等了多少？
做法是分两个量分别测，而不是测一个"总耗时"：
    A. 上传接口的响应时间 —— 用户真正等待的时间
    B. Worker 侧任务的执行时间 —— 从 Celery 日志读取的真实工作时间
同步实现下用户要等的是 A + B，异步化之后只需等 A。

为什么必须用真实 worker（而不是 CELERY_TASK_ALWAYS_EAGER=True）：
eager 模式下任务在请求线程里同步执行，测出来的"接口响应时间"等于"处理时间"，
根本测不出异步的收益。所以本脚本会自己拉起一个 worker 子进程。

用法：
    python benchmarks/bench_async.py
    python benchmarks/bench_async.py --sizes 40 240     # 自定义文档规模

前置：需要可用的 Redis（读取 settings.CELERY_BROKER_URL）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("EMBEDDING_PROVIDER", "sentence")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

RESULT_DIR = Path(__file__).resolve().parent / "results"
WORKER_LOG = Path(tempfile.gettempdir()) / "smartkbqa_bench_worker.log"


def build_doc(sections: int) -> bytes:
    lines = []
    for i in range(1, sections + 1):
        lines.append(f"## 第{i}章 管理规定")
        lines.append(
            f"{i}. 第 {i} 条：申请人在系统提交表单后，由部门管理员在一个工作日内完成审批，"
            f"审批通过后进入处理队列，处理时限为 {i} 小时。"
        )
        lines.append(
            f"{i}. 第 {i} 条补充：如遇特殊情况需延期，须在到期前提交说明并由总管理员确认。"
        )
    return "\n".join(lines).encode("utf-8")


def read_worker_task_times() -> list[dict]:
    """从 worker 日志里抓出每个任务的真实执行耗时。"""
    if not WORKER_LOG.exists():
        return []
    out = []
    pattern = re.compile(
        r"Task documents\.tasks\.process_document_task\[([0-9a-f-]+)\] succeeded in ([\d.]+)"
    )
    for line in WORKER_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = pattern.search(line)
        if m:
            out.append({"task_id": m.group(1), "worker_s": float(m.group(2))})
    return out


def wait_until_ready(proc: subprocess.Popen, max_wait: int = 150) -> tuple[bool, str]:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < max_wait:
        if proc.poll() is not None:
            return False, f"worker 提前退出，code={proc.returncode}"
        if WORKER_LOG.exists():
            content = WORKER_LOG.read_text(encoding="utf-8", errors="ignore").lower()
            if "ready" in content:
                return True, f"{time.perf_counter() - t0:.1f}s"
        time.sleep(1)
    return False, f"等待 {max_wait}s 超时"


def main() -> None:
    parser = argparse.ArgumentParser(description="异步化收益测量")
    parser.add_argument("--sizes", type=int, nargs="+", default=[40, 120, 240],
                        help="各测试文档的段落数")
    parser.add_argument("--keep-worker-log", action="store_true")
    args = parser.parse_args()

    import django

    django.setup()

    from django.conf import settings
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import override_settings
    from rest_framework.test import APIClient

    from accounts.models import User
    from documents.models import Document

    print("=" * 86)
    print("异步化收益测量：接口响应 vs Worker 后台处理")
    print("=" * 86)

    broker = settings.CELERY_BROKER_URL
    print(f"broker = {broker}")
    if broker.startswith("redis://"):
        import socket

        host_port = broker.split("//", 1)[1].split("/", 1)[0]
        host, _, port = host_port.partition(":")
        sock = socket.socket()
        sock.settimeout(3)
        try:
            sock.connect((host, int(port or 6379)))
            print("Redis 可连接 ✅")
        except OSError as exc:
            print(f"Redis 不可连接（{exc}），无法进行异步测量。")
            print("请先启动 Redis，或改用 CELERY_TASK_ALWAYS_EAGER 做对照（见 README）。")
            return
        finally:
            sock.close()

    # ---- 启动真实 worker ----
    env = dict(os.environ)
    env.update({
        "CELERY_TASK_ALWAYS_EAGER": "False",
        "EMBEDDING_PROVIDER": os.environ.get("EMBEDDING_PROVIDER", "sentence"),
        "HF_HUB_OFFLINE": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    if WORKER_LOG.exists():
        WORKER_LOG.unlink()

    cmd = [sys.executable, "-m", "celery", "-A", "config", "worker",
           "-l", "info", "--pool=solo", "-c", "1", "-n", "bench@%h"]
    print(f"\n启动 worker: {' '.join(cmd)}")
    log_fh = open(WORKER_LOG, "w", encoding="utf-8", errors="ignore")
    proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), env=env,
                            stdout=log_fh, stderr=subprocess.STDOUT)
    ok, info = wait_until_ready(proc)
    print(f"worker 就绪: {ok} ({info})")
    if not ok:
        log_fh.close()
        proc.kill()
        print(WORKER_LOG.read_text(encoding="utf-8", errors="ignore")[-2000:])
        return

    report: dict = {"broker": broker}

    def upload_and_wait(client, label: str, sections: int, timeout: int = 300):
        raw = build_doc(sections)
        upload = SimpleUploadedFile(f"bench_{sections}.txt", raw, content_type="text/plain")
        t0 = time.perf_counter()
        resp = client.post("/api/documents/",
                           {"file": upload, "title": f"基准 {label}", "visibility": "public"},
                           format="multipart")
        api_ms = (time.perf_counter() - t0) * 1000
        if resp.status_code != 201:
            body = getattr(resp, "data", None) or resp.content[:200]
            return None, f"HTTP {resp.status_code}: {str(body)[:140]}"

        payload = getattr(resp, "data", {}) or {}
        doc_id = (payload.get("document") or {}).get("id") or payload.get("id")
        if not doc_id:
            return None, f"响应缺少 id: {str(payload)[:140]}"

        doc = Document.objects.get(pk=doc_id)
        status_immediate = doc.status
        deadline = time.time() + timeout
        while time.time() < deadline:
            doc.refresh_from_db()
            if doc.status in (Document.Status.READY, Document.Status.FAILED):
                break
            time.sleep(0.05)
        e2e_ms = (time.perf_counter() - t0) * 1000
        return {"label": label, "kb": round(len(raw) / 1024, 1), "doc_id": doc_id,
                "api_ms": round(api_ms, 1), "e2e_ms": round(e2e_ms, 1),
                "chunks": doc.chunk_count, "status_immediate": status_immediate,
                "status_final": doc.status}, None

    try:
        uploader, _ = User.objects.get_or_create(
            username="bench_uploader", defaults={"role": "super_admin", "is_staff": True})
        if uploader.role != "super_admin":
            uploader.role = "super_admin"
            uploader.is_staff = True
            uploader.save()
        client = APIClient()
        client.force_authenticate(user=uploader)

        with override_settings(ALLOWED_HOSTS=["*"], CELERY_TASK_ALWAYS_EAGER=False):
            # 预热：吃掉 worker 首次加载模型的开销，避免它被算进第一个文档的处理时间
            print("\n预热 worker（加载 embedding 模型，一次性开销）…")
            t0 = time.perf_counter()
            upload_and_wait(client, "预热", 8, timeout=240)
            warmup_s = time.perf_counter() - t0
            print(f"  预热完成 {warmup_s:.1f}s（此耗时不计入后续统计）")
            report["worker_warmup_s"] = round(warmup_s, 1)

            tasks_before = len(read_worker_task_times())

            print("\n" + "-" * 86)
            print(f"{'文档':<14}{'大小':>10}{'接口响应':>12}{'端到端':>12}{'分块':>7}  状态变化")
            print("-" * 86)
            rows = []
            for n in args.sizes:
                row, err = upload_and_wait(client, f"{n} 段", n)
                if row is None:
                    print(f"{n} 段 失败: {err}")
                    continue
                print(f"{row['label']:<14}{row['kb']:>8.1f}KB{row['api_ms']:>10.1f}ms"
                      f"{row['e2e_ms']:>10.1f}ms{row['chunks']:>7}"
                      f"  {row['status_immediate']} → {row['status_final']}")
                rows.append(row)

        worker_tasks = read_worker_task_times()[tasks_before:]
        report["rows"] = rows
        report["worker_tasks"] = worker_tasks

        print("\n" + "-" * 86)
        print("Worker 侧任务耗时（从 Celery 日志读取）")
        print("-" * 86)
        for i, task in enumerate(worker_tasks):
            label = rows[i]["label"] if i < len(rows) else f"task-{i}"
            print(f"  {label:<14} {task['worker_s'] * 1000:>10.1f} ms")

        if rows:
            avg_api = statistics.mean(r["api_ms"] for r in rows)
            avg_chunks = statistics.mean(r["chunks"] for r in rows)
            report["avg_api_ms"] = round(avg_api, 1)
            report["avg_chunks"] = round(avg_chunks, 1)
            print("\n" + "=" * 86)
            print("汇总")
            print("=" * 86)
            print(f"  接口响应（用户等待）  : 平均 {avg_api:.1f} ms")
            if worker_tasks:
                avg_worker = statistics.mean(t["worker_s"] for t in worker_tasks) * 1000
                report["avg_worker_ms"] = round(avg_worker, 1)
                report["ms_per_chunk_worker"] = round(avg_worker / avg_chunks, 1) if avg_chunks else None
                print(f"  Worker 处理（后台）   : 平均 {avg_worker:.1f} ms"
                      f"（{avg_worker / avg_chunks:.1f} ms/分块）")
                print(f"  同步实现用户需等待    : 约 {avg_api + avg_worker:.1f} ms")
                print(f"  → 异步把用户等待降低  : {(avg_api + avg_worker) / avg_api:.0f} 倍")
            print(f"  平均分块数            : {avg_chunks:.0f}")

        # 清理基准文档（含向量）。为避免多进程并发写向量库，
        # 这里只删数据库记录；向量请用 benchmarks/reindex.py 或管理命令统一重建。
        doc_ids = [r["doc_id"] for r in rows]
        if doc_ids:
            Document.objects.filter(id__in=doc_ids).delete()
            print(f"\n已清理 {len(doc_ids)} 篇基准文档（向量库建议随后重建一次）")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fh.close()
        print("worker 已关闭")
        if not args.keep_worker_log:
            try:
                WORKER_LOG.unlink()
            except OSError:
                pass

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = RESULT_DIR / f"result_async_{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"结果已写入 {out.relative_to(BASE_DIR)}")
    print("（仓库中 docs/benchmarks.md 引用的正式数据为 result_async.json）")


if __name__ == "__main__":
    main()
