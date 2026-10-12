# 2026-07-30 周四代码理解：TopIntp 榜单查询业务链路与返回契约

> 本文基于 `top-intp` 模块代码阅读生成；当前未找到可直接读取的 2026-07-30 daily-plan，KU 背景和业务实验口径需人工补充。

## 1. 架构全景图：请求进入、参数校验、榜单回填、PB 返回

<div class="arch-wrap"><style>.arch-wrap{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:14px;padding:18px;margin:16px 0;color:#243b53}.arch-title{font-size:22px;font-weight:850;margin-bottom:10px;color:#102a43;letter-spacing:0}.arch-sub{font-size:13px;color:#627d98;margin-bottom:14px}.arch-grid{display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px}.arch-layer{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px;min-height:190px}.arch-layer h3{margin:0 0 8px 0;font-size:15px;color:#334e68}.arch-box{border:1px solid #bcccdc;border-radius:8px;padding:8px 10px;margin:8px 0;background:#fdfefe;font-size:13px;line-height:1.45}.arch-box strong{display:block;color:#102a43;margin-bottom:2px}.arch-box.hot{border-color:#9fb3c8;background:#eef4f8}.arch-box.muted{background:#f8fafc;color:#486581}.arch-flow{display:flex;align-items:center;justify-content:center;font-size:18px;color:#829ab1;padding:8px 0}.arch-band{margin-top:12px;display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.arch-note{border-left:4px solid #486581;background:#fff;padding:10px 12px;border-radius:8px;font-size:13px;line-height:1.5}.tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:0;text-transform:uppercase;color:#486581;background:#d9e2ec;border-radius:999px;padding:2px 8px;margin-bottom:8px}.small{font-size:12px;color:#627d98}</style><div class="arch-title">TopServicePbImpl 把 PB 请求翻译成 SessionContext，再驱动榜单回填</div><div class="arch-sub">输入侧负责解析请求和校验参数，业务侧根据场景挑选 TopData，输出侧以 `TopResponse` 交付给 RPC 框架。</div><div class="arch-grid"><div class="arch-layer"><h3>入口层</h3><div class="arch-box hot"><strong>TopServicePbImpl::handle()</strong>统一 RPC 入口，串起解析、校验、日志和响应构造。</div><div class="arch-box"><strong>parse_param()</strong>把 `TopRequest` 解析到 `SessionContext` 与 `baidu::rpc::Controller`。</div><div class="arch-box muted"><strong>check_params()</strong>校验必填字段和上下文约束，避免脏请求进入召回链路。</div></div><div class="arch-layer"><h3>业务层</h3><div class="arch-box hot"><strong>TopDataStrategy::get_top_data()</strong>决定是否需要榜单、需要多少条、如何从 `TopDataComponent` 拉取。</div><div class="arch-flow">→ 规则判断 → 回填 →</div><div class="arch-box"><strong>recall_top_datas()</strong>按位置和策略拼装 `RespItem`，再筛掉不可展示项。</div></div><div class="arch-layer"><h3>输出层</h3><div class="arch-box hot"><strong>TopIntpClosure::Run()</strong>在异步完成时收口，完成 TopResponse 的返回。</div><div class="arch-box"><strong>print_request_log()</strong>用于记录请求画像，便于追踪场景命中。</div><div class="arch-box muted"><strong>契约特征</strong>响应是“已清洗、已排序、已筛选”的榜单视图，不是原始数据透传。</div></div></div><div class="arch-band"><div class="arch-note"><span class="tag">业务判断</span>这条链路更像“榜单编排器”而不是简单的配置读取器。`SessionContext` 决定了很多过滤和排序分支。</div><div class="arch-note"><span class="tag">边界</span>当 `check_params()` 失败时，后续策略层不应继续访问 `TopDataComponent`，否则会放大无效请求成本。</div></div></div>

## 2. 核心流程图：请求到响应的时序

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client as C
participant "TopServicePbImpl" as SVC
participant "SessionContext" as SCT
participant "TopDataStrategy" as TDS
participant "TopDataComponent" as TDC
participant "TopIntpClosure" as CLS
participant "TopResponse" as RES
C -> SVC : RPC TopRequest
SVC -> SVC : parse_param(request)
SVC -> SVC : check_params(sctx, cntl, feed_req)
SVC -> CLS : create closure
CLS -> TDS : get_top_data(begin_ms)
TDS -> TDC : get_top_date(top_data, doc)
TDS -> TDS : recall_top_datas(sctx, result_items)
TDS -> TDS : can_be_filtered / is_need_top_data
TDS -> SCT : attach selected resp items
CLS -> RES : fill TopResponse
CLS -> C : return response
@enduml
```

## 3. 结构信息图：业务链路中的关键动作

```infographic
infographic sequence-snake-steps-simple
data
  title TopIntp 业务处理步骤
  desc 从请求进入到响应返回，关键动作按顺序串起来。
  items
    - label 解析请求
      desc TopRequest 进入 handle，转成内部上下文
      icon mdi/play-circle-outline
    - label 校验参数
      desc 过滤非法 scene、uid、标志位和上下文缺失
      icon mdi/shield-check-outline
    - label 读取 TopData
      desc 从双缓冲组件拿到已发布快照
      icon mdi/database-outline
    - label 回填榜单
      desc 筛选、拼装、排序、填充 RespItem
      icon mdi/format-list-bulleted
    - label 返回 PB
      desc 通过 closure 输出 TopResponse
      icon mdi/send-outline
theme
  palette #3d5a80 #98c1d9 #e0fbfc
```

## 4. Pitfalls 卡片

<div class="card-frame"><style>.card-frame{margin:16px 0}.card{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:14px 16px;margin:10px 0}.card-title{font-size:16px;font-weight:800;color:#102a43;margin:0 0 8px 0}.card-body{font-size:13px;line-height:1.6;color:#243b53;margin:0}.card-highlight{border-left:4px solid #486581;padding-left:12px}.card-meta{font-size:11px;font-weight:700;color:#627d98;letter-spacing:0;text-transform:uppercase;margin-bottom:6px}.card-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}</style><div class="card-grid"><div class="card"><div class="card-meta">Pitfall 1</div><div class="card-title">参数校验失败后不要继续拉榜单</div><p class="card-body">`check_params()` 的职责是把无效请求拦在入口。如果继续走 `get_top_data()`，只会放大空耗。</p></div><div class="card"><div class="card-meta">Pitfall 2</div><div class="card-title">回填结果不是原样透传</div><p class="card-body">`recall_top_datas()` 和 `can_be_filtered()` 说明业务层会做过滤、裁剪和位置控制，响应契约比原始数据更窄。</p></div></div><div class="card card-highlight"><div class="card-meta">结论</div><div class="card-title">TopIntp 的价值在于把榜单内容、场景约束和 RPC 响应收口在一处</div><p class="card-body">对调用方而言，返回的是一个完成编排的榜单视图；对内部而言，真正重要的是上下文、过滤逻辑和发布快照三者的组合。</p></div></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 业务链路检查清单
  items
    - label handle() 是否先 parse_param 再 check_params
      done true
    - label SessionContext 是否完整带入 scene / uid / flag
      done true
    - label TopDataStrategy 是否正确决定是否需要榜单
      done true
    - label TopDataComponent 是否只返回已发布快照
      done true
    - label 回填后是否仍会触发 can_be_filtered
      done true
    - label TopResponse 是否只包含最终对外字段
      done true
```

## 6. 证据来源

- `src/main.cpp:7-17`（服务入口和依赖初始化）
- `src/service/top_service_pb.h:21-35`（`TopServicePbImpl`、`handle()`、`parse_param()`、`check_params()`）
- `src/service/top_service.h:23-43`（`TopIntpClosure`、`get_top_data()`）
- `src/strategy/top_data_strategy.h:19-34`（`get_top_data()`、`recall_top_datas()`、`recall_one_item_from_pool()`）
- `src/component/top_data_component.h:46-66`（双缓冲读取接口）
- `conf/common_component/gcms_plugin_pb.conf:1-18`（GCMS 依赖和并发配置）
- `conf/ns_plugin.conf:1-11`（命名空间调度配置）
