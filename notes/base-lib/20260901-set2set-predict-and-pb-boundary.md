# 2026-09-01 周二基础库理解：Set2setPredictFunction 的 protobuf 组包、序列化与调度边界

> 日期：2026-09-01  
> 主题来源：当前可用 daily-plan 仍停留在旧的周计划，未找到 2026-09-01 当日计划；按历史候选回退到 `Protobuf SerializeToString 热点与 FlatBuffers 替代方向`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/processor/set2set_predict_function.cpp`、`src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp`，聚焦 sample 组包、请求序列化、RPC 预测调用、参数字典读取和高频 vector 预留。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求输入层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/processor/set2set_predict_function.cpp:51-143`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">拉取 `CommonInfo`、`SidInfo`、PCS 结果和实验参数，决定本次请求走哪条 predictor 分支。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">组包与序列化层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`PredictorRequest` + `Sample::SerializeToString()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">把 request_feature / user_feature / sequence_feature 收进 pass-through sample，再写入请求体。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">预测与调权层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`Set2setPredictorPlugin` + `MvFanCateDownRankAdjuster`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">RPC 返回后再结合 common dict / exp manager / fan cate 规则做二次调权。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">依赖读取</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">Sample 组包</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">SerializeToString</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">predict / adjust</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title Set2setPredictFunction protobuf boundary
participant "Set2setPredictFunction" as S2S
participant "CommonInfo/SidInfo" as CTX
participant "ExpManager" as EXP
participant "PCS result" as PCS
participant "PredictorRequest" as REQ
participant "Sample" as SAMPLE
participant "Set2setPredictorPlugin" as PRED
participant "MvFanCateDownRankAdjuster" as ADJ
S2S -> CTX : read common_info / sid_info / graph_context
S2S -> EXP : EXPERIMENT_GET_PARAM / HIT_PARAM
S2S -> PCS : read MicrovideoSet2set / freshness / comment PCS
S2S -> SAMPLE : fill request_feature / user_feature / sequence_feature
SAMPLE -> REQ : SerializeToString(pass_through_sample)
S2S -> REQ : set lib_field / use_request_feature_cache
S2S -> PRED : predict(request, response, logid, dt_cntl)
PRED --> S2S : PredictorResponse
S2S -> S2S : process_response() / reserve() / map vector<float>
S2S -> ADJ : read d_grc_param_dict / fan_cate threshold / factor vectors
ADJ -> ADJ : compute factor and clamp bounds
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title Set2set 请求链的 6 个关键落点
  desc 这些落点决定了请求体怎么组、序列化怎么做、预测结果怎么回灌到后续调权
  items
    - label sample 组包
      desc `src/processor/set2set_predict_function.cpp:700-716` 把 request / user / sequence feature 写入 `pass_through_sample`
      icon mdi-package-variant-closed
    - label request lib_field
      desc `src/processor/set2set_predict_function.cpp:718-725` 设置 `rank_type`、`ua`、`dnn_num` 和 cache 开关
      icon mdi-form-select
    - label rpc 预测
      desc `src/processor/set2set_predict_function.cpp:730-759` 通过不同 predictor plugin 发起 RPC 预测
      icon mdi-transit-connection-horizontal
    - label vector 预留
      desc `src/processor/set2set_predict_function.cpp:787-788` 对 `sorted_doc_vec` 先 `reserve(input_vec.size())`
      icon mdi-expand-all
    - label common dict 读取
      desc `src/processor/set2set_predict_function.cpp:299-449` 大量 `GET_COMMON_DICT`，参数按场景拼接
      icon mdi-tune-variant
    - label factor clamp
      desc `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:150-181` 对 factor 做上下界截断
      icon mdi-scale-balance
```

## 3. 代码链路拆解
### 3.1 序列化不是末尾附带动作，而是请求契约的分界线
- `src/processor/set2set_predict_function.cpp:713-726`：`Sample` 组好后通过 `SerializeToString(p_sample)` 写入 `PredictorRequest`，随后再填 `lib_field` 和缓存开关。这个顺序说明 pass-through sample 是对外 RPC 契约的一部分，不是调试辅助字段。
- `src/processor/set2set_predict_function.cpp:730-759`：`SIA_START(set2set_predict_rpc)` 包住真正的 predictor 调用，说明耗时统计明确区分了“组包/序列化”和“远端推理”两个阶段。
- `src/processor/set2set_predict_function.cpp:3303-3308`：在请求尾部用 `SIA_ADD` 和 `SIA_END_WITH_VAL` 收束，表明这条链路最终可观测的不是单次函数返回，而是整条 request 到 response 的累计状态。

### 3.2 高密度参数读取说明这里更像“编排器”而不是纯算法函数
- `src/processor/set2set_predict_function.cpp:53-143`：前半段连续读取 `SidInfo`、实验参数和多个布尔开关，决定 predictor 分支、样本策略和调权路径。
- `src/processor/set2set_predict_function.cpp:299-449`：`GET_COMMON_DICT` 被反复用于不同场景的 factor 表，命名规则依赖 flag 拼接，说明配置键和请求上下文是强绑定的。
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:70-104`：adjuster 再次从 graph_context 读取 exp manager 和 dict，说明调权并不是独立逻辑，而是对上游 set2set 输出的二次修正。

### 3.3 vector 预留和批量写入是这条链路里最直接的性能信号
- `src/processor/set2set_predict_function.cpp:787-788`：`sorted_doc_vec.reserve(input_vec.size())` 是少数明确的容量预留点，说明作者已经意识到后续会进行批量写入。
- `src/processor/set2set_predict_function.cpp:3332-3337`：`model_result.reserve(mt_q_size)` 后再 `emplace_back`，避免了重复扩容和拷贝。
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:63-64`：把向量值逐个 `push_back` 到 `fancate_ratio_vec`，这类地方如果输入规模变大，就会成为可优化点。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#3d5a80;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">这条链路最容易把“参数编排”误读成“算法主逻辑”</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`SerializeToString()` 之前的字段组装、`GET_COMMON_DICT` 的键拼接、`EXPERIMENT_GET_PARAM()` 的分支选择，实际上共同定义了请求行为。只盯 `predict()` 会漏掉绝大多数性能和正确性问题。</div><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`factor` 被 clamp 到上下界后再乘回 `item->grg_new_score`，因此问题常常表现为“调权幅度不对”，而不是直接报错。要先看输入向量和阈值键是否拼对。</div></div><div style="margin-top:10px;font-weight:900;color:#3d5a80;">∎ 排查顺序：依赖读取 → sample 组包 → SerializeToString → predictor 分支 → result_map reserve → adjust clamp</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title Set2set protobuf 边界排查清单
  desc 适用于请求丢字段、predictor 分支错误、调权不生效、vector 扩容抖动和指标缺失
  items
    - label 检查 sample 组包
      desc `pass_through_sample` 是否完整写入 request / user / sequence feature
      done true
    - label 检查序列化位置
      desc `SerializeToString()` 必须发生在 PredictorRequest 组装完成后
      done true
    - label 检查 predictor 分支
      desc 不同 `set2set_predictor_v*` 必须命中对应 plugin
      done true
    - label 检查 common dict 键
      desc `set2set_dict_*` / `cocoon_downrank_vec0514` 的键拼接要和实验位一致
      done true
    - label 检查 reserve
      desc `sorted_doc_vec` 和 `model_result` 的预留容量要与输入规模匹配
      done true
    - label 检查 factor clamp
      desc `factor_min` / `factor_max` 要避免过度截断或数值溢出
      done true
```

## 6. 证据来源
- `src/processor/set2set_predict_function.cpp:51-143`
- `src/processor/set2set_predict_function.cpp:299-449`
- `src/processor/set2set_predict_function.cpp:700-759`
- `src/processor/set2set_predict_function.cpp:787-788`
- `src/processor/set2set_predict_function.cpp:3303-3308`
- `src/processor/set2set_predict_function.cpp:3332-3337`
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:35-104`
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:115-181`

## 7. 说明
当前运行环境未找到 2026-09-01 的 daily-plan 文件；本笔记基于历史候选与本地代码回退生成，KU 正文未读取，业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-02T19:01:41.603476
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 分析摘要

从扫描结果看，`feeda-mv-grg` 和 `feeda-mv-grc` 都存在大量 `std::vector`、`std::string`、`std::unordered_map` 使用点，说明高频组包、结果聚合、特征传递、HTTP 参数解析等路径具备较大的基础容器优化空间。结合本次技术笔记关注的 `Sample` 组包、`SerializeToString()` 边界和 vector 预留策略，迁移重点不应是全量替换，而应优先落在请求构造、预测样本、召回结果聚合、参数字典读取这类高频路径。

两个代码库中都已经发现目标库使用经验：`feeda-mv-grg` 有 1 个文件，`feeda-mv-grc` 有 10 个文件。迁移时可以先参考这些已落地文件的 include 方式、编译依赖和代码风格，再从 `std::unordered_map` 热点、短生命周期 `std::vector`、临时 `std::string` 拼接和 protobuf 序列化前后的内存分配入手。

## 代码库详情

### feeda-mv-grg

- 已发现目标库使用：1 个文件，可优先参考 `strategy/diversity/rule/low_clarity_diversity_rule.cpp`。
- `std::vector` 使用规模较大：1969 次，分布在 356 个文件，说明候选队列、模型输入、规则链路中存在大量顺序容器操作。
- `std::string` 使用 2443 次，分布在 425 个文件，说明参数 key、日志字段、特征名和序列化中间字符串可能有较多临时分配。
- `std::unordered_map` 使用 734 次，分布在 205 个文件，适合重点排查小 map、高频查询 map、构建后只读 map 的替换收益。
- 典型场景如 `model/model.h`、`model/paddle_model.h` 中的 `predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos)`，是模型预测链路中的核心容器传递点，应优先检查是否存在重复扩容、拷贝和不必要的临时 vector。

### feeda-mv-grc

- 已发现目标库使用：10 个文件，可参考 `processor/multi_factor/ltr_factor_gen_scene.cpp`、`processor/filter/user_explore_interest_ugc_filter_operator.cc`、`processor/multi_factor/subcate_future_factor_gen.cpp`、`processor/new_adjust/precise_score_init.cpp`、`processor/new_adjust/precise_score_init_first_refresh.cpp`。
- `std::vector` 使用 8520 次，分布在 1290 个文件，是两个代码库中更明显的优化目标，尤其是召回结果、因子列表、调权列表、HTTP 参数列表。
- `std::string` 使用 7267 次，分布在 1247 个文件，说明 query 参数解析、配置 key 拼接、特征名传递、日志拼接都有潜在优化空间。
- `std::unordered_map` 使用 2860 次，分布在 646 个文件，`service/grc_http_service.cpp` 中的 `depend_map` 属于典型图依赖聚合场景，适合评估哈希表替换、reserve 和只读化。
- `service/grc_http_service.cpp` 中同时出现 `std::unordered_map<std::string, std::vector<int>>`、静态 `std::vector<std::string>`、请求参数 vector，说明该文件适合作为容器优化试点。

## 💡 适用性评估与建议

- 优先在 `feeda-mv-grc/service/grc_http_service.cpp` 评估 `std::unordered_map<std::string, std::vector<int>> depend_map` 的优化。该 map 由 graph vertex 依赖关系构建，若每次请求都会重建，建议先补充 `reserve(all_vertex.size())`，再评估替换为目标高性能 hash map；如果构建后只读，收益通常会比普通业务 map 更稳定。

- 在 `feeda-mv-grc/service/grc_http_service.cpp` 的 `sub_access_off_vec`、`sub_access_on_vec` 参数解析场景中，优先检查 vector 是否能按 query 参数数量提前 `reserve()`。这类短生命周期 vector 不一定需要替换容器类型，但很适合应用本次笔记中 `sorted_doc_vec.reserve(input_vec.size())` 的思路。

- 在 `feeda-mv-grg/model/model.h` 和 `feeda-mv-grg/model/paddle_model.h` 的 `predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos)` 链路中，不建议直接替换函数签名中的 `std::vector`，因为这是模型接口契约。更稳妥的做法是在调用侧减少临时 vector 构造，并检查 `candidate_vec` 进入预测前是否已经完成容量预留和过滤压缩。

- `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 已有目标库使用，可作为 grg 侧迁移参考。建议先对同目录下其他 diversity rule 做局部对比，选择“小范围、高频、行为容易验证”的规则链路试点，而不是直接改模型预测接口。

- `feeda-mv-grc/processor/new_adjust/precise_score_init.cpp` 和 `processor/new_adjust/precise_score_init_first_refresh.cpp` 适合结合本次笔记里的 factor clamp、vector 批量写入场景做优化。调权链路通常对延迟敏感，建议优先检查因子 vector、分数 map、配置 key 拼接是否存在重复分配，再决定是否引入目标库替换。

## ⚠️ 引入风险与限制

- 不建议全量替换 `std::vector`、`std::string`、`std::unordered_map`。这两个代码库中的 std 使用规模很大，盲目替换会引入 ABI、编译依赖、序列化接口兼容和调试成本问题。

- protobuf `SerializeToString()` 是 RPC 请求契约边界，不能只从性能角度替换。若后续评估 FlatBuffers 或其他零拷贝格式，需要同步确认 predictor 服务端协议、日志回放、样本落盘和兼容灰度方案。

- 模型接口如 `predict(std::vector<RidTmpInfoPtr>& candidate_vec, ...)` 属于跨模块契约，直接改容器类型风险较高。更推荐先优化接口内部和调用前后的临时对象、reserve、move 语义和 map 查询结构。

- 目标库在 grg 仅发现 1 个使用点，在 grc 也只有 10 个使用点，说明团队经验和工程模板可能还不充分。迁移前应先确认编译规则、代码规范、线上监控指标和回滚方式。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
