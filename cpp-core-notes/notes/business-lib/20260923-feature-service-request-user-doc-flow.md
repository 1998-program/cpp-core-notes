# 2026-09-23 业务库理解：MV-GRC/GRG 特征请求分流与 DocFeature 样本组装边界

> 日期：2026-09-23（周三）  
> 主题选择：目标日期未发现有效 daily-plan。本次按缺失计划回退，选取与基础库 `FeatureServicePlugin` 自然配对的业务侧边界：`RequestFeatureFunction`、`UserFeatureFunction` 与 DocFeature pipeline 如何把请求、用户与候选内容特征组织为 graph 下游可消费的数据。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：request/user feature 请求构造、pass_through 兜底、`is_open_dnn_soft_rule` 短路、doc feature pipeline 的 batch 输入、sample/context_sample 组装、candidate channel 分发、Yitu 并行 batch 与 SampleContext 生命周期。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7faf5;border:1px solid #d9e2d0;border-radius:8px;padding:16px;margin:16px 0;color:#243126;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">请求级</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">RequestFeatureFunction</div><div style="margin-top:8px;font-size:12px;color:#52615a;">构造 cuid/uid/bid/product/token，拉 `request_feature`，失败时从 pass_through 兜底。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">用户级</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">UserFeatureFunction</div><div style="margin-top:8px;font-size:12px;color:#52615a;">拉 `user_feature`、sequence feature 和 session feature，emit FeatureResponse.sample。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">内容级</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">DocFeature pipelines</div><div style="margin-top:8px;font-size:12px;color:#52615a;">按 queue batch 组装 ContentFeature/RecommendFeature，并向抽取、PK、RLHF、longterm 分支发布候选。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">GRCRequest</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:6px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">FeatureService</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">Sample / RequestFeature</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:10px;text-align:center;color:#991b1b;font-size:13px;">model / extract vertices</div></div></div>

**一句话结论：** MV-GRC/GRG 的 feature 链路不是一次远端调用，而是 request、user、doc 三条数据通道并行补齐模型输入；其中 `is_open_dnn_soft_rule`、pass_through 兜底、candidate publisher 和 SampleContext 生命周期决定了“响应正常但模型特征缺失”的真实边界。

## 1. 核心时序

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title FeatureService 业务侧三路特征组装
participant "GRCRequest" as REQ
participant "RequestFeatureFunction" as RF
participant "UserFeatureFunction" as UF
participant "DocFeaturePipeline" as DF
participant "FeatureServicePlugin" as FP
participant "Feature Service" as FS
participant "Downstream model" as MDL
REQ -> RF : common_info / pass_through
RF -> FP : need_request_feature + REQUEST_FEATURE_FALG
FP -> FS : get_general_feature
FS --> FP : FeatureResponse.sample.request_feature
RF -> RF : emit RequestFeature* or pass_through fallback
REQ -> UF : common_info
UF -> FP : user_feature + beha_session/session/msv_read_list_session
FP -> FS : get_general_feature
UF -> UF : emit Sample*
DF -> DF : batch RidTmpInfo, construct ContentFeature + RecommendFeature
DF -> DF : publish candidate channels for extract / PK / RLHF / longterm
DF --> MDL : SampleContext / DocFeatureInfo / RidTmpInfo channels
RF --> MDL : RequestFeature*
UF --> MDL : Sample*
@enduml
```

## 2. 三路特征合同信息图

```infographic
infographic sequence-snake-steps-simple
data
  title request/user/doc 三路特征如何进入模型
  desc 每一路都有不同的 emit 对象、短路条件和兜底行为
  items
    - label request
      desc 构造 cuid、uid、bid、product、token，并设置 need_request_feature=true，只请求 request_feature
      icon mdi:card-account-details-outline
    - label request fallback
      desc 远端未返回时读取 pass_through.request_feature，并补写 request_id=logid
      icon mdi:backup-restore
    - label user
      desc 请求 user_feature、beha_session、session、msv_read_list_session，输出 FeatureResponse.mutable_sample
      icon mdi:account-cog-outline
    - label doc pipeline
      desc 对候选 RidTmpInfo 批量构造 ContentFeature 与 RecommendFeature，写 context_sample 或 DocFeatureInfo
      icon mdi:file-tree-outline
    - label branch publish
      desc 按 pk_generate_v4/v5/rlhf/longterm 等开关复制候选到对应 channel
      icon mdi:source-branch
    - label short circuit
      desc is_open_dnn_soft_rule 关闭时跳过样本构造，仅转发候选，模型特征为空是预期行为
      icon mdi:electric-switch
theme
  palette #2D6A4F #3D5A80 #F59E0B
```

## 3. 关键机制拆解

### 3.1 RequestFeatureFunction 把请求上下文转为 FeatureGeneralRequest

GRC 版 `src/processor/request_feature.cpp:40-59` 清空请求后填入 cuid、uid、bid、UTF8、prefetch=false、BAIDUAPP、`need_request_feature=true`、`REQUEST_FEATURE_FALG` 和 token；`src/processor/request_feature.cpp:65-76` 调插件发 RPC，成功时从 `sample.request_feature()` 取指针 emit。

这条链路还在 `src/processor/request_feature.cpp:84-91` 加了 pass_through 兜底：若远端没有返回 request feature，但原始请求携带 `pass_through.request_feature`，则复制到本地 `_request_feature` 并补写 `request_id=logid`。因此排查 request feature 缺失时要分清“远端未返回但请求兜底成功”和“两者都没有”。

### 3.2 GRG 版 request/user feature 受 dnn 开关影响

GRG 版 `src/process/feature_service/request_feature.cpp:55-57` 和 `src/process/feature_service/user_feature.cpp:54-56` 在 `is_open_dnn_soft_rule` 关闭时执行 SIA_END，但源码没有立即 `return`，后续仍继续构造请求。这个实现细节容易误导观测：SIA 可能显示 vertex 已结束，而 RPC 仍在执行。

如果要用 `is_open_dnn_soft_rule` 作为强短路，需要确认当前分支是否确实 return；否则“关闭开关仍有 FeatureService RPC”不是配置失效，而是代码本身只打点不退出。

### 3.3 UserFeatureFunction 请求的是一组用户与序列特征

`src/process/feature_service/user_feature.cpp:64-89`（GRG）设置 cuid/uid/bid/UTF8/product/token，并添加 `USER_FEATURE_FALG`、`SEQUENCE_FEATRUE_FLAG` 中的 `beha_session/session`、以及 `SV_SESSION_FEATURE_FALG`。`src/process/feature_service/user_feature.cpp:94-105` 调 RPC，成功时 emit `_general_response.mutable_sample()`。

与 request feature 不同，user feature 没有 pass_through 兜底；远端失败时下游拿到的是空指针而不是旧请求中的备份字段。模型输入为空时要先检查 `_user_response.emit()` 后是否仍为 nullptr。

### 3.4 DocFeaturePipeline 把候选批量转为 Sample 或 DocFeatureInfo

GRG 的 `src/process/feature_service/doc_feature_pipeline.cpp:45-52` 从 `Range` 中收集非空 RidTmpInfo 和 rid；`:64-91` 批量发 FeatureService 请求，`:93-105` 把返回 Sample swap 到 `doc_sample.doc_sample_info_p`。

带 cache 的版本 `src/process/feature_service/doc_feature_with_cache_pipeline.cpp:75-91` 复用 `queue_context.custom_context` 中的 Sample；`:190-198` 为每个 RidTmpInfo 添加 `SampleContext`、构造 content/recommend feature，并把 `rid_info->context_sample` 指向该上下文。这意味着 Sample 的生命周期必须覆盖后续模型抽取；不能只保存裸 `SampleContext*` 后清理 Sample。

### 3.5 candidate channel 是业务分支合同

`src/process/feature_service/doc_feature_with_cache_pipeline.cpp:123-169` 根据 `_is_pk_generate_v4`、`_is_pk_generate_v5`、`_is_pk_generate_rlhf`、`_is_pk_generate_longterm_v1` 等开关，将同一批输入候选复制发布到多个 channel。`:170-179` 在 `_is_open_dnn_soft_rule` 关闭时直接把 input swap 到 output，并仍向 extract channel 发布候选。

这条逻辑说明“不开 DNN”不等于“不走后续候选分支”。如果下游 PK 或抽取分支仍收到候选，但 context_sample 为空，应检查开关分支，而不是只查 FeatureService RPC。

### 3.6 YituDocFeatureWithCacheFunction 的并行 batch 保护指针生命周期

GRC 的 `src/processor/doc_feature_with_cache_yitu.cpp:61-75` 按 `_batch_size` 计算 batch，预先 resize `batch_results` 和 `_sample_holders`，并 reserve future list；`:102-120` 用 `bthread_async` 并行处理后等待所有 Future；`:124-139` 合并结果并记录批量耗时。

关键点在 `src/processor/doc_feature_with_cache_yitu.cpp:146-174`：每个 batch 用 `shared_ptr<Sample>` 持有本 batch 的 Sample，对外只把 `SampleContext*` 写入 map。`:191-194` reset 时清空 `_sample_holders`。如果把 shared_ptr 提前释放，下游 map 中的 SampleContext 指针会悬空。

## 4. 故障对账信息图

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title 模型特征缺失的三层定位
  desc 先确认开关与 emit，再确认远端返回与本地样本生命周期
  items
    - label vertex 开关
      desc 检查 is_open_dnn_soft_rule、pk_generate、rlhf、longterm 是否让分支走了候选转发而非样本构造
      value 1
      icon mdi:electric-switch-closed
    - label FeatureService 返回
      desc 检查 request/user/doc 三路 ret、FeatureResponse.error_code、sample/request_feature 是否为空
      value 2
      icon mdi:cloud-search-outline
    - label 本地组装
      desc 检查 SampleContext 指针是否依赖仍存活的 Sample，candidate channel 是否发布到预期下游
      value 3
      icon mdi:vector-link
theme
  palette #2D6A4F #3D5A80 #DC2626
```

## 5. Pitfalls

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0;color:#263238;"><div style="border-left:4px solid #d97706;background:#fff7ed;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#9a3412;text-transform:uppercase;">SIA_END 不一定意味着 return</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">GRG request/user feature 在 dnn 开关关闭时只打点，不立刻退出；需要读控制流确认是否继续 RPC。</div></div><div style="border-left:4px solid #2563eb;background:#eff6ff;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#1d4ed8;text-transform:uppercase;">request 有兜底，user 没有</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">RequestFeature 可从 pass_through 恢复，UserFeature 失败时通常只留下空 Sample 指针。</div></div><div style="border-left:4px solid #dc2626;background:#fef2f2;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#991b1b;text-transform:uppercase;">不要释放 Sample 后保留指针</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">DocFeature 的 map 保存 SampleContext*，必须由 Sample 或 shared_ptr holder 保证生命周期。</div></div><div style="border-left:4px solid #059669;background:#ecfdf5;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#047857;text-transform:uppercase;">候选分发不是模型输入</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">PK/RLHF/longterm channel 收到候选并不证明 context feature 已构造，需要同时检查 context_sample。</div></div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title MV-GRC/GRG Feature 链路排查清单
  items
    - label request
      desc 记录 cuid、uid、bid、token、REQUEST_FEATURE_FALG 与 pass_through fallback 是否触发
      done false
    - label user
      desc 记录 user_feature、beha_session/session、msv_read_list_session 是否进入 FeatureGeneralRequest
      done false
    - label doc
      desc 记录 batch rid 数、FeatureResponse.sample.context_size 和 doc_sample_info_p 是否为空
      done false
    - label 开关
      desc 对齐 is_open_dnn_soft_rule、pk_generate_v4/v5、rlhf、longterm 的所有分支
      done false
    - label 指针生命周期
      desc 确认 SampleContext* 的拥有者 Sample 在下游消费前仍未释放
      done false
    - label 发布通道
      desc 检查 extract、pk_generate、rlhf、merge、longterm channel 的 publish 数量与 output_vec 一致
      done false
theme
  palette #2D6A4F #0F766E #F59E0B
```

## 7. 证据来源

- `src/processor/request_feature.cpp:17-23`：GRC request feature 初始化时按 channel_name 获取 `FeatureServicePlugin`。
- `src/processor/request_feature.cpp:40-91`：GRC request feature 请求构造、RPC、pass_through 兜底与 request_id 回写。
- `src/process/feature_service/request_feature.cpp:39-107`：GRG request feature 的 dnn 开关、FeatureGeneralRequest 构造与 emit。
- `src/process/feature_service/user_feature.cpp:38-108`：GRG user feature 请求构造、三类 feature flag 和 Sample emit。
- `src/process/feature_service/doc_feature_pipeline.cpp:45-108`：doc feature pipeline 的 rid batch、FeatureService 请求和 Sample swap。
- `src/process/feature_service/doc_feature_with_cache_pipeline.cpp:75-91,123-179,190-208`：带 cache 的输入构造、candidate channel 分发、dnn 关闭分支和 SampleContext 组装。
- `src/process/feature_service/doc_feature_with_cache_pipeline.cpp:334-356`：doc feature graph interface 与所有 emit/depend channel。
- `src/processor/doc_feature_with_cache_yitu.cpp:61-75,102-139`：GRC Yitu batch 并行与结果合并。
- `src/processor/doc_feature_with_cache_yitu.cpp:146-194`：单 batch shared_ptr<Sample> 生命周期与 reset 清理。
