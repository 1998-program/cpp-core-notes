# 2026-07-24 周五代码理解：GRC video_launch 响应出口与 GRG 返回契约

> 本文基于本地 `feeda-mv-grc` 代码阅读生成；未获得可直接读取的 KU 背景文档，线上实验、指标口径和策略负责人需人工补充。

## 1. 架构全景图：从元数据补齐到 GRG 响应

<style>.arch-wrap{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:14px;padding:18px;margin:16px 0;color:#243b53}.arch-title{font-size:22px;font-weight:850;margin-bottom:12px;color:#102a43}.arch-grid{display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px}.arch-layer{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.arch-layer h3{margin:0 0 10px;font-size:14px;color:#102a43}.arch-box{border:1px solid #bcccdc;border-left:4px solid #3d5a80;border-radius:8px;padding:9px;margin:8px 0;background:#f8fafc}.arch-box strong{display:block;font-size:13px;color:#102a43}.arch-box span{display:block;font-size:12px;line-height:1.5;color:#52606d;margin-top:4px}.arch-arrow{text-align:center;font-weight:800;color:#486581;margin:8px 0}.arch-note{font-size:12px;color:#627d98;margin-top:10px}.arch-hot{border-left-color:#c2410c;background:#fff7ed}.arch-data{border-left-color:#047857;background:#ecfdf5}@media(max-width:820px){.arch-grid{grid-template-columns:1fr}}</style><div class="arch-wrap"><div class="arch-title">GRC video_launch Response Contract</div><div class="arch-grid"><div class="arch-layer"><h3>Graph Inputs</h3><div class="arch-box"><strong>FillMetaPipelineFunction</strong><span>读取候选内容并通过 GCMS/本地字典补齐卡片元数据。</span></div><div class="arch-box"><strong>LoadsQueue / SlotPreAssign</strong><span>保量、队列和坑位预分配结果作为响应排序与截断依据。</span></div><div class="arch-box"><strong>NewsResponseForGrg</strong><span>新闻沉浸式条件依赖，命中时合入视频响应出口。</span></div></div><div class="arch-layer"><h3>Response Assembly</h3><div class="arch-box arch-hot"><strong>ResponseForGrgFunction</strong><span>创建 `_response_for_grg`，清空旧响应，逐条写入 content、attachment、ext 特征和截断结果。</span></div><div class="arch-arrow">metadata + queue signals -> protobuf response</div><div class="arch-box arch-data"><strong>DNN Q / SIA / PCS</strong><span>把 average_q、队列长度、PCS quota 等诊断特征写入 ext_msg 或 attachment。</span></div></div><div class="arch-layer"><h3>Consumers</h3><div class="arch-box"><strong>GRG / upper graph</strong><span>消费 ResponseForGrg，继续做跨服务合并、展示控制或回传。</span></div><div class="arch-box"><strong>Debug logs</strong><span>通过 log_id 采样、SIA 计数和 funnel 顶点定位响应异常。</span></div><div class="arch-box"><strong>Experiment guards</strong><span>不同 UA、scene、实验条件会改变新闻合入、quota、截断和字段透传。</span></div></div></div><div class="arch-note">证据入口：`src/main.cpp` 注册服务，`grg_response.conf` 绑定响应顶点，`response_for_grg.cpp` 完成出口组装。</div></div>

## 2. 入口与配置绑定

`src/main.cpp:19-21` 引入 `GrcService`、HTTP service 和全局初始化，说明该服务不是单一算法库，而是 RPC 服务进程。video_launch 的图配置把最终响应出口拆成独立顶点：`conf/plugins/graph/video_launch/grg_response.conf:1-8` 声明 `ResponseForGrgFunction`，emit `_response_for_grg` 与 `_truncated_effect_queue_for_pcs`；`grg_response.conf:9-28` 继续声明新闻响应、loads 队列、IP cluster、slot 预分配和历史满意度等依赖。

这意味着排查“GRC 返回少量”“GRG 收到字段为空”“PCS 截断不符合预期”时，不应只看召回或 DataMerge。最终响应顶点本身会二次消费队列信号、实验参数和历史特征，它是跨服务契约的最后一道写口。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "GRG caller" as caller
participant "GrcService" as svc
participant "GraphEngine\nvideo_launch" as graph
participant "FillMetaPipelineFunction" as fill
participant "ResponseForGrgFunction" as resp
database "GCMS / dict" as gcms
caller -> svc: query video_launch
svc -> graph: build context and execute graph
graph -> fill: candidates + card ids
fill -> gcms: query_common / metadata lookup
gcms --> fill: title, media, card metadata
fill --> graph: FillMetaPipelineResult
graph -> resp: queues + meta + experiment context
resp -> resp: clear response, add content, write attachment/ext
resp --> graph: ResponseForGrg + TruncatedEffectQueueForPcs
graph --> svc: graph outputs
svc --> caller: protobuf response
@enduml
```

## 3. FillMeta 的职责边界

`src/processor/video_launch/fill_meta_pipeline.cpp:19-23` 定义 `FillMetaPipelineFunction`，初始化时把 `_fill_meta_pipeline_result` 和 `_card_fill_meta_result` 绑定到当前 graph vertex。`fill_meta_pipeline.cpp:27-31` 创建 `UpToCostEmit` 和 `ChannelPublisher<RidTmpInfo*>`，说明这里是 pipeline 风格的批处理节点，不是简单同步函数。

该文件在开头引入 `gcms_plugin.h` 与 `GcmsComponent`，并声明 `open_gcms_statistics` 开关，说明元数据补齐具有外部内容服务依赖和统计开关。`pipeline_card_type` 当前只包含 `241`，排查卡片元数据问题时要先确认 card type 是否进入该管线，否则后续响应出口即使正常也只能拿到不完整内容。

```infographic list-grid-badge-card
data
  title FillMeta 关键输入输出
  desc video_launch 响应前的元数据补齐检查点
  items
    - label 输入候选
      desc RidTmpInfo 与候选内容进入 ChannelPublisher
      icon mdi/file-tree
    - label 外部依赖
      desc GCMS 组件和本地字典共同补齐内容字段
      icon mdi/database-search
    - label 卡片白名单
      desc pipeline_card_type 当前关注 241 类型
      icon mdi/card-bulleted-outline
    - label 输出结果
      desc FillMetaPipelineResult 与 CardFillMetaResult 绑定 graph vertex
      icon mdi/export
    - label 成本观测
      desc UpToCostEmit 记录 fill_meta 前后耗时
      icon mdi/timer-outline
```

## 4. ResponseForGrg 的返回契约

`src/processor/video_launch/response_for_grg.cpp:102` 定义 `ResponseForGrgFunction`，继承 `StrategyFunction<DiversityListGeneratorStrategy>`。`response_for_grg.cpp:178-187` 进入 `process_impl` 后先 emit `_response_for_grg` 和 `_truncated_effect_queue_for_pcs`，随后 `response->Clear()`，这解释了为什么上游顶点留下的旧字段不会自然透传：最终出口会重新组装响应。

`response_for_grg.cpp:181-192` 的早期逻辑会创建响应对象、处理直播内容并进入主内容写入；`response_for_grg.cpp:225-231` 写 attachment，例如 actual_reqnum 和 UA 类型；`response_for_grg.cpp:570-578` 遍历 `_res_content_vec` 并为每个 content 写入平均 Q 特征与 SIA 计数。文件顶部的宏在 `response_for_grg.cpp:69-92` 反复调用 `ext_msg.add_dnn_q()`，把模型分数与缓存标记写成通用 DNN Q 结构。

这条链路的关键不是“把候选列表原样返回”，而是把候选、队列、实验、元数据和诊断字段折叠成 GRG 可消费的 ResponseForGrg。任何字段缺失都要区分三类原因：上游没有生产、配置没有声明 depend、出口顶点没有写入。

```infographic sequence-ascending-steps
data
  title ResponseForGrg 组装步骤
  desc 从 graph 输出到跨服务 protobuf 返回
  items
    - label Emit 输出对象
      desc `_response_for_grg` 与 `_truncated_effect_queue_for_pcs`
      icon mdi/source-branch
    - label Clear 旧响应
      desc 避免复用上下文时旧字段残留
      icon mdi/eraser
    - label 写内容列表
      desc zhibo/news/effect queues 逐类落入 content
      icon mdi/format-list-numbered
    - label 写 attachment
      desc actual_reqnum、ua_str_type、quota 诊断信息
      icon mdi/paperclip
    - label 写 ext_msg
      desc dnn_q、average_q、cache 标记等模型特征
      icon mdi/code-json
    - label 输出给 GRG
      desc 下游基于 ResponseForGrg 继续合并或展示
      icon mdi/export-variant
```

## 5. 调试顺序

当线上现象是“GRG 侧看不到 GRC 字段”时，建议按下面顺序排查：

```infographic list-column-done-list
data
  title Debug Checklist
  desc 响应出口问题优先按依赖链排查
  items
    - label 确认图配置加载了 `grg_response.conf`
      desc `function: ResponseForGrgFunction` 必须在目标 graph 中生效
      done true
    - label 检查 depend 是否声明
      desc 缺依赖时出口函数无法消费上游结果
      done true
    - label 检查 FillMeta 是否命中 card type
      desc 元数据缺失先看 `pipeline_card_type` 与 GCMS 返回
      done true
    - label 检查 `_res_content_vec` 是否为空
      desc DataMerge 有量不代表最终响应列表有量
      done true
    - label 检查 ext_msg 写入分支
      desc 模型分数、cache 标记和平均 Q 可能受实验条件控制
      done true
    - label 用 log_id 采样核对 SIA
      desc 出口顶点已有采样日志和计数字段，优先用现成观测
      done true
```

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:16px;margin:16px 0;color:#431407"><div style="font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#9a3412">Pitfalls</div><div style="font-size:22px;font-weight:850;margin:6px 0 10px;color:#7c2d12">不要把召回有量等同于响应有量</div><div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px"><div style="font-size:14px;line-height:1.65">video_launch 出口会重新清空并组装 ResponseForGrg。上游召回、DataMerge、FillMeta 任一阶段正常，都不能单独证明 GRG 最终收到完整字段。最终判断点必须落到 `ResponseForGrgFunction` 的 content、attachment 和 ext_msg 写入分支。</div><div style="border-left:4px solid #ea580c;padding-left:10px;font-size:13px;line-height:1.6;background:#fffbeb;border-radius:8px;padding-top:8px;padding-bottom:8px">常见误判：只查候选队列长度，不查 `_truncated_effect_queue_for_pcs`；只查 GCMS 命中，不查 card type；只查 protobuf content，不查 attachment/ext_msg。</div></div></div>

## 6. 证据来源

- `src/main.cpp:19-21`：服务入口引入 `GrcService`、HTTP service 和全局初始化。
- `conf/plugins/graph/video_launch/grg_response.conf:1-8`：`ResponseForGrgFunction` emit `_response_for_grg` 与 `_truncated_effect_queue_for_pcs`。
- `conf/plugins/graph/video_launch/grg_response.conf:9-28`：响应出口依赖新闻响应、loads 队列、IP cluster、slot 预分配和用户历史满意度。
- `src/processor/video_launch/fill_meta_pipeline.cpp:19-31`：FillMeta pipeline 绑定 graph vertex、创建成本记录和 channel publisher。
- `src/processor/video_launch/response_for_grg.cpp:69-92`：DNN Q / cache 标记写入 ext_msg 的宏。
- `src/processor/video_launch/response_for_grg.cpp:102-187`：`ResponseForGrgFunction` 定义、emit 响应对象并清空旧响应。
- `src/processor/video_launch/response_for_grg.cpp:225-231`：写入 attachment 中的请求数量与 UA 类型。
- `src/processor/video_launch/response_for_grg.cpp:570-578`：遍历结果内容并写入 average_q 与 SIA 计数。

## 7. 结论

本周建议把 GRC video_launch 的排查边界从“召回/DataMerge 是否产出候选”后移到“ResponseForGrg 是否完成最终契约写入”。`FillMetaPipelineFunction` 解决内容元数据可用性，`ResponseForGrgFunction` 解决跨服务返回结构可用性；两者中间由 `grg_response.conf` 的 depend/emit 连接。后续如果要做自动化扫描，可以围绕三类规则：graph 配置是否声明依赖、出口函数是否 Clear 后重写关键字段、ext_msg 是否受实验条件遗漏。
