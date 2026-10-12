# 2026-06-29 业务库代码理解：UA 路由决策与 Graph 选择业务映射

> 今日主题来源：本机未发现可用 `daily-plan` 文件；按周一 fallback 选择 GRC 业务侧 UA 路由决策与 Graph 选择映射，与基础库 GraphEngine 驱动框架配对。  
> 内网 KU：本次任务未提供 KU URL/doc-id；产品场景定义、实验策略需人工补充。本文以代码库证据为准。  
> 范围：UA 值与 Graph 名称的硬编码映射、UA=85 沉浸式视频与 UA=102 二跳底bar 的业务语义差异、End Data 多态路由（GrcResponse/ResponseForGrg/ResData/ClusterData）、SessionContext 关键字段与日志打印。

## 架构全景图

<style>.arch-0629-biz{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #cbd5e1;border-radius:20px;padding:18px;color:#1e293b}.arch-0629-biz .title{font-size:24px;font-weight:900;margin-bottom:6px}.arch-0629-biz .sub{font-size:13px;color:#64748b;margin-bottom:16px}.arch-0629-biz .grid{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px}.arch-0629-biz .lane{background:#fff;border:1px solid #dbeafe;border-radius:16px;padding:12px}.arch-0629-biz h3{font-size:15px;margin:0 0 10px;color:#1d4ed8}.arch-0629-biz .box{background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;padding:10px;margin:8px 0}.arch-0629-biz .box b{display:block;font-size:13px;margin-bottom:4px}.arch-0629-biz .box span{font-size:12px;color:#64748b;line-height:1.5}.arch-0629-biz .hot{background:#eff6ff;border-color:#93c5fd}.arch-0629-biz .note{margin-top:12px;border-left:4px solid #3b82f6;background:#eef6ff;border-radius:10px;padding:10px;font-size:13px;color:#334155}</style><div class="arch-0629-biz"><div class="title">UA 路由与业务 Graph 映射</div><div class="sub">UA 值是 GRC 路由的根键；每种 UA 对应一张 Graph、一个 end_data 节点、一套策略配置和一个业务语义。硬编码路由与 scenes.conf 策略路由是两个独立的分层。</div><div class="grid"><div class="lane"><h3>UA → Graph 硬编码映射</h3><div class="box hot"><b>ua=85 → video_immersion</b><span>沉浸式视频主场景，`graph/video_immersion_graph.conf` 是入口图。</span></div><div class="box hot"><b>ua=102 → news_updates_dibar</b><span>二跳图文底bar，`graph/news_updates_dibar/news_updates_dibar_graph.conf`。</span></div><div class="box"><b>ua=97 → dibar_reddot</b><span>底bar红点场景。</span></div><div class="box"><b>ua=155/156/123 → searchc_related</b><span>搜C 渗透推荐。</span></div><div class="box"><b>ua=110 → interest_card</b><span>兴趣卡场景。</span></div><div class="box"><b>ua=67 → default</b><span>手百二跳落地页合集，特殊 end_data=ClusterData。</span></div></div><div class="lane"><h3>End Data 多态路由</h3><div class="box hot"><b>GrcResponse</b><span>默认 end_data，通用推荐响应。</span></div><div class="box hot"><b>ResponseForGrg</b><span>GRG 专用响应，数据透传给下游 GRG。</span></div><div class="box"><b>ResData</b><span>另一种 GRC 响应格式。</span></div><div class="box"><b>ClusterData</b><span>ua=67 落地页合集专用。</span></div><div class="box"><b>InterestCardData</b><span>ua=110 兴趣卡专用。</span></div></div><div class="lane"><h3>关键业务语义</h3><div class="box hot"><b>is_grg_request</b><span>`request.common_info().is_grg_request()`，区分 GRC 直返和 GRG 转发。</span></div><div class="box"><b>product/tab/tabfrom</b><span>产品形态（手百/好看/极速等）与 Tab 来源。</span></div><div class="box"><b>sids 穿透</b><span>实验 sid 序列从 GR 透传到 GRC，影响策略路由。</span></div><div class="box"><b>ReqExtractPlugin</b><span>`grc_service.cpp:172-175` 向 DynamicStruct 注入 ua/product/channel_id。</span></div></div></div><div class="note"><b>业务映射分层：</b>UA路由（选Graph） → scenes.conf路由（选策略） → sid实验路由（选参数） → processor执行</div></div>

## 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
title UA 路由决策与 End Data 多态选择
|GR upstream|
start
:GRCRequest(uid/cuid/ua/product/tab/sids/dynamic_timeout)];
note right
  UA 是路由根键：
  - 决定执行哪张 Graph
  - 决定 end_data 类型
  - 决定 Dapper 是否上报
end note
|#AliceBlue|GenericGRCService::query|
:SessionContext.init(request);
:ReqExtractPlugin 注入 ua/product/channel_id;
:GraphPool.try_get(graph_name);
switch (ua value)
  case (ua == 97)
    :graph_name = "dibar_reddot";
    :end = GrcResponse;
  case (ua == 85)
    :graph_name = "video_immersion";
    :end = GrcResponse;
  case (ua == 155 || 156 || 123)
    :graph_name = "searchc_related";
    :end = GrcResponse;
  case (ua == 102)
    :graph_name = "news_updates_dibar";
    :end = GrcResponse;
  case (ua == 67)
    :graph_name = "default";
    :end = ClusterData;
  case (ua == 110)
    :graph_name = "interest_card";
    :end = InterestCardData;
  case (default)
    :graph_name = "default";
    :end = GrcResponse;
end switch
|#LightYellow|emit_common_data|
:Graph 注入 Request/uid/cuid/ua/product/ExpInfo;
|#LightGreen|Graph run|
:graph->run(end);
:阻塞等待 DAG 完成;
|#AliceBlue|响应序列化|
alt ret == 0
  :response.CopyFrom(*end_data);
  :sctx._response->set_error_msg("success");
else
  :response.set_error_num(ret);
  :cntl->SetFailed(ret);
end note
|#LightBlue|GraphMonitor|
alt ua in {85, 87, 123}
  :upload_dapper();
end note
|#LightPink|日志打印|
:NEWDAPPER 日志 / RESPONSE 日志;
|GR upstream|
:send_response();
stop
@enduml
```

## UA 场景对照表

```infographic compare-binary-horizontal-underline-text-vs
data
  title UA 值与业务场景映射对照
  desc grc_service.cpp:184-198 硬编码映射；与 scenes.conf 策略路由是两层不同的决策
  items
    - label ua=85
      desc video_immersion，沉浸式视频主场景，Dapper 上报
      value 85
    - label ua=102
      desc news_updates_dibar，二跳图文底bar推荐
      value 102
    - label ua=97
      desc dibar_reddot，底bar红点场景
      value 97
    - label ua=155/156/123
      desc searchc_related，搜C 渗透推荐
      value 155
    - label ua=110
      desc interest_card，兴趣卡场景
      value 110
    - label ua=67
      desc default Graph + ClusterData，手百二跳落地页合集
      value 67
    - label default
      desc default Graph + GrcResponse，兜底场景
      value 0
```

## 业务语义说明

| UA | 业务含义 | Graph 名称 | End Data | Dapper | 典型场景 |
|---:|---------|-----------|----------|:------:|---------|
| 85 | 沉浸式视频 | `video_immersion` | GrcResponse | ✓ | 好看视频 Feed |
| 102 | 二跳图文底bar | `news_updates_dibar` | GrcResponse | ✗ | 手百图文详情页底Bar |
| 97 | 底bar红点 | `dibar_reddot` | GrcResponse | ✗ | 底Bar 红点推荐 |
| 155/156 | 搜C渗透 | `searchc_related` | GrcResponse | ✓ | 搜索结果页渗透 |
| 123 | 搜C沉浸渗透 | `searchc_immersive_related` | GrcResponse | ✓ | 搜索沉浸页渗透 |
| 110 | 兴趣卡 | `interest_card` | InterestCardData | ✗ | 兴趣卡形态 |
| 67 | 二跳落地页合集 | `default` | ClusterData | ✗ | 手百落地页合集 |
| 其他 | 兜底 | `default` | GrcResponse | ✗ | 通用推荐 |

## 关键结论

- **UA 路由硬编码在 `grc_service.cpp:184-198`**，不是从配置文件读取；新增场景必须修改代码并重新编译。
- **`scenes.conf` 与 UA 路由是两个独立分层**：UA 路由决定执行哪张 Graph，scenes.conf 决定同一张 Graph 内队列的配额比例和优先级。
- **End Data 多态由 UA 值决定**：`ua=67` 用 `ClusterData`，`ua=110` 用 `InterestCardData`，其他主要用 `GrcResponse`。
- **ResponseForGrg** 是为 GRG 转发场景预留的响应节点，数据通过 preset 预填后透传给下游 GRG 服务。
- **Dapper 上报仅限 ua=85/87/123**；ua=102 的 news_updates_dibar 场景不在 Dapper 白名单中，链路可观测性依赖 NEWDAPPER 日志。
- `ReqExtractPlugin` 向 DynamicStruct 注入的 `ua/product/channel_id` 是调权引擎和策略配置的输入，变更 UA 路由时需确认插件是否正确注册。
- `is_grg_request` 字段控制是否打印完整 RESPONSE 日志（`grc_service.cpp:513-527`）；GRG 转发场景不打印原始响应避免敏感数据泄露。

## Pitfalls 卡片

<style>.pit-0629-biz{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1000px;border-radius:20px;background:#f5f7fa;border:1px solid #c7d2fe;padding:20px;color:#1e293b}.pit-0629-biz .meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#1d4ed8;font-weight:900}.pit-0629-biz .headline{font-size:28px;line-height:1.15;font-weight:900;margin:8px 0 12px}.pit-0629-biz .grid{display:grid;grid-template-columns:1.15fr .95fr;gap:14px}.pit-0629-biz .panel{background:#fff;border-top:4px solid #3b82f6;border-radius:12px;padding:12px}.pit-0629-biz .panel b{display:block;margin-bottom:6px}.pit-0629-biz .panel p{margin:0;font-size:14px;line-height:1.65;color:#334155}.pit-0629-biz .end{margin-top:12px;font-weight:900;color:#1d4ed8}</style><div class="pit-0629-biz"><div class="meta">pitfall · business routing</div><div class="headline">UA 路由与 scenes.conf 策略路由是两层决策，别混为一谈</div><div class="grid"><div class="panel"><b>UA 路由选 Graph，scenes.conf 路由选策略</b><p>`grc_service.cpp:184-198` 决定用哪张 Graph（video_immersion vs news_updates_dibar）；scenes.conf 决定同一张 Graph 内各队列的 quota_ratio。两者独立，不能互相替代。</p></div><div class="panel"><b>ua=102 不上报 Dapper</b><p>Dapper 白名单只有 {85,87,123}；ua=102 的 news_updates_dibar 场景排查只能靠 NEWDAPPER 日志和 GRC_LOG。</p></div></div><div class="end">∎ 定位问题时先确认 UA 值，再判断在哪个分层出问题。</div></div>

## 调试 checklist

```infographic list-column-done-list
data
  title UA 路由业务排查清单
  items
    - label 确认请求 UA 值
      desc 从 GRCRequest.common_info().ua() 获取 UA 值
      done false
    - label 确认 Graph 名称
      desc 按 UA 路由表确认 graph_name，检查 Graph 是否已加载
      done false
    - label 确认 End Data 类型
      desc `run():296-309` 中 end_data 的选择逻辑与 `find_data()` 是否成功
      done false
    - label 确认 scenes.conf 策略
      desc scenes.conf 中 UA 对应的策略配置（fork_type → quota_ratio）
      done false
    - label 确认 Dapper 是否上报
      desc ua in {85,87,123} 才会上报；否则查 NEWDAPPER 日志
      done false
    - label 确认 is_grg_request
      desc 影响 RESPONSE 日志打印与 GRG 转发链路
      done false
    - label 确认 ReqExtractPlugin 注入
      desc `grc_service.cpp:172-175` 向 DynamicStruct 注入 ua/product/channel_id
      done false
```

## 证据来源

- `src/service/grc_service.cpp:184-198`：UA 到 graph_name 的完整路由映射。
- `src/service/grc_service.cpp:296-309`：End Data 多态选择逻辑（GrcResponse/ClusterData/InterestCardData）。
- `src/service/grc_service.cpp:172-175`：`ReqExtractPlugin` 向 DynamicStruct 注入业务字段。
- `src/service/grc_service.cpp:151-224`：`query()` 中 SessionContext 初始化与 graph 获取。
- `src/service/grc_service.cpp:513-527`：`is_grg_request` 控制 RESPONSE 日志打印。
- `src/service/grc_service.cpp:437-439`：Dapper 上报 UA 白名单 `{85, 87, 123}`。
- `src/service/grc_service.cpp:493-510`：`printNoticeLog` 日志格式包含 graph_name/sids/exp_params。
- `src/service/grc_service.cpp:427-432`：NEWDAPPER 日志写入 `[ua/sid/final_path]`。
- `conf/plugins/scenes.conf`：UA 到策略名称的映射（需人工补充具体内容）。
- `conf/plugins/graph/video_immersion_graph/video_immersion_graph.conf`：ua=85 主图入口。
- `conf/plugins/graph/news_updates_dibar/news_updates_dibar_graph.conf`：ua=102 底bar 图文图入口。
