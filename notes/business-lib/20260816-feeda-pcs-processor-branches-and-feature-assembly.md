# 2026-08-16 周日代码理解：feeda-pcs 业务分支与特征装配

> 日期：2026-08-16  
> 主题来源：2026-06-01 daily-plan 文件未发现，按历史未覆盖主题 fallback 到 `feeda-pcs` 的业务 processor 分支与特征装配链路；KU/业务背景需人工补充。  
> 范围：只分析 `shoubai/processor/diver_feature_sample_processor.cpp` 与 `microvideo/processor/mv_diver_xgb_feature_processor.cpp`，关注业务分支、GCMS/DeepES 依赖、mid_data 绑定与结果装配，不扩展到全部 recall/merge 逻辑。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937">
  <div style="font-size:20px;font-weight:800;margin-bottom:10px;">feeda-pcs 业务特征装配全景</div>
  <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">Shoubai branch</div>
      <div style="font-size:13px;line-height:1.5;">以 `GCMS` 和多类子类字典为依赖，组装图上的图像/内容相关特征。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">Microvideo branch</div>
      <div style="font-size:13px;line-height:1.5;">依赖 `DeepES`、`parameter_result_cache`、`mid_data_cache` 和预测器输出节点。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">AOPProcessor</div>
      <div style="font-size:13px;line-height:1.5;">各 processor 只做 graph 节点绑定与选择性写入，真正业务数据来自外部组件和字典。</div>
    </div>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:#334155;">
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">GCMS</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">DeepES</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">mid_data_cache</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">feature response</span>
  </div>
</div>

## 1. 业务分支的核心判断
`DiverFeatureSampleProcessor` 更像一个带条件的 feature 装配器。它在 `setup()` 中绑定 `author_graph_similarity_news_info`、`author_graph_similarity_video_info` 等 graph dependency，在 `process()` 里则直接用 `return -1` / `return 0` 控制是否继续向下游传播。`EXPERIMENT_HIT_SID(...)` 虽然被注释掉，但注释位置说明这个 processor 以前或计划中就有 sid 门控。证据见 `shoubai/processor/diver_feature_sample_processor.cpp:35-52`、`shoubai/processor/diver_feature_sample_processor.cpp:61-62`。

`MvDiverXgbFeatureProcessor` 的结构更偏向模型特征侧。它引入 `parameter_result_cache`、`feature_info`、`nids_gcf_data`、`recall_gcf_data` 和 `deepes_component`，说明这个分支要做的不只是拼字段，而是把召回、特征和预测器输入一起接到图上。证据见 `microvideo/processor/mv_diver_xgb_feature_processor.cpp:1-13`、`microvideo/processor/mv_diver_xgb_feature_processor.cpp:44-60`。

## 2. 调用链时序图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "DiverFeatureSampleProcessor" as S
participant "MvDiverXgbFeatureProcessor" as M
participant "GCMS / DeepES" as C
participant "mid_data_cache" as Cache
participant "GraphDependency" as G
S -> G : bind author similarity nodes
S -> S : sid / experiment gate
S -> G : emit downstream feature references
M -> G : bind FeatureResponse / FeatureLuopan
M -> C : read GCMS / DeepES derived inputs
M -> Cache : read/write mid_data
M -> G : expose prediction-ready dependencies
@enduml
```

## 3. 业务结构信息图
```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title Processor branch comparison
  desc One branch is selective and sid-gated, the other is model-feature heavy and dependency-rich.
  items
    - label DiverFeatureSampleProcessor
      desc Shoubai side, graph similarity feature wiring
      children
        - label GCMS component
          desc Provides show / content side signals
        - label subcate dicts
          desc video / news / micro dictionaries
        - label gate
          desc return -1 or 0 based on sid or experiment conditions
    - label MvDiverXgbFeatureProcessor
      desc Microvideo side, prediction feature assembly
      children
        - label DeepES component
          desc Model-side external dependency
        - label parameter_result_cache
          desc Reuses parameter evaluation results
        - label mid_data_cache
          desc Carries intermediate graph data
        - label FeatureResponse / FeatureLuopan
          desc Downstream dependencies for final assembly
theme
  palette #0f766e #2563eb #7c3aed
```

## 4. 关键实现细节
Shoubai 分支里最重要的信号是“字典 + GCMS + 选择性门控”的组合。头部同时包含 `gcms_component.h`、`video_subcate_mid_dict.h`、`news_subcate_mid_dict.h`、`micro_subcate_mid_dict.h`，说明它不是单一来源的数据路径，而是把多个内容域的中间字典合并后再决定是否放行。证据见 `shoubai/processor/diver_feature_sample_processor.cpp:9-17`。

Microvideo 分支则把 `feature_response` 和 `feature_luopan` 作为显式 `GraphDependency` 挂出来，同时把 `deepes_component`、`parallel_task`、`mid_data_cache` 拉进来。这个形态说明后续不仅会读取缓存，还会把模型或策略所需的中间态原样传给下游。证据见 `microvideo/processor/mv_diver_xgb_feature_processor.cpp:37-60`。

## 5. Pitfalls
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0;">
  <div style="border:1px solid #cbd5e1;border-left:4px solid #f59e0b;border-radius:10px;background:#fff;padding:12px;">
    <div style="font-weight:800;margin-bottom:6px;">sid 门控可能被误判为无业务</div>
    <div style="font-size:13px;line-height:1.6;">`EXPERIMENT_HIT_SID` 被注释不代表逻辑不存在，真正的门控仍可能在上游图节点或外部配置里生效。</div>
  </div>
  <div style="border:1px solid #cbd5e1;border-left:4px solid #ef4444;border-radius:10px;background:#fff;padding:12px;">
    <div style="font-weight:800;margin-bottom:6px;">依赖很多不等于会全部写入</div>
    <div style="font-size:13px;line-height:1.6;">`FeatureResponse`、`FeatureLuopan`、`mid_data_cache` 这些节点是挂载点，不要把 include 列表直接等同于最终输出字段。</div>
  </div>
</div>

## 6. 调试 Checklist
```infographic
infographic sequence-ascending-steps
data
  title Debug checklist
  items
    - label Check branch-specific include stack
      desc GCMS for shoubai, DeepES for microvideo
      done true
    - label Confirm graph dependencies are bound in setup()
      desc Named dependencies must exist before process()
      done true
    - label Trace sid gate or return -1 early exit
      desc Branch may short-circuit before any feature write
      done true
    - label Inspect mid_data_cache and parameter_result_cache usage
      desc Verify whether values are reused or recomputed
      done true
    - label Keep evidence paths short and relative
      desc Use src/... or conf/... only
      done true
theme
  palette #2563eb #7c3aed #64748b
```

## 7. 证据来源
- `src/shoubai/processor/diver_feature_sample_processor.cpp:9-17`
- `src/shoubai/processor/diver_feature_sample_processor.cpp:35-52`
- `src/shoubai/processor/diver_feature_sample_processor.cpp:61-62`
- `src/microvideo/processor/mv_diver_xgb_feature_processor.cpp:1-13`
- `src/microvideo/processor/mv_diver_xgb_feature_processor.cpp:37-60`

## 8. 结论
`feeda-pcs` 的业务层不是单一路径，而是按内容域拆成多个 processor 分支。Shoubai 分支偏 GCMS 与字典驱动的选择性放行，Microvideo 分支偏 DeepES、cache 和模型输入装配。两者共同点是都把业务复杂度下沉到图依赖和 processor 的边界上，而不是塞进入口函数。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260816-feeda-pcs-processor-branches-and-feature-assembly.md)。
