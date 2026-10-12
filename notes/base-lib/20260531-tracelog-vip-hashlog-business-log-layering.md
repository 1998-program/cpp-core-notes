# TraceLog / VIP / HashLog 采样与业务日志分层

> 日期：2026-05-31  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的 `sun.base_lib` 计划项  
> 范围：GRC/GRG 服务入口对 `is_debug`、`is_vip_cuid`、`is_hash_log` 的注入，`Util::print_trace_data()` 的 DEBUG_TRACE 输出，以及 GraphMonitor/NEWDAPPER/业务明细日志的分层。  
> 内网文档：今日计划未提供 KU URL/doc-id；当前未执行全站 KU 正文检索，本文以代码库检索结果为主，业务语义需人工补充。

---

## 0. 架构全景图

<div class="arch-wrapper trace-arch"><style scoped>.trace-arch{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #dbe4ee;border-radius:14px;padding:22px;margin:16px 0;color:#172033}.trace-arch .arch-title{font-size:18px;font-weight:900;margin-bottom:14px}.trace-arch .arch-layer{border-radius:10px;padding:14px;margin:10px 0}.trace-arch .user{background:#dbeafe;border-left:5px solid #2563eb}.trace-arch .application{background:#dcfce7;border-left:5px solid #16a34a}.trace-arch .ai{background:#fef3c7;border-left:5px solid #d97706}.trace-arch .data{background:#fce7f3;border-left:5px solid #db2777}.trace-arch .infra{background:#ede9fe;border-left:5px solid #7c3aed}.trace-arch .arch-layer-title{font-size:13px;font-weight:800;margin-bottom:8px}.trace-arch .arch-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.trace-arch .arch-box{background:rgba(255,255,255,.86);border:1px solid rgba(15,23,42,.08);border-radius:8px;padding:9px;font-size:12px;line-height:1.35}.trace-arch .arch-box.highlight{border:2px solid #d97706;background:#fff7ed;font-weight:800}.trace-arch small{display:block;color:#64748b;margin-top:3px}</style><div class="arch-title">Trace / VIP / HashLog 在 Graph-Engine 请求中的分层</div><div class="arch-layer user"><div class="arch-layer-title">① Request Sampling Inputs</div><div class="arch-grid"><div class="arch-box">`common_info.is_debug()`<small>端上或上游显式 debug</small></div><div class="arch-box">`vip_id_dict` / `d_target_cuid`<small>白名单用户或 UID/CUID</small></div><div class="arch-box">`logid % 100`<small>HashLog 低比例采样</small></div><div class="arch-box">UA / flow_loc / SID<small>决定图与实验日志上下文</small></div></div></div><div class="arch-layer application"><div class="arch-layer-title">② Service Entry Injection</div><div class="arch-grid"><div class="arch-box highlight">GRC `GRCSessionContext::init()`<small>计算 debug/vip/hash/new_user</small></div><div class="arch-box">GRC `emit_common_data()`<small>写入 GraphData</small></div><div class="arch-box highlight">GRG `fill_basic_data_for_graph()`<small>写入 is_vip_cuid / is_debug</small></div><div class="arch-box">GRG `Context`<small>保留 is_hash_log 接口</small></div></div></div><div class="arch-layer ai"><div class="arch-layer-title">③ Graph Processor Trace Producers</div><div class="arch-grid"><div class="arch-box">`trace_data_pb`<small>processor 写入 TraceInfo</small></div><div class="arch-box">`ftrace.cpp`<small>调权因子与 LCN 轨迹</small></div><div class="arch-box">Adjust Engine<small>VIP 精排因子 DEBUG_TRACE</small></div><div class="arch-box">GraphMonitor<small>关键路径与 vertex cost</small></div></div></div><div class="arch-layer data"><div class="arch-layer-title">④ Log Sinks / Observability</div><div class="arch-grid"><div class="arch-box highlight">`DEBUG_TRACE`<small>[FEED-TRACE] JSON trace</small></div><div class="arch-box">`REQUEST_DETAIL` / `RESPONSE`<small>VIP 请求/响应明细</small></div><div class="arch-box">`NEWDAPPER`<small>final_path 长尾路径</small></div><div class="arch-box">Bvar / SIA<small>大小、耗时与模块归因</small></div></div></div></div>

---

## 1. 关键结论

Trace 日志不是一个单点开关，而是由 **采样标记注入 → GraphData 传播 → processor 写 trace → service 尾部统一 flush** 组成的链路：

1. **GRC 的采样入口最完整**：`GRCSessionContext::init()` 同时计算 `is_vip_cuid`、`is_hash_log`、`is_debug`、`is_new_user`，再由 `emit_common_data()` 注入到图中。
2. **GRG 偏向服务入口日志与最终路径日志**：`fill_basic_data_for_graph()` 注入 `is_vip_cuid` 和 `is_debug`；`Context` 有 `is_hash_log` 字段接口，但当前服务入口未看到对 `is_hash_log` 的填充。
3. **DEBUG_TRACE 是结构化 trace 的统一落点**：GRC/GRG 的 `Util::print_trace_data()` 都将 `trace_data_pb` 转 JSON 后写入 `DEBUG_TRACE`，并在写完后 `Clear()`，避免对象池复用导致串包。
4. **VIP 日志更重，HashLog 更轻**：VIP 会触发请求/响应明细、精排因子等重日志；HashLog 主要用于抽样打开更多 ftrace/调权因子观察，避免全流量成本。

---

## 2. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam sequenceMessageAlign center
skinparam ParticipantPadding 18

title TraceLog / VIP / HashLog 注入与 flush 时序

actor Upstream as U
participant "GRC/GRG Service" as S
participant "Session/Request Context" as C
participant "GraphData" as D
participant "Graph Processors" as P
participant "trace_data_pb" as T
participant "Util::print_trace_data" as F
participant "Log Sink" as L

U -> S: query(request, response, done)
S -> C: read uid/cuid/baiduid/logid/is_debug
alt GRC
  C -> C: vip_id_dict 命中 => is_vip_cuid=1
  C -> C: logid % 100 < FLAGS_log_hash_flow => is_hash_log=1
  C -> C: FLAGS_is_debug_switch & request.is_debug => is_debug=1
else GRG
  S -> S: is_hit_vip(cuid, uid)
  S -> C: FLAGS_is_debug_switch && request.is_debug
end
S -> D: emit is_vip_cuid / is_hash_log / is_debug / is_new_user
D -> P: processors read context flags
P -> T: append TraceInfo / ftrace / adjust factors
S -> F: graph->func_each_vertex(print_trace_data)
F -> L: com_writelog("DEBUG_TRACE", [FEED-TRACE] JSON)
F -> T: Clear()
S -> L: NEWDAPPER / REQUEST_DETAIL / RESPONSE / GraphMonitor
S -> D: graph->reset()
@enduml
```

### GRC 入口与采样

| 机制 | 证据 | 说明 |
|---|---|---|
| VIP 命中 | `src/common/session_context.cpp:62-70` | 从 `vip_id_dict` 读取白名单，命中 CUID 或 UID 时置 `_is_vip_cuid = 1`。 |
| HashLog 采样 | `src/common/session_context.cpp:21-23`, `src/common/session_context.cpp:72-74` | `FLAGS_log_hash_flow` 默认 5，即 `logid % 100 < 5` 的低比例抽样。 |
| Debug 开关 | `src/common/session_context.cpp:21`, `src/common/session_context.cpp:72-75` | `FLAGS_is_debug_switch & req_common_info.is_debug()` 共同决定。 |
| 图数据注入 | `src/service/grc_service.cpp:129-148` | 将 `is_debug`、`is_vip_cuid`、`is_hash_log`、`is_new_user`、`ExpInfo` 写入 GraphData。 |
| 尾部 flush | `src/service/grc_service.cpp:213-220`, `src/service/grc_service.cpp:345-440` | Debug/VIP/HashLog 命中时打印 trace；同时输出 GraphMonitor/NEWDAPPER。 |

### GRG 入口与日志

| 机制 | 证据 | 说明 |
|---|---|---|
| VIP 命中 | `src/service/grg_service.cpp:153-154`, `src/service/grg_service.cpp:361-374` | 通过 `d_target_cuid` common dict 分别查 CUID/UID。 |
| Debug 注入 | `src/service/grg_service.cpp:162-163` | `FLAGS_is_debug_switch && request->common_info().is_debug()`。 |
| Trace flush | `src/service/grg_service.cpp:302-305`, `src/util/util.cpp:125-158` | 每次打印服务日志后对所有 vertex 执行 `Util::print_trace_data()`。 |
| VIP 响应明细 | `src/service/grg_service.cpp:295-300` | VIP 用户会将 response JSON 写入 `RESPONSE`。 |
| NEWDAPPER 路径 | `src/service/grg_service.cpp:326-355` | 仅 UA 85/87/123 上传/打印 graph path 与 vertex cost。 |

---

## 3. 配置/结构信息图

```infographic
infographic list-grid-badge-card
data
  title Trace 采样信号速览
  desc 哪些信号会打开更重的日志路径
  items
    - label is_debug
      desc 上游请求 debug 且服务 flag 开启；用于 microvideo_debug appId
      icon mdi/bug-check
    - label is_vip_cuid
      desc 白名单 CUID/UID；触发请求/响应明细与调权因子日志
      icon mdi/account-star
    - label is_hash_log
      desc GRC 中按 logid 百分比抽样；默认约 5%
      icon mdi/dice-5
    - label is_new_user
      desc GRC 从 first_visit_info 解析；参与 trace scene_type
      icon mdi/account-plus
```

```infographic
infographic sequence-timeline-simple
data
  title 一次请求的可观测层级
  desc 从轻量 NOTICE 到重型 DEBUG_TRACE 的递进
  items
    - time L0
      label NOTICE / BLOG
      desc logid、ua、sid、cost、errno、graph_name 等基础日志
    - time L1
      label SIA / Bvar
      desc 模块耗时、请求/响应大小、RPC 耗时
    - time L2
      label GraphMonitor / NEWDAPPER
      desc final_path、vertex cost、长尾路径定位
    - time L3
      label DEBUG_TRACE
      desc TraceInfo JSON、调权因子、LCN/ftrace 明细
    - time L4
      label VIP Detail
      desc REQUEST_DETAIL / RESPONSE 全量 JSON，成本最高
```

---

## 4. 关键实现细节

### 4.1 `DEBUG_TRACE` 的生命周期

GRC 与 GRG 的 `Util::print_trace_data()` 逻辑高度相似：遍历 `trace_data_pb`，过滤无数据项，调用 `ProtoMessageToJson()`，格式化 `[FEED-TRACE]` 行，写入 `DEBUG_TRACE`，最后 `Clear()`。这里的 `Clear()` 很关键，因为 Graph 对象池复用后，如果 trace PB 没有清空，下一次请求可能携带上一轮的 trace 数据。

- GRC：`src/util/util.cpp:147-186`
- GRG：`src/util/util.cpp:125-158`

### 4.2 VIP 与 HashLog 的成本差异

VIP 日志面向定向排查，允许输出较大的 request/response JSON 和精排因子。HashLog 面向抽样排查，适合全量线上低比例观测。不要把 VIP 级别的明细直接搬到 HashLog 分支，否则会把低比例采样变成高成本日志放大器。

### 4.3 GRC 特有的调权因子输出

`ftrace.cpp` 在 debug/hash/vip/new_user 任一命中时，会补充 LCN、topic/category/author/tag/ip/vector 等调权轨迹；UA 87 还会输出若干排序位置 trace。该文件属于策略排查的“深水区”，适合针对单 logid 或 VIP 用户定点分析。

---

## 5. Pitfalls 卡片

<div class="card-frame trace-card"><style scoped>.trace-card{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:18px 0}.trace-card .card{background:#f5f7fa;border:1px solid #d8e0ea;border-radius:18px;padding:24px;color:#1f2937}.trace-card .card-meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800}.trace-card .card-title{font-size:28px;line-height:1.1;font-weight:900;margin:8px 0 12px}.trace-card .card-grid{display:grid;grid-template-columns:2fr 1fr;gap:14px}.trace-card .card-panel{background:rgba(255,255,255,.78);border-top:4px solid #3d5a80;border-radius:10px;padding:14px;font-size:14px;line-height:1.65}.trace-card .card-panel strong{color:#172033}.trace-card .card-tag{display:inline-block;background:#e0e7ff;color:#3730a3;border-radius:999px;padding:3px 8px;margin:3px;font-size:12px;font-weight:700}</style><div class="card"><div class="card-meta">Pitfalls · Trace Logging</div><div class="card-title">最危险的不是没日志，而是日志级别混在一起</div><div class="card-grid"><div class="card-panel"><strong>复用风险：</strong>GraphPool 复用要求 trace PB 在打印后清空；否则 DEBUG_TRACE 会串到下一次请求。<br><strong>成本风险：</strong>VIP 明细可能包含完整 request/response JSON，不能扩大到普通 hash 采样。<br><strong>语义风险：</strong>GRG `Context` 有 `is_hash_log` 字段接口，但当前入口未看到显式填充，排查时不要假设所有 processor 都能读到有效 HashLog 标记。</div><div class="card-panel"><span class="card-tag">Clear trace PB</span><span class="card-tag">Limit VIP JSON</span><span class="card-tag">Check GraphData</span><span class="card-tag">Use logid</span></div></div></div></div>

---

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title Trace/VIP/HashLog 排查 checklist
  desc 从入口标记到最终日志落点逐层确认
  items
    - label 确认请求 logid、ua、uid/cuid/baiduid
      desc 用于判断 hash 采样、VIP 命中和 graph_name
      done true
    - label 检查 GRCSessionContext 或 GRG fill_basic_data 注入
      desc 确认 GraphData 中 is_debug/is_vip_cuid/is_hash_log 是否存在
      done true
    - label 定位 processor 是否写入 trace_data_pb
      desc 只有写入 TraceInfo 的 vertex 才会出 DEBUG_TRACE
      done false
    - label 检查 service 尾部是否调用 print_trace_data
      desc GRC 有条件打印，GRG 在 print_log 中统一打印
      done true
    - label 检查 trace PB 是否 Clear
      desc 防止 GraphPool 复用串日志
      done true
    - label 区分 DEBUG_TRACE、NEWDAPPER、REQUEST_DETAIL、RESPONSE
      desc 不同日志名对应不同成本和排查用途
      done false
```

---

## 7. 证据来源

- `src/common/session_context.cpp:21-23`：GRC debug/hash flag 定义。
- `src/common/session_context.cpp:62-75`：GRC VIP、HashLog、Debug、NewUser 计算。
- `src/service/grc_service.cpp:129-148`：GRC 将日志标记注入 GraphData。
- `src/service/grc_service.cpp:213-220`：GRC 条件触发 trace 打印与 graph reset。
- `src/service/grc_service.cpp:345-440`：GRC VIP 明细、GraphMonitor、NEWDAPPER、Dapper 上传。
- `src/processor/new_adjust/ftrace.cpp:64-119`：GRC ftrace 在 debug/hash/vip/new_user 下输出调权轨迹。
- `src/util/util.cpp:147-186`（GRC）：DEBUG_TRACE 输出与 trace PB 清理。
- `src/service/grg_service.cpp:153-163`：GRG VIP/debug 注入。
- `src/service/grg_service.cpp:295-305`：GRG VIP response 与 trace 打印。
- `src/service/grg_service.cpp:326-355`：GRG NEWDAPPER/GraphMonitor/Dapper。
- `src/service/grg_service.cpp:361-374`：GRG VIP dict 命中逻辑。
- `src/util/util.cpp:125-158`（GRG）：DEBUG_TRACE 输出与 trace PB 清理。

---

## 七、业务代码库适配分析
> **分析时间**：2026-06-20T18:33:41.034361
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 与 `feeda-mv-grc` 都已经具备 Trace / VIP / HashLog 分层日志机制的基础。其中，VIP CUID 相关逻辑在两个代码库中均已有落点：`plugin/vip_cuid.cpp` 可作为现有白名单命中逻辑的参考实现；`feeda-mv-grc` 还在 `util/util.cpp`、`initializer/global.h` 中发现相关使用，说明 GRC 侧的链路更完整，已经覆盖了入口采样、GraphData 注入、Trace flush 等关键环节。

- 从规模上看，两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 使用量都很大，尤其是 `feeda-mv-grc`：`std::vector` 达 8426 次、`std::string` 达 7150 次、`std::unordered_map` 达 2833 次。这说明日志、GraphData、上下文、processor 中存在大量可承载采样标记和 trace 数据的业务结构。迁移潜力主要不在于替换基础容器，而在于**统一采样标记的传播语义、降低重日志成本、修复 GRG HashLog 注入缺口、规范 trace PB 生命周期**。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现目标技术相关文件：
  - `plugin/vip_cuid.cpp`
    - 说明 GRG 侧已经有 VIP CUID 识别能力，可作为后续统一 VIP 采样逻辑的参考实现。
    - 当前技术笔记中提到 GRG 的 VIP 命中主要在 `src/service/grg_service.cpp:153-154`、`src/service/grg_service.cpp:361-374`，通过 `d_target_cuid` common dict 分别检查 CUID / UID。

- 现有标准库使用规模：
  - `std::vector`：1969 次，分布在 356 个文件。
  - `std::string`：2443 次，分布在 425 个文件。
  - `std::unordered_map`：734 次，分布在 205 个文件。

- 典型业务场景：
  - `model/model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
    - 该类接口面向候选集预测，属于高频路径，不建议直接在模型预测接口中加入重型日志。
    - 如果要补充 trace，应通过上游 `GraphData` 或轻量上下文字段传递采样标记，在命中 debug / vip 时才记录关键因子。

  - `model/paddle_model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) {
        return 0;
    }
    ```
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                general_predict::PredictSample* predict_sample = nullptr,
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```
    - 模型预测路径的 `candidate_vec` 可能很大，不适合在 HashLog 分支中输出完整候选集。
    - 更适合输出候选数量、topN 关键字段、模型耗时、命中特征摘要等低成本 trace。

- 当前适配判断：
  - GRG 已具备 `is_vip_cuid` 与 `is_debug` 注入能力。
  - `Context` 中虽然有 `is_hash_log` 字段接口，但技术笔记显示服务入口未看到显式填充，因此 GRG 的 HashLog 链路存在补齐空间。
  - `Util::print_trace_data()` 已作为 DEBUG_TRACE 输出落点，应继续复用，避免各 processor 自行写散乱日志。

#### feeda-mv-grc：召回汇聚服务

- 已发现目标技术相关文件：
  - `util/util.cpp`
    - 可作为 `DEBUG_TRACE` 输出与 `trace_data_pb.Clear()` 的参考实现。
    - 技术笔记中 GRC 对应位置为 `src/util/util.cpp:147-186`，负责将 `trace_data_pb` 转 JSON 后写入 `DEBUG_TRACE`，随后清空 PB。
  - `initializer/global.h`
    - 可能包含全局配置、dict、flag 或初始化对象，适合作为 VIP dict、HashLog flag 等全局采样配置的维护入口。
  - `plugin/vip_cuid.cpp`
    - 可作为 VIP CUID / UID 白名单命中逻辑参考。

- 现有标准库使用规模：
  - `std::vector`：8426 次，分布在 1273 个文件。
  - `std::string`：7150 次，分布在 1228 个文件。
  - `std::unordered_map`：2833 次，分布在 638 个文件。

- 典型业务场景：
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
    - 该文件涉及 graph vertex 依赖关系展示或调试，对 GraphMonitor / NEWDAPPER / DEBUG_TRACE 的可视化排查有天然适配价值。
    - 可考虑在 HTTP debug 页面中展示 vertex cost、final_path、trace 命中原因，但需要避免直接暴露完整 request / response。

  - `service/grc_http_service.cpp`
    ```cpp
    static std::vector<std::string> colors{"#FFB6C1", "#DC143C", "#DB7093", ...};
    ```
    - 属于调试展示辅助数据，不是核心性能路径。
    - 可作为 Graph path 可视化增强点，但不应混入业务采样逻辑。

  - `service/grc_http_service.cpp`
    ```cpp
    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
    ```
    - 该场景说明 GRC HTTP 服务已有按 query 参数控制访问或开关的能力。
    - 可考虑增加只读型 trace 查询能力，例如通过 logid 查询某次请求是否命中 debug / vip / hash，但不建议在 HTTP 参数中直接打开线上重日志。

- 当前适配判断：
  - GRC 是两套服务中链路最完整的一侧。
  - `GRCSessionContext::init()` 已负责计算 `is_vip_cuid`、`is_hash_log`、`is_debug`、`is_new_user`。
  - `emit_common_data()` 已将采样标记写入 GraphData。
  - `Util::print_trace_data()` 已统一 DEBUG_TRACE 输出和清理。
  - 后续优化重点应放在日志成本治理、trace 字段标准化、processor 写 trace 的边界控制上。

---

### 3. 💡 适用性评估与建议

- **建议 1：以 `feeda-mv-grc/util/util.cpp` 作为 DEBUG_TRACE 统一模板，避免 processor 分散写日志**
  - 适用文件：
    - `feeda-mv-grc/util/util.cpp`
    - `feeda-mv-grg/src/util/util.cpp` 或对应 `util/util.cpp`
  - 建议：
    - 两个代码库都应统一通过 `Util::print_trace_data()` flush `trace_data_pb`。
    - processor 只负责写入结构化 `TraceInfo`，不要直接调用 `com_writelog("DEBUG_TRACE", ...)`。
    - 保留打印后的 `trace_data_pb.Clear()`，这是 GraphPool / 对象池复用场景下防止串日志的关键。
  - 参考：
    - GRC 当前 `util/util.cpp` 已是较完整参考实现。
    - GRG 侧已有类似逻辑，可对齐字段过滤、JSON 格式、日志前缀 `[FEED-TRACE]`。

- **建议 2：补齐 `feeda-mv-grg` 的 HashLog 注入链路**
  - 适用文件：
    - `feeda-mv-grg/src/service/grg_service.cpp`
    - `feeda-mv-grg/plugin/vip_cuid.cpp`
    - GRG `Context` 定义文件，如 `context.h` / `session_context.h` / 业务上下文相关文件
  - 建议：
    - 当前 GRG 已有 `is_vip_cuid` 和 `is_debug` 注入，但 `Context` 虽有 `is_hash_log` 接口，入口未看到明确填充。
    - 可参考 GRC 的 `GRCSessionContext::init()`，在 GRG 服务入口按 `logid % 100 < FLAGS_log_hash_flow` 计算 `is_hash_log`。
    - 计算后写入 GraphData 或 Context，保证 downstream processor 能统一读取。
  - 注意：
    - HashLog 只应打开低成本 trace，例如候选量、耗时、命中策略名、topN 摘要。
    - 不要把 VIP 的 request / response 明细直接迁移到 HashLog 分支。

- **建议 3：在 `feeda-mv-grc/service/grc_http_service.cpp` 增强 Graph 调试可视化，但限制重日志入口**
  - 适用文件：
    - `feeda-mv-grc/service/grc_http_service.cpp`
  - 建议：
    - 该文件已经处理 graph vertex 依赖关系，例如：
      ```cpp
      std::unordered_map<std::string, std::vector<int>> depend_map;
      auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
      ```
    - 可扩展展示：
      - vertex 名称；
      - depends 关系；
      - vertex cost；
      - final_path；
      - 是否命中 debug / vip / hash；
      - trace 数据条数。
    - 这类能力适合做“只读观测”，用于快速定位图执行路径和慢节点。
  - 不建议：
    - 不建议通过 HTTP query 参数直接打开线上 DEBUG_TRACE 或 VIP RESPONSE 全量日志。
    - 如果确需临时打开，应加白名单、权限校验、过期时间和采样比例限制。

- **建议 4：在模型预测路径只记录摘要型 trace，避免放大 `std::vector<RidTmpInfoPtr>` 成本**
  - 适用文件：
    - `feeda-mv-grg/model/model.h`
    - `feeda-mv-grg/model/paddle_model.h`
  - 建议：
    - `predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos)` 是高频调用路径，不应输出完整候选集合。
    - 在 debug / vip 命中时，可记录：
      - `candidate_vec.size()`；
      - 当前 `pos`；
      - 模型名称；
      - 预测耗时；
      - topN 候选的 rid / score 摘要；
      - 是否走 cube / tensor input。
    - 对 HashLog 命中，仅建议记录候选量、耗时、错误码等低成本字段。
  - 目标：
    - 保留排查能力，同时避免日志序列化和字符串拼接成为模型预测路径的新瓶颈。

- **建议 5：统一 VIP CUID 逻辑，避免 GRC / GRG 白名单语义漂移**
  - 适用文件：
    - `feeda-mv-grg/plugin/vip_cuid.cpp`
    - `feeda-mv-grc/plugin/vip_cuid.cpp`
    - `feeda-mv-grc/initializer/global.h`
  - 建议：
    - 两个代码库都已经存在 `plugin/vip_cuid.cpp`，应将其作为 VIP 命中逻辑的统一参考。
    - 明确 CUID、UID、BAIDUID 的优先级和匹配规则。
    - 对外暴露统一函数，例如：
      - `is_hit_vip(cuid, uid)`；
      - `is_vip_cuid()`；
      - `vip_hit_reason()`。
    - 在 DEBUG_TRACE 中可以补充轻量 hit reason，例如 `hit_cuid`、`hit_uid`，但不要输出敏感用户信息全文。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：VIP 明细日志不能扩散到 HashLog**
  - VIP 日志通常包含 request / response JSON、精排因子、候选明细，成本很高。
  - HashLog 是低比例线上抽样，如果直接复用 VIP 明细逻辑，会导致日志量、序列化 CPU、磁盘 IO 被放大。
  - 建议严格区分：
    - `is_vip_cuid`：允许定向重日志；
    - `is_hash_log`：只允许摘要型 trace；
    - `is_debug`：按上游显式请求和服务开关控制。

- **风险 2：GraphPool / 对象池复用下必须清理 trace PB**
  - `Util::print_trace_data()` 中的 `trace_data_pb.Clear()` 不能删除。
  - 如果迁移或重构时遗漏清理，下一次请求可能携带上一轮 trace，造成排查误判，甚至产生用户数据串扰风险。
  - 建议为 `print_trace_data()` 增加单元测试或回归用例，覆盖连续两次请求 trace 不串包的场景。

- **风险 3：GRG 当前 HashLog 语义可能不完整**
  - 技术笔记显示 GRG `Context` 有 `is_hash_log` 字段接口，但服务入口未看到显式填充。
  - 因此在 GRG processor 中直接判断 `is_hash_log` 可能得不到预期效果。
  - 迁移前应先确认：
    - `logid` 是否稳定可用；
    - `FLAGS_log_hash_flow` 是否存在；
    - `is_hash_log` 是否写入 GraphData；
    - processor 读取的是 Context 还是 GraphData。

- **风险 4：高频路径中字符串拼接和 JSON 序列化容易成为隐性性能问题**
  - 两个代码库中 `std::string` 使用规模很大，GRC 达 7150 次，GRG 达 2443 次。
  - 如果在 processor、model predict、候选遍历中频繁构造 JSON 字符串，会显著增加 CPU 和内存分配。
  - 建议：
    - 先判断采样标记，再构造 trace 内容；
    - 优先记录结构化 PB，尾部统一转 JSON；
    - 对大 vector 只记录 size 和 topN 摘要；
    - 对普通 HashLog 禁止全量候选 dump。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
