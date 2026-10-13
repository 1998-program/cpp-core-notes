# 2026-09-19 基础库理解：bthread_async 并行预测与 Future 汇总合同
<!-- topic-nav:start -->
> 主题：`base/bthread-future` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260528-bthread-async-fan-out-future-join-usage-deep-dive.md)
<!-- topic-nav:end -->


> 日期：2026-09-19  
> 主题选择：未发现本日有效 daily-plan；历史计划中的 `bthread_async 批量并行模式与捕获安全` 尚未在当前 2026-09 系列形成对应双笔记，因此按邻近未覆盖主题回退。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：GRC `main.cpp` 启动、`ParallelPredictorPlugin::predict/parallel_predict` 的请求过滤与 Future 生命周期、`ParallelCtrRankProcessor` 的 batch 请求构造、结果回填和 SIA 归因。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.25fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">服务启动</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">main.cpp + GlobalInitializer</div><div style="margin-top:8px;font-size:12px;color:#52606d;">注册 reusable protocol，初始化组件、实验参数和 brpc Server。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">并行调度边界</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">ParallelPredictorPlugin</div><div style="margin-top:8px;font-size:12px;color:#52606d;">跳过非零状态；校验 request/response/req_type 对齐；为每个 RPC 任务创建 Future。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">完成与观测</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">get() + report_timeout</div><div style="margin-top:8px;font-size:12px;color:#52606d;">所有 Future join 后聚合 ret，并以等待区间上报 RPC 耗时。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">GraphVertex 输入</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">batch request</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">bthread_async</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;padding:10px;text-align:center;color:#334155;font-size:13px;">response 回填</div></div></div>

**一句话结论：** 这套并行合同的安全边界不是“启动了多少 bthread”，而是任务参数指向的 `rpc_name/request/response/req_type` 在所有 Future `get()` 完成前必须保持有效，并且每条并行支路都要经过状态过滤、尺寸对齐和最终 join。

## 1. 请求到结果的核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title ParallelPredictorPlugin 并行预测与 Future 汇总
participant "ParallelCtrRankProcessor" as PCR
participant "ParallelPredictorPlugin" as PP
participant "bthread_async" as BA
participant "RouterPlugin" as ROUTER
participant "PredictorRequest/Response" as PB
participant "Context/SIA" as OBS
PCR -> PCR : init_rpc_context_map()
PCR -> PCR : gen_request() 按 batch_nid_size 装请求
PCR -> PP : parallel_predict(rpc_context_map)
PP -> PP : get_dynamic_timeout(context, rpc_name)
loop 每个有效 rpc_context
  PP -> PP : 校验 request/response/req_type size
  PP -> BA : bthread_async(predictor_task, 参数)
  BA -> ROUTER : 执行 router_plugin->predict()
  ROUTER -> PB : 写 response
  BA --> PP : Future<int>
end
PP -> PP : 遍历 future_list
alt Future valid
  PP -> BA : future.get()
  BA --> PP : ret
else Future invalid
  PP -> PP : ret |= 1 + WARNING
end
PP -> OBS : report_timeout(wait_ms, ret)
PP --> PCR : 返回聚合 ret
PCR -> PCR : get_response() 回填 RidTmpInfo
@enduml
```

## 2. 并行合同信息图

```infographic
infographic sequence-snake-steps-simple
data
  title bthread_async 并行预测的五个合同面
  desc 任务创建、参数生命周期和结果汇总必须作为一个整体检查
  items
    - label 过滤
      desc status 非零的 rpc_context 不创建任务；避免把已失败分支再次送入 RouterPlugin
      icon mdi:filter-check-outline
    - label 对齐
      desc parallel_predict 要求 request、response、req_type 三个数组长度一致且非空
      icon mdi:format-align-center
    - label 捕获
      desc predictor_task 接收 rpc_name 指针以及 request/response 指针，调用方必须在 Future 完成前保持它们有效
      icon mdi:link-variant
    - label 汇总
      desc Future valid 才 get；无效 Future 置 ret bit 并记录 WARNING；每个任务返回值用按位或聚合
      icon mdi:source-merge
    - label 归因
      desc 所有 Future join 后再 report_timeout，随后由 ParallelCtrRankProcessor 解析 response 并写 SIA
      icon mdi:timer-check-outline
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 服务入口先注册 reusable protocol，再进入 Graph/HTTP 服务

`src/main.cpp:73-130` 的 GRC 入口依次解析 flags、加载日志、注册 `ReusableRPCProtocol`、初始化 `GlobalInitializer`/`ExpManager`/log funnel，然后以 `baidu_std_reuse` 启动 Generic GRC 与 HTTP service。`ParallelPredictorPlugin` 由组件注册宏提供，启动链本身不创建并行任务；并行发生在图处理器调用插件之后。

### 3.2 两种 predict API 共享“任务列表 + join”模式

`src/plugin/parallel_predictor.cpp:34-72` 的 map 版本针对每个 `PredictRpcContext` 创建一个 Future；`src/plugin/parallel_predictor.cpp:99-145` 的 `parallel_predict` 则展开每个 `ParallelPredictRpcContext` 内的 request 数组，为每个 batch request 创建 Future。两者都先 `reserve` Future 容器，随后逐项 `valid()`/`get()`，把返回码通过 `ret |= item.get()` 汇总。

这里的 `ret |=` 意味着返回值被当作错误位集合使用，而不是“最后一个任务的错误覆盖前序错误”。排查时应记录具体 `req_type` 或 map key，否则只能知道并行阶段失败，不能知道哪个批次失败。

### 3.3 捕获安全来自“指针只跨越任务窗口，且调用方等待完成”

`src/plugin/parallel_predictor.cpp:48-57` 和 `:120-129` 把 `&rpc_name`、request 指针、response 指针和 req_type 传入 `bthread_async`。`predictor_task` 在 `:148-167` 中解引用这些参数并调用 `RouterPlugin::predict`。当前实现随后立即遍历 `future_list` 并 `get()`，因此 `rpc_name` 引用和请求/响应对象至少要活到全部 Future 完成；如果未来改成异步返回或把 join 移到函数外，这个隐含前提就会失效。

### 3.4 ParallelCtrRankProcessor 先分批，再把响应映射回 rid

`src/processor/parallel_ctr_rank.cpp:34-56` 根据 `FEED_CTR_MAP` 建立可复用的 request/response 数组，并按 `batch_nid_size` 计算请求批数。`process` 在 `:58-88` 清理输出、初始化 map 和 `rid_map`，在 `:96-125` 根据 slide-short 实验选择 SID/请求类型并调用 `parallel_predict`。并行返回后，` :127-157` 跳过无效 context/空 response，再调用 `get_response`，最后把 `feed_ctr_result` 引用回原始候选。

### 3.5 计时口径是“任务提交后的等待与汇总区间”

`parallel_predict` 在创建所有 Future 后才记录 `start`，完成所有 `get()` 后记录 `end`，再调用 `Util::report_timeout`。因此该指标主要覆盖 bthread 任务执行等待和 join，不覆盖前面的 request 构造，也不等于单个最慢任务的完整端到端耗时。若监控上升，需同时检查 `gen_request`、RouterPlugin 后端耗时和 batch 数量。

## 4. 输入与错误边界对账

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title 并行预测的输入边界与失败表现
  desc 同一个 ret 非零可能来自输入不一致、任务参数错误或下游 RPC 失败
  items
    - label context status
      desc status != 0 的 context 被跳过；后续结果解析也跳过 valid=false 或 status=-1 的分支
      icon mdi:skip-next-circle-outline
    - label vector shape
      desc request/response/req_type 长度不一致或 request 为空时只记录 WARNING 并继续处理其他 context
      icon mdi:vector-difference
    - label task parameter
      desc RouterPlugin、request 或 response 为空时 predictor_task 返回 -1；单条失败通过 ret |= 保留
      icon mdi:alert-outline
    - label response parse
      desc get_response 失败转成 ERR_CTR_RANK_RESPONSE，不能把“预测成功”误判为“业务结果成功”
      icon mdi:code-json
    - label observability
      desc feed_ctr_predictor_rpc 的 SIA 区间包住 parallel_predict，便于区分并行 RPC 失败与结果解析失败
      icon mdi:chart-timeline-variant
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#ffffff;border:1px solid #d7dee8;border-left:6px solid #3d5a80;border-radius:8px;padding:18px;margin:18px 0;color:#1f2937;line-height:1.6;"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800;">Pitfalls · bthread_async</div><div style="font-size:24px;line-height:1.2;font-weight:900;margin:7px 0 12px;">并行返回，不等于并行结果已经可用</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:6px;padding:12px;font-size:14px;"><b>四个高风险误判：</b><br>① 只看 bthread 创建成功，不检查 Future valid；② 把 `ret |=` 当成单一最后错误码；③ 改成异步返回后忘记延长 `rpc_name` 与 protobuf 指针生命周期；④ 只看 predictor RPC 成功，不看 `get_response` 解析是否成功。</div><div style="background:#fff7ed;border-top:3px solid #f59e0b;border-radius:6px;padding:12px;font-size:14px;color:#7c2d12;"><b>核对顺序</b><br>context status → 三数组尺寸 → request/response 非空 → Future valid/get → ret 聚合 → report_timeout → response parse → SIA_END。</div></div><div style="margin-top:12px;font-weight:900;color:#3d5a80;">∎ 这段代码的真正同步点是 Future::get()，不是 bthread_async() 返回 Future 的那一刻。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title ParallelPredictor 并行故障排查清单
  desc 从图处理器输入一路核对到结果回填和耗时归因
  items
    - label 确认 rpc_context_map 状态
      desc status 非零的 context 是否被预期跳过，valid 是否在 init_rpc_context_map 中置为 true
      done true
    - label 确认三数组对齐
      desc request、response、req_type 长度相同且 request 非空
      done true
    - label 确认 batch 构造
      desc batch_nid_size、vfs_copy_result size、SID 和 queue/token 是否匹配实验分支
      done true
    - label 确认参数生命周期
      desc rpc_name、request、response 在所有 Future get 完成前不可释放或移动
      done false
    - label 确认 Future 汇总
      desc valid() 失败是否出现，get() 返回值是否被 ret |= 保留
      done false
    - label 区分执行与解析失败
      desc 对照 feed_ctr_predictor_rpc SIA 与 get_response 的 ERR_CTR_RANK_RESPONSE
      done false
    - label 检查最终引用
      desc feed_ctr_result 是否 ref 回 vfs_copy_result，空 response 是否被跳过并留有日志
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/main.cpp:73-130`：GRC 启动、ReusableRPCProtocol、GlobalInitializer、ExpManager 和 brpc service 注册。
- `src/plugin/parallel_predictor.cpp:34-72`：map 版本的 bthread_async、Future reserve、valid/get 和错误聚合。
- `src/plugin/parallel_predictor.cpp:99-145`：批量版本的尺寸校验、逐请求异步任务和 report_timeout。
- `src/plugin/parallel_predictor.cpp:148-167`：`predictor_task` 参数解引用、RouterPlugin 调用和失败返回。
- `src/plugin/parallel_predictor.h:59-90`：ParallelPredictorPlugin 公共接口与任务参数形态。
- `src/processor/parallel_ctr_rank.cpp:18-56`：Graph dependency、RPC context map 和 batch request/response 创建。
- `src/processor/parallel_ctr_rank.cpp:58-157`：处理生命周期、实验分支、并行调用、response 解析与结果回填。
- `src/processor/parallel_ctr_rank.cpp:162-240`：用户标识、SID、queue/token 和 request feature 构造。

> KU 正文未读取，需人工补充 bthread executor 的线程池容量、`Future<int, bthread::Mutex>` 的取消/超时语义、不同 RPC queue 的限流策略，以及 `report_timeout` 在生产监控中的告警阈值。