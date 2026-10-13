# C++ 基础库与源码实践笔记

<!-- catalog-summary:start -->

按主题阅读 C++ 基础库、框架机制、业务链路和 AI 工具。现有 **375 篇资料，214 个主题入口**；同题历史版本和独立场景折叠在各主题的补充阅读中。

## 阅读目录

| 分类 | 主题数 | 资料数 | 入口 |
|---|---:|---:|---|
| C++ 基础库 | 27 | 34 | [主题目录](notes/README.md) |
| 基础框架与源码实践 | 69 | 144 | [主题目录](notes/base-lib/README.md) |
| 业务链路与契约 | 70 | 143 | [主题目录](notes/business-lib/README.md) |
| AI 工具 | 46 | 49 | [主题目录](ai-tools/README.md) |
| 历史选题与计划 | 2 | 5 | [主题目录](notes/weekly-topic-selection/README.md) |

<!-- catalog-summary:end -->

## 常见主题

- [FlatBuffers：零拷贝序列化](notes/01-flatbuffers-zero-copy.md)
- [Swiss Table：absl::flat_hash_map](notes/41-absl-flat-hash-map.md)
- [jemalloc：Arena 与内存管理](notes/24-jemalloc.md)
- [bthread：M:N 调度](notes/18-brpc-bthread.md)
- [GraphPool：请求图复用与 reset](notes/base-lib/20260827-graphpool-try-get-run-reset-lifecycle.md)
- [MultiStreamEngine：GraphDependency 绑定](notes/base-lib/20260529-exec-engine-multistreamengine-graphdependency-deep-dive.md)
- [DynamicTimeOut：全链路预算](notes/base-lib/20260725-dynamic-timeout-plugin-budget-control.md)
- [PipelineGraphFunction：并行消费](notes/base-lib/20260612-pipelinegraphfunction-parallel-consume.md)
- [GCMS/IFCS：FillMeta 正排链路](notes/business-lib/20260601-grc-gcms-ifcs-fill-meta-chain.md)

## 内容维护

同题先更新主文；新增文章需明确场景、版本或证据增量。日期不同不自动构成新主题。历史文章中的源码版本、行号、性能数字与工具命令仍需按使用场景复核。

本次整理净减少笔记正文 **6,241 行**，保留原文件路径，并给移出正文的材料附上固定版本链接。详见[治理报告](governance/REPORT.md)、[修改台账](governance/changes.json)和[贡献约定](CONTRIBUTING.md)。

```sh
python3 scripts/content_governance.py candidate --topic flatbuffers
python3 scripts/content_governance.py render
python3 scripts/content_governance.py check
```

目录以 [`governance/catalog.json`](governance/catalog.json) 为准。`pushed_history.json` 保留旧推送记录，不作为当前内容计数；旧周计划不应被自动任务循环回退使用。
