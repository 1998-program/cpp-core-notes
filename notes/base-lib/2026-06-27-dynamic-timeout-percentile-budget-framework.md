# 2026-06-27 基础库代码理解：DynamicTimeOut 百分位预算分配与降级边界
<!-- topic-nav:start -->
> 主题：`base/dynamic-timeout` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260725-dynamic-timeout-plugin-budget-control.md)
<!-- topic-nav:end -->


> 日期：2026-06-27  
> 今日主题来源：本机未发现可用 `daily-plan` 文件；按近期双 Markdown 工作流周六 fallback，继续选择基础库 `feed-general/framework` 的 DynamicTimeOut 框架，本次聚焦“百分位窗口如何转换成 RPC 预算”。已按要求设置 `BAIDU_CC_USERNAME=hujiyang`；未提供 KU URL/doc-id，内网策略背景需人工补充。  
> 范围：插件初始化、controller 池化、scene/stage/rpc 配置解析、默认值降级、请求剩余预算、stage priority、分位窗口双缓冲更新。

## 架构全景图
<style>.arch{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #dbe4ef;border-radius:18px;padding:18px;margin:16px 0;color:#1f2937}.arch-title{font-size:22px;font-weight:850;letter-spacing:-.02em;margin-bottom:12px}.arch-grid{display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px}.arch-layer{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:12px}.arch-layer h3{font-size:15px;margin:0 0 8px;color:#334155}.arch-box{border-radius:12px;background:#f1f5f9;border-left:4px solid #3d5a80;padding:10px;margin:8px 0;font-size:13px;line-height:1.5}.arch-box strong{color:#1e3a5f}.arch-note{font-size:12px;color:#64748b;line-height:1.55;margin-top:10px}</style><div class="arch"><div class="arch-title">DynamicTimeOut：从配置 stage 到在线百分位预算</div><div class="arch-grid"><div class="arch-layer"><h3>配置输入</h3><div class="arch-box"><strong>controller_pool</strong><br/>`size` 决定请求级 controller 预分配规模。</div><div class="arch-box"><strong>scene/stage</strong><br/>每个 scene 独立 window；stage 声明 priority、default/min/max 与 rpc_name。</div><div class="arch-box"><strong>concurrent</strong><br/>并发 stage 可挂多个 RPC，窗口更新时取同 stage 内分位最大值。</div></div><div class="arch-layer"><h3>运行态核心</h3><div class="arch-box"><strong>DynamicTimeOutPlugin</strong><br/>加载配置，初始化 controller 池和 scene→FullLinkDynamicTimeOut map。</div><div class="arch-box"><strong>FullLinkDynamicTimeOutController</strong><br/>请求级状态：total timeout、已耗时 stage、当前 scene 对象。</div><div class="arch-box"><strong>FullLinkDynamicTimeOut</strong><br/>保存 stage 元信息、rpc→stage map、PercentileRecord、LatencyRecorder 和双缓冲 stage_latency。</div></div><div class="arch-layer"><h3>预算输出</h3><div class="arch-box"><strong>默认值路径</strong><br/>开关关闭、样本不足或业务要求默认值时返回 `default_timeout`。</div><div class="arch-box"><strong>动态路径</strong><br/>计算剩余总预算，按 stage priority 和历史分位选择当前 RPC timeout。</div><div class="arch-box"><strong>边界保护</strong><br/>结果再经过 `min_timeout` / `max_timeout` clamp。</div></div></div><div class="arch-note">读图顺序：配置定义 stage 空间 → 插件构建 scene 运行态 → 请求 controller 按剩余预算和分位窗口输出每个 RPC 的 timeout。</div></div>

## 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "Plugin\ninitialize" as P
participant "Config" as CFG
participant "ControllerPool" as POOL
participant "FullLinkDynamicTimeOut" as F
participant "Request Controller" as C
participant "RPC Caller" as R
P -> CFG: load dynamic_timeout.conf
alt load failed
  CFG --> P: ret != 0
  P --> P: LOG(WARNING), return 0
else load ok
  P -> POOL: init_controller_pool(size)
  P -> F: init each scene/stage/rpc
  F -> F: start_update_latency_thread()
end
R -> P: get_dt_controller()
P -> POOL: get pooled object
POOL --> P: controller
P -> C: clear()
R -> P: set_request_timeout(scene,total)
P -> C: bind FullLinkDynamicTimeOut
P -> C: set_total_timeout(scene,total)
R -> P: get_rpc_timeout(rpc,is_use_default)
P -> C: get_rpc_timeout()
alt switch off or sample low or default requested
  C --> R: stage.default_timeout
else dynamic path
  C -> C: get_remain_time(stage)
  C -> F: run_time_out_algorithm(rpc,remain,stage,latency)
  F --> C: stage_cost[stage]
  C -> C: clamp min_timeout/max_timeout
  C --> R: allocated timeout
end
R -> P: report_rpc_time(rpc,cost)
P -> F: PercentileRecord << cost
@enduml
```

## 配置结构信息图
```infographic
infographic sequence-ascending-steps
data
  title dynamic_timeout.conf 到预算输出的 6 个转换点
  desc 每一步都可能影响最终 RPC timeout 的来源与含义
  items
    - label controller_pool.size
      desc GR 示例 120；GRC 示例 80，决定池化 controller 数量
      icon mdi/database-cog
    - label scene.name
      desc micro_feed/default/spark 等；请求 scene 未命中只 debug 并返回 0
      icon mdi/source-branch
    - label stages.window_size
      desc 控制 PercentileWindow 周期，GRC default=480、GR micro_feed=300
      icon mdi/chart-bell-curve
    - label stage.priority
      desc 剩余预算按 P0-P4 比例补给当前 stage
      icon mdi/format-list-numbered
    - label default/min/max
      desc 默认降级值和动态结果 clamp 边界
      icon mdi/tune-variant
    - label rpc_name/concurrent
      desc 写入 rpc_stage_map；并发 stage 使用同组 RPC 分位最大值
      icon mdi/access-point
```

## 关键实现解读

### 1. 初始化不是全失败语义
`DynamicTimeOutPlugin::initialize()` 如果 `dynamic_timeout.conf` 加载失败，只输出 warning 并返回 0；后续 `controller_pool` 或 `scene` 缺失才走 FATAL。排障时不能只看插件是否注册成功，必须确认配置加载日志、池大小、scene 初始化日志。

### 2. 请求 controller 是可复用对象，必须先清空
`get_dt_controller()` 从 ObjectPool 取对象后调用 `clear()`。这保护了请求级 `_stage_states`、`_scene`、`_fdtl` 等状态不跨请求泄漏，尤其适合 GraphPool/线程复用场景。

### 3. scene 未命中是静默降级风险
`set_request_timeout()` 找不到 scene 时只打 debug 并返回 0，不会 FATAL。业务侧传错 scene 会表现为“动态超时无效果”，不是明确错误。GRG 中还存在 `news_updates_dibar` → `short_micro_video` 的手工映射，查配置时不要只搜原 graph_name。

### 4. 默认值路径有三层短路
`get_rpc_timeout()` 在三种情况下返回 stage 的 `default_timeout`：全局 `FLAGS_is_use_dynamic_timeout == 0`、请求数小于 `FLAGS_req_num_limit`、调用方显式 `is_use_default=true`。GR 业务封装还要求字典 `open_dynamic_timeout=1` 且 sid `dynamic_timeout>0` 才传 `false` 进入真实动态路径。

### 5. 动态预算由“剩余时间 + 历史分位”共同决定
动态路径先计算当前 stage 之前已消耗/估计的成本，得到 `remain_time`；再对当前及后续 stage 按 priority 排序，用 9999/999/99/90/80/50 多档分位尝试适配总预算。若还有剩余时间，再按当前 stage 的 priority 比例补给，最终再执行 min/max clamp。

### 6. 并发 stage 的分位更新采用同组最大值
后台线程更新 `_stage_latency_double_buffer` 时，如果一个 stage 下有多个 `rpc_names`，同一分位取组内最大值。这能避免并发组内慢 RPC 被平均值掩盖，但也意味着一个异常 RPC 会抬高整个并发 stage 的预算基线。

## 调试 checklist
```infographic
infographic list-column-done-list
data
  title DynamicTimeOut 百分位预算排障 checklist
  desc 从配置加载到分位窗口更新逐层验证
  items
    - label 配置文件是否加载成功
      desc 查 fail to load dynamic_timeout conf；插件存在不等于配置生效
      done true
      icon mdi/file-check
    - label scene 是否命中
      desc 输入 scene 必须存在；GRG news_updates_dibar 需查 short_micro_video
      done true
      icon mdi/source-branch-check
    - label controller 是否先 clear 后注入
      desc Graph context 的 timeout_cntl 非空且无跨请求残留
      done true
      icon mdi-broom
    - label 是否进入动态路径
      desc 确认 FLAGS、req_num_limit、open_dynamic_timeout、sid、is_use_default
      done true
      icon mdi-toggle-switch
    - label rpc_name 是否登记
      desc 未在 rpc_stage_map 中会返回 UNSET_MAGIC_NUM
      done true
      icon mdi-format-list-checks
    - label report 是否回写窗口
      desc report_rpc_time 决定 PercentileRecord 和双缓冲是否更新
      done true
      icon mdi-chart-line
```

## Pitfalls 卡片
<style>.card{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f7fa;border:1px solid #d8e1ec;border-radius:18px;padding:18px;margin:16px 0;color:#1f2937}.card-meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800}.card-title{font-size:26px;font-weight:850;letter-spacing:-.02em;margin:6px 0 10px}.card-grid{display:grid;grid-template-columns:1.35fr 1fr;gap:12px}.card-panel{background:#fff;border-top:4px solid #3d5a80;border-radius:12px;padding:12px;font-size:14px;line-height:1.65}.card-panel strong{color:#1e3a5f}.card-end{font-weight:800;color:#3d5a80;margin-top:10px}</style><div class="card"><div class="card-meta">Pitfall / Dynamic Timeout</div><div class="card-title">timeout 数值相同，不代表路径相同</div><div class="card-grid"><div class="card-panel"><strong>容易误判：</strong>默认值路径、动态路径 clamp 后、rpc 未登记、scene 未命中，都可能让最终观测到的 RPC timeout 看起来“合理”。只看 timeout 值无法证明算法生效。</div><div class="card-panel"><strong>正确证据：</strong>同时查看 `get real dynamic timeout` 日志、`dynamic_timeout_scene_rpc` bvar、`set_rpc_latency` 回报和 `latency update info` 双缓冲更新。</div></div><div class="card-end">∎ 先判断路径，再解释数值。</div></div>

## 证据来源
- `framework/src/dynamic_timeout_plugin.cpp:12`：插件初始化入口。
- `framework/src/dynamic_timeout_plugin.cpp:14`：加载 `dynamic_timeout.conf`。
- `framework/src/dynamic_timeout_plugin.cpp:16`：配置加载失败只 warning。
- `framework/src/dynamic_timeout_plugin.cpp:25`：从 controller pool 取对象。
- `framework/src/dynamic_timeout_plugin.cpp:29`：复用前 `clear()`。
- `framework/src/dynamic_timeout_plugin.cpp:34`：按 scene 设置请求总超时。
- `framework/src/dynamic_timeout_plugin.cpp:40`：scene 未命中返回 0。
- `framework/src/dynamic_timeout.cpp:177`：RPC timeout 计算入口。
- `framework/src/dynamic_timeout.cpp:185`：默认值短路条件。
- `framework/src/dynamic_timeout.cpp:190`：计算当前剩余时间。
- `framework/src/dynamic_timeout.cpp:204`：运行动态预算算法。
- `framework/src/dynamic_timeout.cpp:206`：应用 `min_timeout`。
- `framework/src/dynamic_timeout.cpp:210`：应用 `max_timeout`。
- `framework/src/dynamic_timeout.cpp:230`：scene/stage 配置初始化。
- `framework/src/dynamic_timeout.cpp:279`：并发 RPC 写入 `_rpc_stage_map`。
- `framework/src/dynamic_timeout.cpp:337`：按 stage priority 排序。
- `framework/src/dynamic_timeout.cpp:432`：读取 p9999/p999/p99/p90/p80/p50。
- `framework/src/dynamic_timeout.cpp:440`：并发 stage 取分位最大值。
- `framework/src/dynamic_timeout.cpp:468`：双缓冲切换。
- `feeda-mv-gr/conf/plugins/dynamic_timeout.conf:1`：GR controller_pool 示例。
- `feeda-mv-gr/conf/plugins/dynamic_timeout.conf:4`：GR `micro_feed` scene 示例。
- `feeda-mv-grc/conf/plugins/dynamic_timeout.conf:1`：GRC controller_pool 示例。
- `feeda-mv-grc/conf/plugins/dynamic_timeout.conf:4`：GRC `default` scene 示例。
- `feeda-mv-gr/src/util/util.cpp:571`：业务封装获取动态超时。
- `feeda-mv-gr/src/util/util.cpp:584`：读取 `open_dynamic_timeout`。
- `feeda-mv-gr/src/util/util.cpp:586`：读取 sid `dynamic_timeout`。

## 需人工补充
- KU/周报中 `FLAGS_is_use_dynamic_timeout`、`FLAGS_req_num_limit` 与 sid 灰度策略的 owner 和上线窗口。
- 线上各 scene 的 bvar 曲线：默认值占比、动态值分布、clamp 命中率、超时误伤率。
