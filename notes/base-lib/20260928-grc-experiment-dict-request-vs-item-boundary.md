# 2026-09-28 基础库代码理解：GRC 实验/词典读取在请求级一次与 item 热循环之间的边界

> 日期：2026-09-28（周一）
> 主题选择：今日没有新的 daily-plan；仓库内 `daily-plan-20260529.json` 已过期，且该计划列出的 GRC 调权算子（microvideo_fancate_downrank_adjuster / kanju_lcn_precise_adjuster / kanju_days_lt_precise_adjuster 等）在当前 GRC 快照中并不存在，因此不能伪造历史算子。回退到当前快照真实存在、并被 2026-09-26 成本/CPU 复盘与 2026-06-16 复盘同时关注的**实验参数与词典读取的分层位置**主题：把它从调权算子抽出来，只描述 GRC 主链路上的位置规则。KU 正文未读取，需人工补充业务背景与收益；本文只依据代码证据。
> 范围：`src/processor/request.cpp`、`src/processor/micro_grc_recall.cpp`、`src/processor/gen_response.cpp`、`src/processor/feed_ufs_function.cpp`、`src/processor/common_pcs_rpc.cpp`。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.05em;">实验/词典读取在 GRC 请求里的分层</div><div style="display:grid;grid-template-columns:1.1fr 24px 1.25fr 24px 1.35fr 24px 1.1fr;gap:8px;align-items:center;margin-top:12px;"><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #3d5a80;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">Request 入口</div><div style="margin-top:7px;font-size:12px;color:#52606d;">`RequestFunction` 在请求级一次性读取端上重排开关和 client_rerank_v2/v3_num，并把结果写进 refresh_info / common_info。</div></div><div style="text-align:center;color:#64748b;font-size:20px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #2d6a4f;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">Recall/Pre 阶段</div><div style="margin-top:7px;font-size:12px;color:#52606d;">`MicroGRCRecall`、`FeedUFSFunction`、`FeedUFSPreFunction` 在请求级读 `hit_block_searchc`、`client_rerank_dup_key`，并把结果写到 pass_through。</div></div><div style="text-align:center;color:#64748b;font-size:20px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #9a5b28;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">Response 准备</div><div style="margin-top:7px;font-size:12px;color:#52606d;">`GenResponse` 集中读 `preload_str`、`reqnum_reload`、`kanu_dhc_*` 实验组，再用 `GET_DICT(shangyang_cupai_adjust_params_dict)` 拉取组合参数。</div></div><div style="text-align:center;color:#64748b;font-size:20px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #b8432f;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">Item 热循环禁区</div><div style="margin-top:7px;font-size:12px;color:#52606d;">item factor 计算只读已被本仓缓存到 context 的派生值；不要在这里重读 EXPERIMENT/GET_COMMON_DICT。</div></div></div><div style="margin-top:10px;font-size:12px;color:#52606d;">关键约束：上面四层的参数读取顺序保证“每请求一次”发生在 item 循环之前；下游只能消费已经缓存到 graph/scatter context 的结果，否则会变成每 item 一次。</div></div>

## 1. 核心结论

GRC 主链路上 `EXPERIMENT_GET_PARAM` / `GET_COMMON_DICT` / `GET_DICT` 调用必须按层级划分：

1. **请求级入口**：`RequestFunction` 一次性完成 `client_rerank_v2_key` / `client_rerank_v3_key` 的命中判定，并把数量写到 `refresh_info` 与 `common_info`（`src/processor/request.cpp:1306-1312`）。判断结果会被同一请求的多个下游消费，因此属于"读一次用 N 次"。
2. **Recall/Pre 阶段**：`MicroGRCRecall`、`FeedUFSFunction`、`FeedUFSPreFunction` 在请求级读 `hit_block_searchc`、`client_rerank_dup_key`、`ertiao_ufs_not_xintan_hit`，并把 pass_through 准备好（`src/processor/micro_grc_recall.cpp:509-516`、`src/processor/feed_ufs_function.cpp:195`、`feed_ufs_function.cpp:501`、`feed_ufs_function.cpp:678`、`feed_ufs_pre_function.cpp:116`、`common_pcs_rpc.cpp:159`）。
3. **响应级组合**：`GenResponse` 在响应组装早期集中读 `preload_str`、`reqnum_reload`、`kanu_dhc_thres_flag` 等，并通过 `GET_DICT(shangyang_cupai_adjust_params_dict)` 读取 `shangyang_cupai` 组合参数（`src/processor/gen_response.cpp:884-892`、`:2855-2898`、`:2923-2924`）。这层读取的总量虽小，但向量长度可达多项，需要明确日志采样。
4. **item factor 计算**：当前快照中**没有任何调权算子**包含 `EXPERIMENT_GET_PARAM` / `GET_COMMON_DICT` 调用。所有请求级参数都已经在前三层把结果写入 graph/scatter context，下游策略只读取派生值。这与 2026-06-16 复盘笔记给出的"实验参数应在列表级读取"的判断一致。

排查 CPU 异常时应先确认新增策略是否把"每请求一次"的读取误放进了 item 热循环，因为一旦放进循环就意味着 N 次重复解析 + 日志拼接。

## 2. 请求级与 item 级的边界

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title 实验/词典读取的分层时序

participant "RequestFunction" as REQ
participant "MicroGRCRecall" as RECALL
participant "FeedUFSFunction" as UFS
participant "Graph/scatter ctx" as CTX
participant "Item loop" as LOOP
participant "GenResponse" as RESP

REQ -> REQ : EXPERIMENT_GET_PARAM(client_rerank_v2_key)
REQ -> CTX : write refresh_info / common_info
RECALL -> RECALL : EXPERIMENT_HIT_PARAM(my_param_b)
RECALL -> RECALL : EXPERIMENT_GET_PARAM(my_param_a)
RECALL -> CTX : pass_through.exp_params
UFS -> UFS : EXPERIMENT_GET_PARAM(hit_block_searchc)
UFS -> CTX : write client_rerank_dup_is_hit
LOOP -> CTX : read derived values only
RESP -> RESP : EXPERIMENT_GET_PARAM(preload_str, kanu_dhc_*)
RESP -> RESP : GET_DICT(shangyang_cupai_adjust_params_dict)
RESP -> RESP : split result into val_vector
note over LOOP,RESP
  Item loop must not call EXPERIMENT_GET_PARAM /
  GET_COMMON_DICT / GET_DICT; use cached values from CTX.
end note
@enduml
```

## 3. 实验参数与词典的契约

```infographic
infographic sequence-ascending-steps
data
  title 参数读取的请求级合约
  desc 五步合同保证每请求只读取一次
  items
    - label 入口读
      desc RequestFunction 在 graph 启动前完成 client_rerank_v2/v3 命中判断
      icon mdi/login
    - label 缓存写
      desc 把命中结果写到 refresh_info / common_info / pass_through.exp_params
      icon mdi/content-save
    - label Recall 读
      desc MicroGRCRecall/FeedUFS 读取同一实验参数，命中后立即写回 pass_through
      icon mdi/magnify-scan
    - label 下游消费
      desc item factor 读取散点上下文派生值，禁止再次访问 EXPERIMENT 接口
      icon mdi/check-decagram
    - label 响应收尾
      desc GenResponse 收尾时集中读 preload_str/kanu_dhc_* 与 shangyang_cupai 词典
      icon mdi/flag-checkered
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 4. 关键机制拆解

### 4.1 `client_rerank_v2/v3` 必须请求级一次性写入

`RequestFunction` 在 `refresh_info` 准备好之后立刻 `EXPERIMENT_GET_PARAM((*exp_manager), client_rerank_v2_key)` 和 `client_rerank_v3_key`，并根据两个数量组合 `os_branch`/`app_version` 判断是否触发端上重排（`src/processor/request.cpp:1306-1330`）。这把"请求级一次"的判断固定在入口；下游若需要重做必须显式通过 `pass_through` 或 `common_info` 取值。

### 4.2 `pass_through.exp_params` 是 Recall 阶段的承载容器

`MicroGRCRecall` 在 `pass_through` 上执行 `CopyFrom(*request_feature_p)` 与 `CopyFrom(*exp_params_p)`，再调用 `EXPERIMENT_GET_PARAM(my_param_a/my_param_b)` 写入上下文（`src/processor/micro_grc_recall.cpp:497-516`）。同时该 processor 通过 `SIA_ADD(is_general_microvideo_scene / is_general_search_scene)` 区分场景（`:494-495`）。`FeedUFSFunction` 在 195/501/678 三处直接读 `EXPERIMENT_GET_PARAM(hit_block_searchc / client_rerank_dup_key / ertiao_ufs_not_xintan_hit)`，分别覆盖是否拦截搜索召回、二跳是否走 dup 和是否绕开新弹（`src/processor/feed_ufs_function.cpp:195`、`feed_ufs_function.cpp:501`、`feed_ufs_function.cpp:678`）。同一份实验参数被三条链路消费，但都发生在请求级。

### 4.3 `GenResponse` 把响应级实验组和词典集中读取

`GenResponse` 在处理"变更 reqnum / 预加载位置"时连续调用 `get_sid_map_var("reqnum_reload", ...)`，随后用 `EXPERIMENT_GET_PARAM(preload_str)` 拿到参数名，并 `GET_DICT("shangyang_cupai_adjust_params_dict", KvDict)` 取参数值，再按逗号 split 解析（`src/processor/gen_response.cpp:884-895`）。响应尾部还集中读取 `kanu_dhc_thres_flag` / `kanu_dhc_andor_flag` / `kanu_dhc_rec_real_thresv0` 等 7 项实验组（`:2855-2898`），以及 `client_rerank_v2_key_0509` / `client_rerank_v3_key_0509`（`:2923-2924`、`:4170`）。这一连串读取形成"响应级一次"的合同，避免后续再向响应字段注入实验判断。

### 4.4 当前快照内没有任何调权算子持有参数读取

对 `src/operator/adjuster/` 全树做 grep，GRC 当前的调权算子文件不存在 `EXPERIMENT_GET_PARAM` 或 `GET_COMMON_DICT` 调用；调权所需的实验开关与参数都通过前文三层下沉到 GraphData。这意味着两件事：

- 任何新策略若在调权算子中新增 `EXPERIMENT_GET_PARAM`，就破坏了"每请求一次"的分层；它会被每个候选执行一次。
- 当 `kanu_dhc_*` 这类响应级参数需要被前置调权使用时，必须显式走 `pass_through` 或 `common_info`，不能在算子内重读。

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#f5f7fa;border:1px solid #d8e0ea;border-left:8px solid #b8432f;border-radius:16px;padding:20px;margin:18px 0;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#b8432f;font-weight:800;">Pitfalls · 实验/词典读取位置</div><div style="font-size:26px;line-height:1.15;font-weight:900;margin:8px 0 12px;">一次读取出现在 item 循环里，等价于把 N 倍 CPU 卖给每一个候选。</div><div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;"><div style="background:#fff;border-top:4px solid #b8432f;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;"><b>三类风险：</b><br>① 把 `EXPERIMENT_GET_PARAM` 放进 item 热循环，相当于在每个候选上重做一次参数解析 + 日志输出；② `GET_COMMON_DICT` 在 item 循环里使用未缓存的向量，结果不可预期且不报告失败；③ 日志缺少采样（`% 100` / `% 1000`），拼接出来的字符串会成为隐藏 IO。</div><div style="background:#fef3c7;border-top:4px solid #f59e0b;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;color:#7c2d12;"><b>评审顺序</b><br>入口读 → 缓存写 → Recall 读 → 下游消费 → 响应收尾；任何超过这五层之外的参数读取都先怀疑。</div></div><div style="margin-top:12px;font-weight:900;color:#b8432f;">∎ 把"每请求一次"和"每 item 一次"画在图上，再讨论 CPU。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 实验/词典读取位置评审清单
  desc 五步定位每条新增参数读取是否破坏了分层
  items
    - label 标注读取位置
      desc 在 commit diff 中区分 RequestFunction/Recall/Response/Adjuster 四层
      done true
    - label 检查是否进入 item 循环
      desc grep 调权算子源码，确认没有 EXPERIMENT_GET_PARAM / GET_COMMON_DICT
      done true
    - label 检查缓存写
      desc RequestFunction 是否把结果写到了 refresh_info / common_info
      done true
    - label 检查日志采样
      desc 大段 NOTICE 日志是否带 % 100 / % 1000 采样
      done true
    - label 检查词典读取失败回退
      desc GET_DICT / GET_COMMON_DICT 在 dict 为空时是否回退到默认向量
      done false
    - label 检查回滚路径
      desc 实验参数变更是否能通过 sid 直接关停
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/processor/request.cpp:1306-1330`：请求级一次性读 `client_rerank_v2_key/v3_key`，根据 os_branch + app_version 判断端上重排。
- `src/processor/micro_grc_recall.cpp:497-516`：Recall 阶段把 `exp_params` 拷到 `pass_through`，并读 `my_param_a/my_param_b`。
- `src/processor/feed_ufs_function.cpp:195`：`EXPERIMENT_GET_PARAM(hit_block_searchc)`，拦截搜索召回。
- `src/processor/feed_ufs_function.cpp:501`：`client_rerank_dup_is_hit` 来自 `EXPERIMENT_GET_PARAM(client_rerank_dup_key)`。
- `src/processor/feed_ufs_function.cpp:678`：`EXPERIMENT_GET_PARAM(ertiao_ufs_not_xintan_hit)`，二跳召回配置开关。
- `src/processor/feed_ufs_pre_function.cpp:116`：与 `feed_ufs_function` 配对的 pre-function，同名实验参数。
- `src/processor/common_pcs_rpc.cpp:159`：PCS RPC 入口再次读 `hit_block_searchc`，保证 PCS 链与主链分叉一致。
- `src/processor/gen_response.cpp:884-895`：响应级一次性读 `preload_str` + `GET_DICT(shangyang_cupai_adjust_params_dict)`。
- `src/processor/gen_response.cpp:2855-2898`：`kanu_dhc_*` 实验组集中读取。
- `src/processor/gen_response.cpp:2923-2924`、`:4170`：`client_rerank_v2_key_0509/v3_key_0509` 响应级参数读取。

> KU 正文未读取；线上实验开关、收益与 owner 需人工补充。本笔记只描述 GRC 主链路上"参数读取必须分层"的代码契约，不评价具体业务效果。