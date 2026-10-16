# 基准测试原始结果

本目录存放基准脚本输出的原始 JSON，供核对与复现。各项数据的解读见 [../../docs/benchmarks.md](../../docs/benchmarks.md)。

| 文件 | 来源 | 说明 |
|---|---|---|
| `result_ingest_sentence.json` | `bench_ingest.py --provider sentence` | **正式数据**：使用 BAAI/bge-small-zh-v1.5 真实模型推理，反映实际部署的耗时构成 |
| `result_async.json` | `bench_async.py` | **正式数据**：真实 Celery worker（`--pool=solo`）下的接口响应 vs 后台处理耗时 |

## 两份入库数据的用途区别

对比 `result_ingest_sentence.json` 与 `result_ingest_hash.json` 可以分离出两笔成本：

| | 向量编码 | 向量写入 |
|---|---|---|
| sentence（真实模型，CPU 推理） | 1,697.8 ms / 39 块 ≈ 43.5 ms/块 | 1,674.5 ms ≈ 42.9 ms/块 |
| hash（无模型） | 17.5 ms / 60 块 ≈ 0.3 ms/块 | 80.2 ms / 60 块 ≈ 1.3 ms/块 |

hash 模式下编码几乎为零，此时写入成本约 **1.3 ms/块**——这是 Chroma 本地模式的纯写入开销。
sentence 模式下写入涨到 **≈42.9 ms/块**，多出来的部分来自：写入前需要为每块计算 embedding
（Chroma 在 upsert 时会调用 embedding function），以及更大的向量维度带来的索引更新成本。

**所以"向量写入"这一项在真实模型下并不是纯粹的存储成本，它包含了重复的编码开销。**
若要精确区分，需要预先算好向量再直接写库（绕过 Chroma 的 embedding function）。

## 测试环境

所有数据均在以下环境测得，更换硬件或部署形态后需重测：

- CPU：Intel Core i5-1135G7（4 核 8 线程）
- 内存 15.7 GB / NVMe SSD / Windows 10
- **推理设备：CPU-only**（`torch 2.14.1+cpu`，无可用 GPU）
- MySQL 8 + Redis + Chroma 1.5.9（persistent 本地模式）
- Celery `--pool=solo -c 1`（单 worker 串行）
- **LLM 调用已排除**，测的是系统自身耗时，不含外部 API 的网络等待
