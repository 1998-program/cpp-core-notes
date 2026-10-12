# 2026-07-04 基础库代码理解：DynamicTimeOut Controller 复用与预算生命周期

> 日期：2026-07-04｜类型：基础库笔记  
> 今日主题来源：本机未发现可用 `daily-plan` 文件；按周六 fallback 继续选择 `feed-general/framework` 的 DynamicTimeOut 框架。本次聚焦请求级 controller 复用、scene 绑定、默认值短路、分位窗口双缓冲与预算输出生命周期。已按任务要求设置 `BAIDU_CC_USERNAME=hujiyang`；当前未提供 KU URL/doc-id，内网策略背景需人工补充，本文以本地代码库检索结果为准。

## 架构全景图
<style>.arch{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #dbe4ef;border-radius:18px;padding:18px;margin:16px 0;color:#1f2937}.arch-title{font-size:22px;font-weight:850;letter-spacing:-.02em;margin-bottom:12px}.arch-grid{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px}.arch-layer{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:12px}.arch-layer h3{font-size:15px;margin:0 0 8px;color:#334155}.arch-box{border-radius:12px;background:#f1f5f9;border-left:4px solid #3d5a80;padding:10px;margin:8px 0;font-size:13px;line-height:1.55}.arch-box strong{color:#1e3a5f}.arch-note{font-size:12px;color:#64748b;line-height:1.55;margin-top:10px}</style><div class="arch"><div class="arch-title">DynamicTimeOut：请求 controller 从池化复用到 RPC timeout 输出</div><div class="arch-grid"><div class="arch-layer"><h3>初始化层</h3><div class="arch-box"><strong>配置加载</strong><br/>`dynamic_timeout.conf` 失败只 warning 并返回 0；`controller_pool` 或 stage 缺失才 FATAL。</div><div class="arch-box"><strong>Scene Map</strong><br/>每个 `[@scene]` 初始化一个 `FullLinkDynamicTimeOut`，内部维护 stage、rpc→stage、分位记录。</div><div class="arch-box"><strong>Controller Pool</strong><br/>按 `controller_pool.size` 建池；请求从池取对象，复用前必须 `clear()`。</div></div><div class="arch-layer"><h3>请求运行层</h3><div class="arch-box"><strong>绑定 scene</strong><br/>业务调用 `set_request_timeout(scene,total)`，将请求 controller 绑定到对应 `FullLinkDynamicTimeOut`。</div><div class="arch-box"><strong>默认值短路</strong><br/>全局开关、样本不足、调用方要求 default 时直接返回 stage `default_timeout`。</div><div class="arch-box"><strong>动态预算</strong><br/>计算剩余预算，按当前及后续 stage priority 排序，从历史分位中选择可装入总预算的组合。</div></div><div class="arch-layer"><h3>观测与反馈层</h3><div class="arch-box"><strong>bvar 输出</strong><br/>`dynamic_timeout_{scene}_{rpc}` 记录分配结果。</div><div class="arch-box"><strong>RPC 成本回写</strong><br/>`report_rpc_time` → `PercentileRecord`，决定下一轮分位窗口。</div><div class="arch-box"><strong>双缓冲更新</strong><br/>后台线程清空 bg buffer、填入 p9999/p999/p99/p90/p80/p50，再切换 `_buffer_index`。</div></div></div><div class="arch-note">排障顺序：先确认配置与 scene 命中，再确认请求 controller 绑定，最后解释 timeout 数值与分位窗口。</div></div>

## 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "DynamicTimeOutPlugin\ninitialize" as P
participant "dynamic_timeout.conf" as CFG
participant "ControllerPool" as POOL
participant "FullLinkDynamicTimeOut" as F
participant "Request Controller" as C
participant "RPC Wrapper" as R
P -> CFG: load(conf/plugins, dynamic_timeout.conf)
alt load failed
  CFG --> P: ret != 0
  P --> P: LOG(WARNING); return 0
else load ok
  P -> POOL: init_controller_pool(size)
  P -> F: init_scene_timeout_map(scene/stage/rpc)
  F -> F: start_update_latency_thread()
end
R -> P: get_dt_controller()
P -> POOL: get pooled object
P -> C: clear()
R -> P: set_request_timeout(scene,total_timeout)
P -> F: lookup scene
alt scene missing
  P --> R: return 0 with DEBUG
else scene found
  P -> C: set_dynamic_timeout_object(F)
  P -> C: set_total_timeout(scene,total)
  C -> C: run_time_out_algorithm()
end
R -> P: get_rpc_timeout(rpc,is_use_default)
alt flag off or req low or default requested
  C --> R: stage.default_timeout
else dynamic path
  C -> C: get_remain_time(stage)
  C -> F: run_time_out_algorithm(rpc,remain,stage_latency)
  F --> C: stage_cost[stage]
  C -> C: clamp(min_timeout,max_timeout)
  C --> R: allocated timeout
end
R -> P: report_rpc_time(rpc,cost)
P -> F: PercentileRecord << cost
@enduml
```

## 配置/结构信息图
```infographic
infographic sequence-ascending-steps
data
  title DynamicTimeOut 预算输出的 6 个关键节点
  desc 每个节点都可能导致“看似正常但实际走默认值”的结果
  items
    - label controller_pool.size
      desc GR 示例 120，GRC 示例 80；决定可并发复用的请求 controller 数量
      icon mdi/database-cog
    - label scene.name
      desc scene 未命中只返回 0；业务层可能误以为动态超时已注册成功
      icon mdi/source-branch
    - label stage.priority
      desc 当前及后续 stage 按 priority 排序，并影响剩余预算补给比例
      icon mdi/sort-ascending
    - label default/min/max
      desc 默认短路值与动态结果 clamp 边界；最终数值相同不代表路径相同
      icon mdi/tune-variant
    - label rpc_name/concurrent
      desc 写入 rpc_stage_map；并发组更新分位时取组内最大值
      icon mdi/vector-polyline
    - label PercentileRecord
      desc p9999/p999/p99/p90/p80/p50 写入双缓冲，供后续请求读取
      icon mdi/chart-bell-curve
```

## 关键实现解读

### 1. 配置加载失败不是插件初始化失败
`DynamicTimeOutPlugin::initialize()` 在 `config.load()` 失败时只打印 `fail to load dynamic_timeout conf.` 并返回 0。因此“插件注册成功”不能证明动态超时可用；必须继续确认 `controller_pool`、scene、stage 与 rpc 配置是否初始化。

### 2. Controller 是请求级状态容器，复用前必须清空
`get_dt_controller()` 从对象池取出 controller 后立即调用 `clear()`。这个动作清理上次请求的 `_stage_states`、`_scene`、`_fdtl` 等状态，是避免 Graph/线程复用下跨请求污染的关键。

### 3. Scene miss 是静默降级风险
`set_request_timeout()` 找不到 scene 时只打 DEBUG 并返回 0，不会 FATAL。业务传入错 scene 或配置缺 scene 时，下游表现可能是“动态超时没有效果”，而不是显式失败。

### 4. 默认值路径和动态路径最终可能输出同一个数
`get_rpc_timeout()` 的短路条件包括 `FLAGS_is_use_dynamic_timeout == 0`、请求量低于 `FLAGS_req_num_limit`、调用方传 `is_use_default=true`。即使进入动态路径，最终还会经过 `min_timeout` / `max_timeout` clamp，因此仅凭 timeout 数值不能判断算法是否生效。

### 5. 分位窗口是反馈闭环，不是实时全量统计
后台线程读取每个 rpc 的 `PercentileRecord`，填充 p9999/p999/p99/p90/p80/p50；并发 stage 取同 stage 下所有 rpc 的分位最大值。这样能保护慢 RPC 不被平均掩盖，但也会让单个异常 RPC 抬高整个并发 stage 的预算基线。

## Pitfalls 卡片
<style>.card{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f7fa;border:1px solid #d8e1ec;border-radius:18px;padding:18px;margin:16px 0;color:#1f2937}.card-meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800}.card-title{font-size:26px;font-weight:850;letter-spacing:-.02em;margin:6px 0 10px}.card-grid{display:grid;grid-template-columns:1.35fr 1fr;gap:12px}.card-panel{background:#fff;border-top:4px solid #3d5a80;border-radius:12px;padding:12px;font-size:14px;line-height:1.65}.card-panel strong{color:#1e3a5f}.card-end{font-weight:800;color:#3d5a80;margin-top:10px}</style><div class="card"><div class="card-meta">Pitfall / Controller Lifecycle</div><div class="card-title">不要把“返回 0”当成动态超时已生效</div><div class="card-grid"><div class="card-panel"><strong>容易误判：</strong>配置加载失败返回 0、scene 未命中也返回 0；业务侧如果只看调用返回码，可能误认为 controller 已绑定并开始动态分配。</div><div class="card-panel"><strong>正确证据：</strong>同时检查配置加载日志、`dynamic_timeout_{scene}_{rpc}` bvar、`get real dynamic timeout` 日志、`set_rpc_latency` 回写与 `latency update info` 双缓冲更新。</div></div><div class="card-end">∎ 先证明路径，再解释数值。</div></div>

## 调试 checklist
```infographic
infographic list-column-done-list
data
  title DynamicTimeOut Controller 与预算排障 checklist
  desc 按“配置 → 绑定 → 计算 → 回写”顺序缩小问题
  items
    - label 配置是否被真正加载
      desc 查 fail to load dynamic_timeout conf；插件存在不等于配置生效
      done true
      icon mdi/file-check
    - label controller_pool 是否初始化
      desc controller_pool.size 必须存在且大于 0；缺失会 FATAL
      done true
      icon mdi-database-check
    - label scene 是否命中
      desc scene miss 只 DEBUG 返回 0；需要查业务传入 scene 与 conf 中 name
      done true
      icon mdi-source-branch-check
    - label 请求 controller 是否 clear 后绑定
      desc 复用对象必须清空，再 set_dynamic_timeout_object 与 set_total_timeout
      done true
      icon mdi-broom
    - label RPC 是否登记到 rpc_stage_map
      desc 未登记会返回 UNSET_MAGIC_NUM，不进入 stage 分配
      done true
      icon mdi-format-list-checks
    - label 是否进入真实动态路径
      desc 排除 FLAGS、req_num_limit、is_use_default、业务 sid/字典开关
      done true
      icon mdi-toggle-switch
    - label 成本是否回写分位窗口
      desc report_rpc_time/set_rpc_latency 决定下一轮 PercentileRecord 与双缓冲
      done true
      icon mdi-chart-line
```

## 证据来源
- `framework/src/dynamic_timeout_plugin.cpp:12-22`：插件初始化、配置加载失败返回 0、池和 scene 初始化。
- `framework/src/dynamic_timeout_plugin.cpp:25-31`：从 controller pool 取对象并 `clear()`。
- `framework/src/dynamic_timeout_plugin.cpp:34-56`：按 scene 绑定请求 controller；scene miss 返回 0。
- `framework/src/dynamic_timeout.cpp:177-227`：RPC timeout 计算入口、默认值短路、动态计算、min/max clamp、未登记返回 `UNSET_MAGIC_NUM`。
- `framework/src/dynamic_timeout.cpp:230-330`：scene/stage/rpc 配置解析，构建 `_rpc_stage_map` 与 bvar key。
- `framework/src/dynamic_timeout.cpp:332-410`：按 stage priority 与历史分位组合分配预算。
- `framework/src/dynamic_timeout.cpp:412-480`：后台更新双缓冲分位窗口。
- `feeda-mv-gr/conf/plugins/dynamic_timeout.conf:1-37`：GR `controller_pool` 与 `micro_feed` scene 示例。
- `feeda-mv-grc/conf/plugins/dynamic_timeout.conf:1-74`：GRC `default` scene 与多 RPC 并发 stage 示例。
- `feeda-mv-gr/src/util/util.cpp:571-590`：业务封装读取字典/sid，决定是否传 `is_use_default=false` 进入真实动态路径。

## 需人工补充
- KU/周报中 `FLAGS_is_use_dynamic_timeout`、`FLAGS_req_num_limit`、sid `dynamic_timeout` 的上线节奏与 owner。
- 线上 bvar 曲线：默认值占比、动态值分布、clamp 命中率、scene miss 计数或日志采样。
