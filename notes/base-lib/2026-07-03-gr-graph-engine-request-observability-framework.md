# feeda-mv-gr GraphEngine 请求执行与观测框架

> 日期：2026-07-03｜类型：基础库笔记｜主题来源：本机未发现可用 `daily-plan` 文件；按周五 fallback 从 GR 服务入口与图引擎框架继续分析。已按任务要求设置 `BAIDU_CC_USERNAME=hujiyang`；未提供 KU URL/doc-id，内网知识口径需人工补充，本文以本地代码库检索结果为准。

## 1. 架构全景：双协议入口共用一套 GraphEngine 执行骨架

<style>.grarch{font-family:Inter,Arial,sans-serif;background:#f8fafc;border:1px solid #d7e3ef;border-radius:18px;padding:18px;margin:14px 0;color:#172033}.grarch-title{font-size:25px;font-weight:850;letter-spacing:-.02em}.grarch-sub{font-size:13px;color:#526274;margin:8px 0 16px}.grarch-layer{background:#fff;border:1px solid #d7e3ef;border-radius:14px;padding:12px;margin:10px 0}.grarch-layer h3{margin:0 0 10px;color:#224a74;font-size:15px}.grarch-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.grarch-box{border-radius:12px;padding:10px;background:#eef6ff;border:1px solid #c7def5;min-height:68px}.grarch-box b{display:block;font-size:13px;margin-bottom:5px}.grarch-box span{font-size:12px;color:#526274;line-height:1.45}.grarch-box.hot{background:#fff7ed;border-color:#fed7aa}.grarch-box.ok{background:#ecfdf5;border-color:#bbf7d0}.grarch-arrow{text-align:center;color:#64748b;font-weight:900;margin:2px 0}.grarch-foot{font-size:12px;color:#64748b;margin-top:10px}</style><div class="grarch"><div class="grarch-title">GR GraphEngine 请求执行框架</div><div class="grarch-sub">`main.cpp` 注册 nshead 与 protobuf 两类入口；两条入口都把请求字段注入 GraphData，再运行配置化 DAG，最后由 GraphMonitor / Dapper / Health 输出观测信号。</div><div class="grarch-layer"><h3>服务入口层</h3><div class="grarch-grid"><div class="grarch-box"><b>main.cpp</b><span>加载 comlog/gflags/global initializer/ExpManager，并启动 brpc Server。</span></div><div class="grarch-box"><b>GenericGRService</b><span>nshead 协议入口，默认响应 GraphData 为 `Response`。</span></div><div class="grarch-box"><b>GeneralGRService</b><span>protobuf reusable 协议入口，默认响应 GraphData 为 `GeneralResponse`。</span></div><div class="grarch-box hot"><b>GraphEngine</b><span>`ApplicationContext` 中名为 `graph_engine` 的插件，按场景取 `default` / `author_graph` / `interest_card`。</span></div></div></div><div class="grarch-arrow">↓</div><div class="grarch-layer"><h3>执行上下文层</h3><div class="grarch-grid"><div class="grarch-box"><b>ReqExtractPlugin</b><span>把 uid/cuid/ua/tab/channel_id/sid 等写入动态结构 `ReqInfo`。</span></div><div class="grarch-box"><b>GraphData 注入</b><span>`Request`、`log_id`、`uid`、`UA`、`ExpInfo`、`graph_begin_us` 等成为全局依赖。</span></div><div class="grarch-box"><b>DynamicTimeout</b><span>根据 ua 映射 `micro_feed_rec` / `micro_feed_spark` / `micro_feed` 场景。</span></div><div class="grarch-box ok"><b>HealthContext</b><span>挂到 MutableFrameworkContext，图执行后 dump 到日志或响应 health_item。</span></div></div></div><div class="grarch-arrow">↓</div><div class="grarch-layer"><h3>图配置与观测层</h3><div class="grarch-grid"><div class="grarch-box"><b>global.conf</b><span>`pool_size:30`，通过 `$include` 装配 request/history/GRC/merge/response 等子图。</span></div><div class="grarch-box"><b>graph->run</b><span>按目标 end data 触发 DAG；普通请求同时等待 response 与 sample log。</span></div><div class="grarch-box"><b>GraphMonitor</b><span>生成 final_path，并追加 `total_cost` 作为长尾路径节点。</span></div><div class="grarch-box hot"><b>Dapper + NEWDAPPER</b><span>ua 85/87 等场景上报链路耗时，并打印 final_path 便于定位慢点。</span></div></div></div><div class="grarch-foot">核心结论：框架层不是业务逻辑本身，而是“字段注入 → 配置化 DAG → end data 收敛 → 观测输出”的稳定骨架。</div></div>

## 2. 核心流程：从 brpc 请求到 final_path

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client as client
participant "main.cpp\nbrpc Server" as main
participant "Generic/GeneralGRService" as svc
participant "GRSessionContext" as sctx
participant "ReqExtractPlugin" as reqx
participant "GraphEngine" as ge
participant "GraphData" as gd
participant "DynamicTimeout + Health" as ctl
participant "GraphMonitor" as gm
participant "Dapper/Log" as out
client -> main: nshead or baidu_std_reuse request
main -> svc: dispatch service method
svc -> sctx: init(request, response)
svc -> reqx: build ReqInfo dynamic struct
svc -> ge: try_get(default/author_graph/interest_card)
ge --> svc: pooled Graph
svc -> gd: emit Request / uid / cuid / UA / ExpInfo / graph_begin_us
svc -> ctl: set_request_timeout(scene_name, timeout)
svc -> ge: graph->run(end[, MicrovideoSampleLogRet])
ge --> svc: closure ret + end GraphData
svc -> ctl: report_request_time + dump health
svc -> out: response send + DapperDirectCollectWithRequest
svc -> gm: fill_graph_path + generate_path + add total_cost
gm --> out: final_path to NOTICE and NEWDAPPER
@enduml
```

## 3. 图装配结构信息图

```infographic
infographic list-grid-badge-card
data
  title default 图装配结构
  desc global.conf 是框架层入口，业务能力由子配置逐步挂入
  items
    - label graph 基础区
      desc graph.conf、parse_request.conf、recall_user.conf、history.conf 负责基础请求与用户/历史数据
      icon mdi/graph-outline
    - label GRC 召回区
      desc cache_request.conf 与 micro_video.conf 构造请求特征并触发 MicroVideoGrcRecallProcessor
      icon mdi/radar
    - label 融合区
      desc merge_graph.conf 是旧融合框架；exec_merge.conf 把 ExecEngine 挂入 GraphEngine
      icon mdi/source-merge
    - label 场景扩展区
      desc query_recall、jisu_duanju_dibar_recall、outer_net_mv_recall 等按业务场景补充分支
      icon mdi/routes
    - label 响应区
      desc post_vertex.conf 和 expression.conf 决定 EndMergeResult 与最终 Response/GeneralResponse
      icon mdi/file-export-outline
```

## 4. 框架实现要点

### 4.1 入口注册：协议不同，图骨架一致

- `src/main.cpp:44-83`：服务启动顺序是 ParseFlags → comlog → reusable protocol → gflags → `GlobalInitializer` → ExpManager → brpc Server。
- `src/main.cpp:73-81`：`options.nshead_service = new GenericGRService`，同时 `server.AddService(&general_gr_service, SERVER_DOESNT_OWN_SERVICE)`，说明 nshead 与 protobuf 入口并存。
- `src/service/gr_service.cpp:42-53` 与 `src/service/general_gr_service.cpp:35-45`：两个服务构造函数都从 `ApplicationContext` 获取 `DynamicTimeOutPlugin`、`ReqExtractPlugin`、`GraphEngine("graph_engine")`。

### 4.2 图选择：ua 决定特殊子图，否则走 default

- `src/service/gr_service.cpp:96-105`：默认 `graph_name = "default"`，ua 100/101 切 `author_graph`，ua 110 切 `interest_card`。
- `src/service/general_gr_service.cpp:87-97`：protobuf 入口复用同样的图选择规则。
- `conf/plugins/graph/global.conf:1-4`：default 图 `pool_size: 30`，说明 GraphPool 是服务并发执行的基础资源。

### 4.3 GraphData 注入：请求字段变成全局依赖

- `src/service/gr_service.cpp:222-279`：把 `Request`、`log_id`、`uid`、`cuid`、`baiduid`、`UA`、`channel_id`、`tab`、`flow_loc`、`product`、`ExpInfo`、`graph_begin_us` 写入 GraphData。
- `src/service/general_gr_service.cpp:204-260`：protobuf 入口同样注入这些字段，只是 end data 后续指向 `GeneralResponse`。
- `conf/plugins/graph/global.conf:6-29`：这些 GraphData 被声明成 `@global_depend`，让任意 Processor 都能通过框架依赖获得请求上下文。

### 4.4 运行与响应：end data 是 DAG 的收敛点

- `src/service/gr_service.cpp:280-300`：普通 nshead 请求运行 `graph->run(end, microvideo_samplelog)`；作者/兴趣卡场景运行特殊 end data。
- `src/service/general_gr_service.cpp:262-277`：protobuf 请求使用 `GeneralResponse`，作者/兴趣卡场景对应 `PersonalCenterResponseNomal` / `IntrestCardResponseNomal`。
- `src/service/gr_service.cpp:332-348` 与 `src/service/general_gr_service.cpp:307-341`：ret=0 才从 end GraphData 取响应；部分非法请求走 empty response；其他错误走 `graph run failed`。

### 4.5 观测闭环：Health、Dapper、GraphMonitor 三条线

- `src/service/gr_service.cpp:317-330`：DynamicTimeout 上报请求耗时，HealthContext dump 到 `HEALTH MONITOR` 日志。
- `src/service/general_gr_service.cpp:301-315`：GeneralGRService 将 HealthContext dump 写入 `health_item`，同时打印日志。
- `src/service/gr_service.cpp:350-379`：ua 85/87 执行 DapperDirectCollectWithRequest，上报 `feeda-mv-grf → feeda-mv-gr` 链路。
- `src/service/gr_service.cpp:381-400` 与 `src/service/general_gr_service.cpp:376-400`：GraphMonitor 生成 final_path，追加 `total_cost`，并写 NOTICE / NEWDAPPER。

## 5. Pitfalls 卡片

<style>.pitcard{font-family:Inter,Arial,sans-serif;background:#faf8f4;border:1px solid #e4d8c8;border-radius:18px;padding:18px;margin:18px 0;color:#1f2933}.pitmeta{font-size:12px;font-weight:800;letter-spacing:.08em;color:#7c6853;text-transform:uppercase}.pittitle{font-size:28px;font-weight:850;letter-spacing:-.03em;margin:6px 0 12px}.pitgrid{display:grid;grid-template-columns:2fr 1fr;gap:14px}.pitmain{font-size:14px;line-height:1.7;color:#3f3f46}.pitpanel{border-top:4px solid #7c6853;background:#fffdf8;border-radius:10px;padding:12px;font-size:13px;line-height:1.6}.pitpanel b{display:block;margin-bottom:5px;color:#2f2f2f}.pitend{font-size:12px;color:#7c6853;font-weight:800;margin-top:12px}</style><div class="pitcard"><div class="pitmeta">Framework Pitfalls</div><div class="pittitle">不要把“图配置问题”误判成“服务入口问题”</div><div class="pitgrid"><div class="pitmain">入口层只负责取图、注入 GraphData、设置 timeout、运行 end data。若某个业务数据缺失，优先检查 `global.conf` 是否 include 了对应子配置、vertex 的 `@depend` 是否满足、表达式是否把 end result 切到预期分支。</div><div class="pitpanel"><b>最容易踩的点</b>nshead 与 protobuf 入口差异主要在响应类型和 send_response 方式；GraphData 注入、DynamicTimeout、GraphMonitor 的骨架基本一致。</div></div><div class="pitend">∎ 先看 end data，再看依赖闭包，最后才怀疑 RPC 框架。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title GraphEngine 请求问题排查 checklist
  desc 从入口、图选择、依赖注入、DAG 执行到观测输出逐层确认
  items
    - label 确认协议入口
      desc nshead 看 GenericGRService，protobuf 看 GeneralGRService；先确认实际进入哪条 run 方法
      done true
    - label 确认 graph_name
      desc ua 100/101 是 author_graph，ua 110 是 interest_card，其余默认 default
      done true
    - label 确认 GraphData 注入
      desc Request、UA、ExpInfo、graph_begin_us、ReqInfo 缺失会导致后续 Processor 依赖异常
      done true
    - label 确认 timeout scene
      desc ua 62/64/66/70/71 走 micro_feed_spark，ua 85/87/102 等走 micro_feed
      done true
    - label 查看 final_path
      desc NOTICE 与 NEWDAPPER 中的 final_path 能定位长尾 vertex、emit 与 cost_us
      done true
```

## 7. 证据来源

- `src/main.cpp:44-83`：服务初始化、协议注册、服务注册与启动。
- `src/service/gr_service.cpp:42-53`：GenericGRService 依赖插件获取。
- `src/service/gr_service.cpp:96-115`：图选择、ReqInfo 注入与 run 调用。
- `src/service/gr_service.cpp:222-300`：GraphData 注入与 `graph->run`。
- `src/service/gr_service.cpp:350-400`：Dapper 与 GraphMonitor final_path 输出。
- `src/service/general_gr_service.cpp:48-107`：protobuf 入口、图选择与 run 调用。
- `src/service/general_gr_service.cpp:262-400`：GeneralResponse、Health 与 GraphMonitor 输出。
- `conf/plugins/graph/global.conf:1-64`：default 图全局依赖与子配置 include 列表。
