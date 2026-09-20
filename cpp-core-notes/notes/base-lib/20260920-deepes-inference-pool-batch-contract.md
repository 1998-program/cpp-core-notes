# 2026-09-20 基础库理解：DeepEs 热加载池与 batch 推理并发合同

> 日期：2026-09-20（周日）  
> 主题选择：未发现 2026-09-20 有效 daily-plan；最近笔记已经覆盖 fork/framework、gr-rank ContextDNN、Deepes quota 等相邻主题，因此按“邻近未覆盖主题”回退到 **DeepEsComponent 热加载池、模型就绪闸门、DeepEsGraph 选图与 InferProcessor batch 并发合同**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：gr-rank 启动初始化、`DeepEsComponent` reload/pool、`GRRService::predict` 的 model/extractor 校验与图选择、`InferProcessor` 的 batch 消费、`DeepEs::feed_forward` 和 Future join。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">启动与热加载</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">main.cc → DeepEsGraph / Component</div><div style="margin-top:8px;font-size:12px;color:#52606d;">启动先初始化 DeepEsGraph；DeepEsComponent 通过 reloader 注册配置事件并提供池化实例。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">请求闸门与图选择</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">GRRService::predict</div><div style="margin-top:8px;font-size:12px;color:#52606d;">检查 model/extractor 就绪，再将 request_type 与 model 映射到 str_graph，并获取 graph instance。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">批执行与收口</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">InferProcessor → DeepEs</div><div style="margin-top:8px;font-size:12px;color:#52606d;">完整 batch 进入 bthread_async，尾 batch 原地执行；所有 Future join 后才返回结果。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">model config / reload</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">check_model_ok</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">graph instance + batch</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;padding:10px;text-align:center;color:#334155;font-size:13px;">feed_forward + join</div></div></div>

**一句话结论：** DeepEs 推理的同步边界不是 `bthread_async()` 返回，而是模型实例已经通过就绪校验、每个 batch 的结果写入完成且所有 Future `get()` 返回之后；任何提前释放或提前读结果都会破坏这条合同。

## 1. 请求到推理的核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title DeepEs 热加载池到 batch 推理
participant "main.cc" as MAIN
participant "DeepEsGraph" as GRAPH
participant "GRRService::predict" as RPC
participant "DeepEsComponent" as COMP
participant "InferProcessor" as INF
participant "DeepEs::feed_forward" as DEEP
participant "Future join" as JOIN
MAIN -> GRAPH : init()
MAIN -> COMP : component init / reloader start
RPC -> RPC : build InferReq
RPC -> COMP : check_model_ok(model)
alt model or extractor unavailable
  COMP --> RPC : error response
else ready
  RPC -> GRAPH : get_deep_es_instance(str_graph)
  RPC -> GRAPH : instance.call(infer_req, resp_data)
  GRAPH -> INF : process()
  INF -> INF : item_size / num_per_batch -> batch count
  loop complete batches
    INF -> INF : bthread_async(consume_data_func)
    INF -> DEEP : pack inputs + feed_forward(model)
  end
  INF -> INF : tail batch runs inline
  INF -> JOIN : future.valid() + future.get()
  JOIN --> GRAPH : all results ready
  GRAPH --> RPC : response maps
  RPC -> RPC : graph reset after send_response
end
@enduml
```

## 2. 运行时合同信息图

```infographic
infographic sequence-snake-steps-simple
data
  title DeepEs 推理的五个合同面
  desc 从配置热加载到结果可读必须按顺序满足
  items
    - label 热加载
      desc DeepEsComponent 初始化 DNN/ES 库，注册 reloader 事件并从当前 pool 借出 DeepEsPoolObject
      icon mdi:refresh-auto
    - label 就绪校验
      desc GRRService 先检查 deepes_model_name，再检查可选 extractor_name；失败通过 set_error 结束请求
      icon mdi:shield-check-outline
    - label 图选择
      desc request_type 与 deepes_model_name 共同决定 str_graph，随后获取 graph instance 并设置 trace 信息
      icon mdi:graph-outline
    - label 批处理
      desc InferProcessor 根据 context_size、pos_num 和 num_per_batch 预分配 results 与 item_contextss
      icon mdi:view-grid-outline
    - label 汇总
      desc 完整 batch 异步、尾 batch 同步；所有 valid Future get 完成后才交出结果
      icon mdi:source-merge
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 启动初始化与热加载池

`src/main.cc:54-78` 的 `init()` 先初始化 `FeedLocaltimeManager`，再初始化 `DeepEsGraph`、`ChannelsManager`、`ReusableRPCProtocol` 和过期缓存组件；任一关键初始化失败都会返回错误。`src/es/deep_es_component.cc:11-25` 中 `DeepEsComponent::init` 先调用 DNN/ES 全局初始化，再让 `_load` 初始化配置键，注册 reloader 的 load/check 回调，最后启动 reloader。

`src/es/deep_es_component.cc:27-34` 的 `get()` 从当前 `DeepEsPool` 借出对象；取不到 pool 直接记录 FATAL 并返回空对象。因此调用方不能把 `get()` 当成永远成功的普通 getter，必须检查返回对象和模型实例。

### 3.2 model/extractor 闸门早于图执行

`src/service/gr_rank_service.cc:120-149` 先把请求字段复制到 `InferReq`，再用 `DeepEsComponent::get_instance().get_deep_es()->check_model_ok()` 检查模型。模型未就绪时通过 `set_error` 返回 `-2`；如果请求携带 extractor 且找不到对应 extractor，则返回 extractor 错误。该顺序把配置错误挡在 `DeepEsGraph::get_deep_es_instance()` 和实际图执行之前。

`src/service/gr_rank_service.cc:150-170` 以及后续 request 分支将 `request_type`、model 名和 extractor 映射到 `deep_es`、`deep_es_refresh` 等 `str_graph`。因此“请求成功但走错模型”应先检查路由分支，而不能只检查模型服务是否存活。

### 3.3 InferProcessor 的 batch 尺寸决定 fan-out

`src/es/processor/infer_processor.cc:40-75` 从配置读取 `num_per_batch` 与 item embedding channel；默认上下文中的 `num_per_batch` 为 10。`src/es/processor/infer_processor.cc:88-114` 计算 `item_size`、`pos_num`、`num_of_batch = ceil(item_size / num_per_batch)`，并按 `num_of_batch * pos_num` 预分配结果。

`src/es/processor/infer_processor.cc:150-256` 每个消费范围先收集 ITEMBASE/ITEMNOPOS embedding 和 item context，再从池中拿到对应模型的 DNN/ES instances；每个 position 首次走 `pack_dnn_infer_data`，后续 position 使用 `update_dnn_pos_infer_data`，随后调用 `feed_forward` 写入对应结果槽。

### 3.4 完整 batch 异步，尾 batch 原地执行

`src/es/processor/infer_processor.cc:259-279` 只有 `range.size() == num_per_batch` 时才调用 `bthread_async`；最后不足一个 batch 的 range 直接调用 `consume_data_func`。随后遍历 Future，检查 `valid()` 后 `get()`。这是一种减少尾批线程开销的明确策略，也意味着监控中的并发数不一定等于 `ceil(item_size / num_per_batch)`。

### 3.5 DeepEs 内部还存在第二层并发

`src/es/deep_es.cc:17-67` 的 `DeepEsInference::run` 对 DNN/ES instance 列表从索引 1 开始异步执行，索引 0 在当前线程执行，之后统一 Future join。`src/es/deep_es.cc:521-537` 的 `DeepEs::feed_forward` 按模型名查找 inference；查不到模型会记录 FATAL 并返回 -1。外层 InferProcessor batch 并发和内层 model instance 并发叠加，排查延迟时应分别看 batch 数、instance 数和每次 feed_forward 成本。

### 3.6 graph reset 是响应后的资源收口

`src/service/gr_rank_service.cc:392-410` 获取图实例、执行 `instance.call`，失败时先 reset graph 再返回错误。成功路径在 `:412-447` 把 pos/nid/model 结果写入 protobuf，调用 reusable closure 的 `send_response()` 后再 reset graph。这里的 reset 发生在发包之后，不能提前挪到 response 填充之前。

## 4. 参数与失败表现对账

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title DeepEs 问题的四个定位层
  desc 同样表现为“结果少或请求失败”，根因可能来自不同层
  items
    - label pool / reload
      desc get() 取不到当前 DeepEsPool，或模型配置未被 reloader 接受；优先查初始化和 reload 日志
      icon mdi:reload-alert
    - label request gate
      desc check_model_ok 或 extractor 查找失败，通常在图执行前通过 set_error 返回
      icon mdi:gate-alert
    - label batch contract
      desc item_size、pos_num、num_per_batch 决定结果槽位和异步批次；tail batch 不创建 Future
      icon mdi:table-split-cell
    - label inference / reset
      desc feed_forward 查不到模型或 graph call 失败；成功发送后才 reset graph，不能混同为 protobuf 写出失败
      icon mdi:alert-decagram-outline
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#ffffff;border:1px solid #d7dee8;border-left:6px solid #3d5a80;border-radius:8px;padding:18px;margin:18px 0;color:#1f2937;line-height:1.6;"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800;">Pitfalls · DeepEs runtime</div><div style="font-size:24px;line-height:1.2;font-weight:900;margin:7px 0 12px;">“模型存在”不等于“这一批结果可读”</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:6px;padding:12px;font-size:14px;"><b>四个常见误判：</b><br>① 只看配置文件存在，不看 check_model_ok；② 只看外层 batch Future，不看 DeepEs 内层 instance Future；③ 把尾 batch 没有 bthread 当成漏算；④ 在 send_response 前 reset graph，导致响应填充依赖失效。</div><div style="background:#fff7ed;border-top:3px solid #f59e0b;border-radius:6px;padding:12px;font-size:14px;color:#7c2d12;"><b>核对顺序</b><br>reloader → pool get → model gate → extractor gate → str_graph → batch shape → pack/update → feed_forward → Future join → send_response → graph reset。</div></div><div style="margin-top:12px;font-weight:900;color:#3d5a80;">∎ 真正的完成点是“所有结果槽位已写入且 Future 已 join”，不是“线程已经启动”。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title DeepEs 热加载与并行推理排查清单
  desc 从服务启动、请求闸门一路核对到结果回填
  items
    - label 确认 DeepEsGraph 初始化
      desc main.cc 中 DeepEsGraph、ChannelsManager、ReusableRPCProtocol 初始化顺序正确
      done true
    - label 确认 reloader 状态
      desc DeepEsComponent register_event/start 成功，当前 pool 可以被 get()
      done false
    - label 确认模型就绪
      desc check_model_ok(deepes_model_name) 成功；失败码与 error_msg 不能被吞掉
      done false
    - label 确认 extractor
      desc 请求携带 extractor_name 时，FeatureExtractorManager 必须返回非空对象
      done false
    - label 确认图路由
      desc request_type、model、str_graph 和 graph instance 的映射符合预期
      done false
    - label 确认 batch 形状
      desc item_size、pos_num、num_per_batch 与 results/item_contextss 的预分配一致
      done false
    - label 确认尾 batch
      desc 不足 num_per_batch 的最后一批应原地执行，不应期待对应 Future
      done true
    - label 确认两层 join
      desc 外层 InferProcessor 和内层 DeepEsInference 都完成 valid/get 后再读结果
      done false
    - label 确认 reset 时机
      desc graph call 失败先 reset；成功路径 send_response 后再 reset
      done false
    - label 补齐业务背景
      desc 从 KU/模型平台确认 reload 周期、模型灰度、batch 调优和告警阈值
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/main.cc:54-78`：FeedLocaltimeManager、DeepEsGraph、ChannelsManager、ReusableRPCProtocol 和 ExpiredCache 初始化。
- `src/es/deep_es_component.cc:11-34`：DNN/ES 初始化、reloader 注册、pool 借出和失败行为。
- `src/service/gr_rank_service.cc:120-149`：InferReq 字段装配、model/extractor 就绪闸门。
- `src/service/gr_rank_service.cc:150-170`：request_type 到 graph/extractor 的初始路由分支。
- `src/service/gr_rank_service.cc:392-410`：graph instance 获取、call、失败 reset 和错误返回。
- `src/service/gr_rank_service.cc:412-447`：结果写回、send_response 与成功后的 graph reset。
- `src/es/processor/infer_processor.cc:40-75`：`num_per_batch` 配置读取和 channel 依赖。
- `src/es/processor/infer_processor.cc:88-114`：batch 数、结果槽位和上下文容器预分配。
- `src/es/processor/infer_processor.cc:150-256`：embedding 消费、pack/update、模型池借出和 feed_forward。
- `src/es/processor/infer_processor.cc:259-279`：完整 batch 异步、尾 batch 同步和 Future join。
- `src/es/deep_es.cc:17-67`：DNN/ES instance 两层并发与 Future join。
- `src/es/deep_es.cc:521-537`：按模型名调用 inference，缺失模型的失败行为。
- `conf/deep_es.yaml`：源码默认配置路径；当前代码快照未提供该文件正文，具体模型/instance 配置需人工补充。

> KU 正文未读取，需人工补充 DeepEsPool reload 的线上版本策略、模型灰度和回滚语义、`num_per_batch` 的容量基线、外层/内层并发上限，以及 graph reset 对请求复用池的监控口径。
