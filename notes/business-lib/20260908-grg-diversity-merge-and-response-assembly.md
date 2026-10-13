# 2026-09-08 周一业务库理解：DiversityMerge 并行切片、结果汇聚与响应组装边界
<!-- topic-nav:start -->
> 主题：`business/diversity-merge` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260619-grg-new-diversity-merge-multistream-engine.md)
<!-- topic-nav:end -->


> 日期：2026-09-08  
> 主题来源：当前 daily-plan 仍未覆盖本日；按历史候选回退到 `GRG 成本/CPU 性能优化热点复盘` + `bthread_async 批量并行模式与捕获安全`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/process/diversity_merge.cpp`、`src/process/response_function.cpp`，聚焦 extmsg 回填、并行软规则、Future 汇聚、结果生成与输出约束。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">输入组装层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/process/response_function.cpp:180-239`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先写 attachment、content、extmsg 和请求派生字段，再进入后续规则链。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">并行调权层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/process/diversity_merge.cpp:251-260` / `743-774`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">`_input_map` 预留通道后，按固定切片发射 `bthread_async` worker。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">结果回收层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`future.get()` + `output.next_all()` + `_div_nid_set.reserve()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先收敛并行结果，再生成最终 `div_result`，避免输出和中间态不同步。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">extmsg</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">DiversityMerge</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">bthread_async</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">result / response</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRG diversity merge and response assembly boundary
participant "response_function.cpp" as RF
participant "extmsg / attachment" as EXT
participant "DiversityMergeFunction" as DM
participant "bthread_async" as ASYNC
participant "Future<void, Mutex>" as FUT
participant "output.next_all()" as OUT
participant "div_result" as RES
RF -> EXT : fill attachment / content / extmsg
EXT -> EXT : SerializeToString()
RF -> RF : base64_encode()
RF -> RF : set_predictor_extmsg()
DM -> DM : reserve input_map / ignore indexes
DM -> ASYNC : spawn workers by slice
ASYNC --> FUT : future handles
DM -> FUT : future.get()
DM -> OUT : collect next_all()
OUT --> DM : result_vec
DM -> RES : push_item_to_result()
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title GRG 业务链上的 6 个关键点
  desc 这些点决定了字段回填何时结束、并行切片如何展开，以及结果何时可以安全生成
  items
    - label extmsg 组装
      desc `src/process/response_function.cpp:193-230` 先补 attachment，再读请求 pass-through
      icon mdi-archive-edit-outline
    - label 结果字段填充
      desc `src/process/response_function.cpp:233-260` 补 local_city、sample_rate、set2set_key 等请求派生数据
      icon mdi-form-textbox
    - label 输入预留
      desc `src/process/diversity_merge.cpp:232-255` 先 `reserve()` 再填 `_input_map`
      icon mdi-database-plus
    - label 并行切片
      desc `src/process/diversity_merge.cpp:749-755` 固定 `items_per_thread` 并发派发 worker
      icon mdi-call-split
    - label future 汇聚
      desc `src/process/diversity_merge.cpp:770-774` 统一等待 worker 完成
      icon mdi-vector-link
    - label 结果生成
      desc `src/process/diversity_merge.cpp:808-814` 通过 `output.next_all()` 组装最终结果
      icon mdi-playlist-check
```

## 3. 代码链路拆解
### 3.1 响应组装先于并行调权，字段要在序列化前一次写完
- `src/process/response_function.cpp:193-206`：先处理 `_pass_though_response->mutable_attachment()`，把短剧合集的 `duanju_7d_tgi`、`heji_7d_tgi` 填进去。
- `src/process/response_function.cpp:210-231`：再写 `content_ptr`、`extmsg`、`sample_dnn_q`，并读取 `red_point_info_pass_through()` 作为 `recall_by` 输入。
- `src/process/response_function.cpp:233-260`：继续补 `local_city`、`local_city_ml`、`sample_rate` 等派生字段。这个顺序说明响应组装的边界在“字段写完”之前，不能先序列化后回头补。

### 3.2 并行切片的核心不在线程数，而在输入预留与边界划分
- `src/process/diversity_merge.cpp:232-255`：先把 `_ignore_rule_indexes`、`_ignore_loads_indexes` 做 `reserve()`，再构建 `_input_map`，说明输入侧的容量契约是显式的。
- `src/process/diversity_merge.cpp:749-755`：根据 `total_items` 和 `concurrent_num` 算 `items_per_thread`，随后把每段切片交给 `bthread_async`。worker 只负责自己那一段，不碰其他区间。
- `src/process/diversity_merge.cpp:751-767`：lambda 捕获了 `effect_input`、`general_adjust_rule_operators` 和 `exec_context`，因此这些对象的生命周期和只读/局部写语义必须保持稳定。

### 3.3 future 汇聚后才进入结果生成，reserve 是输出契约的一部分
- `src/process/diversity_merge.cpp:770-774`：`future.valid()` 后统一 `get()`，先确保所有并行段都结束，再向下走。
- `src/process/diversity_merge.cpp:808-811`：`_div_nid_set.reserve(result_num + effect_input_size)` 之后才调用 `output.next_all()`，说明结果生成前必须先把输出容量准备好。
- `src/process/diversity_merge.cpp:811-814`：`next_all()` 返回的 `result_vec` 再逐项拆解到 `div_result`，这里的输出不是边算边出，而是收敛后一次性组装。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">并行边界错一次，结果集会安静地漂移，不一定立刻崩</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`bthread_async` 捕获了外层输入和执行上下文，若 `future.get()` 前把这些对象提前释放，异常不会总是马上暴露，但结果会间歇性损坏。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`SerializeToString()` 之后再改 `extmsg` 的字段，会让 `predictor_extmsg` 和内存对象不一致。所有字段必须先回填完，再进入编码输出。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 排查顺序：attachment/content/extmsg → SerializeToString → base64 → predictor_extmsg → reserve → async 切片 → future.get() → output.next_all()</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title GRG 业务链排查清单
  desc 适用于 extmsg 缺字段、predictor_extmsg 不一致、并行任务悬挂、结果顺序漂移和 reserve 不足
  items
    - label 检查字段顺序
      desc 所有业务字段必须在 `SerializeToString()` 前写完
      done true
    - label 检查 base64 输出
      desc `predictor_extmsg` 要和序列化后的 payload 一致
      done true
    - label 检查输入预留
      desc `_input_map.reserve()` 和忽略索引的 reserve 要先于填充
      done true
    - label 检查 async 切片
      desc `items_per_thread` 与 `total_items` 要匹配
      done true
    - label 检查 future 收敛
      desc 所有有效 future 都要 `get()` 完
      done true
    - label 检查结果生成
      desc `output.next_all()` 之后再组装 `div_result`
      done true
```

## 6. 证据来源
- `src/process/response_function.cpp:193-260`
- `src/process/response_function.cpp:4218-4294`
- `src/process/diversity_merge.cpp:232-255`
- `src/process/diversity_merge.cpp:749-774`
- `src/process/diversity_merge.cpp:808-814`

## 7. 说明
当前运行环境没有 2026-09-08 的 daily-plan 文件；本笔记基于历史候选回退生成，KU 正文未读取，业务背景需人工补充。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260908-grg-diversity-merge-and-response-assembly.md)。
