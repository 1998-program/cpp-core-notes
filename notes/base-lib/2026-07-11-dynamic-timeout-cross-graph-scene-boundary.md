# 2026-07-11 基础库代码理解：DynamicTimeOut 跨图注入与 scene 降级边界
<!-- topic-nav:start -->
> 主题：`base/dynamic-timeout` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260725-dynamic-timeout-plugin-budget-control.md)
<!-- topic-nav:end -->


> 日期：2026-07-11｜类型：基础库笔记  
> 今日主题来源：本机仅发现历史 `daily-plan-20260529.json`；按周六 fallback 继续选择 `DynamicTimeOutPlugin 动态超时注入与场景映射`。已按任务要求设置 `BAIDU_CC_USERNAME=hujiyang`；本次没有 KU URL/doc-id，内网策略背景需人工补充，以下结论来自本地代码库检索。

## 1. 架构全景图
<style>.dtarch711{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #dbe4ef;border-radius:18px;padding:18px;margin:16px 0;color:#1f2937}.dtarch711 .title{font-size:22px;font-weight:850;letter-spacing:-.02em;margin-bottom:6px}.dtarch711 .sub{font-size:13px;color:#64748b;margin-bottom:14px}.dtarch711 .grid{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px}.dtarch711 .panel{background:white;border:1px solid #e5e7eb;border-radius:14px;padding:12px}.dtarch711 .panel h3{font-size:15px;margin:0 0 8px;color:#334155}.dtarch711 .box{border-radius:12px;background:#f1f5f9;border-left:4px solid #3d5a80;padding:10px;margin:8px 0;font-size:13px;line-height:1.55}.dtarch711 .warn{border-left-color:#b45309;background:#fff7ed}.dtarch711 .risk{border-left-color:#b91c1c;background:#fef2f2}.dtarch711 strong{color:#1e3a5f}.dtarch711 .foot{font-size:12px;color:#64748b;margin-top:10px;line-height:1.55}</style><div class="dtarch711"><div class="title">DynamicTimeOut 跨服务注入：GRC 固定 default，GRG 按 graph_name 映射 scene</div><div class="sub">同一个基础库 controller，被不同业务入口用不同 scene 绑定；排障要先确认“scene 名称”而不是只看 timeout 数值。</div><div class="grid"><div class="panel"><h3>配置层</h3><div class="box"><strong>controller_pool</strong><br/>`dynamic_timeout.conf:1-2` 配置池大小 80，请求级 controller 从池中取出并注入图上下文。</div><div class="box"><strong>scene/stage/rpc</strong><br/>`default` scene 下按 stage priority、default/min/max timeout 和 rpc_name/concurrent 建模。</div></div><div class="panel"><h3>注入层</h3><div class="box"><strong>GRC Service</strong><br/>`grc_service.cpp:233-269` 获取 controller，timeout 缺省回退 700ms，固定 scene=`default`，再写入 `MutableFrameworkContext.timeout_cntl`。</div><div class="box warn"><strong>GRG Service</strong><br/>`grg_service.cpp:177-199` 以 `graph_name` 作为 scene；但 `news_updates_dibar` 会映射为 `short_micro_video` 后再绑定。</div></div><div class="panel"><h3>反馈层</h3><div class="box"><strong>图执行结束</strong><br/>`grc_service.cpp:312-320` 与 `grg_service.cpp:218-221` 在 graph run 后 report request time，给后续分位窗口提供反馈。</div><div class="box risk"><strong>观测陷阱</strong><br/>scene miss 或配置缺失可能表现为默认值/错误码/日志差异；数值相同不能证明动态路径生效。</div></div></div><div class="foot">证据路径均使用相对路径，模块根分别为 feeda-mv-grc / feeda-mv-grg。</div></div>

## 2. 核心流程图：controller 注入与请求耗时回写
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "GRC/GRG Service" as S
participant "DynamicTimeOutPlugin" as P
participant "DTController" as C
participant "Graph MutableFrameworkContext" as G
participant "Graph Run" as R
participant "Percentile Feedback" as F
S -> P: get_dt_controller()
P --> S: pooled controller
alt GRC path
  S -> S: timeout = brpc timeout / request dynamic_timeout / 700
  S -> P: set_request_timeout(controller, "default", timeout)
else GRG path
  S -> S: timeout = brpc timeout / request dynamic_timeout / 800
  S -> S: scene = graph_name
  alt graph_name == news_updates_dibar
    S -> S: scene = short_micro_video
  end
  S -> P: set_request_timeout(controller, scene, timeout)
end
P -> C: bind scene + total timeout
S -> G: timeout_cntl = controller
S -> R: graph->run(end_node)
R --> S: closure.get()
S -> P: report_request_time(controller, graph_cost_ms)
P -> F: update request-level latency statistics
@enduml
```

## 3. 配置/结构信息图
```infographic
infographic list-grid-badge-card
data
  title DynamicTimeOut 配置读法：先 scene，再 stage，再 rpc
  desc 本次检索到的 GRC dynamic_timeout.conf 展示了 default/spark/guanzhu 三类 scene 片段
  items
    - label controller_pool.size
      desc conf/plugins/dynamic_timeout.conf:1-2；请求 controller 池大小为 80
      value 80
      icon mdi/database-cog
    - label default scene
      desc conf/plugins/dynamic_timeout.conf:4-75；GRC 入口固定绑定 default
      value 1
      icon mdi/source-branch
    - label recall stage
      desc priority 0；video_micro_rec/video_micro_cf/query 等召回 RPC 并发组，default_timeout 300
      value 300
      icon mdi/vector-polyline
    - label rank stage
      desc cupai/ctr_rank 等后段 RPC 默认 180ms，min/max 形成 clamp 边界
      value 180
      icon mdi-timer-cog
    - label scene remap
      desc GRG news_updates_dibar 不按原名查配置，而转为 short_micro_video
      value 1
      icon mdi-swap-horizontal
```

## 4. 关键实现解读

### 4.1 GRC 的 scene 固定为 `default`
`grc_service.cpp:233-269` 先调用 `_dynamic_timeout_plugin->get_dt_controller()`，随后按 `cntl->timeout_ms()` → `request.common_info().dynamic_timeout()` → `700` 的顺序确定总预算；scene 固定写为 `default`。因此在 GRC 排障中，`conf/plugins/dynamic_timeout.conf:4-75` 的 default scene 是首要入口。

### 4.2 GRG 的 scene 来自 graph_name，但存在业务重映射
`grg_service.cpp:177-199` 中 `scene = graph_name`，但当 graph 为 `news_updates_dibar` 时改写为 `short_micro_video`。这意味着观察 bvar、配置或日志时，不能只按业务图名查找；尤其 Dibar 场景会落到 short_micro_video 的动态超时预算。

### 4.3 timeout controller 是图上下文的一部分
GRC 和 GRG 都通过 `graph->mutable_context<MutableFrameworkContext>()` 写入 `timeout_cntl`。图内 RPC wrapper 如果要走动态预算，依赖的是这个上下文，而不是 service 层的局部变量。

### 4.4 request time 反馈发生在 graph run 之后
GRC 在 `closure.get()` 后调用 `report_request_time(dynamic_timeout_cntl, (end_us - begin_us) / 1000)`；GRG 在 `closure.get()` 后用 `end_graph_time - start_graph_run_time` 回写。两者统计口径不同：GRC 更接近请求总耗时，GRG 更接近 graph 执行耗时。

## 5. Pitfalls 卡片
<style>.dtcard711{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f7fa;border:1px solid #d8e1ec;border-radius:18px;padding:18px;margin:16px 0;color:#1f2937}.dtcard711 .meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800}.dtcard711 .title{font-size:26px;font-weight:850;letter-spacing:-.02em;margin:6px 0 10px}.dtcard711 .grid{display:grid;grid-template-columns:1.35fr 1fr;gap:12px}.dtcard711 .panel{background:#fff;border-top:4px solid #3d5a80;border-radius:12px;padding:12px;font-size:14px;line-height:1.65}.dtcard711 .panel strong{color:#1e3a5f}.dtcard711 .end{font-weight:800;color:#3d5a80;margin-top:10px}</style><div class="dtcard711"><div class="meta">Pitfall / Scene Boundary</div><div class="title">查动态超时时，scene 名称比 graph 名称更重要</div><div class="grid"><div class="panel"><strong>典型误判：</strong>在 GRG 的 `news_updates_dibar` 场景下按原 graph_name 查配置或 bvar，可能找不到对应动态超时项，因为代码会映射到 `short_micro_video`。</div><div class="panel"><strong>排障动作：</strong>先从入口 service 确认 scene 生成规则，再看 `MutableFrameworkContext.timeout_cntl` 是否写入，最后才解释单个 RPC 的 timeout 输出。</div></div><div class="end">∎ 先证明绑定，再解释预算。</div></div>

## 6. 调试 checklist
```infographic
infographic list-column-done-list
data
  title DynamicTimeOut 注入排障 checklist
  desc 按入口、scene、上下文、反馈四步排查
  items
    - label 确认入口服务
      desc GRC 固定 default；GRG 默认 graph_name，news_updates_dibar 重映射 short_micro_video
      done true
      icon mdi-map-marker-path
    - label 确认总预算来源
      desc brpc timeout 优先，其次 request.common_info.dynamic_timeout，最后 GRC 700 / GRG 800 默认值
      done true
      icon mdi-timer-outline
    - label 确认上下文注入
      desc 查 MutableFrameworkContext.timeout_cntl 是否被写入；图内 RPC 依赖该上下文
      done true
      icon mdi-graph-outline
    - label 确认配置 scene/stage/rpc
      desc scene、stage priority、default/min/max、rpc_name/concurrent 要同时命中
      done true
      icon mdi-file-tree
    - label 确认反馈回写
      desc graph run 后是否调用 report_request_time；注意 GRC/GRG 统计口径不同
      done true
      icon mdi-chart-timeline-variant
```

## 7. 证据来源
- `src/service/grc_service.cpp:233-269`：GRC 获取 DT controller、设置 scene=`default`、注入 `MutableFrameworkContext.timeout_cntl`。
- `src/service/grc_service.cpp:312-320`：GRC graph run 后回写 request time。
- `src/service/grg_service.cpp:177-199`：GRG 动态超时 scene 绑定与 `news_updates_dibar` → `short_micro_video` 映射。
- `src/service/grg_service.cpp:218-221`：GRG graph run 后回写 graph 执行耗时。
- `conf/plugins/dynamic_timeout.conf:1-75`：controller pool、default scene、stage/rpc timeout 配置样例。

## 8. 需人工补充
- KU/周报中的动态超时策略背景、线上 bvar 命名规范、`short_micro_video` scene 在 GRG 生产配置中的完整 stage 列表。
