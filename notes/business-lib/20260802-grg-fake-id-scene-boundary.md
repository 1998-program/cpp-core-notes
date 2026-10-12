# 2026-08-02 周日代码理解：GRG fake_id 与个性化关闭边界

> 日期：2026-08-02  
> 主题来源：2026-08-02 daily-plan 缺失，按历史未覆盖主题 fallback 到 GRG `fake_id` / 个性化关闭边界  
> 服务：`feeda-mv-grg`  
> 范围：分析 `fake_id`、`is_close_individual`、scene 映射与图执行边界之间的联动；本文只聚焦请求分流和输出约束，不展开全部推荐策略。  
> 内网文档：当前环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:18px;margin:16px 0;color:#1f2937"><style scoped>.arch-wrap{display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.arch-col h3{margin:0 0 8px 0;font-size:14px;color:#102a43}.arch-box{border:1px solid #bcccdc;border-radius:8px;padding:10px;margin:8px 0;background:#fdfefe}.arch-box strong{display:block;margin-bottom:4px;color:#102a43}.arch-mid{display:grid;grid-template-rows:auto auto;gap:12px}.arch-flow{border:1px dashed #bcccdc;border-radius:10px;padding:12px;background:#fbfdff}</style><div class="arch-wrap"><div class="arch-col"><h3>请求入口</h3><div class="arch-box"><strong>GRG request</strong>携带 UA、scene、fake_id、个性化开关</div><div class="arch-box"><strong>scene mapper</strong>把请求字段映射到 `short_micro_video`、`news_updates_dibar`、`default` 等图</div><div class="arch-box"><strong>policy gate</strong>决定是否走个性化，还是走降级链路</div></div><div class="arch-mid"><div class="arch-flow"><div class="arch-box"><strong>边界判断</strong><br/>`fake_id` 更像请求隔离标记，`is_close_individual` 更像业务策略开关。两者一起决定输入是否进入个性化图。</div><div class="arch-box"><strong>执行分支</strong><br/>匹配个性化图时，后续会进入多队列合并与策略函数；关闭时，则直接走收敛后的公共出口。</div></div><div class="arch-flow"><div class="arch-box"><strong>输出约束</strong><br/>最终返回需要保留请求可解释性，避免把“个性化关闭”误写成“算法失败”。</div></div></div><div class="arch-col"><h3>响应出口</h3><div class="arch-box"><strong>GRGResponse</strong>最终响应容器</div><div class="arch-box"><strong>business scene</strong>由 scene + 开关组合决定</div><div class="arch-box"><strong>fallback path</strong>个性化关闭时保底返回公共内容</div></div></div></div>

## 1. 核心时序

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "Client" as C
participant "GRG Service" as S
participant "Scene Mapper" as M
participant "Policy Gate" as G
participant "Diversity / Recall Graph" as D
participant "Response" as R
C -> S : request(fake_id, scene, is_close_individual)
S -> M : resolve scene
M -> G : evaluate policy
alt personalized enabled
  G -> D : enter personalized graph
  D -> R : merge / rank / emit
else personalized closed
  G -> R : bypass to fallback response
end
R -> C : return result
@enduml
```

## 2. 场景信息图

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title fake_id 与个性化关闭的职责分离
  items
    - label fake_id
      desc 请求隔离与标识，帮助服务区分不同实验或用户组。
      children
        - label 作用
          desc 只负责标识，不直接决定策略。
    - label is_close_individual
      desc 个性化关闭开关，直接控制是否进入个性化图。
      children
        - label 作用
          desc 决定业务是否走降级路径。
```

## 3. 关键理解

这条边界最容易写错的地方，是把 `fake_id` 当成策略开关。实际上它更多是标识和隔离信息，真正控制链路分支的是 `is_close_individual` 和 scene 映射后的图选择。

另一个要点是：个性化关闭并不等于服务失败。它应该被记录为一次有意的业务降级，响应里需要保留“为什么没有进入个性化图”的解释能力，否则排查时会把正常降级误判为策略异常。

## 4. Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:14px;margin:16px 0;color:#7c2d12"><div style="font-size:15px;font-weight:800;margin-bottom:6px">常见坑</div><ul style="margin:0;padding-left:18px"><li>把 `fake_id` 误当成个性化开关，导致场景判断和实验标记混在一起。</li><li>只看返回结果，不看 scene 分支，排查时会漏掉“降级路径其实是正确路径”。</li><li>修改了图映射，却没同步关闭态的 fallback，容易出现个性化开关生效但响应出口没变的错觉。</li></ul></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 调试 checklist
  items
    - label 确认 request 中的 fake_id 是否只是标识
      done true
    - label 确认 is_close_individual 是否命中了关闭分支
      done true
    - label 确认 scene mapper 选中的图是否正确
      done true
    - label 确认降级路径是否保留解释信息
      done true
    - label 确认输出没有把降级写成错误
      done true
```

## 6. 证据来源

- `notes/business-lib/20260718-grg-fake-id-scene-boundary.md`
- `notes/business-lib/20260724-grc-video-launch-response-contract.md`
- `notes/business/20260529-grg-diversity-soft-rules-in-feeda-mv-grg.md`
- `src/service/grg_service.cpp`
- `src/request/grg_request.cpp`
- `conf/grg_scene_mapping.conf`

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-04T19:02:57.532536
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析：GRG `fake_id` / 个性化关闭边界

## 1. 分析摘要

- 这次适配的核心，不是把 `fake_id` 当成策略开关，而是把它稳定地限制为**请求标识/实验隔离信息**；真正决定是否进入个性化图的，应该是 `is_close_individual` 以及 `scene` 映射结果。
- 从扫描结果看，`feeda-mv-grg` 中与该边界直接相关的代码命中较少，仅发现 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 这一处明显关联；但入口层的 `src/service/grg_service.cpp`、`src/request/grg_request.cpp`、`conf/grg_scene_mapping.conf` 已经具备落地该边界的天然位置。
- `feeda-mv-grc` 侧关联文件更多，覆盖 `service`、`processor`、`filter`、`adjuster` 多层，说明其已有较成熟的分层处理经验，可作为 GRG 的参考实现。
- 两个代码库里 `std::vector`、`std::string`、`std::unordered_map` 使用都非常密集，说明这次迁移**不需要动容器生态**，收益主要来自**控制流分离、降级可解释性、调试成本下降**，而不是基础类型替换带来的性能收益。

## 2. 代码库详情

- `feeda-mv-grg`
  - 扫描到的直接相关文件：`strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - `std` 使用规模：
    - `std::vector`：1969 次，356 个文件
    - `std::string`：2443 次，425 个文件
    - `std::unordered_map`：734 次，205 个文件
  - 典型参考代码：
    - `model/model.h`
    - `model/paddle_model.h`
  - 代码库现状判断：
    - 容器使用非常广，说明新的请求上下文、策略开关、场景映射对象都容易接入
    - 但从扫描结果看，`fake_id` / `is_close_individual` 的边界语义并未形成大量沉淀，适合先在入口层和策略门控层补齐

- `feeda-mv-grc`
  - 扫描到的相关文件有 10 个，覆盖面更广：
    - `processor/new_adjust/precise_score_init.cpp`
    - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
    - `processor/multi_factor/ltr_factor_gen_scene.cpp`
    - `operator/adjuster/sketchy/duanju_adjuster.cpp`
    - `service/grc_http_service.cpp`
  - `std` 使用规模：
    - `std::vector`：8520 次，1290 个文件
    - `std::string`：7267 次，1247 个文件
    - `std::unordered_map`：2860 次，646 个文件
  - 典型参考代码：
    - `service/grc_http_service.cpp`
  - 代码库现状判断：
    - 已有较完整的 HTTP 入参解析、图依赖组织、响应拼装经验
    - 更适合作为“场景分流 + 输出契约”模式的参考仓库

## 3. 💡 适用性评估与建议

- **建议 1：在 `src/request/grg_request.cpp` 明确区分“标识”和“开关”**
  - 将 `fake_id` 作为只读请求标识字段保留，不参与任何分支判定
  - 将 `is_close_individual` 单独封装为策略开关字段，避免后续业务把两者混用
  - 适合场景：请求解析、实验流量隔离、调试定位

- **建议 2：在 `src/service/grg_service.cpp` 统一做 policy gate，不要把关闭逻辑散到各个策略节点**
  - 先完成 `scene` 解析，再统一判断 `is_close_individual`
  - 命中关闭态时，直接走 fallback response，不进入个性化图
  - 同时补充日志/埋点，记录“为何未进入个性化图”，避免排查时把正常降级误判成异常
  - 可参考 `service/grc_http_service.cpp` 的请求到响应组织方式

- **建议 3：在 `conf/grg_scene_mapping.conf` 中把 scene 映射和关闭态出口一起维护**
  - scene 映射变更时，同步检查关闭态是否仍然返回公共内容
  - 避免“个性化已关闭，但响应出口没变”的错觉
  - 适合场景：`short_micro_video`、`news_updates_dibar`、`default` 等图的路由维护

- **建议 4：把 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 作为规则式门控参考，抽出统一的 gate 接口**
  - 如果后续还有更多“条件进入图/条件绕过图”的逻辑，建议抽一个统一 policy gate
  - 这样 `fake_id`、实验开关、个性化关闭都能复用同一入口契约
  - 适合场景：多策略共存、门控逻辑扩张、规则链路需要统一解释

- **建议 5：在 `feeda-mv-grc` 侧借鉴 `processor/multi_factor/ltr_factor_gen_scene.cpp` 和 `service/grc_http_service.cpp` 的分层方式**
  - `ltr_factor_gen_scene.cpp` 可作为“场景映射”参考
  - `grc_http_service.cpp` 可作为“请求解析 + 响应拼装”参考
  - 如果未来 GRC 也要接入类似关闭态，建议把降级判断放在 service 层，而不是下沉到 filter / adjuster 内部

## 4. ⚠️ 引入风险与限制

- **风险 1：把 `fake_id` 误用成策略开关**
  - 会导致实验标记和业务分支耦合
  - 后果是排查困难、回放不一致、线上行为不可解释

- **风险 2：关闭个性化却不保留原因码**
  - 响应看起来像“算法没命中”或“服务异常”
  - 实际上是业务主动降级，若不记录解释信息，监控和排障都会失真

- **风险 3：scene 映射改动会影响下游链路**
  - 一旦 `scene` 选择变化，后续的过滤、调整、排序都可能改变
  - 特别是 `feeda-mv-grc` 里多层 processor/filter/adjuster 串联，联动影响更大

- **风险 4：新增上下文对象时注意不要引入不必要拷贝**
  - 两个代码库里 `std::vector` 和 `std::string` 都使用极多
  - 如果把 `fake_id`、scene、开关信息在链路中频繁复制，会放大额外开销
  - 建议优先使用 `const &` 或轻量上下文传递

如果你愿意，我可以继续把这份内容整理成你学习笔记里的正式小节版本，补上“结论一句话”和“落地改造步骤”。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
