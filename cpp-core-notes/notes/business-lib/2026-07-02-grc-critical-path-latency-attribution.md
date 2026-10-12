# GRC CriticalPath 业务排障：last_queue、DNN 与 pure_grc 延迟归因

> 日期：2026-07-02｜类型：业务库笔记｜主题来源：本机未发现可用 `daily-plan` 文件；按周四 fallback 选择 GRC 主链路 CriticalPath 观测与延迟归因。已按任务要求设置 `BAIDU_CC_USERNAME=hujiyang`；当前未提供 KU URL/doc-id，业务场景口径需人工补充，本文以本地代码库检索结果为准。

## 1. 架构全景：业务排障看的是“哪段链路拖慢了请求”

<style>.biz-arch{font-family:Inter,Arial,sans-serif;background:#f8fafc;border:1px solid #d7e3ef;border-radius:18px;padding:18px;margin:14px 0;color:#172033}.biz-title{font-size:25px;font-weight:850;letter-spacing:-.02em}.biz-sub{font-size:13px;color:#526274;margin:8px 0 16px}.lane{display:grid;grid-template-columns:1fr 42px 1fr 42px 1fr;align-items:stretch;gap:8px}.lane-box{background:white;border:1px solid #d7e3ef;border-radius:14px;padding:12px}.lane-box h3{margin:0 0 8px;color:#1f4f7a;font-size:15px}.lane-box p{font-size:12.5px;line-height:1.55;color:#526274;margin:0}.lane-arrow{display:flex;align-items:center;justify-content:center;font-size:22px;color:#64748b;font-weight:900}.signal{margin-top:12px;display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.sig{border-radius:12px;padding:10px;background:#eef6ff;border:1px solid #c7def5}.sig b{font-size:13px;display:block}.sig span{font-size:12px;color:#526274}.sig.hot{background:#fff7ed;border-color:#fed7aa}.sig.ok{background:#ecfdf5;border-color:#bbf7d0}</style><div class="biz-arch"><div class="biz-title">CriticalPath 业务延迟归因全景</div><div class="biz-sub">业务侧不需要先理解所有图节点，而是先把请求耗时切成 total、last_queue、DNN、pure_grc 四类可解释信号。</div><div class="lane"><div class="lane-box"><h3>请求执行</h3><p>Feed/GRC 请求进入图调度，召回、合流、预测、排序、GRG stage 等节点产生 final_path。</p></div><div class="lane-arrow">→</div><div class="lane-box"><h3>CriticalPath 抽取</h3><p>`upload_virtual_node` 遍历 final_path，按节点名、last_emit_data、NodeType 把复杂图压缩成业务可读切片。</p></div><div class="lane-arrow">→</div><div class="lane-box"><h3>排障输出</h3><p>Dapper 虚拟节点、bvar LatencyRecorder、TimeSummary 字符串和动态图 JSON 分别服务看板、日志与图结构定位。</p></div></div><div class="signal"><div class="sig ok"><b>total_cost</b><span>请求全链路总耗时，feed channel 写 `g_feed_latency`。</span></div><div class="sig hot"><b>last_queue</b><span>最后一个 Fork / UniQueIONode 的 IO_NODE 耗时，常用于识别队列尾部阻塞。</span></div><div class="sig hot"><b>DNN</b><span>由 `PredictorData` 等 last_emit_data 识别，代表预测链路耗时。</span></div><div class="sig ok"><b>pure_grc</b><span>`total_cost - last_queue` 或 CPU_NODE 累加视角，用于剥离等待后的 GRC 主体耗时。</span></div></div></div>

## 2. 业务调用链：从服务主流程到排障信号

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "线上请求" as req
participant "service/feed.cc" as feed
participant "Graph Schedule" as graph
participant "GraphMonitor final_path" as monitor
participant "CriticalPath" as cp
participant "Dapper / bvar / log" as out
req -> feed: handle request
feed -> graph: execute main graph / recall graph / GRG stage graph
graph -> monitor: fill_graph_path(vertex.name, data)
graph -> cp: fill_graph_path(vertex.name, data, graph_name)
feed -> cp: upload_virtual_node()
cp -> monitor: get_final_path()
alt predictor path exists
  cp -> cp: set dnn_cost_us and predictor_name
  cp -> out: dnn / dnn_last_queue / dnn_pure_grc
else no predictor path
  cp -> out: nodnn_last_queue / nodnn_pure_grc
end
cp -> out: last_queue / pure_grc / feed_pure_grc_* bvar
feed -> out: append time_summary() into request log
@enduml
```

## 3. 指标结构：业务排障优先看这几组

```infographic
infographic list-grid-badge-card
data
  title CriticalPath 业务指标字典
  desc 从代码可验证的虚拟节点和日志字段出发，不依赖外部口径
  items
    - label last_queue
      desc 最后一个 Fork/UniQueIONode IO 节点；用于判断是否卡在队列/IO 尾部
      icon mdi/source-branch
    - label pure_grc
      desc 通过 total 减 last_queue 得到的主体耗时，也有 CPU_NODE 累加的 real_pure_grc 视角
      icon mdi/timer-cog-outline
    - label dnn
      desc PredictorData/CVRPredictorData/DppData/LiveCVRPredictorData 对应预测耗时
      icon mdi/brain
    - label feed_pure_grc_dnn
      desc 仅 TAB_RECOMMENDATION 写入，用于 feed channel 下 DNN 请求看板
      icon mdi/chart-line
    - label feed_pure_grc_nodnn
      desc 无 predictor 路径时的 feed channel 主体耗时
      icon mdi/chart-bell-curve
    - label graph_dynamic_json
      desc 开启动态图采样后可还原 GraphData 边关系，适合定位图结构或配置差异
      icon mdi/graph-outline
```

## 4. 排障读法

### 4.1 先判断是不是 last_queue 拖慢

- `processor/critical_path/critical_path.cc:117-123`：节点名包含 `Fork` 或 `UniQueIONode` 且 `NodeType::IO_NODE` 时更新 `last_queue_cost_us` 与 `last_queue_name`。
- `processor/critical_path/critical_path.cc:129-136`：无论是否经过 DNN，都会上传 `last_queue` 和 `pure_grc`。
- 业务含义：如果 `last_queue` 高、`pure_grc` 不高，问题更像是尾部队列/IO 等待；如果两者都高，需要继续看 CPU_NODE 累加、预测节点与召回/合流阶段。

### 4.2 再区分 DNN 与 no-DNN 请求

- `processor/critical_path/critical_path.cc:106-116`：`SlowRecallFunnelDone` 只用于判断 `call_dnn`；真正的 `dnn_cost_us` 来自 `PredictorData` / `CVRPredictorData` / `DppData` / `LiveCVRPredictorData`。
- `processor/critical_path/critical_path.cc:137-156`：按 `call_dnn` 分支上传 `dnn_last_queue` / `dnn_pure_grc` 或 `nodnn_last_queue` / `nodnn_pure_grc`。
- `processor/critical_path/critical_path.cc:157`：代码注释明确“dnn 的判断逻辑不准确”，因为长尾路径里不一定拿得到 DNN 数据。
- 业务含义：DNN/no-DNN 分桶适合趋势看板；单 PV 定位时要结合 `last_predictor_per_pv_cost` 与 predictor name，不要只看 `call_dnn`。

### 4.3 最后用 TimeSummary 回到单请求日志

- `processor/critical_path/critical_path.h:14-31`：`TimeSummary` 包含 last node、predictor、pure_grc、feed predictor/no predictor、real pure_grc 等片段。
- `processor/critical_path/critical_path.cc:164-194`：根据是否拿到 `dnn_cost_us` 写不同字段。
- `service/feed.cc:1995`：服务日志拼入 `_critical_path.time_summary().to_string()`。
- 业务含义：看板发现某类异常后，用 logid 回查单 PV，优先对照 `last_nodnn_node_per_pv_cost_ms`、`last_predictor_per_pv_cost_ms` 与 `pure_grc_cost_ms`。

## 5. 业务决策流

```infographic
infographic sequence-snake-steps-simple
data
  title CriticalPath 延迟归因流程
  desc 从看板异常到单 PV 排障的推荐顺序
  items
    - label 看 total_cost
      desc 判断是否为 GRC 总耗时异常，feed channel 可看 `g_feed_latency`
      icon mdi/numeric-1-circle
    - label 分 last_queue
      desc last_queue 高说明尾部队列/IO 等待明显，先查 Fork/UniQueIONode 对应节点
      icon mdi/numeric-2-circle
    - label 分 DNN/no-DNN
      desc 对比 dnn_pure_grc 与 nodnn_pure_grc，确认是否预测路径特异
      icon mdi/numeric-3-circle
    - label 回查 TimeSummary
      desc 用 logid 查看 per-PV 字段，定位 predictor_name 或 last_queue_name
      icon mdi/numeric-4-circle
    - label 必要时开动态图
      desc 仅复现请求开启 `open_print_graph_json`，用 graph_dynamic_json 校验图结构
      icon mdi/numeric-5-circle
```

## 6. Pitfalls 卡片

<style>.biz-pit{font-family:Inter,Arial,sans-serif;background:#f6f3f0;border:1px solid #d6c8b8;border-radius:18px;padding:18px;margin:16px 0;color:#2f2a24}.bp-meta{font-size:12px;font-weight:800;letter-spacing:.08em;color:#7c5d3d;text-transform:uppercase}.bp-title{font-size:26px;font-weight:850;letter-spacing:-.02em;margin:6px 0 12px}.bp-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:14px}.bp-main{font-size:14px;line-height:1.65}.bp-side{background:#fffaf4;border-left:4px solid #a47545;border-radius:10px;padding:12px;font-size:13px;line-height:1.55}.bp-k{display:inline-block;border:1px solid #d7b98f;border-radius:999px;padding:3px 8px;margin:3px;font-size:12px;font-weight:700;background:#fff}</style><div class="biz-pit"><div class="bp-meta">Pitfall / Latency Attribution</div><div class="bp-title">“pure_grc”不是唯一口径，先确认你看的字段来自哪条公式</div><div class="bp-grid"><div class="bp-main">代码中同时存在两类主体耗时视角：一类是 `total_cost_us - last_queue_cost_us` 写入 `pure_grc_cost_ms`、`pure_grc_predictor_cost_ms`；另一类是遍历 CPU_NODE 累加得到的 `pure_total_cost_us`，写入 `real_dnn_pure_grc_cost_ms` / `real_nodnn_pure_grc_cost_ms` 和相关 bvar。看板、日志、Dapper 名称相似，但语义不同。</div><div class="bp-side"><span class="bp-k">total-last_queue</span><span class="bp-k">CPU_NODE sum</span><span class="bp-k">DNN 分桶</span><br/>建议：周报或事故复盘中必须写明采用哪个 pure_grc 口径，避免把队列等待与 CPU 主体耗时混算。</div></div></div>

## 7. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 业务排障 checklist
  items
    - label 看 logid 是否能串起来
      desc Dapper、CRITICAL_PATH 日志、TimeSummary 都依赖 logid/cuid 关联
      done true
    - label 先查 last_queue_name
      desc 如果 last_queue 异常，定位具体 Fork/UniQueIONode，而不是先怀疑预测
      done true
    - label 校验 predictor_name
      desc DNN 单 PV 需看 `last_predictor_per_pv_cost` 中的节点名
      done true
    - label 区分 feed channel
      desc 部分 bvar 只在 `TAB_RECOMMENDATION` 下写入
      done true
    - label 动态图谨慎开启
      desc `open_print_graph_json` 会写 GraphData JSON，建议只做短时间定向采样
      done true
    - label 内网背景需人工补充
      desc 本次未使用 KU/doc-id，业务目标、SLO、owner 需人工确认
      done false
```

## 8. 证据来源

- `src/processor/critical_path/critical_path.cc:73-128`：遍历 final_path 并提取 total、DNN、last_queue、CPU_NODE。
- `src/processor/critical_path/critical_path.cc:129-163`：上传 Dapper 虚拟节点。
- `src/processor/critical_path/critical_path.cc:164-194`：生成 per-PV TimeSummary 字段。
- `src/processor/critical_path/critical_path.h:14-31`：TimeSummary 字段清单与拼接顺序。
- `src/service/feed.cc:1638`：主流程触发 `upload_virtual_node()`。
- `src/service/feed.cc:1995`：请求日志拼接 `time_summary()`。
- `src/global_initializer.cc:51-56`：`feed_pure_grc_*` 与 `feed_real_pure_grc_*` bvar 定义。
- `src/graph_schedule/recall_processor.cpp:127-128`、`src/graph_schedule/recall_data_processor.cpp:117-118`：召回图调度阶段写 CriticalPath 动态图。
- `src/service/feed.cc:668-669`、`src/service/feed.cc:1753-1754`：主图与 GRG stage 图写 CriticalPath 动态图。
