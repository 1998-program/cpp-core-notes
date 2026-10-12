# 2026-06-20 基础库代码理解：DynamicTimeOutPlugin 全链路超时分配框架

> 日期：2026-06-20  
> 今日主题来源：本机未发现可用 `daily-plan` 文件；按近期双 Markdown 工作流的周六 fallback，选择基础库 `feed-general/framework` 的 `DynamicTimeOutPlugin` 与 `FullLinkDynamicTimeOut`。已按要求设置 `BAIDU_CC_USERNAME=hujiyang`；未提供 KU URL/doc-id，内网策略背景需人工补充。  
> 范围：插件初始化、scene/stage 配置解析、controller 池化、请求级总超时绑定、RPC 级默认/动态超时获取、耗时回报与分位窗口更新。

## 架构全景图
<style>.arch{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #dbe4ef;border-radius:18px;padding:18px;margin:16px 0;color:#1f2937}.arch-title{font-size:22px;font-weight:800;margin-bottom:12px}.arch-grid{display:grid;grid-template-columns:1fr 1.25fr 1fr;gap:12px}.arch-layer{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:12px}.arch-layer h3{font-size:15px;margin:0 0 8px;color:#334155}.arch-box{border-radius:12px;background:#f1f5f9;border-left:4px solid #3d5a80;padding:10px;margin:8px 0;font-size:13px;line-height:1.5}.arch-box strong{color:#1e3a5f}.arch-arrow{text-align:center;color:#64748b;font-weight:700;padding:6px}.arch-note{font-size:12px;color:#64748b;margin-top:10px}</style><div class="arch"><div class="arch-title">DynamicTimeOutPlugin：配置驱动的请求级超时预算器</div><div class="arch-grid"><div class="arch-layer"><h3>配置层</h3><div class="arch-box"><strong>dynamic_timeout.conf</strong><br/>controller_pool.size；多 scene；每个 scene 下多 stage。</div><div class="arch-box"><strong>StageInfo</strong><br/>priority / default_timeout / min_timeout / max_timeout / rpc_names。</div></div><div class="arch-layer"><h3>插件与运行时</h3><div class="arch-box"><strong>DynamicTimeOutPlugin::initialize()</strong><br/>load conf → init_controller_pool → init_scene_timeout_map。</div><div class="arch-box"><strong>FullLinkDynamicTimeOut</strong><br/>为每个 scene 保存 stage、rpc→stage map、分位窗口与 bvar 统计。</div><div class="arch-box"><strong>FullLinkDynamicTimeOutController</strong><br/>每个请求从池中取出，绑定 scene 对象与 total timeout。</div></div><div class="arch-layer"><h3>业务接入层</h3><div class="arch-box"><strong>Service fill_basic_data_for_graph</strong><br/>把 dt_cntl 注入 FrameworkContext.timeout_cntl。</div><div class="arch-box"><strong>Util::get_dynamic_timeout()</strong><br/>按字典开关 + sid 命中决定默认值还是动态计算。</div><div class="arch-box"><strong>Util::report_timeout()</strong><br/>RPC 完成后回报耗时，供窗口更新。</div></div></div><div class="arch-note">读图顺序：配置定义预算空间 → 插件构建 scene 对象 → 请求绑定 controller → 业务 RPC 获取/回报超时。</div></div>

## 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Request
participant "Service\nfill_basic_data" as S
participant "DynamicTimeOutPlugin" as P
participant "DTController" as C
participant "FullLinkDynamicTimeOut" as F
participant "Business RPC" as R
Request -> S: request + graph_name + cntl.timeout_ms
S -> P: get_dt_controller()
P -> C: pooled object + clear()
S -> P: set_request_timeout(scene, total_timeout)
P -> F: find scene object
P -> C: bind F + set_total_timeout()
S -> S: FrameworkContext.timeout_cntl = C
R -> P: get_rpc_timeout(C, rpc_name, is_use_default)
P -> C: get_rpc_timeout()
C -> F: read stage map / percentile latency
F --> C: allocated timeout or default_timeout
R -> P: report_rpc_time(C, rpc_name, cost)
P -> C: rpc_done()
C -> F: set_rpc_latency()
Request -> P: report_request_time(C, request_cost)
P -> F: update request/rpc aggregate latency
@enduml
```

## 配置结构信息图
```infographic
infographic sequence-timeline-simple
data
  title dynamic_timeout.conf 解析顺序
  desc 从 controller 池到 scene/stage/rpc 的层级配置
  items
    - label controller_pool
      time 1
      desc size=80，决定可同时复用的请求级 controller 数量
      icon mdi/database-cog
    - label scene
      time 2
      desc default/spark/guanzhu 等，每个 scene 独立一个 FullLinkDynamicTimeOut
      icon mdi/source-branch
    - label stages.window_size
      time 3
      desc 分位窗口大小，如 default=480、spark=400、guanzhu=600
      icon mdi/chart-bell-curve
    - label stage
      time 4
      desc priority/default/min/max，单 rpc 或 concurrent rpc 组
      icon mdi/format-list-numbered
    - label rpc_name
      time 5
      desc 写入 _rpc_stage_map，并暴露 dynamic_timeout_scene_rpc bvar
      icon mdi/access-point
```

## 关键实现解读

### 1. 插件初始化是“失败可降级”的
`DynamicTimeOutPlugin::initialize()` 从 `FLAGS_dynamic_timeout_conf_path` 与 `FLAGS_dynamic_timeout_conf_file` 加载配置；如果 `config.load()` 失败，只打 warning 并返回 0。换句话说，插件注册不必然意味着动态超时已生效，排障时需要同时看配置加载日志与 controller/scene 初始化日志。

### 2. 请求级 controller 必须先 clear 再复用
`get_dt_controller()` 从 ObjectPool 取对象后调用 `clear()`，避免上一次请求残留 `_stage_timeout`、`_concurrent_rpc_time`、`_scene`、`_fdtl`。这对 GraphPool 复用场景很关键：Graph 可复用，DTController 也可复用，但请求级状态不能跨请求泄漏。

### 3. scene 找不到不会强制失败
`set_request_timeout()` 在 `_scene_timeout_map` 找不到输入 scene 时只打 debug 并返回 0；只有 `run_time_out_algorithm()` 返回 false 才 FATAL。因此接入方传错 scene 可能表现为“没有动态超时效果”，而不是明显启动失败。

### 4. 当前入口算法有短路，真实分配发生在 get_rpc_timeout
`FullLinkDynamicTimeOutController::run_time_out_algorithm()` 目前第一行直接 `return true`，原先一次性预分配 `_stage_timeout` 的逻辑被注释。实际 RPC 级动态分配发生在 `get_rpc_timeout()` 内：当开关开启、请求量超过阈值且不是 default 模式时，按剩余预算、stage 分位 latency、priority、min/max 重新计算当前 RPC 的超时。

### 5. 默认值路径由业务开关控制
GR 侧 `Util::get_dynamic_timeout()` 先取 `msv_gr_controll_dict` 的 `open_dynamic_timeout`，再看 sid 参数 `dynamic_timeout`。只有字典开关为 `1` 且 sid 命中时才走真实动态分配，否则仍调用 `plugin->get_rpc_timeout(..., true)` 取 `default_timeout`。

## 调试 checklist
```infographic
infographic list-column-done-list
data
  title DynamicTimeOutPlugin 排障 checklist
  desc 从配置、scene、开关到 RPC 回报逐层定位
  items
    - label 配置是否加载
      desc 搜 dynamic_timeout_conf_path / dynamic_timeout_conf_file；确认没有 fail to load dynamic_timeout conf
      done true
      icon mdi/file-check
    - label scene 是否命中
      desc set_request_timeout 的 scene 必须存在于 dynamic_timeout.conf；GRG news_updates_dibar 会映射到 short_micro_video
      done true
      icon mdi/source-branch-check
    - label controller 是否注入
      desc FrameworkContext.timeout_cntl 非空，业务 Util 才能取到动态超时
      done true
      icon mdi/link-variant
    - label 开关与 sid 是否命中
      desc open_dynamic_timeout=1 且 sid dynamic_timeout > 0 才走真实动态分配
      done true
      icon mdi/toggle-switch
    - label RPC 是否登记
      desc rpc_name 不在 _rpc_stage_map 时返回 UNSET_MAGIC_NUM
      done true
      icon mdi/format-list-checks
    - label 是否回报耗时
      desc report_rpc_time / report_request_time 决定分位窗口和 CPU cost 是否更新
      done true
      icon mdi/chart-line
```

## Pitfalls 卡片
<style>.card{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f7fa;border:1px solid #d8e1ec;border-radius:18px;padding:18px;margin:16px 0;color:#1f2937}.card-meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800}.card-title{font-size:26px;font-weight:850;letter-spacing:-.02em;margin:6px 0 10px}.card-grid{display:grid;grid-template-columns:1.3fr 1fr;gap:12px}.card-panel{background:#fff;border-top:4px solid #3d5a80;border-radius:12px;padding:12px;font-size:14px;line-height:1.65}.card-panel strong{color:#1e3a5f}.card-end{font-weight:800;color:#3d5a80;margin-top:10px}</style><div class="card"><div class="card-meta">Pitfalls / Dynamic Timeout</div><div class="card-title">“插件存在”不等于“动态预算生效”</div><div class="card-grid"><div class="card-panel"><strong>容易误判：</strong>配置加载失败会返回 0；scene 未命中也返回 0；开关/sid 未命中会走 default_timeout；rpc_name 未登记返回 UNSET_MAGIC_NUM。这些路径都可能没有明显错误，但效果等于未启用。</div><div class="card-panel"><strong>正确排查：</strong>同时确认 scene 命中、controller 注入、字典开关、sid、rpc_name 登记和 report 回报。只看最终 RPC timeout 值，无法区分默认值、动态值和未登记。</div></div><div class="card-end">∎ 先证实“进入动态路径”，再讨论算法参数。</div></div>

## 证据来源
- `feed-general/framework/src/dynamic_timeout_plugin.cpp:9-22`：配置加载与初始化入口。
- `feed-general/framework/src/dynamic_timeout_plugin.cpp:25-31`：controller 池对象获取与 clear。
- `feed-general/framework/src/dynamic_timeout_plugin.cpp:34-56`：scene 绑定与 request total timeout 设置。
- `feed-general/framework/src/dynamic_timeout.cpp:177-220`：RPC 级 timeout 获取、默认/动态分支、min/max clamp。
- `feed-general/framework/src/dynamic_timeout.cpp:230-330`：scene/stage/rpc 配置解析与 bvar 注册。
- `feeda-mv-gr/conf/plugins/dynamic_timeout.conf:1-75`：default scene 的 pool/stage/rpc 配置样例。
- `feeda-mv-gr/src/util/util.cpp:571-607`：业务侧动态超时获取与耗时回报封装。

## 需人工补充
- KU/周报中关于 `open_dynamic_timeout`、sid `dynamic_timeout` 的上线策略与实验 owner。
- 线上各 scene 的真实 p99/p999 收敛效果与误杀率。
