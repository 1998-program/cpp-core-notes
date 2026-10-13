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

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260901-set2set-predict-and-pb-boundary.md)。
