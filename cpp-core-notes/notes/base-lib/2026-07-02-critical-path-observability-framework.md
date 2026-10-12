# GRC/PCS CriticalPath 观测框架：动态图、Dapper 虚拟节点与耗时切片

> 日期：2026-07-02｜类型：基础库笔记｜主题来源：本机未发现可用 `daily-plan` 文件；按周四 fallback 选择 CriticalPath 观测框架主题。已按任务要求设置 `BAIDU_CC_USERNAME=hujiyang`；未提供 KU URL/doc-id，内网平台规范需人工补充，本文以本地代码库检索为准。

## 1. 架构全景：CriticalPath 是“图结构 + 耗时切片 + 输出通道”的薄封装

<style>.cp-arch{font-family:Inter,Arial,sans-serif;background:#f8fafc;border:1px solid #d8e2ee;border-radius:18px;padding:18px;margin:14px 0;color:#172033}.cp-title{font-size:24px;font-weight:800;letter-spacing:-.02em;margin-bottom:8px}.cp-sub{font-size:13px;color:#526274;margin-bottom:16px}.cp-layer{border:1px solid #d7e3ef;border-radius:14px;background:white;margin:10px 0;padding:12px}.cp-layer h3{margin:0 0 10px 0;font-size:15px;color:#224a74}.cp-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.cp-box{border-radius:12px;padding:10px;background:#edf6ff;border:1px solid #c6def5;min-height:58px}.cp-box b{display:block;font-size:13px;margin-bottom:5px}.cp-box span{font-size:12px;color:#526274;line-height:1.45}.cp-box.hot{background:#fff7ed;border-color:#fed7aa}.cp-box.out{background:#f0fdf4;border-color:#bbf7d0}.cp-arrow{text-align:center;color:#64748b;font-weight:700;font-size:13px;margin:2px 0}.cp-foot{font-size:12px;color:#64748b;margin-top:10px}</style><div class="cp-arch"><div class="cp-title">CriticalPath 观测框架全景</div><div class="cp-sub">从 GraphData 结构采样，到 final_path 耗时切片，再到 bvar / Dapper / monitor log 输出；核心职责是把图引擎执行路径转换成可排障信号。</div><div class="cp-layer"><h3>入口层：服务上下文与图调度节点</h3><div class="cp-grid"><div class="cp-box"><b>Session 初始化</b><span>`init_critical_path` 绑定 `GRCSessionContext`，保证每个请求拥有独立观测容器。</span></div><div class="cp-box"><b>图调度回调</b><span>`recall_processor` / `recall_data_processor` 在 GraphData 完成后填充动态图路径。</span></div><div class="cp-box"><b>PCS Dispatch</b><span>PCS 在 dispatch 末端对 `_end_graph_data` 生成 final path。</span></div><div class="cp-box hot"><b>gflag 闸门</b><span>`open_print_graph_json=false` 默认关闭，避免动态图 JSON 常态化放大。</span></div></div></div><div class="cp-arrow">↓</div><div class="cp-layer"><h3>核心层：路径提取与指标归因</h3><div class="cp-grid"><div class="cp-box"><b>GraphJsonBuilder</b><span>将 GraphData 转换为边关系 JSON，供动态拓扑定位使用。</span></div><div class="cp-box"><b>GraphPath</b><span>PCS 使用 `generate_path_info` 生成节点序列，condition 默认不统计。</span></div><div class="cp-box hot"><b>虚拟节点</b><span>GRC 扫描 final_path，识别 last_queue、DNN、pure_grc。</span></div><div class="cp-box"><b>TimeSummary</b><span>把关键切片拼成请求日志中的结构化耗时片段。</span></div></div></div><div class="cp-arrow">↓</div><div class="cp-layer"><h3>输出层：三类可观测载体</h3><div class="cp-grid"><div class="cp-box out"><b>bvar meta</b><span>`graph_dynamic_json` 写入 `feed_meta_info` / `feed_meta_info_dynamic_graph`。</span></div><div class="cp-box out"><b>Dapper</b><span>`DAPPER_DIRECT_COLLECT` 上传 last_queue、pure_grc、dnn 等虚拟节点。</span></div><div class="cp-box out"><b>LatencyRecorder</b><span>`feed_pure_grc_*` 等 bvar 记录 feed channel 视角耗时。</span></div><div class="cp-box out"><b>Monitor Log</b><span>PCS 输出 `CRITICAL_PATH` 日志，携带 sid、req_type、final_path。</span></div></div></div><div class="cp-foot">设计要点：CriticalPath 不接管业务执行，只消费 GraphMonitor/GraphData 的完成态；因此风险集中在采样闸门、节点命名约定和输出量控制。</div></div>

## 2. 核心流程：从请求态到动态图与虚拟节点

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Request as req
participant "GRCSessionContext" as sctx
participant "CriticalPath" as cp
participant "GraphData / GraphMonitor" as graph
participant "GraphJsonBuilder" as json
participant "BvarWrapper" as bvar
participant "Dapper Collector" as dapper
req -> sctx: create request context
sctx -> cp: init_critical_path / set_sctx
req -> graph: run graph schedule / recall graph
sctx -> cp: fill_graph_path(graph_name, data)
cp -> cp: check FLAGS_open_print_graph_json
cp -> json: generate_map_json(data)
json --> cp: edge_json_str
cp -> bvar: set_mbvar_meta_value(graph_dynamic_json, key, json)
req -> cp: upload_virtual_node()
cp -> graph: get_final_path()
loop final_path nodes
  cp -> cp: identify total_cost / DNN / IO last_queue / CPU_NODE
end
cp -> dapper: DAPPER_DIRECT_COLLECT(last_queue, pure_grc, dnn...)
cp -> sctx: time_summary().to_string()
@enduml
```

## 3. 关键结构信息图

```infographic
infographic list-grid-badge-card
data
  title CriticalPath 关键结构
  desc GRC 与 PCS 共享的观测语义，但输出目标不同
  items
    - label GRC::CriticalPath
      desc 持有 GRCSessionContext 指针；负责动态图 JSON、虚拟节点与 TimeSummary
      icon mdi/routes
    - label TimeSummary
      desc last_nodnn、predictor、pure_grc、real_pure_grc 等日志片段
      icon mdi/timer-outline
    - label PCS::CriticalPath
      desc 持有 final_path 列表与 pre_log；负责 CRITICAL_PATH monitor log
      icon mdi/chart-timeline-variant
    - label _dynamic_graph_json_cnt
      desc graph_name × channel_id 原子计数器；避免重复输出动态图
      icon mdi/counter
```

## 4. 代码要点

### 4.1 GRC 侧：动态图 JSON 是受控采样，不是全量链路日志

- `processor/critical_path/critical_path.cc:36-70`：`fill_graph_path` 仅在 `FLAGS_open_print_graph_json` 打开时生效。
- `processor/critical_path/critical_path.cc:46-65`：以 `graph_name` / `real_graph_name` 找 `_dynamic_graph_json_cnt`，再按 `channel_id` 控制采样次数，写入 `graph_dynamic_json`。
- `graph_schedule/recall_processor.cpp:127-128` 与 `graph_schedule/recall_data_processor.cpp:117-118`：图调度完成后同时写 GraphMonitor 与 CriticalPath。
- `service/feed.cc:668-669`、`service/feed.cc:1753-1754`：主图与 GRG stage 图也会进入同样的动态图输出路径。

### 4.2 GRC 侧：虚拟节点把 long-tail final_path 折算成排障指标

- `processor/critical_path/critical_path.cc:73-128`：扫描 `_graph_monitor.get_final_path()`，识别：
  - `total_cost`：请求总耗时；feed channel 额外写 `g_feed_latency`。
  - `last_emit_data` 为 `PredictorData` / `CVRPredictorData` / `DppData` / `LiveCVRPredictorData`：识别 DNN 节点。
  - 名称包含 `Fork` 或 `UniQueIONode` 且类型为 `IO_NODE`：识别 last_queue。
  - 类型为 `CPU_NODE`：累加 `pure_total_cost_us`。
- `processor/critical_path/critical_path.cc:129-163`：通过 `DAPPER_DIRECT_COLLECT` 上传 `last_queue`、`pure_grc`、`dnn_last_queue`、`nodnn_pure_grc`、`dnn` 等虚拟节点。
- `processor/critical_path/critical_path.cc:164-194`：写 `TimeSummary` 字符串，后续由 `service/feed.cc:1995` 拼入日志。

### 4.3 PCS 侧：final_path 更偏“图结构打印”而不是 Dapper 上报

- `common_base/critical_path/critical_path.cc:29-50`：`generate_path` 调 `GraphPath::generate_path_info(data, resolve_condition=false)`，默认不统计 condition。
- `common_base/service/dispatch.cpp:313`：dispatch 末端基于 `_end_graph_data` 生成路径。
- `common_base/critical_path/critical_path.cc:60-80`：`upload_dapper` 已构造 `PathInfo`，但真正的 `DapperDirectCollectLongTail` 被注释为“暂时不上传”。
- `common_base/critical_path/critical_path.cc:83-117`：`print` 输出 `CRITICAL_PATH` monitor log，包含 channel、sid、req_type、final_path。
- `conf/gflags.conf.template:6`：`open_print_graph_json=false`，说明动态图输出默认关闭。

## 5. 输出指标与语义边界

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title GRC vs PCS CriticalPath 输出差异
  items
    - label GRC
      desc 动态图 JSON + Dapper 虚拟节点 + TimeSummary + bvar LatencyRecorder；适合延迟归因和 long-tail 定位
      value 1
    - label PCS
      desc GraphPath final_path + CRITICAL_PATH monitor log；Dapper 上传代码存在但暂未启用
      value 1
```

## 6. Pitfalls 卡片

<style>.pit-card{font-family:Inter,Arial,sans-serif;background:#fffaf0;border:1px solid #fed7aa;border-radius:18px;padding:18px;margin:16px 0;color:#2f2417;max-width:980px}.pit-meta{font-size:12px;font-weight:800;color:#9a5b13;letter-spacing:.08em;text-transform:uppercase}.pit-title{font-size:25px;font-weight:850;letter-spacing:-.02em;margin:6px 0 12px}.pit-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px}.pit-main{font-size:14px;line-height:1.65}.pit-side{border-left:4px solid #f59e0b;padding-left:12px;font-size:13px;line-height:1.55;background:#fff7ed;border-radius:8px}.pit-tag{display:inline-block;background:#fde68a;border-radius:999px;padding:3px 8px;margin:3px;font-size:12px;font-weight:700}</style><div class="pit-card"><div class="pit-meta">Pitfall / CriticalPath</div><div class="pit-title">不要把动态图开关当成普通日志开关</div><div class="pit-grid"><div class="pit-main">`open_print_graph_json` 一旦打开，会把 GraphData 边关系序列化为 JSON 并写入 bvar meta。GRC 侧还有 `_dynamic_graph_json_cnt` 采样保护，但不同分支实现采样次数并不完全一致：一个分支使用 `<= 1`，另一个分支使用 `== 0`。排障时应先确认目标二进制所在仓库与分支，再解释“为什么只看到一条/两条动态图”。</div><div class="pit-side"><span class="pit-tag">采样次数</span><span class="pit-tag">bvar meta</span><span class="pit-tag">GraphData</span><br/>建议：只在明确 graph_name、channel_id 与复现请求后开启；不要在高流量场景长期开启。</div></div></div>

## 7. 调试 checklist

```infographic
infographic list-column-done-list
data
  title CriticalPath 调试 checklist
  items
    - label 确认入口是否初始化
      desc 检查 `common/session_context.cc:261` 是否调用 `init_critical_path(this)`
      done true
    - label 确认动态图闸门
      desc 检查 gflag `open_print_graph_json` 与 `_dynamic_graph_json_cnt` 是否注册 graph/channel
      done true
    - label 确认 final_path 来源
      desc GRC 看 `_graph_monitor.get_final_path()`；PCS 看 `GraphPath::generate_path_info`
      done true
    - label 区分 DNN 与 no-DNN
      desc GRC 以 `last_emit_data` 字面量识别 predictor，代码注释承认判断并不完全准确
      done true
    - label 检查输出通道
      desc bvar meta、Dapper、LatencyRecorder、monitor log 四个通道不要混为一谈
      done true
```

## 8. 证据来源

- `src/processor/critical_path/critical_path.cc:36-70`：GRC 动态图 JSON 生成与 bvar 写入。
- `src/processor/critical_path/critical_path.cc:73-195`：GRC 虚拟节点上传与 TimeSummary 生成。
- `src/processor/critical_path/critical_path.h:14-53`：`TimeSummary` 与 `CriticalPath` 结构。
- `src/common/session_context.cc:261`、`src/common/session_context.h:2338`：CriticalPath 在请求上下文中初始化/持有。
- `src/graph_schedule/recall_processor.cpp:127-128`、`src/graph_schedule/recall_data_processor.cpp:117-118`：图调度完成后填充 CriticalPath。
- `src/service/feed.cc:668-669`、`src/service/feed.cc:1638`、`src/service/feed.cc:1753-1754`、`src/service/feed.cc:1995`：主流程填图、上传虚拟节点、打印 TimeSummary。
- `src/common_base/critical_path/critical_path.cc:29-117`：PCS final_path、动态图与 CRITICAL_PATH 日志。
- `src/common_base/service/dispatch.cpp:313`：PCS dispatch 入口。
- `conf/gflags.conf.template:6`：PCS 默认关闭动态图输出。
