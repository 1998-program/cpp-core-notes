# DynamicTimeOutPlugin 动态超时注入与场景映射

- 日期：2026-06-05
- 代码库：feeda-mv-grc / feeda-mv-grg
- 本文定位：服务入口如何把上游 timeout 转换为图运行期 FrameworkContext 中可被 RPC plugin 消费的 DTController，以及 GRC/GRG 场景映射差异。
- 内网检索：本次 cron 未执行额外全文检索；如需和 KU 规范/稳定性复盘对齐，需人工补充。

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<style>.arch-title{font-size:22px;font-weight:800;margin-bottom:6px;color:#23364f}.arch-sub{font-size:13px;color:#607085;margin-bottom:14px}.arch-layer{border:1px solid #cbd5e1;border-radius:12px;margin:10px 0;padding:12px;background:#fff}.arch-layer h3{margin:0 0 10px 0;font-size:15px;color:#334155}.arch-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.arch-box{border-radius:10px;padding:10px;border:1px solid #dbe3ef;background:#f8fafc;min-height:54px}.arch-box b{display:block;font-size:13px;color:#24364b}.arch-box span{display:block;font-size:12px;color:#64748b;margin-top:4px;line-height:1.35}.highlight{background:#e8f1ff;border-color:#8fb7e8}.warn{background:#fff7ed;border-color:#fdba74}.data{background:#f0fdf4;border-color:#86efac}.arrow{font-size:18px;text-align:center;color:#64748b;padding-top:18px}</style>
<div class="arch-title">动态超时全景：入口注入，图内消费，结束反馈</div>
<div class="arch-sub">核心结论：GRC 固定使用 scene=default；GRG 按 graph_name 选择 scene，但 news_updates_dibar 被归并到 short_micro_video。</div>
<div class="arch-layer"><h3>1. 服务入口层</h3><div class="arch-grid"><div class="arch-box highlight"><b>GRC query/run</b><span>grc_service.cpp:151-211<br/>run 中申请 DTController</span></div><div class="arch-box highlight"><b>GRG query</b><span>grg_service.cpp:35-89<br/>query 中申请 DTController</span></div><div class="arch-box"><b>上游 timeout</b><span>cntl->timeout_ms()<br/>common_info.dynamic_timeout()</span></div><div class="arch-box warn"><b>兜底值</b><span>GRC: 700ms<br/>GRG: 800ms</span></div></div></div>
<div class="arch-layer"><h3>2. DynamicTimeOutPlugin 层</h3><div class="arch-grid"><div class="arch-box"><b>get_dt_controller()</b><span>从 controller_pool 获取本请求控制器</span></div><div class="arch-box"><b>set_request_timeout()</b><span>按 scene + 总预算计算每个 stage/rpc 的预算</span></div><div class="arch-box data"><b>配置：GRC</b><span>dynamic_timeout.conf<br/>default/spark/guanzhu</span></div><div class="arch-box data"><b>配置：GRG</b><span>plugins/dynamic_timeout/*.conf<br/>short_micro_video/news_updates_dibar</span></div></div></div>
<div class="arch-layer"><h3>3. 图运行与 RPC 消费层</h3><div class="arch-grid"><div class="arch-box highlight"><b>FrameworkContext</b><span>graph_mutable_context->timeout_cntl = dt_cntl</span></div><div class="arch-box"><b>图内节点</b><span>通过 vertex context 读取 m_framework_context</span></div><div class="arch-box"><b>RPC plugin</b><span>Util::get_dynamic_timeout 或 DynamicChannel::on</span></div><div class="arch-box warn"><b>反馈</b><span>report_request_time / feedback 用于闭环</span></div></div></div>
</div>

## 1. 入口链路对比

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRC / GRG 动态超时注入时序对比
actor Upstream as U
participant "brpc Controller" as C
participant "GenericGRCService" as GRC
participant "GenericGRGService" as GRG
participant "DynamicTimeOutPlugin" as DTP
participant "Graph / FrameworkContext" as Graph
participant "Graph RPC Node" as Node
U -> C: query(request, response)
C -> GRC: GRC query()
GRC -> DTP: get_dt_controller()
GRC -> GRC: timeout = cntl.timeout_ms\nelse request.dynamic_timeout\nelse 700
GRC -> DTP: set_request_timeout(dt, "default", timeout)
GRC -> Graph: mutable_context.timeout_cntl = dt
Graph -> Node: run(end)
Node -> DTP: Util::get_dynamic_timeout(context, rpc_name)
GRC -> DTP: report_request_time(dt, total_ms)
...
C -> GRG: GRG query()
GRG -> DTP: get_dt_controller()
GRG -> GRG: graph_name = get_graph_name(ua)
GRG -> GRG: timeout = cntl.timeout_ms\nelse request.dynamic_timeout\nelse 800
GRG -> GRG: scene = graph_name\nnews_updates_dibar => short_micro_video
GRG -> DTP: set_request_timeout(dt, scene, timeout)
GRG -> Graph: mutable_context.timeout_cntl = dt
Graph -> Node: run(end, err_node)
Node -> DTP: DynamicChannel::on(cntl, dt, rpc_name)
GRG -> DTP: report_request_time(dt, graph_ms)
@enduml
```

| 对比项 | GRC | GRG | 影响 |
|---|---|---|---|
| 插件获取 | 构造函数中从 `ApplicationContext` 获取 `_dynamic_timeout_plugin` | 构造函数中获取 `_dynamic_timeout_plugin`，query 中额外判空 | 插件缺失时 GRC run 才暴露；GRG 入口更早失败 |
| controller 获取 | `run()` 内 `get_dt_controller()` | `query()` 内 `get_dt_controller()` | GRC 的 timeout 注入更靠近图运行；GRG 在填充图基础数据前完成 |
| 总预算来源 | `cntl->timeout_ms()` → `request.common_info().dynamic_timeout()` → `700` | `cntl->timeout_ms()` → `request.common_info().dynamic_timeout()` → `800` | 同样请求在两服务兜底预算不同 |
| scene 选择 | 固定 `default` | `graph_name`，但 `news_updates_dibar` 改成 `short_micro_video` | GRC 多图共用 default；GRG 场景粒度更细但存在归并 |
| 注入点 | `MutableFrameworkContext.timeout_cntl` | `MutableFrameworkContext.timeout_cntl` | 图内 Processor/RPC plugin 统一从 FrameworkContext 拿控制器 |
| 反馈 | `report_request_time(dt, request_total_ms)` | `report_request_time(dt, graph_run_ms)` | 反馈口径不同：GRC 包含更多入口/回包开销，GRG 更贴近 graph cost |

## 2. 代码证据

### 2.1 GRC：固定 default scene

关键路径：

- `src/service/grc_service.cpp:56-58`：构造函数获取 `DynamicTimeOutPlugin`。
- `src/service/grc_service.cpp:233-250`：run 中从 plugin 申请 controller，并按 `cntl->timeout_ms()` / `request.dynamic_timeout()` / `700` 选择总预算。
- `src/service/grc_service.cpp:253-269`：`scene` 固定为 `default`，调用 `set_request_timeout()` 后写入 `MutableFrameworkContext.timeout_cntl`。
- `src/service/grc_service.cpp:319-320`：图运行结束后调用 `report_request_time()`。

这意味着即使 query 入口根据 UA 选择了 `dibar_reddot`、`video_immersion`、`searchc_related`、`news_updates_dibar` 等 graph，动态超时配置仍走同一个 `default` scene。配置上的 `spark`、`guanzhu` scene 在当前入口代码中没有被直接映射到 graph_name，需要确认是否由其他环境/版本使用。

### 2.2 GRG：graph_name 到 scene 的映射

关键路径：

- `src/service/grg_service.cpp:30-33`：构造函数获取 `DynamicTimeOutPlugin`。
- `src/service/grg_service.cpp:64-67`：先按 UA 选择 graph，再获取 DTController。
- `src/service/grg_service.cpp:177-200`：在 `fill_basic_data_for_graph()` 中选择预算、映射 scene、写入 `MutableFrameworkContext.timeout_cntl`。
- `src/service/grg_service.cpp:221`：图运行结束后上报 request time。

GRG 的 scene 默认等于 `graph_name`，但 `news_updates_dibar` 被强制映射到 `short_micro_video`。从配置看 `plugins/dynamic_timeout/news_updates_dibar.conf` 存在独立 scene，但入口逻辑当前不会使用它；实际生效的是 `short_micro_video.conf`。

## 3. 配置结构信息图

infographic compare-binary-horizontal-underline-text-vs
data
  title 动态超时配置结构：GRC vs GRG
  desc 两边都使用 controller_pool + scene + stages，但入口 scene 选择策略不同
  items
    - label GRC
      desc conf/plugins/dynamic_timeout.conf；controller_pool size=80；default scene 包含 ums/embedding/recall/dup/ctr_rank 等 stage；入口固定 default
      value 1
    - label GRG
      desc conf/plugins/dynamic_timeout/global.conf include short_micro_video/news_updates_dibar；入口按 graph_name 选 scene，但 news_updates_dibar 归并 short_micro_video
      value 1

theme
  palette #3D5A80 #6B7280 #D97706

### 3.1 GRC 配置重点

- `conf/plugins/dynamic_timeout.conf:1-2`：`controller_pool.size = 80`。
- `conf/plugins/dynamic_timeout.conf:4-75`：`default` scene，window_size 480，含多个 stage：
  - priority 2：`ums`、`embedding_service`。
  - priority 0：多路召回，如 `video_micro_rec`、`video_micro_cf_new`、`q_cache_recall`。
  - priority 2：`dup_rpc`。
  - priority 1：`ctr_rank_rpc`。
- `conf/plugins/dynamic_timeout.conf:76-119`：`spark` scene。
- `conf/plugins/dynamic_timeout.conf:121-135`：`guanzhu` scene。

### 3.2 GRG 配置重点

- `conf/plugins/dynamic_timeout/global.conf:1-4`：`controller_pool.size = 80`，include `short_micro_video.conf` 和 `news_updates_dibar.conf`。
- `conf/plugins/dynamic_timeout/short_micro_video.conf:1-25`：`short_micro_video` scene，window_size 300；priority 0 覆盖 `ums_rpc` 与 `microvideo_grc_recall_rpc`，priority 1 覆盖 `user_feature_service_rpc`、`request_feature_service_rpc`。
- `conf/plugins/dynamic_timeout/news_updates_dibar.conf:1-15`：存在 `news_updates_dibar` scene，但当前入口代码把该 scene 映射为 `short_micro_video`，需确认是否为刻意复用或遗留配置。

## 4. 图内消费：不是只有入口设置就结束

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
start
:入口创建 DTController;
:根据总预算和 scene 初始化各 stage/rpc timeout;
:写入 MutableFrameworkContext.timeout_cntl;
if (图内节点发 RPC?) then (yes)
  :从 VertexContext / FrameworkContext 取 timeout_cntl;
  if (GRC Predictor 类 RPC?) then (GRC)
    :Util::get_dynamic_timeout(context, rpc_name);
    :RouterPlugin::predict(... timeout ...);
    :Util::report_timeout(context, rpc_name, cost, ret);
  else (GRG DynamicChannel)
    :DynamicChannel::on(cntl, dt_cntl, rpc_name);
    :把 cntl.timeout_ms 回填 request.dynamic_timeout;
    :DynamicChannel::feedback(cntl, dt_cntl, rpc_name);
  endif
else (no)
  :普通图计算，不消费动态超时;
endif
:服务结束 report_request_time;
stop
@enduml
```

补充证据：

- `src/plugin/parallel_predictor.cpp:39-70`：批量预测前调用 `Util::get_dynamic_timeout(context, rpc_name)`，异步完成后 `Util::report_timeout()`。
- `src/processor/reddot/reddot_index_recall.cpp:71-77`：通过 `vertext_context.m_framework_context->timeout_cntl` 传给 URS plugin。
- `src/plugin/grc.cpp:35-51`（GRG）：`DynamicChannel::on(cntl, dt_cntl, rpc_name)` 后，如果存在 dt_cntl，会把计算出的 `cntl.timeout_ms()` 回填到 GRCRequest 的 `common_info.dynamic_timeout`，RPC 完成后再 `feedback()`。

## 5. Pitfalls 卡片

<div class="card-frame" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:980px;margin:18px 0;">
<style>.card{background:#f4f3f0;border:1px solid #ddd6cc;border-radius:18px;padding:22px;color:#2f2f2f}.card-meta{font-size:12px;font-weight:700;letter-spacing:.08em;color:#6b7280;text-transform:uppercase}.card-title{font-size:30px;line-height:1.05;font-weight:850;letter-spacing:-.02em;margin:8px 0 14px}.card-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px}.card-panel{background:#fffaf3;border-top:4px solid #9a6b3f;border-radius:12px;padding:14px}.card-panel h4{margin:0 0 8px;font-size:15px}.card-panel p{margin:0;font-size:13px;line-height:1.65;color:#4b5563}.card-highlight{border-left:5px solid #9a6b3f;padding-left:12px;font-size:15px;line-height:1.55;background:#fff;padding-top:10px;padding-bottom:10px;border-radius:8px}</style>
<div class="card"><div class="card-meta">Pitfalls / Dynamic Timeout</div><div class="card-title">排查超时问题时，先问 scene 是否真的命中</div><div class="card-grid"><div class="card-panel"><h4>1. 配置存在 ≠ 入口使用</h4><p>GRC 配了 default/spark/guanzhu，但入口固定 default；GRG 配了 news_updates_dibar，但入口把它改成 short_micro_video。改配置前必须从 service 入口确认 scene 映射。</p></div><div class="card-panel"><h4>2. 反馈口径不同</h4><p>GRC 上报 request 总耗时，GRG 上报 graph run 耗时。跨服务比较动态超时学习效果时不能直接对齐。</p></div><div class="card-panel"><h4>3. controller 生命周期跟图复用绑定</h4><p>DTController 来自 pool，指针注入 FrameworkContext。不要在 graph reset 后持有该指针或跨请求复用。</p></div><div class="card-highlight">最容易误判的问题：看到 GRG 有 `news_updates_dibar.conf` 就以为线上在用；当前代码路径实际会走 `short_micro_video` scene。</div></div></div>
</div>

## 6. 调试 checklist

infographic list-column-done-list
data
  title 动态超时排查 Checklist
  desc 从入口、scene、配置、图内消费、反馈五个点闭环
  items
    - label 确认入口 graph_name
      desc GRC 看 ua 到 graph_name 的 if-else；GRG 看 get_graph_name()
      done true
    - label 确认 scene 映射
      desc GRC 是否仍固定 default；GRG news_updates_dibar 是否仍归并 short_micro_video
      done true
    - label 核对总预算来源
      desc cntl.timeout_ms 优先，其次 request.common_info.dynamic_timeout，最后服务兜底
      done true
    - label 检查 FrameworkContext 注入
      desc graph_mutable_context->timeout_cntl 必须在 graph->run 前完成
      done true
    - label 检查 RPC 名称和配置一致
      desc rpc_name 必须能在对应 scene 的 stage/concurrent 中找到，否则可能走默认或无动态预算
      done false
    - label 检查反馈口径
      desc 区分 report_request_time、Util::report_timeout、DynamicChannel::feedback
      done false

theme
  palette #3D5A80 #94A3B8 #D97706

## 7. 建议后续动作

1. **确认 GRG `news_updates_dibar` scene 是否应生效**：如果希望独立预算，应修改 `grg_service.cpp:186-190` 的映射；如果复用 `short_micro_video` 是预期，应删除或注释配置中的歧义说明。
2. **确认 GRC 是否需要按 graph_name 选择 scene**：当前固定 default 会让 `spark`、`guanzhu` scene 难以通过入口命中。
3. **统一监控口径**：GRC `report_request_time` 使用总请求耗时，GRG 使用 graph run 耗时；建议日志中明确字段含义，避免性能复盘时误读。
4. **对 rpc_name 做静态校验**：扫描图内 `Util::get_dynamic_timeout(context, rpc_name)` / `DynamicChannel::on(... rpc_name)` 的 rpc_name 是否全部出现在动态超时配置中。

---

## 七、业务代码库适配分析
> **分析时间**：2026-06-22T19:02:09.579247
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，`DynamicTimeOutPlugin` 已经在两个业务代码库中落地使用，但接入深度和场景映射策略存在明显差异：
  - `feeda-mv-grc` 已在 `service/grc_service.cpp` / `service/grc_service.h` 以及 `util/util.cpp` / `util/util.hpp` 中接入动态超时能力，主要表现为服务入口注入 `DTController`，图内 RPC 通过工具函数消费动态预算。
  - `feeda-mv-grg` 已在 `service/grg_service.cpp` / `service/grg_service.h` 和 `init/global.h` 中接入动态超时能力，入口侧根据 `graph_name` 选择 scene，但存在 `news_updates_dibar` 被强制映射到 `short_micro_video` 的特殊逻辑。

- 迁移和优化潜力主要集中在 **scene 映射一致性、配置有效性校验、RPC 名称静态校验、反馈口径统一** 四个方面。两个代码库都已经具备可参考的现有接入代码，因此不属于从零引入；更适合做 **增量治理和配置收敛**。  
  同时，扫描结果显示两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 使用规模很大，说明业务逻辑中存在大量图配置、请求上下文、RPC 参数和中间结果处理代码。虽然这些标准容器本身不是本次动态超时技术的直接替换对象，但在后续做 RPC 名称索引、scene 映射表、配置静态校验工具时，可以优先关注这些高频容器使用区域，避免引入额外性能开销。

---

### 2. 代码库详情

#### 2.1 `feeda-mv-grg`：序列生成服务

- **已发现动态超时相关文件：**
  - `service/grg_service.h`
  - `service/grg_service.cpp`
  - `init/global.h`

- **当前接入现状：**
  - `service/grg_service.cpp` 中已经在服务构造或初始化阶段获取 `DynamicTimeOutPlugin`。
  - `query()` 入口中先根据 UA 或请求信息确定 `graph_name`，再申请 `DTController`。
  - 在 `fill_basic_data_for_graph()` 中完成：
    - 总预算选择：
      - 优先使用 `cntl->timeout_ms()`
      - 其次使用 `request.common_info().dynamic_timeout()`
      - 最后使用兜底值 `800ms`
    - scene 映射：
      - 默认 `scene = graph_name`
      - 但 `news_updates_dibar` 会被强制改为 `short_micro_video`
    - 将 `DTController` 写入 `MutableFrameworkContext.timeout_cntl`
  - 图执行结束后通过 `report_request_time()` 上报图运行耗时。

- **配置与代码的匹配情况：**
  - `conf/plugins/dynamic_timeout/global.conf` include 了：
    - `short_micro_video.conf`
    - `news_updates_dibar.conf`
  - 但当前 `service/grg_service.cpp` 的映射逻辑会让 `news_updates_dibar` 实际走 `short_micro_video` scene。
  - 因此，`news_updates_dibar.conf` 当前可能处于 **配置存在但入口不可达** 的状态。

- **现有标准库使用规模：**
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- **典型业务代码位置：**
  - `model/model.h`
  - `model/paddle_model.h`

- **适合作为动态超时接入参考的代码：**
  - `service/grg_service.cpp`
    - 可作为服务入口申请 `DTController`、选择 scene、注入 `FrameworkContext` 的参考。
  - `src/plugin/grc.cpp`
    - 可作为图内 RPC 通过 `DynamicChannel::on()` 消费动态超时预算，并通过 `feedback()` 反馈结果的参考。

---

#### 2.2 `feeda-mv-grc`：召回汇聚服务

- **已发现动态超时相关文件：**
  - `util/util.cpp`
  - `util/util.hpp`
  - `service/grc_service.h`
  - `service/grc_service.cpp`

- **当前接入现状：**
  - `service/grc_service.cpp` 中已经在服务构造函数中从 `ApplicationContext` 获取 `DynamicTimeOutPlugin`。
  - 动态超时控制器在 `run()` 内申请，注入点更靠近图运行阶段。
  - 总预算选择逻辑为：
    - 优先使用 `cntl->timeout_ms()`
    - 其次使用 `request.common_info().dynamic_timeout()`
    - 最后使用兜底值 `700ms`
  - scene 当前固定为 `default`。
  - `MutableFrameworkContext.timeout_cntl` 在图运行前写入。
  - 图执行结束后调用 `report_request_time()`，但上报的是更接近完整请求总耗时的口径。

- **配置与代码的匹配情况：**
  - `conf/plugins/dynamic_timeout.conf` 中存在多个 scene：
    - `default`
    - `spark`
    - `guanzhu`
  - 但当前 `service/grc_service.cpp` 入口固定使用 `default`。
  - 因此，`spark`、`guanzhu` 是否实际生效，需要继续确认是否存在其他入口、环境或历史版本引用。就当前扫描结果看，它们在主服务入口中并不会被直接命中。

- **图内消费现状：**
  - `util/util.cpp` / `util/util.hpp` 中已有动态超时工具函数，可作为业务 RPC 取预算和反馈的统一入口。
  - `src/plugin/parallel_predictor.cpp` 中存在：
    - `Util::get_dynamic_timeout(context, rpc_name)`
    - `Util::report_timeout(context, rpc_name, cost, ret)`
  - `src/processor/reddot/reddot_index_recall.cpp` 中通过 `vertext_context.m_framework_context->timeout_cntl` 将控制器传给下游 plugin。

- **现有标准库使用规模：**
  - `std::vector`：8432 次，分布在 1275 个文件
  - `std::string`：7153 次，分布在 1231 个文件
  - `std::unordered_map`：2833 次，分布在 638 个文件

- **典型业务代码位置：**
  - `service/grc_http_service.cpp`
    - 使用 `std::unordered_map<std::string, std::vector<int>> depend_map`
    - 使用静态 `std::vector<std::string> colors`
    - 解析 HTTP query 参数时使用多个 `std::vector<std::string>`
  - 这些位置虽然不是动态超时主链路，但体现出图依赖、HTTP 调试、配置展示等代码中字符串和容器使用较重，后续做静态校验或可视化排查工具时可以复用这些数据结构处理逻辑。

- **适合作为动态超时接入参考的代码：**
  - `service/grc_service.cpp`
    - 可作为 GRC 入口注入 `DTController` 的参考。
  - `util/util.cpp` / `util/util.hpp`
    - 可作为图内 RPC 获取动态超时预算和反馈调用结果的统一封装参考。
  - `src/plugin/parallel_predictor.cpp`
    - 可作为批量预测 RPC 接入动态超时的参考。

---

### 3. 💡 适用性评估与建议

- **建议 1：优先治理 `feeda-mv-grg` 中 `news_updates_dibar` 的 scene 映射歧义**
  - 相关文件：
    - `service/grg_service.cpp`
    - `conf/plugins/dynamic_timeout/news_updates_dibar.conf`
    - `conf/plugins/dynamic_timeout/short_micro_video.conf`
  - 当前问题：
    - 配置中存在 `news_updates_dibar` 独立 scene。
    - 但入口代码会将 `news_updates_dibar` 强制映射到 `short_micro_video`。
  - 建议处理方式：
    - 如果业务确实希望 `news_updates_dibar` 复用 `short_micro_video` 的预算策略，建议在 `service/grg_service.cpp` 映射处增加明确注释，并在 `news_updates_dibar.conf` 中标注该配置当前不生效或删除该配置，避免误导。
    - 如果业务希望 `news_updates_dibar` 独立调参，则应修改 `service/grg_service.cpp` 中的 scene 映射逻辑，让 `scene = graph_name` 直接生效。
  - 预期收益：
    - 避免配置变更后线上无效果。
    - 降低动态超时问题排查成本。

- **建议 2：评估 `feeda-mv-grc` 是否需要从固定 `default` scene 升级为按 `graph_name` 选择 scene**
  - 相关文件：
    - `service/grc_service.cpp`
    - `service/grc_service.h`
    - `conf/plugins/dynamic_timeout.conf`
  - 当前问题：
    - GRC 配置中存在 `default`、`spark`、`guanzhu` 多个 scene。
    - 但当前入口固定使用 `default`。
    - 如果 query 入口实际会路由到 `dibar_reddot`、`video_immersion`、`searchc_related`、`news_updates_dibar` 等不同 graph，它们仍会共享同一套动态超时策略。
  - 建议处理方式：
    - 在 `service/grc_service.cpp` 中将 scene 选择逻辑从固定 `"default"` 抽成函数，例如：
      - `get_dynamic_timeout_scene(graph_name, request)`
    - 初期可以保持默认返回 `"default"`，只对白名单 graph 开启独立 scene。
    - 对 `spark`、`guanzhu` 做一次配置溯源，确认是否仍有线上入口依赖。
  - 预期收益：
    - 不同召回图可以根据 RPC 结构、耗时分布、降级优先级配置不同预算。
    - 避免所有 GRC 场景共用 `default` 导致调参互相影响。

- **建议 3：基于 `util/util.cpp` / `util/util.hpp` 建立 RPC 名称静态校验工具**
  - 相关文件：
    - `util/util.cpp`
    - `util/util.hpp`
    - `src/plugin/parallel_predictor.cpp`
    - `src/processor/reddot/reddot_index_recall.cpp`
    - `conf/plugins/dynamic_timeout.conf`
    - `conf/plugins/dynamic_timeout/*.conf`
  - 当前问题：
    - 图内 RPC 通过 `rpc_name` 获取动态超时预算。
    - 如果代码中的 `rpc_name` 与配置中的 stage / concurrent 名称不一致，可能会走默认预算，甚至无法获得动态预算。
  - 建议处理方式：
    - 扫描以下调用点：
      - `Util::get_dynamic_timeout(context, rpc_name)`
      - `Util::report_timeout(context, rpc_name, cost, ret)`
      - `DynamicChannel::on(cntl, dt_cntl, rpc_name)`
      - `DynamicChannel::feedback(cntl, dt_cntl, rpc_name)`
    - 将提取到的 `rpc_name` 与动态超时配置中的 stage 名称做对比。
    - 可以先输出 warning，不阻断编译；稳定后再接入 CI。
  - 预期收益：
    - 提前发现配置未命中问题。
    - 降低“改了 timeout 配置但线上没有变化”的排查成本。

- **建议 4：统一或显式标注 GRC / GRG 的 `report_request_time()` 反馈口径**
  - 相关文件：
    - `service/grc_service.cpp`
    - `service/grg_service.cpp`
  - 当前问题：
    - GRC 上报更接近完整 request 总耗时。
    - GRG 上报更接近 graph run 耗时。
    - 两者如果进入同一套动态超时学习或监控系统，容易产生口径混淆。
  - 建议处理方式：
    - 在日志字段中显式区分：
      - `request_total_cost_ms`
      - `graph_run_cost_ms`
      - `dynamic_timeout_feedback_cost_ms`
    - 如果 `DynamicTimeOutPlugin::report_request_time()` 无法区分来源，建议在调用前后的日志中补充 scene、graph_name、cost_type。
  - 预期收益：
    - 便于稳定性复盘。
    - 避免跨服务比较时误判动态超时策略效果。

- **建议 5：对 `service/grc_http_service.cpp` 这类图依赖展示代码补充动态超时配置可视化信息**
  - 相关文件：
    - `service/grc_http_service.cpp`
  - 当前代码特征：
    - 已经存在图依赖关系处理逻辑，例如：
      - `std::unordered_map<std::string, std::vector<int>> depend_map`
      - `graph_engine->get_vertexs_message(graph_name)`
      - 静态颜色列表 `std::vector<std::string> colors`
  - 建议处理方式：
    - 在 HTTP 图展示或调试页面中增加当前 graph 对应的动态超时 scene。
    - 展示每个 RPC 节点是否命中动态超时配置。
    - 对未命中的 RPC 节点标记为 warning。
  - 预期收益：
    - 排查人员可以从图视角直接看到动态超时是否生效。
    - 对 GRC 这种固定 `default` scene 的服务尤其有价值，可以快速确认 graph 与 scene 的真实关系。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：配置存在不代表线上真实生效**
  - 在 `feeda-mv-grg` 中，`news_updates_dibar.conf` 存在，但 `service/grg_service.cpp` 会把 `news_updates_dibar` 映射到 `short_micro_video`。
  - 在 `feeda-mv-grc` 中，`dynamic_timeout.conf` 中存在 `spark`、`guanzhu`，但入口固定使用 `default`。
  - 因此，后续修改配置前必须先确认服务入口的 scene 映射逻辑，否则容易出现“配置已发布但策略未变化”的问题。

- **风险 2：`DTController` 生命周期不能跨请求或跨 graph reset 复用**
  - `DTController` 来自 `controller_pool`，并通过 `MutableFrameworkContext.timeout_cntl` 注入图运行上下文。
  - 图内 Processor、RPC Plugin 只能在当前请求生命周期内使用该指针。
  - 不建议在异步回调、全局缓存、静态对象中长期保存 `timeout_cntl`。
  - 尤其需要关注：
    - `src/plugin/parallel_predictor.cpp`
    - `src/plugin/grc.cpp`
    - `src/processor/reddot/reddot_index_recall.cpp`

- **风险 3：RPC 名称不一致会导致动态预算失效**
  - 动态超时策略依赖 `rpc_name` 与配置中的 stage / concurrent 名称匹配。
  - 代码中如果存在硬编码字符串、拼接字符串、历史别名，容易造成配置无法命中。
  - 建议不要仅依赖人工检查，应增加静态扫描或启动期校验。

- **风险 4：GRC 与 GRG 的默认预算不同，迁移策略不能直接照搬**
  - GRC 兜底值为 `700ms`。
  - GRG 兜底值为 `800ms`。
  - 如果后续统一封装入口逻辑，需要保留服务级差异，不能简单抽成一个固定默认值。
  - 同时，两者反馈耗时口径也不同，动态超时学习参数不宜直接共用。

---

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
