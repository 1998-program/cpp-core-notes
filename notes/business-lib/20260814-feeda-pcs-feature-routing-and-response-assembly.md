# 2026-08-14 周五代码理解：feeda-pcs 特征路由与响应装配边界

> 日期：2026-08-14  
> 主题来源：2026-08-14 daily-plan 文件未发现，按历史未覆盖主题 fallback 到 `feeda-pcs` 业务装配链路；KU/业务背景需人工补充。  
> 范围：只分析 `client_tag` 驱动的请求分支、特征字段装配、响应参数回填与日志出口；不展开全部 PCS 协议。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.15fr 1.85fr;gap:12px}.arch-col{display:flex;flex-direction:column;gap:12px}.arch-box{background:#fff;border:1px solid #dbe3ea;border-radius:10px;padding:12px;box-shadow:0 1px 0 rgba(15,23,42,.03)}.arch-title{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#5b6b7c;margin-bottom:8px}.arch-main{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.arch-mini{background:#f8fafc;border:1px solid #e5ecf2;border-radius:8px;padding:10px}.arch-mini strong{display:block;font-size:13px;margin-bottom:4px;color:#0f172a}.arch-mini span{display:block;font-size:12px;line-height:1.45;color:#475569}.arch-flow{display:flex;flex-direction:column;gap:8px}.arch-flow .step{padding:10px 12px;border-radius:8px;border:1px solid #dbe3ea;background:#fff;font-size:12px;line-height:1.45}.arch-flow .step b{display:block;color:#0f172a;margin-bottom:3px}.arch-note{font-size:12px;line-height:1.55;color:#475569}.tag{display:inline-block;padding:2px 8px;border-radius:999px;background:#e8f1ff;color:#1d4ed8;font-size:11px;font-weight:700;margin-right:6px}</style><div class="arch-wrap"><div class="arch-col"><div class="arch-box"><div class="arch-title">业务分流</div><div class="arch-flow"><div class="step"><b>client_tag = gr</b>走标准 `PCSReqConv::convert`，把召回上下文、通用字段和 feature 请求合成 PCS 请求。</div><div class="step"><b>client_tag = gr_sample</b>走 sample / 偏好字段路径，和同步装配方式配合，适合调试与样本观测。</div><div class="step"><b>client_tag = lite-gr</b>走 lite 变体，对应更轻量的请求和更强的触发控制。</div><div class="step"><b>client_tag = shoubai-gr</b>走独立业务 tag，体现同一 PCS 服务对不同产品线的路由隔离。</div></div></div><div class="arch-box"><div class="arch-title">结果出口</div><div class="arch-main"><div class="arch-mini"><strong>全局参数</strong><span>从 `PcsResponse` 解析并注入上下文，后续推荐/实验链路可直接读取。</span></div><div class="arch-mini"><strong>样本日志</strong><span>`sample_log` 记录特征命中与回填结果，便于离线核对。</span></div><div class="arch-mini"><strong>feature 结果</strong><span>按 `parameter_result` 遍历，决定哪些字段进入最终回复。</span></div><div class="arch-mini"><strong>channel 语义</strong><span>`microvideo-pcs` 与 `lite-microvideo-pcs` 决定是完整特征链路还是更轻的调用。</span></div></div></div></div><div class="arch-col"><div class="arch-box"><div class="arch-title">边界说明</div><div class="arch-note"><span class="tag">业务边界</span>`feeda-pcs` 的业务价值不是单纯“发 RPC”，而是把用户、实验、特征、样本和响应状态装配到同一条可控链路里。`client_tag` 是最关键的业务开关，`channel` 是它背后的物理路由。</div></div><div class="arch-box"><div class="arch-title">观测点</div><div class="arch-flow"><div class="step"><b>请求前</b>看 `FeedReq` 是否携带正确的 `command`、`tab`、`product`、`channel_id`、`uid`。</div><div class="step"><b>请求中</b>看 `PCSReqConv` 是否进入预期分支，是否带上实验和偏好字段。</div><div class="step"><b>响应后</b>看 `PCSRespConv` 是否把 `global_param` 与 `sample_log` 写回。</div></div></div></div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam shadowing false
left to right direction
actor ProductCaller
participant "pcs.cpp\nclient_tag router" as Router
participant "PCSReqConv\npcs_reqconv.cc" as Req
participant "PcsService_Stub" as Stub
participant "PCSRespConv\npcs_respconv.cc" as Resp
participant "sample_log / global_param" as Log
ProductCaller -> Router : FeedReq + client_tag
Router -> Req : choose convert branch
Req -> Stub : build PcsRequest
Stub --> Resp : PcsResponse + parameter_result
Resp -> Log : parse_global_param + sample_log
Resp --> ProductCaller : assembled response
@enduml
```

## 2. 配置与路由信息图
```infographic
infographic list-grid-badge-card
data
  title 路由与触发配置
  desc 这些字段决定 PCS 请求是走完整链路还是轻量分支
  items
    - label microvideo-pcs
      desc 主业务 channel，配合 gr / gr_sample 使用
      value 2
    - label lite-microvideo-pcs
      desc 轻量业务 channel，配合 lite-gr 使用
      value 1
    - label shoubai-pcs
      desc 独立业务线 channel，配合 shoubai-gr 使用
      value 1
    - label async_call
      desc 异步调用标记，决定是否在请求链路里提前返回或并发处理
      value 1
    - label trigger_all
      desc 是否触发全部相关组件
      value 2
```

## 3. 业务分支拆解
- `feeda-dc-gr/plugin/pcs.cpp:23-110`：`client_tag` 分支路由的入口，决定调用 `PCSReqConv` 的哪条变换路径。
- `feeda-dc-gr/conv/pcs_reqconv.cc:17-33`：通用字段从 `FeedReq` 传入 `CommonInfo`，是业务上下文的最小公共集。
- `feeda-dc-gr/conv/pcs_respconv.cc:19-56`：`parse_global_param` 与 `convert` 把服务端结果转成可消费的业务输出。
- `feeda-mv-gr/conf/common_component/pcs_common_plugin.conf:1-42`：业务 tag 与 channel 的映射，定义哪条业务线能触发哪类 PCS 请求。

## 4. Pitfalls
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #dbe3ea;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px"><div style="background:#fff;border:1px solid #e5ecf2;border-radius:10px;padding:12px"><div style="font-weight:700;color:#0f172a;margin-bottom:6px">tag 误配</div><div style="font-size:12px;line-height:1.55;color:#475569">`gr`、`gr_sample`、`lite-gr`、`shoubai-gr` 不是等价名，错配后业务会落到错误的 convert 分支。</div></div><div style="background:#fff;border:1px solid #e5ecf2;border-radius:10px;padding:12px"><div style="font-weight:700;color:#0f172a;margin-bottom:6px">字段漏传</div><div style="font-size:12px;line-height:1.55;color:#475569">`CommonInfo` 里缺 `uid`、`channel_id` 或实验字段时，后续特征与日志都会失真。</div></div><div style="background:#fff;border:1px solid #e5ecf2;border-radius:10px;padding:12px"><div style="font-weight:700;color:#0f172a;margin-bottom:6px">回填不完整</div><div style="font-size:12px;line-height:1.55;color:#475569">只写响应不写 `sample_log`，会让业务看起来“返回了”，但实验和排障没有证据链。</div></div></div></div>

## 5. 调试 Checklist
```infographic
infographic list-column-done-list
data
  title 业务调试检查清单
  items
    - label 确认 client_tag 进入预期分支
      done true
    - label 确认 channel 与 tag 的组合和配置一致
      done true
    - label 确认 CommonInfo 已带上 command/tab/product/channel_id/uid
      done true
    - label 确认 parameter_result 能在响应里回写
      done true
    - label 确认 sample_log 可用于离线复盘
      done true
    - label KU 背景缺失时明确写“需人工补充”
      done true
```

## 证据来源
- `feeda-mv-gr/conf/common_component/pcs_common_plugin.conf:1-42`
- `feeda-dc-gr/plugin/pcs.cpp:23-110`
- `feeda-dc-gr/conv/pcs_reqconv.cc:17-33`
- `feeda-dc-gr/conv/pcs_respconv.cc:19-56`
- `common-component/src/plugin/pcs_common_plugin.cpp:13-36`

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-25T19:02:21.378087
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 本次扫描结果显示，`feeda-mv-grg` 和 `feeda-mv-grc` 中**没有直接出现**类似 `feeda-pcs` 里那种 `client_tag` 路由、`PCSReqConv` / `PCSRespConv` 统一装配的现成实现，说明这套“请求分流 + 特征装配 + 响应回填”的模式在两个业务库里**尚未形成标准化边界**。
- 但从代码分布看，`feeda-mv-grc` 已经有较多处理链路命中，且包含 `service/grc_http_service.cpp` 这类明显的请求/响应处理入口；`feeda-mv-grg` 则只命中 1 个文件，适合做小范围试点。结合两边海量的 `std::vector`、`std::string`、`std::unordered_map` 使用规模，说明这类“上下文装配/路由分层”如果落地，**更可能先在 grc 获得收益**，grg 更适合局部验证。

## 2. 代码库详情

- `feeda-mv-grg`
  - 仅发现 1 个相关文件：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 这表明 `grg` 对应的业务链路中，和本次主题相关的处理点非常少，整体更偏单点策略规则，而不是完整的请求装配链。
  - 现有标准容器使用规模很大：
    - `std::vector`：1969 次，356 个文件
    - `std::string`：2443 次，425 个文件
    - `std::unordered_map`：734 次，205 个文件
  - 可作为参考的代码：
    - `model/model.h:9`，`predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos)`
    - `model/paddle_model.h:103`，`predict(std::vector<RidTmpInfoPtr>& candidate_vec, ...)`
    - `model/paddle_model.h:107`，`predict_with_tensor_input(...)`
  - 结论：`grg` 里已经有以 `std::vector` 为核心的候选集处理接口，说明如果要引入类似“上下文对象 + 统一装配”的模式，可以从模型输入侧做轻量扩展，而不是大改链路。

- `feeda-mv-grc`
  - 发现 10 个相关文件，覆盖面明显更广：
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
    - `processor/new_adjust/precise_score_init.cpp`
    - `processor/multi_factor/subcate_future_factor_gen.cpp`
    - `operator/adjuster/sketchy/duanju_adjuster.cpp`
    - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
  - 这说明 `grc` 的业务链路更接近“处理器 + 调整器 + 因子生成 + 服务入口”的复合架构，天然适合拆出路由层、装配层和回填层。
  - 现有标准容器使用规模也很大：
    - `std::vector`：8520 次，1290 个文件
    - `std::string`：7267 次，1247 个文件
    - `std::unordered_map`：2860 次，646 个文件
  - 可作为参考的代码：
    - `service/grc_http_service.cpp:62`，`std::unordered_map<std::string, std::vector<int>> depend_map;`
    - `service/grc_http_service.cpp:81`，`std::set<std::pair<int, int>> ...`
    - `service/grc_http_service.cpp:152`，`resp_str`、`sub_access_off_vec`、`sub_access_on_vec` 的请求解析与响应组装
  - 结论：`grc` 已经具备明显的“服务入口 + 中间处理链”特征，是最适合参考 `feeda-pcs` 中“路由 / 装配 / 回填”边界设计的代码库。

## 3. 💡 适用性评估与建议

- **优先在 `feeda-mv-grc/service/grc_http_service.cpp` 做架构试点**
  - 这里已经存在 URI 参数解析、响应字符串组装、容器拼装逻辑，最接近 `feeda-pcs` 里的 `client_tag router + resp assembler`。
  - 建议把当前散落在 handler 里的解析逻辑抽成两个小层：
    - `RequestRouter`：负责识别业务分支、参数合法性检查
    - `ResponseAssembler`：负责把中间结果回填到 `resp_str` 或响应对象
  - 这样后续如果要加类似 `client_tag` 的分支，改动会更集中。

- **在 `feeda-mv-grc/processor/new_adjust/precise_score_init.cpp` 和 `processor/multi_factor/subcate_future_factor_gen.cpp` 统一上下文入参**
  - 这两个文件看起来都属于“计算前置初始化 / 因子生成”类型，容易出现多个参数散落传递的问题。
  - 建议引入一个轻量上下文结构体，类似 `CommonInfo` 的思路，把 `uid`、实验信息、业务标识、输入特征等收束起来。
  - 好处是后续新增字段时，不需要频繁改函数签名，也更利于做统一日志和调试。

- **在 `feeda-mv-grc/operator/adjuster/sketchy/duanju_adjuster.cpp` 和 `processor/filter/low_agile_goodrate_filter_operator.cc` 规范分支路由**
  - 这类调整器/过滤器通常会出现大量 `if-else` 或条件树，适合参考 `client_tag -> handler` 的模式。
  - 建议将“条件判断”改造成“标签到处理器”的映射表，例如用 `std::unordered_map<std::string, Handler>` 或静态策略表来组织。
  - 这样可减少分支膨胀，也方便后续新增规则时局部扩展。

- **在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 做小流量验证**
  - `grg` 当前只命中 1 个目标文件，说明它不适合作为大规模重构起点，但非常适合做低风险试验。
  - 可以先尝试把候选集处理与规则判定拆成“输入上下文 + 规则执行器”两层，借鉴 `PCSReqConv` / `PCSRespConv` 的分层思想。
  - 如果效果稳定，再逐步推广到 `model/model.h` 和 `model/paddle_model.h` 的候选推理链路。

- **把 `model/paddle_model.h` 作为 `vector` 传参优化参考点**
  - 该文件已经有 `std::vector<RidTmpInfoPtr>& candidate_vec` 这类接口，说明 `grg` 侧的候选处理偏重数据流。
  - 若未来引入更复杂的装配字段，建议保持 `vector` 引用传递，避免额外拷贝；新增元信息尽量放到 sidecar 结构里，不要污染主预测接口。
  - 这对保持推理链路性能很重要。

## 4. ⚠️ 引入风险与限制

- **没有现成的 `client_tag` 路由经验可直接复用**
  - 两个代码库里没有找到与 `feeda-pcs` 完全一致的路由/装配实现，所以这次适配更像“架构迁移”，不是“替换式接入”。
  - 需要先统一业务分支定义，否则容易出现标签和实际处理器不一致的问题。

- **`grc` 业务链路长，抽象层过多可能带来额外开销**
  - `grc` 中 `std::vector`、`std::unordered_map` 使用非常密集，如果新增路由/装配层处理不当，可能引入多一次拷贝、字符串拼接或临时对象分配。
  - 建议优先使用引用、`const&`、`move`，避免把“可读性改造”变成“性能回退”。

- **`grg` 适合试点，但不适合直接推全量**
  - 由于只命中 1 个相关文件，说明它对这类模式的暴露面太小，适合验证思想，不适合直接作为全局模板。
  - 如果要推广，需要先确认 `model` 层和 `strategy` 层的职责边界，否则容易出现接口改动扩散。

- **日志与回填链路要一起设计**
  - 参考 `feeda-pcs` 的经验，只有响应、不写中间日志会导致问题难复盘。
  - 在 `service/grc_http_service.cpp` 这类入口里，建议同步考虑请求分支记录、参数回填和异常路径日志，否则后续排障成本会很高。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
