# 2026-09-28 业务代码理解：GRG AIGC 硬规则与低清策略链（规则注册 / 正排字段 / HistoryInterest 依赖）

> 日期：2026-09-28（周一）
> 主题选择：今日没有新的 daily-plan；仓库内 `daily-plan-20260529.json` 已过期。按"邻近未覆盖主题"回退：**AIGC 硬规则与低清策略链**。AIGC 在 2026-06-16 成本复盘中只作为正排字段被提及，没有形成独立近期业务笔记；低清规则最近一次出现是 2026-06-16 复盘里"正排字段宽度决定 CPU 底盘"的间接讨论，没有作为单独业务主题。两者在源码中都由 GRG 多样性框架承担，因此把基础侧的请求级参数分层（2026-09-28 base-lib 笔记）和这里的 AIGC/低清业务链自然配对。KU 正文未读取，规则上线背景与业务收益需人工补充；本文只依据代码与配置证据。
> 范围：`src/operator/diversity/aigc_hard_rule.cpp`、`src/strategy/diversity/rule/low_clarity_diversity_rule.cpp`、`src/process/history_interest_info_function.cpp`、`src/plugin/gcms_parser.cpp`、`src/data/video_info.h`、`conf/plugins/exec_engine/multi_stream_select_hard_rule.conf`、`conf/plugins/exec_engine/multi_stream_select_soft_rule.conf`、`conf/common_component/gcms_common_pb_plugin.conf`、`conf/exp_params.conf`、`conf/sid.conf`。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.05em;">AIGC 与低清策略的字段-规则链路</div><div style="display:grid;grid-template-columns:1.1fr 24px 1.3fr 24px 1.2fr 24px 1.2fr;gap:8px;align-items:center;margin-top:12px;"><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #3d5a80;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">正排字段</div><div style="margin-top:7px;font-size:12px;color:#52606d;">GCMS PB 拉取 aigc_resource / aigc_from / aigc_generate_type / video_clarity_rank，写入 VideoInfo。</div></div><div style="text-align:center;color:#64748b;font-size:20px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #2d6a4f;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">依赖构建</div><div style="margin-top:7px;font-size:12px;color:#52606d;">GetHistoryInterestInfoFunction 读取 PCS 中 MicrovideoLowClarityTGI，写入 HistoryInterestInfoData.low_clarity_tgi。</div></div><div style="text-align:center;color:#64748b;font-size:20px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #9a5b28;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">规则注册</div><div style="margin-top:7px;font-size:12px;color:#52606d;">multi_stream_select_hard_rule.conf 注册 AIGCContentHardRule，soft_rule 注册 SvDurationSoftRule.low_clarity_is_hit 命中。</div></div><div style="text-align:center;color:#64748b;font-size:20px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #b8432f;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">打散逻辑</div><div style="margin-top:7px;font-size:12px;color:#52606d;">AIGC 垂类 N 出 1 / 整体 N 出 1；低清按 6-in-N / 3-in-N 窗口限制重复。</div></div></div><div style="margin-top:10px;font-size:12px;color:#52606d;">关键字段来源：aigc_* 三元组、news_res_tag、video_clarity_rank（PB parser）、low_clarity_tgi（PCS）、实验组（sid）。</div></div>

## 1. 主题边界与核心结论

AIGC 与低清策略由两条并行链组成，但共享同一套依赖注入与配置注册：

1. **AIGCContentHardRule**（`REGISTER_STATE_DIVERSITY_RULE`）通过 `update_context()` 维护两个 32 位滑动窗口：`aigc_show_status_ins` 记录总体连续位置，AIGC 垂类哈希 `aigc_vertical_status` 记录每个垂类最近 32 个位置的命中位图；`run_hard_rule()` 根据三个实验组（`aigc_hard_rule_exp_group1/2/3`）和"用户对该垂类 TGI < 1.2"分别决定垂类内 N 出 1（preference gap）和整体兜底 N 出 1（overall gap）（`src/operator/diversity/aigc_hard_rule.cpp:24-116`、`:125-150`、`:270-286`）。
2. **LowClarityDiversityRule**（`BaseDiversityRule` 子类，未单独 `REGISTER_*`）先用 `EXPERIMENT_HIT_PARAM(low_clarity_is_hit)` 或 `Util::hit_abtest("low_clarity_is_hit", ...)` 判定命中；命中后用 `EXPERIMENT_GET_PARAM(low_clarity_smfw)` 拼出 `low_clarity_<flag>` 字典键，再用 `GET_COMMON_DICT(d_cupai_adjust_params_dict)` 读出阈值向量 (tgi_thred, thred1, thred2)，按 `HistoryInterestInfoData.low_clarity_tgi` 是否达到阈值决定 6-in-N / 3-in-N 窗口（`src/strategy/diversity/rule/low_clarity_diversity_rule.cpp:27-94`）。
3. **依赖输入**：`GetHistoryInterestInfoFunction` 在 PCS 结果中取 `MicrovideoLowClarityTGI` 的 `twoshot_result`，匹配 `nid == "low_clarity_tgi"` 后写入 `HistoryInterestInfo::low_clarity_tgi`（`src/process/history_interest_info_function.cpp:177-191`）。`HistoryInterestInfo` 本身在 `src/data/base.h:905-932` 持有 `low_clarity_tgi` 并在 reset 中清零，保证 pool 复用安全。
4. **正排字段**：GCMS PB 解析器将 `aigc_generate_type` / `aigc_resource` / `aigc_from` 三个字段从 `ContentFeature` 复制到 `GcmsItem`；当 `aigc_resource == 1 && aigc_from == 2 && aigc_generate_type == "NewsAutoNormal"` 时把 `news_res_tag = 1`（`src/plugin/gcms_parser.cpp:279-286`）。`VideoInfo` 在结构体中保留 `aigc_generate_type/aigc_resource/aigc_from` 三字段并在 reset 中清零（`src/data/video_info.h:279-332`、`:477-485`）。
5. **配置注册**：AIGCContentHardRule 在 `multi_stream_select_hard_rule.conf:534-537` 用 `condition: ua = 85` 限定 UA 85，默认 valid=1；SvDurationSoftRule 在 `multi_stream_select_soft_rule.conf:2207-2212` 用 `low_clarity_is_hit:1` 二次开关；AB 实验分桶见 `conf/sid.conf:5196-5199`、`conf/sid.conf:7428-7441`。

由此判断：**AIGC 与低清的策略输入完全分离**——AIGC 看正排字段三元组和垂类窗口；低清看 PCS 给出的 `low_clarity_tgi` 与词典参数。两者汇合点是 `DiversityScatterContext` 持有的依赖与状态，但写回响应字段的路径不同。

## 2. AIGC 硬规则详细机制

### 2.1 滑动窗口与垂类状态

`update_context` 在每次候选处理时 `aigc_show_status_ins <<= 1`，并在 `is_aigc` 时把最低位设为 1；AIGC 命中时若垂类可索引（0-31）则对 `aigc_vertical_status[index]` 同样更新（`src/operator/diversity/aigc_hard_rule.cpp:33-47`）。`clear()` 把 `aigc_show_status_ins` 清零并清空 `aigc_vertical_status`（`:120-123`），因此每次 scatter 重置都会丢掉历史位图；进入新 loop 时 `run_hard_rule` 用 `sub_scatter_context->aigc_show_status` 同步 `aigc_show_status_ins`（`:58-62`）。

### 2.2 三组实验与 N 出 1

`run_hard_rule` 用 `get_user_preference_tgi_with_validity` 计算近三天 AIGC 完播率 / 整体完播率比值 `user_preference_tgi`，并返回 `is_tgi_valid`（`:75-77`、`:185-249`）。三组实验策略：

| 实验组 | 命中条件 | preference gap（用户不偏好） | overall gap（兜底） |
|---|---|---|---|
| `aigc_hard_rule_exp_group1` | `hit_abtest` 返回 true | 6 出 1（`preference_gap_num = 6`） | 3 出 1 |
| `aigc_hard_rule_exp_group2` | `hit_abtest` 返回 true | 6 出 1 | 4 出 1 |
| `aigc_hard_rule_exp_group3` | `hit_abtest` 返回 true | 8 出 1 | 6 出 1 |

兜底窗口与垂类窗口分别通过 `check_preference_gap_valid`（`:125-150`）和 `check_overall_gap_valid`（`:270-286`）校验；任一失败则 `run_hard_rule` 返回 false。

### 2.3 AIGC 识别边界

`DiversityScatterContext::check_is_aigc_rid` 在 `src/operator/diversity/scatter_context.cpp:10368-10401` 给出最终判定：它检查 `creative_method`（非空且非 `cover_beauty`），或 `pub_source_from` / `bjh_source_name` 中是否含 `aigc` / `huiboxing` / `magic` / `emotion` 等关键字，并排除 `vtv_zidong` / `vtv_fuzhu`。这条函数同时被 `AIGCContentHardRule::update_context` 和 `run_hard_rule` 调用（`src/operator/diversity/aigc_hard_rule.cpp:34`、`:66`），保证两条规则使用同一份 AIGC 判定，避免在 `update_context` 标记为 AIGC 但 `run_hard_rule` 又被过滤掉的偏差。

## 3. 低清策略详细机制

### 3.1 命中与词典读取

`LowClarityDiversityRule::init_rule_config` 读取 `EXPERIMENT_GET_PARAM(low_clarity_smfw)` 拼出 `low_clarity_<flag>` 字符串作为字典键，然后用 `GET_COMMON_DICT(d_cupai_adjust_params_dict, low_clarity_param_name, low_clarity_params)` 读出向量；向量前三项分别是阈值 (`tgi_thred`)、敏感用户窗口 (`thred1`)、普通用户窗口 (`thred2`)（`src/strategy/diversity/rule/low_clarity_diversity_rule.cpp:45-62`）。若 `res != 0` 或向量长度不足则保留默认值 1.2 / 6 / 3。

### 3.2 TGI 阈值判定

`scatter_context.get_dependency<HistoryInterestInfo>("HistoryInterestInfoData")` 取 `low_clarity_tgi`（`src/strategy/diversity/rule/low_clarity_diversity_rule.cpp:65-69`）；若 `low_clarity_tgi >= low_clarity_tgi_thred` 则视作"敏感用户"，挂 `thred1 in thred1` 窗口并打印 `sensitive_user=1` 日志，否则挂 `thred2 in thred2` 窗口并打印 `sensitive_user=0`（`:70-94`）。两条日志均带 `log_id % 100 == 0` 采样。

### 3.3 `HistoryInterestInfoData` 的来源

`GetHistoryInterestInfoFunction::process` 在 `_pcs_result` 中查 `MicrovideoLowClarityTGI`，拿到 `new_fusion_ctr_dc_q()` 中 `nid == "low_clarity_tgi"` 的 `q_value`，写入 `_microvideo_pcs_history_interest_info_p->low_clarity_tgi`（`src/process/history_interest_info_function.cpp:177-191`）。同一个 processor 还同时写 `att_interest_finish_7days_tgi` / `transfer_interest12_interest_finish_7days_tgi` / `cate_recent_satisfy_bydur` / `subcate_recent_satisfy_bydur` / `att_recent_satisfy_bydur` / `author_vec_finish_7days_tgi` 等 9+ 个兴趣字段，并在末尾 `REGISTER_GRG_FUNCTION` 注册（`:42-244`、`:541-547`）。该函数负责把 PCS 的多模型产出转化为统一的兴趣画像，是 `LowClarityDiversityRule` 唯一能拿到的低清敏感度输入。

### 3.4 字段存储与 reset

`HistoryInterestInfo` 在 `src/data/base.h:905-935` 同时持有 `low_clarity_tgi = 1.0`（默认非敏感）并在 reset 中清零 + 清空 map；`VideoInfo` 在 `src/data/video_info.h:279-332` 持有 `aigc_generate_type/aigc_resource/aigc_from` 三字段，reset 时字符串清空、数值清零。两边 reset 路径都覆盖到字段，保证 pool 复用不会跨请求串状态。

## 4. 配置合同信息图

```infographic
infographic list-grid-badge-card
data
  title AIGC 与低清配置分桶
  desc 配置键与判定的对应关系
  items
    - label multi_stream_select_hard_rule.conf
      desc AIGCContentHardRule: condition ua=85, default=1
      icon mdi/hammer-wrench
    - label multi_stream_select_soft_rule.conf
      desc SvDurationSoftRule: low_clarity_is_hit:1 作为二次开关
      icon mdi/tune-vertical
    - label exp_params.conf
      desc low_clarity_is_hit / low_clarity_smfw / low_clarity_param1
      icon mdi/test-tube
    - label sid.conf
      desc low_clarity_is_hit 2203312:1；aigc_hard_rule_exp_group1/2/3 默认 0/1/0
      icon mdi/account-multiple
    - label gcms_common_pb_plugin.conf
      desc pb_field aigc_generate_type / aigc_resource / aigc_from 三个字段
      icon mdi/database
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. 关键流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title AIGC + LowClarity 在 DiversityScatterContext 中的并行链

participant "GCMS PB parser" as GCMS
participant "VideoInfo" as VI
participant "PCS / MicrovideoLowClarityTGI" as PCS
participant "GetHistoryInterestInfoFunction" as HII
participant "DiversityScatterContext" as DSC
participant "AIGCContentHardRule" as AIGC
participant "LowClarityDiversityRule" as LC
participant "MultiStreamHard/Soft rule" as MS
participant "Response" as RESP

GCMS -> VI : copy aigc_*, news_res_tag
PCS -> HII : MicrovideoLowClarityTGI.twoshot_result
HII -> DSC : HistoryInterestInfoData.low_clarity_tgi
MS -> AIGC : REGISTER_STATE_DIVERSITY_RULE (ua=85)
MS -> LC : bind low_clarity_is_hit
loop 每个候选
  DSC -> AIGC : update_context
  AIGC -> DSC : aigc_show_status_ins / aigc_vertical_status
  DSC -> AIGC : run_hard_rule
  DSC -> LC : init_rule_config + get_labels
  AIGC --> DSC : preference/overall gap pass?
  LC --> DSC : 6/3-in-N window conf
end
DSC -> RESP : window conf 进入 showlist 序列
@enduml
```

## 6. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#f5f7fa;border:1px solid #d8e0ea;border-left:8px solid #b8432f;border-radius:16px;padding:20px;margin:18px 0;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#b8432f;font-weight:800;">Pitfalls · AIGC / LowClarity</div><div style="font-size:26px;line-height:1.15;font-weight:900;margin:8px 0 12px;">同一窗口被两条规则同时写入，会让打散阈值叠加成 “永远不展现”。</div><div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;"><div style="background:#fff;border-top:4px solid #b8432f;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;"><b>三类风险：</b><br>① AIGCContentHardRule 的 `aigc_show_status_ins` 与 LowClarity 共享一个 `DiversityScatterContext`，但前者按 RID 是否 AIGC 滑动，后者按 TGI 阈值选择窗口；日志里若只看 `ret=false` 容易误判源头；② `news_res_tag = 1` 同时由 GCMS PB 解析（aigc_resource/from/generate_type）与 `video_news_res_*` 前缀命中决定，需要同时核对；③ `EXPERIMENT_GET_PARAM(low_clarity_smfw)` 默认 123456，未命中实验时字典键变成 `low_clarity_123456`，请人工确认该键已配置。</div><div style="background:#fef3c7;border-top:4px solid #f59e0b;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;color:#7c2d12;"><b>评审顺序</b><br>PB 字段 → PCS 输出 → Function 写依赖 → 规则读依赖 → 实验组命中；任何链路里字段缺失都要回到上一环。</div></div><div style="margin-top:12px;font-weight:900;color:#b8432f;">∎ 打散失败先看依赖，再看规则；不要直接调窗口数。</div></div>

## 7. 调试 checklist

```infographic
infographic list-column-done-list
data
  title AIGC + LowClarity 排查清单
  desc 六步定位窗口叠加或依赖缺失
  items
    - label 核对 GCMS 字段
      desc 确认 aigc_resource / aigc_from / aigc_generate_type / video_clarity_rank 已正确写入
      done true
    - label 核对 PCS 输出
      desc 确认 MicrovideoLowClarityTGI 在 _pcs_result 中存在且 nid == "low_clarity_tgi"
      done true
    - label 核对历史兴趣依赖
      desc 确认 HistoryInterestInfoData 被 scatter ctx 注入且 low_clarity_tgi 被赋值
      done true
    - label 核对实验组命中
      desc 用 log_id 取 aigc_hard_rule_exp_group1/2/3 与 low_clarity_is_hit 的 abtest 结果
      done true
    - label 核对字典键
      desc 确认 low_clarity_<smfw_flag> 键在 d_cupai_adjust_params_dict 中存在
      done false
    - label 核对窗口叠加
      desc 当多条规则共同写入 SAME_MOST_M_IN_N 时合并到最严格一条
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 8. 证据来源

- `src/operator/diversity/aigc_hard_rule.cpp:24-116`：AIGCContentHardRule 的滑动窗口、垂类状态和三组实验分桶。
- `src/operator/diversity/aigc_hard_rule.cpp:120-150`：`clear()` 与垂类 N 出 1 校验。
- `src/operator/diversity/aigc_hard_rule.cpp:185-249`：近三天 AIGC/整体完播率与 TGI 计算。
- `src/operator/diversity/aigc_hard_rule.cpp:270-286`：整体 N 出 1 窗口校验。
- `src/operator/diversity/scatter_context.cpp:10368-10401`：`check_is_aigc_rid` 的 AIGC 识别边界。
- `src/strategy/diversity/rule/low_clarity_diversity_rule.cpp:20-95`：低清策略命中判定、词典读取与窗口写入。
- `src/process/history_interest_info_function.cpp:42-244`：`GetHistoryInterestInfoFunction` 把 PCS 多模型结果写入 `HistoryInterestInfoData`，包含 `low_clarity_tgi`。
- `src/process/history_interest_info_function.cpp:541-547`：`REGISTER_GRG_FUNCTION(GetHistoryInterestInfoFunction)` 注册位置。
- `src/plugin/gcms_parser.cpp:279-286`：GCMS PB 把 aigc 三元组写入 `news_res_tag`。
- `src/data/video_info.h:279-332`、`:477-485`：`VideoInfo` 持有 aigc 字段并在 reset 中清零。
- `src/data/base.h:905-935`：`HistoryInterestInfo` 持有 `low_clarity_tgi` 并在 reset 中清零。
- `conf/plugins/exec_engine/multi_stream_select_hard_rule.conf:534-537`：AIGCContentHardRule 注册与 UA 限定。
- `conf/plugins/exec_engine/multi_stream_select_soft_rule.conf:2207-2212`：SvDurationSoftRule 借助 `low_clarity_is_hit` 二次开关。
- `conf/common_component/gcms_common_pb_plugin.conf:248-255`：aigc 三字段 PB parser 注册。
- `conf/exp_params.conf:485-495`：low_clarity_is_hit / low_clarity_smfw / low_clarity_param1 参数定义。
- `conf/sid.conf:5196-5199`、`conf/sid.conf:7428-7441`：低清与 AIGC 实验组 sid 默认值与分桶。

> KU 正文未读取；AB 实验上线背景、收益指标、owner 与回滚记录需人工补充。本文只描述 AIGC 与低清策略的代码与配置合同，不评价具体业务效果。