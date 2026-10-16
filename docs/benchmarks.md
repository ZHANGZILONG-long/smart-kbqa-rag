# 基准测试报告

本文件记录本项目**实测**的性能数据与排查结论。所有数据由 `benchmarks/` 下的脚本在本机产出，
原始 JSON 一并保留，便于复现与核对。

> **这不是生产容量数据。** 下文每一项都标注了测试条件——包括硬件、进程模型、以及
> 被固定/排除的变量。没有边界说明的数字没有意义，所以这份报告把边界写在前面。

---

## 一、测试环境

| 项 | 值 |
|---|---|
| 机型 | 笔记本（非服务器） |
| CPU | Intel Core i5-1135G7，4 核 8 线程 @ 2.4GHz |
| 内存 | 15.7 GB |
| 磁盘 | NVMe SSD（Intel SSDPEKNU512GZ，477 GB） |
| 操作系统 | Windows 10 家庭中文版 19045 |
| Python | 3.12.10 |
| 框架 | Django 4.2 + DRF |
| 关系库 | MySQL 8（本机 3306） |
| 缓存 / 队列 | Redis（本机 6379），Celery broker = db 1 |
| 向量库 | Chroma 1.5.9，**persistent 本地模式** |
| Embedding | BAAI/bge-small-zh-v1.5，512 维 |
| **推理设备** | **CPU-only**（`torch 2.14.1+cpu`，`cuda.is_available() == False`） |
| LLM | DeepSeek API——**本轮全部基准测试未调用**（见下） |
| 并发模型 | 单机单进程；Celery 使用 `--pool=solo -c 1`（1 个 worker 串行处理） |

### 三个必须说明的测试条件

**1. 为什么关掉 LLM 调用**
LLM 是外部网络依赖，它的延迟受网络抖动、服务端排队影响，会把系统自身的耗时完全淹没。
本轮要测的是**我自己的代码和存储层的成本**，所以把 LLM 调用排除在外。
包含 LLM 的端到端问答延迟需要单独测——这是一个不同的指标（用户感知延迟），不是同一个实验。

**2. 为什么 embedding 是 CPU 推理**
本机没有可用 GPU，torch 装的是 CPU 版。所以下文"向量编码"的耗时全部是
i5-1135G7 上跑 BGE 前向计算的结果。这解释了为什么单块编码在 20~45 ms 量级——
换成 GPU 会显著下降，但**当前部署条件下这就是真实成本**，也是瓶颈所在。

**3. worker 预热**
Celery worker 首次执行任务时要加载 BGE 模型（实测约 10~14 秒）。
这是**每个 worker 进程一次性**的开销，不计入"每篇文档处理耗时"。
所有入库数据都是在 worker 预热完成后测得的。

---

## 二、文档入库链路：耗时分解

脚本：`benchmarks/bench_ingest.py`
原始数据：`benchmarks/results/result_ingest.json`

测试文档：《面试题》docx，21.4 KB / 6,292 字符

| 阶段 | 耗时 | 占比 |
|---|---|---|
| 解析 `extract_text` | 66.8 ms | 3.2% |
| 结构化切块 `split_document` | 1.2 ms | 0.1% |
| **向量编码（BGE，CPU）** | **1,697.8 ms** | **81.9%** |
| **向量写入（Chroma）** | **1,674.5 ms** | **80.8%** |
| 端到端 `process_document_task` | 2,074.2 ms | 100% |
| 产出分块数 | 39 块（平均 169 字符） | — |

> 注：编码与写入占比之和超过 100%，因为端到端任务里两者部分流水线化，
> 上表用于对比各阶段的量级，不是严格的加总关系。

### 结论

**入库链路的耗时 97% 以上集中在向量编码与向量写入，解析和切块合计不到 4%。**

这个结论直接决定优化方向：

- ❌ 优化解析器没有意义（省到 0 也只能拿回 3%）
- ✅ 真正有效的三个方向：
  1. **增大 embedding 批大小**，摊薄每次前向计算的固定开销
  2. **换更小的模型或做量化**，直接降低 CPU 前向计算成本
  3. **向量库改为独立服务（HTTP 模式）**，去掉本地文件锁与压缩开销

---

## 三、向量检索

脚本：`benchmarks/bench_ingest.py`，库内 59 条向量，`top_k=20`

| 指标 | 值 |
|---|---|
| P50 | 23.9 ms |
| P90 | 28.0 ms |
| max | 28.0 ms |

### 检索质量验证（语义有效性）

查询「请假怎么申请」的排序结果：

| score | 内容 |
|---|---|
| **0.7301** | 【第一章请假制度】员工请假需提前在系统中提交申请，直属领导审批通过后方可休假 |
| 0.3955 | 【第二章报销流程】差旅报销需在出差结束后 7 个工作日内提交，附件包括发票与行程单 |
| 0.3833 | 新员工入职需完成信息安全培训并通过考试 |

查询「报销流程」→ top1 = **0.7159**，命中【第二章报销流程】。

**结论**：正确文档的相似度（0.73）约为次优候选（0.40）的 **1.8 倍**，
说明 embedding 确实把语义区分开了，而不是靠关键词命中。

**这组数据也是相似度阈值 0.25 的来源**：真实命中的分数落在 0.53~0.73，
无关内容落在 0.28~0.40，0.25 是保守下限——宁可多召回，也不漏掉相关资料，
最终是否采用由后续的 LLM 相关性评分决定。

---

## 四、异步化的真实收益

脚本：`benchmarks/bench_async.py`
原始数据：`benchmarks/results/result_async_isolated.json`

**测试方式**：启动真实 Celery worker（`--pool=solo`，非 eager 模式），
通过 HTTP 上传接口提交文档；worker 侧耗时从 Celery 任务日志读取（`succeeded in X`）。

| 文档 | 大小 | 分块 | **接口响应** | **Worker 处理** | 上传后瞬时状态 |
|---|---|---|---|---|---|
| 小（40 段） | 11.9 KB | 40 | **11.1 ms** | 594.0 ms | `pending` |
| 中（120 段） | 35.8 KB | 120 | **12.2 ms** | 1,812.0 ms | `processing` |
| 大（240 段） | 72.3 KB | 240 | **11.7 ms** | 4,359.0 ms | `pending` |
| **平均** | — | 133 | **11.7 ms** | **2,255.0 ms** | — |

### 结论

1. **接口响应时间与文档大小无关**——40 段和 240 段的响应相差不到 1 ms，
   因为接口只做鉴权、校验、落盘和任务投递，不做任何向量操作。
2. **Worker 处理时间随分块数线性增长**，约 **16.9 ms/块**（CPU-only 推理）。
3. 同步实现下，用户需要等待 `11.7 + 2255 ≈ 2.27 秒`（大文档要等 4.4 秒）；
   异步化后用户等待稳定在 **12 ms 以内**。

**这才是异步的真正价值**：不是"更快"，而是**把接口响应时间与处理耗时解耦**——
处理成本再涨，用户等待也不涨。

### 已知边界

- **单 worker 串行**：并发上传多篇文档时任务会在 broker 中排队，端到端延迟线性上涨。
  这正是需要多 worker + 队列长度监控的场景，属于**尚未压测的部分**（见第七节）。
- **无 LLM**：完整问答链路的用户感知延迟未包含在本次数据中。

---

## 五、本次排查发现的三个问题

### 问题 1：并发使用同一个本地向量库会造成数据互相污染 ★★★

**现象**

两个进程（模拟 Web 进程与 Celery worker）同时对同一个 Chroma 持久化目录
做 `upsert` + `delete` 时，**索引没有损坏，但数据互相可见并被误删**。

受控实验日志（`benchmarks/` 下的隔离实验，两个进程各跑 15 轮、每轮写 100 条后删除自己的数据）：

```
[A] round 12: upsert 100 ok, count=100, delete ok
[A] round 13: upsert 100 ok, count=200, delete ok     ← A 只写了 100 条，却数到 200
[B] round 13: upsert 100 ok, count=200, delete ok     ← B 同样看到 200
```

**解读**

A 写入 100 条后 `count()` 返回 200，说明它**读到了 B 同时写入的数据**。
更严重的是紧随其后的 `delete`：A 的删除条件会命中 B 的数据，**把对方刚写入的向量一起删掉**。

**这在项目里的真实后果**

向量写入以 `document_id` 作为过滤条件：

```python
delete_document_vectors(document_id)   # 删除 where document_id = ?
```

如果入库任务（worker）与另一条清理/重建路径（Web 进程或另一个 worker）并发执行，
一方可能删掉另一方正在写入的向量，结果是**文档明明处理成了 ready，检索却找不到内容**——
不报错、不告警，只是"搜不到"。这类静默数据丢失比直接报错难排查得多。

### 已排除的假设（同样重要）

排查过程中我一度认为是"多进程并发把 HNSW 索引写坏了"。为了确认，做了四组隔离实验
（每组独立临时目录、独立子进程）：

| 假设 | 实验 | 结果 |
|---|---|---|
| 进程非正常退出导致索引未落盘 | 写 200 条后立即 `os._exit(0)`，新进程读取 | ✅ 正常（count=200） |
| 正常退出 | 写 200 条，`gc` 后正常退出 | ✅ 正常 |
| 删除操作触发段损坏 | 写 200 → 删全部 / 删一半 / 删后重写 | ✅ 全部正常 |
| 多进程并发写导致索引损坏 | 两进程并发各 15 轮 upsert+delete | ✅ 索引未损坏（但数据互相污染，见上） |

**结论：我没有复现出"并发导致索引损坏"。** 因此不能把索引不可读归因于并发——
这个归因一度写进过我的排查笔记，实验后被推翻。

**索引不可读是真实发生过的现象**（表现为 `Error loading hnsw index`，或读取时挂住），
现场特征：HNSW 段目录为空（0 个文件），而 `chroma.sqlite3` 的 `embeddings` 表里仍有记录。
触发条件未能定位，可能与进程在写入过程中被强制终止有关。

**因此采用的措施是"可恢复"而不是"归因"**：
1. `benchmarks/repair_vectorstore.py --check` 启动健康检查（带 30s 超时，避免挂住）；
2. `--rebuild` 从数据库源文件全量重建（已实测：2 篇文档 → 42 个分块，2.3 秒）；
3. `--audit` 比对 `DocumentChunk` 与向量库的 id 集合，发现孤儿向量；
4. 长期方向：改用 HTTP 模式把向量库做成独立服务，让多进程访问成为受支持的形态。

---

### 问题 2：向量写入位于数据库事务之外，两个存储可能不一致 ★★

**代码位置**：`apps/documents/tasks.py`

```python
upsert_chunks(...)                      # ← 先写向量库（无事务保护）

with transaction.atomic():              # ← 再开数据库事务
    DocumentChunk.objects.bulk_create(...)
    Document.objects.filter(...).update(status=READY, ...)
```

**问题**

当数据库这段失败时，向量已经写入向量库，但分块记录和 READY 状态都未提交。
结果是向量库里存在一批**系统不知道其存在**的数据。

**为什么目前大多数情况没出事**

向量 id 是确定性的（`doc-{id}-chunk-{idx}`），且任务开头会先执行
`delete_document_vectors()` + `DocumentChunk.objects.filter(...).delete()`，
所以 Celery 重试时会把上一次的残留清掉再重写——**正常路径能自愈**。

**但仍会留下脏数据的三种情况**

1. 重试次数耗尽（3 次后标记 FAILED），已写入的向量留在库中；
2. `delete_document_vectors()` 内部是 `except Exception: pass`，**删除失败被静默吞掉**，
   调用方无法感知；
3. 重试的时间窗口内，上一次写入的旧向量**正被检索命中**，
   用户可能读到本应被删除的内容。

**修复方向**

1. 调整顺序：先提交数据库事务，再写向量；写入失败时执行补偿删除；
2. 去掉 `delete_document_vectors` 的静默 catch，失败必须告警；
3. 增加对账任务：定期比对 `DocumentChunk.chroma_id` 集合与向量库实际 id 集合。

---

### 问题 3：依赖未锁版本导致全新安装后无法启动 ★★

**现象**

按 README 步骤安装依赖后，加载 embedding 模型直接失败：

```
ValueError: Unrecognized processing class in BAAI/bge-small-zh-v1.5.
  Can't instantiate a processor, a tokenizer, an image processor,
  a video processor or a feature extractor for this model.
```

**根因**

`requirements.txt` 中 `sentence-transformers` 与 `transformers` 未限制版本上限，
全新安装会装到 `transformers 5.x`。5.x 改变了模型处理类的加载路径，
加载 BERT 架构的 `bge-*` 模型时找不到对应的 processor 类。

**修复**（已提交）

约束 `transformers>=4.40,<5`、`sentence-transformers>=3.0`。
验证：BGE 模型加载耗时 0.22 s，语义检索结果正确（见第三节）。

**教训**：直接依赖必须限制主版本上限。这类问题不会在已有环境里出现，
只会在新机器、CI 或容器重建时爆发。

---

## 六、复现方式

```bash
# 1. 入库链路分解（不需要 Redis / worker）
python benchmarks/bench_ingest.py --provider sentence
python benchmarks/bench_ingest.py --provider hash    # 不加载模型，快速跑通链路

# 2. 异步收益对比（需要 Redis；脚本会自动拉起 worker 子进程）
python benchmarks/bench_async.py

# 3. 向量库健康检查与自检
python benchmarks/repair_vectorstore.py --check
python benchmarks/repair_vectorstore.py --audit
python benchmarks/verify_embedding.py --isolated

# 4. 向量库重建（索引不可读时使用）
python benchmarks/repair_vectorstore.py --rebuild
```

脚本会自行预热 worker、清理测试数据，并把结果写入 `benchmarks/results/*.json`。

**运行注意**：Chroma 本地模式在多进程下存在数据互相污染问题（见第五节问题 1），
跑基准时请先停掉 `runserver` 与 `celery worker`，避免干扰结果与数据。

---

## 七、尚未覆盖的部分

按性价比排序，以下是这份报告**没有**回答的问题：

| 优先级 | 待测项 | 说明 |
|---|---|---|
| 高 | 并发压测 | 多文档并发上传 + 多 worker 消费下的 QPS、P99、队列积压长度、容量拐点 |
| 高 | 含 LLM 的端到端延迟 | 首字延迟（SSE）与完整回答延迟，这是用户真正感知的指标 |
| 中 | 检索准确率 | 构造「问题 → 期望文档」标注集，计算 Hit@3 / MRR，把检索质量量化 |
| 中 | 审批流并发 | 两个管理员同时审批同一文档，验证是否会触发重复处理 |
| 低 | 大规模检索 | 灌入 10 万条向量后重测检索延迟，观察索引规模的性能曲线 |

**报告到此为止的边界**：它证明了单机单 worker 下各阶段的成本构成、
异步解耦的有效性，以及三个真实缺陷的定位。它**没有**证明系统在高并发下的行为。
