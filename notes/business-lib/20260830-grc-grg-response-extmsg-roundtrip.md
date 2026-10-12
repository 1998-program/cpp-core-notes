# 2026-08-30 周日业务代码库理解：GRC/GRG 响应装配中的 extmsg 回传与再装配

> 日期：2026-08-30  
> 主题来源：当前没有可用的当日 daily-plan，回退到 `notes/weekly-topic-selection/daily-plan-20260529.json` 里的响应装配 / extmsg 边界候选主题；KU 正文未读取，业务背景需人工补充。  
> 范围：`src/processor/response.cpp`、`src/processor/video_launch/response_for_grg.cpp`、`src/process/response_function.cpp`、`src/process/new_response_function.cpp`，聚焦响应体装配、`predictor_extmsg` 透传、SIA 打点和二次解码。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.25fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">生成阶段</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`response.cpp` / `response_for_grg.cpp`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">把 extmsg、红点、意图分数、标题改写和集合标记装进响应 item。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">传输阶段</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`predictor_extmsg` / `predictor_extmsg_bytes`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">GRC 侧编码后的 payload 进入内容 item，GRG 侧按 bytes 或 base64 再读出。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">消费阶段</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`new_response_function.cpp` / `response_function.cpp`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">后续装饰策略、debug 读取和 first_ext_msg 合并继续消费同一段 extmsg。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">GRC 装配 extmsg</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">base64 / bytes 透传</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">GRG 解码再装饰</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">二次合并 / 打点</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRC / GRG extmsg roundtrip and response assembly
participant "GRC response.cpp" as GRC_R
participant "GRC response_for_grg.cpp" as GRC_V
participant "content_item.ext" as EXT
participant "GRG response_function.cpp" as GRG_R
participant "GRG new_response_function.cpp" as GRG_N
participant "SIA / log" as SIA
participant "Client" as C
GRC_R -> EXT : extmsg.SerializeToString()
GRC_R -> EXT : set_predictor_extmsg(base64)
GRC_R -> SIA : final_score_norm / intent / trigger id
GRC_V -> EXT : set_predictor_extmsg(base64)
GRC_V -> EXT : set_collection_id / bind ids / ai sentence
GRG_N -> EXT : prefer bytes if present
GRG_N -> EXT : else base64_decode + ParseFromString()
GRG_R -> EXT : decorate strategies update extmsg
GRG_R -> EXT : SerializeToString() + base64_encode()
GRG_R -> SIA : final queue stats / mark counters
GRG_R --> C : response.release()
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title 响应装配链的 6 个关键字段
  desc 这些字段决定 extmsg 如何穿过 GRC/GRG、如何被下游重新装配
  items
    - label predictor_extmsg
      desc `src/processor/response.cpp:4218-4223` 把 extmsg 串化并 base64 后写入内容项
      icon mdi/package-variant
    - label predictor_extmsg_bytes
      desc `src/process/new_response_function.cpp:81-95` bytes 优先，snappy 解压后直接进入 extmsg
      icon mdi/package-variant-closed
    - label first_ext_msg
      desc `src/process/new_response_function.cpp:195-205` 作为首条 item 的合并起点
      icon mdi/star-circle-outline
    - label final_score_norm
      desc `src/processor/response.cpp:4227-4228` 写入最终分数，和 extmsg 一起构成结果语义
      icon mdi/chart-line
    - label collection_id / bind ids
      desc `src/processor/response.cpp:4256-4293` 为短剧/合集/绑 id 提供再排序标识
      icon mdi/link-variant
    - label SIA counter
      desc `src/process/response_function.cpp:154-189` 记录最终队列、打点和样本计数
      icon mdi/chart-timeline-variant
```

## 3. 业务链路拆解
### 3.1 GRC 侧：extmsg 是响应的一部分，不是附加日志
- `src/processor/response.cpp:4218-4223`：`extmsg` 在最终落盘前被串化并 base64 后写入 `content_item_ptr->mutable_ext()->set_predictor_extmsg()`。这意味着后续所有消费方都依赖这条编码约定，而不是依赖原始内存对象。
- `src/processor/response.cpp:4229-4289`：同一段响应装配里继续注入热搜卡替换标记、短剧/合集 `collection_id`、`other_bind_id_vec` 和 AI 文案字段，说明 extmsg 只是大响应对象中的一部分，但它承载的是下游会二次消费的结构化语义。
- `src/processor/response.cpp:4296-4356`：标题改写、推荐语、动机类型等字段继续写回 `RespItemExt`，这让“响应结果”本身成为一条多阶段装配链，而不是单次赋值。

### 3.2 GRG 侧：响应装饰会再次读写 extmsg
- `src/process/response_function.cpp:116-121`：GRG 在装饰阶段先读取 extmsg，再做策略装饰，然后把结果重新 SerializeToString 并 base64 回写。这里不是单纯透传，而是“读 -> 改 -> 写”的循环。
- `src/process/response_function.cpp:151-189`：响应释放前还要汇总 funnel / queue 指标和 SIA 计数，说明 extmsg 改写与观测打点共享同一条收束路径。
- `src/process/new_response_function.cpp:81-121`：如果内容项携带 bytes，优先走 snappy；否则回退到 base64 + ParseFromString。这个分流说明业务链已经为不同下游版本留了兼容口子。

### 3.3 再装配的顺序不能乱
- `src/process/new_response_function.cpp:193-230`：`add_not_show_info()` 会把 `_predictor_extmsg` 重新解码到 `_ext_msg`，再和 `first_ext_msg` 合并 `dnn_q`、`sketchy_not_show_rid_info` 等字段。合并动作依赖首条响应已经存在，不是独立可运行的逻辑。
- `src/process/new_response_function.cpp:232-240`：继续把剩余 `dnn_q` 和 `ctr_not_show_rid` 补回去，说明这个阶段处理的是“首条 + 剩余候选”合并，而不是简单复制。
- 这条顺序链和 `response.cpp` 的编码顺序是对偶关系：先编码好，后在 GRG 中复原并改写。任何一端顺序漂移都会让字段缺失或重复。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">extmsg 不是一次性 payload，而是跨层可重放的响应载体</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">如果只查 GRC 的写点，会漏掉 GRG 侧的二次 Parse 和 decorator。很多线上问题不是“没写进去”，而是“写进去了但后续读法不同”，比如 bytes 优先和 base64 回退的路径不一致。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`response.release()` 之前必须完成 extmsg 合并、SIA 统计和所有内容项装饰。提前释放会把仍在使用的响应对象切掉，问题通常表现成缺字段而不是明显崩溃。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 判断顺序：GRC 写入 → 传输形态 → GRG 解码 → 装饰策略 → 统计/释放</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title GRC / GRG 响应链排查清单
  desc 适用于 extmsg 丢失、装饰字段缺失、bytes/base64 不兼容、统计不全的问题
  items
    - label 检查 GRC 写点
      desc 确认 `predictor_extmsg` 在 GRC 侧确实被写入，并区分 base64 与 bytes 形态
      done true
    - label 检查 GRG 读点
      desc 确认 `new_response_function` 能识别 bytes 优先路径和 base64 回退路径
      done true
    - label 检查再装配顺序
      desc 首条 `first_ext_msg` 必须先建立，再合并剩余 dnn_q / not_show 字段
      done true
    - label 检查装饰策略
      desc `decorate_ext_msg` 和 `decorate_content_item` 是否都被执行到
      done true
    - label 检查释放前收束
      desc `response.release()` 前完成 SIA、funnel 和 queue 统计
      done true
    - label 检查兼容字段
      desc `final_score_norm`、`collection_id`、`other_bind_id_vec` 与 extmsg 一起校验
      done true
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/processor/response.cpp:4218-4356`
- `src/process/response_function.cpp:116-189`
- `src/process/response_function.cpp:2496-2503`
- `src/process/response_function.cpp:4800-4804`
- `src/process/new_response_function.cpp:81-121`
- `src/process/new_response_function.cpp:193-240`

## 7. 说明
当前运行环境未发现 2026-08-30 的 daily-plan 文件，也没有读取 KU 正文；本笔记使用本地代码包与历史候选主题回退生成，KU/业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-01T19:21:32.520814
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析：GRC/GRG 响应装配中的 extmsg 回传与再装配

## 1. 分析摘要

- 这次技术点的核心不是单次“写字段”，而是 **`extmsg` 在 GRC → 传输 → GRG → 再装配 → 打点** 的完整闭环。根据笔记中的链路，`src/processor/response.cpp` 负责首次装配与编码，`src/process/new_response_function.cpp` 和 `src/process/response_function.cpp` 负责解码、合并、再写回与收束统计。说明该能力已经从“附属日志”演化为“响应体的一部分”，迁移时必须按协议级别处理。

- 从扫描结果看，**feeda-mv-grc 的相关处理面更广**：发现 10 个目标文件涉及，且 `std::vector` / `std::string` / `std::unordered_map` 使用非常密集，说明该库具备承载这类响应链改造的基础；**feeda-mv-grg 目前只发现 1 个相关文件**，说明 GRG 侧经验更集中、改造面更窄，但也意味着一旦需要统一 extmsg 处理，收益会比较直接。整体判断：**迁移潜力中高，适合以统一 codec + 装配顺序约束的方式落地**。

## 2. 代码库详情

### feeda-mv-grg

- 扫描到的目标库使用文件较少，仅发现 1 个文件：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 这说明 GRG 侧与“响应后置策略/装饰规则”相关的代码点较集中，适合作为 **再装配、二次解码、字段保留** 的接入位置。
- 现有基础容器使用非常多：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 典型参考代码：
  - `model/model.h`
  - `model/paddle_model.h`
- 结论：
  - GRG 侧并不是没有相关能力，而是 **extmsg 类逻辑尚未形成大面积铺开**。
  - 适配时更适合优先在策略层和响应后处理层做统一封装，避免把 codec 逻辑散落到各个模型类中。

### feeda-mv-grc

- 扫描到的目标库使用文件较多，共发现 10 个文件，覆盖面更广：
  - `processor/new_adjust/precise_score_init.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
- 这说明 GRC 侧更接近 **响应生成、特征聚合、过滤、打分初始化** 的主链路，天然适合承接 `predictor_extmsg`、`collection_id`、`bind id` 等结构化字段。
- 现有基础容器同样非常成熟：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件
- 典型参考代码：
  - `service/grc_http_service.cpp`
- 结论：
  - GRC 侧具备较好的工程承载能力，适合先做 **extmsg 的统一写入、序列化与兼容输出**。
  - 如果要推广到更多 processor 文件，建议先建立一套共用封装，而不是每个文件重复处理 base64 / bytes / ParseFromString。

## 3. 💡 适用性评估与建议

- **建议 1：在 `src/processor/response.cpp` 和 GRC 对应生成链中统一封装 `extmsg` 编码出口**
  - 场景：首次生成响应体时，将 `extmsg.SerializeToString()`、base64 编码、`predictor_extmsg` 写入集中到一个 helper 中。
  - 原因：避免 `response.cpp:4218-4223` 这类逻辑在多个文件重复，降低编码格式漂移风险。
  - 可迁移目标：
    - `processor/new_adjust/precise_score_init.cpp`
    - `processor/multi_factor/subcate_future_factor_gen.cpp`
    - `processor/multi_factor/ltr_factor_gen_scene.cpp`

- **建议 2：在 `src/process/new_response_function.cpp` 风格的逻辑中实现“bytes 优先、base64 回退”的统一解码器**
  - 场景：GRG 消费响应时，先读 `predictor_extmsg_bytes`，只有缺失时才走 base64 + `ParseFromString()`。
  - 原因：这条路径已经在笔记里明确是兼容策略，适合抽成标准适配层。
  - 可参考实现：
    - `src/process/new_response_function.cpp:81-121`
  - 适合落点：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
    - 任何需要二次装饰响应的后处理文件

- **建议 3：把 `first_ext_msg` 合并顺序固化为“先首条，再补剩余候选”**
  - 场景：在 `src/process/new_response_function.cpp:193-240` 这类逻辑中，先建立首条响应，再合并 `dnn_q`、`sketchy_not_show_rid_info`、`ctr_not_show_rid` 等字段。
  - 原因：这是强顺序依赖，顺序错了会出现字段丢失或重复。
  - 可优先检查文件：
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
    - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
  - 适配建议：
    - 不要在过滤/重排阶段提前重建 extmsg 对象，尽量只做字段补充。

- **建议 4：在 `src/process/response_function.cpp` 和 GRC 的收束链路中补齐 SIA / 统计 / 释放前校验**
  - 场景：响应释放前必须完成 extmsg 合并、统计上报和打点。
  - 原因：这类问题通常不是崩溃，而是“字段缺失、统计缺失”，排查成本高。
  - 可参考逻辑：
    - `src/process/response_function.cpp:151-189`
  - 建议加的校验：
    - `final_score_norm`
    - `collection_id`
    - `other_bind_id_vec`
    - `predictor_extmsg` 是否成功回写

- **建议 5：在 `service/grc_http_service.cpp` 这类服务入口增加 extmsg 兼容性日志**
  - 场景：请求入口层需要快速判断当前是 bytes 还是 base64 路径，便于定位线上兼容问题。
  - 原因：GRC 侧 `std::unordered_map`、`std::string` 使用量很高，适合做轻量诊断聚合。
  - 建议关注：
    - payload 长度
    - 解码失败次数
    - `ParseFromString()` 返回值
    - 字段缺失率

## 4. ⚠️ 引入风险与限制

- **风险 1：bytes / base64 双协议不一致**
  - 如果 GRC 写入的是 base64，而 GRG 侧优先按 bytes 解码，可能直接导致 extmsg 缺失。
  - 需要在编码端和解码端统一版本策略，最好加显式字段标识或兼容探测。

- **风险 2：再装配顺序敏感**
  - `first_ext_msg`、`dnn_q`、`not_show` 等字段存在强依赖顺序。
  - 如果在 `response_function.cpp` 之后再做过滤、重排或提早释放对象，容易出现“看起来有数据，实际上合并错位”。

- **风险 3：序列化与 base64 带来额外 CPU / 内存开销**
  - extmsg 在 GRC → GRG 往返过程中至少经历串化、编码、解码、反序列化，多次拷贝对高 QPS 场景不友好。
  - 需要关注热路径上的对象复用、移动语义和 buffer 复用，避免在 processor 链路里反复创建临时字符串。

- **风险 4：可观测性不足会放大排障成本**
  - 如果只在 GRC 写点看日志，而不看 GRG 的二次 Parse 和 decorator，线上问题会表现为“字段突然没了”，但日志并不直接报错。
  - 建议把解码失败、字段缺失、释放前统计不完整都纳入统一埋点。

如果你愿意，我可以继续把这份内容整理成你笔记里可直接粘贴的 **“业务代码库适配分析”正式章节**，并补成更像技术文档的语气。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
