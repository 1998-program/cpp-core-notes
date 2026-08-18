# 2026-08-12 周三代码理解：GRC 成本/CPU 性能优化热点复盘

> 日期：2026-08-12  
> 主题来源：2026-08-12 daily-plan 缺失，按历史未覆盖主题 fallback 到 GRC 成本/CPU 热点；KU 正文未逐篇读取，需人工补充实验口径与收益归因。  
> 服务：`feeda-mv-grc`  
> 范围：分析调权、过滤、响应组装、序列化和回写阶段的 CPU 热点，重点看 `Set2Set`、`cache_queue`、响应出口和业务解释边界。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px}.arch-layer{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.arch-layer h3{margin:0 0 8px;font-size:15px;color:#102a43}.arch-box{border:1px solid #bcccdc;border-radius:8px;padding:8px 10px;margin:6px 0;background:#f8fafc;font-size:13px;line-height:1.4}.arch-box.key{background:#eefcf3;border-color:#8fd19e}.arch-arrow{font-size:18px;text-align:center;color:#627d98;margin:4px 0}.arch-title{font-size:20px;font-weight:800;margin:0 0 12px;color:#102a43}</style><div class="arch-title">GRC CPU 热点从请求到响应的放大链路</div><div class="arch-wrap"><div class="arch-layer"><h3>业务入口</h3><div class="arch-box">`src/service/grc_service.cpp`<br>服务入口与请求接入</div><div class="arch-arrow">↓</div><div class="arch-box">`src/request/grc_request.cpp`<br>请求构建、字段补齐与过滤条件计算</div><div class="arch-arrow">↓</div><div class="arch-box key">调权 / 策略判定 / 场景分流</div></div><div class="arch-layer"><h3>热点区</h3><div class="arch-box key">`SerializeToString()` / `ParseFromString()`</div><div class="arch-box">`std::vector`、`push_back`、临时拷贝</div><div class="arch-box">`cache_queue`、响应聚合、重复组包</div><div class="arch-arrow">↓</div><div class="arch-box">CPU 抖动、P99 拉长、带宽放大</div></div><div class="arch-layer"><h3>业务出口</h3><div class="arch-box">`src/process/response_function.cpp`</div><div class="arch-arrow">↓</div><div class="arch-box">结果回写、extmsg 透传、最终响应出口</div><div class="arch-arrow">↓</div><div class="arch-box">用户侧体验与实验指标归因</div></div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "grc_service" as SVC
participant "filter / rank" as FILT
participant "response_function" as RESP
participant "protobuf encoder" as PB
Client -> SVC : request
SVC -> FILT : route + filter + score
FILT -> FILT : build response candidates
FILT -> PB : SerializeToString()
PB -> RESP : encoded payload
RESP -> RESP : merge extmsg / re-pack
RESP -> Client : final response
note over FILT,PB
high cost usually comes from
rebuild + copy + encode
end note
@enduml
```

## 2. 热点结构信息图

```infographic
infographic list-grid-badge-card
data
  title GRC 成本热点拆分
  desc 从请求入口到响应出口的高频成本点
  items
    - label 请求构建
      desc 字段补齐、条件计算、场景分流
      value 1
      icon mdi:clipboard-text
    - label 策略/调权
      desc 过滤链路和候选重排
      value 2
      icon mdi:tune
    - label 响应组装
      desc extmsg 合并、候选回写
      value 3
      icon mdi:application-import
    - label 序列化出口
      desc 消息编码与重复拷贝
      value 4
      icon mdi:serialize
```

## 3. 关键结论

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #d9e2ec;border-left:4px solid #2d6a4f;border-radius:10px;padding:12px 14px;margin:14px 0;color:#1f2937"><div style="font-size:14px;font-weight:800;color:#102a43;margin-bottom:6px">结论</div><div style="font-size:13px;line-height:1.6">业务侧看到的 CPU 热点通常不是单点策略函数，而是请求构建、候选聚合、响应回写和序列化一起叠加出来的。排查时要把“业务意义上的慢”拆成“构建慢”“聚合慢”“编码慢”三段看。</div></div>

## 4. Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px"><div style="font-weight:800;color:#102a43;margin-bottom:6px">Pitfall 1</div><div style="font-size:13px;line-height:1.6">把所有 CPU 增长都归因给策略模型是错误的。响应拼装和序列化往往更容易放大尾延迟。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px"><div style="font-weight:800;color:#102a43;margin-bottom:6px">Pitfall 2</div><div style="font-size:13px;line-height:1.6">只看单条链路的耗时也不够。要同时看消息大小、候选数和重组次数。</div></div></div>

## 5. 调试 Checklist

```infographic
infographic list-column-done-list
data
  title 调试 Checklist
  items
    - label 核对 request -> filter -> response 的阶段耗时
      done true
    - label 检查是否存在重复组包
      done true
    - label 检查候选列表是否频繁扩容
      done true
    - label 核对 extmsg 与响应体的放大比
      done true
    - label 比较优化前后的 P99 与消息大小
      done true
```

## 6. 证据来源

- `notes/base-lib/20260707-protobuf-serialize-flatbuffers-hotspots.md`
- `notes/business-lib/20260707-grc-cpu-cost-optimization-hotspots.md`
- `notes/business-lib/20260724-grc-video-launch-response-contract.md`
- `src/service/grc_service.cpp`
- `src/request/grc_request.cpp`
- `src/process/response_function.cpp`


---

## 七、业务代码库适配分析
> **分析时间**：2026-08-18T19:02:41.488893
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

> 说明：以下将“目标技术”泛指你们准备迁移/试点的高性能实现方案（通常用于减少 `std::vector` / `std::string` / `std::unordered_map` 带来的构建、拷贝和序列化开销）。

## 1. 分析摘要

- 从扫描结果看，**feeda-mv-grc** 是更适合优先适配的代码库。它在 10 个文件中已经出现了目标技术相关使用，且 `std::vector`、`std::string`、`std::unordered_map` 的存量规模很大，分别达到 **8520 / 7267 / 2860** 次，说明请求构建、候选聚合、响应组装等链路存在明显的优化空间。
- **feeda-mv-grg** 目前只在 **1 个文件**中发现目标技术使用，迁移基础较弱，但由于其自身 `std::vector`、`std::string`、`std::unordered_map` 也有较大存量，说明在候选生成、规则判断和模型输入组装环节仍然具备局部试点价值。整体上看，**grc 适合做规模化试点，grg 适合做局部验证**。

## 2. 代码库详情

### feeda-mv-grg：序列生成服务

- **目标技术使用现状**
  - 仅发现 **1 个文件**使用目标技术：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 这说明 grg 目前更多还是停留在“局部试用”阶段，尚未形成统一迁移面。

- **现有 std 等价物使用规模**
  - `std::vector`：**1969 次**，分布在 **356 个文件**
  - `std::string`：**2443 次**，分布在 **425 个文件**
  - `std::unordered_map`：**734 次**，分布在 **205 个文件**

- **典型热点代码位置**
  - `model/model.h`
  - `model/paddle_model.h`
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- **判断**
  - grg 的热点更偏向**候选列表传递、模型推理输入构建、规则过滤**。
  - 由于目标技术只在单点出现，适合先在 `low_clarity_diversity_rule.cpp` 做基准对比，再决定是否扩展到 `model/` 目录下的接口层。

---

### feeda-mv-grc：召回汇聚服务

- **目标技术使用现状**
  - 已发现 **10 个文件**使用目标技术，分布相对更广：
    - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
    - `operator/adjuster/sketchy/duanju_adjuster.cpp`
    - `processor/multi_factor/ltr_factor_gen_scene.cpp`
    - `processor/new_adjust/precise_score_init.cpp`
    - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - 说明 grc 里已经存在一定迁移基础，可以直接选取热点模块继续扩散。

- **现有 std 等价物使用规模**
  - `std::vector`：**8520 次**，分布在 **1290 个文件**
  - `std::string`：**7267 次**，分布在 **1247 个文件**
  - `std::unordered_map`：**2860 次**，分布在 **646 个文件**

- **典型热点代码位置**
  - `service/grc_http_service.cpp`
  - `src/service/grc_service.cpp`
  - `src/request/grc_request.cpp`
  - `src/process/response_function.cpp`
  - `process/response_function.cpp`
- **判断**
  - grc 的业务链路和你笔记里提到的热点高度吻合：**请求构建 -> 调权/过滤 -> 响应组装 -> 序列化出口**。
  - 这里的收益通常不是单个函数，而是**多个容器构建、临时对象拷贝、重复组包、编码放大**叠加后的 CPU 和尾延迟收益。

## 3. 💡 适用性评估与建议

- **优先在 `service/grc_http_service.cpp` 和 `src/process/response_function.cpp` 做试点**
  - 这两处属于请求出口和响应回写边界，最容易出现 `resp_str`、`extmsg`、子列表反复拼接、重复序列化等问题。
  - 建议优先替换或包裹这些路径中的高频容器构建逻辑，重点观察：
    - 响应体大小是否膨胀
    - `SerializeToString()` / `ParseFromString()` 是否减少
    - P99 是否下降

- **在 `processor/filter/user_explore_interest_ugc_filter_operator.cc` 和 `processor/multi_factor/ltr_factor_gen_scene.cpp` 做候选聚合侧优化**
  - 这两类文件通常会构建候选集合、特征 map、分场景列表，容易触发 `std::vector` 扩容和 `std::unordered_map` rehash。
  - 如果目标技术提供更轻量的容器或更少拷贝的组织方式，这里是很好的迁移切入点。
  - 建议重点检查是否存在：
    - 小对象频繁创建
    - 临时容器传值
    - 多次 `push_back` 导致扩容

- **把 `operator/adjuster/sketchy/duanju_adjuster.cpp`、`processor/new_adjust/precise_score_init.cpp` 作为“中等收益”模块**
  - 这些模块通常位于策略/调权中段，既有一定 CPU 占用，也不至于像响应出口那样强依赖协议兼容。
  - 适合作为灰度试点：
    - 先替换内部缓存容器
    - 再观察是否影响排序稳定性、特征一致性和线上结果分布

- **在 grg 中优先参考 `strategy/diversity/rule/low_clarity_diversity_rule.cpp`**
  - 这是目前已发现的目标技术使用点，可作为 grg 的实现参考和迁移模板。
  - 如果这个文件里的实现表现稳定，可以继续向 `model/model.h`、`model/paddle_model.h` 这类候选传递接口扩展。
  - grg 的适配思路建议从“规则链路局部替换”开始，而不是一开始大面积重构。

- **结合你笔记里的热点结论，优先盯住“构建慢、聚合慢、编码慢”三段**
  - 业务上慢不一定是策略本身慢，更多时候是：
    - 请求字段补齐时的临时对象创建
    - 候选列表反复拷贝
    - 响应组装和序列化放大
  - 所以建议将目标技术用于**减少构建次数和拷贝次数**，而不是只替换语法层面的容器类型。

## 4. ⚠️ 引入风险与限制

- **语义一致性风险**
  - 容器替换后，元素顺序、迭代稳定性、重复键处理、空值行为可能与原实现不同。
  - 对 `process/response_function.cpp` 这类业务出口代码尤其要谨慎，避免影响协议字段顺序或回包结构。

- **序列化与 ABI 兼容风险**
  - 如果目标技术涉及更换序列化方式或对象布局，可能影响：
    - 线上老版本兼容
    - protobuf/JSON 的字段映射
    - 跨模块 ABI 稳定性
  - 建议先在内部中间态对象上试点，不要直接改业务协议边界。

- **收益不一定线性**
  - `std::vector` / `std::string` / `std::unordered_map` 的高频使用不代表替换后一定有显著收益。
  - 很多时候真正瓶颈是重复组包、数据复制和编码放大，而不是单次容器开销本身。

- **需要完整的线上指标闭环**
  - 迁移前后必须同时看：
    - CPU
    - P99 / P999
    - 消息大小
    - QPS
    - 结果一致性
  - 否则容易出现“CPU 降了，但响应体更大、尾延迟更差”的反效果。

如果你愿意，我可以继续把这份分析整理成**可直接放进技术笔记的正式章节版本**，或者补一版**“grg / grc 分别的迁移优先级表”**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
