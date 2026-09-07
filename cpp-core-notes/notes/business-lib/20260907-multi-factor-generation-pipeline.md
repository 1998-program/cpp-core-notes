# 2026-09-07 周一业务库理解：多因子生成（multi_factor）业务流水线

> 日期：2026-09-07  
> 主题来源：当前 canonical 笔记仓库未找到 2026-09-07 当日 daily-plan；旧计划 Monday 槽位“GRC 性能优化主线”已在 20260901/20260903 覆盖；按邻近主题回退到 GRC `src/processor/multi_factor/` 下 24 个因子生成算子的业务流水线，聚焦“abtest 闸门 + 神经网络权重加载 + 候选循环打分 + state_code 上报”这一可复制的业务范式。KU 正文未读取，业务背景需人工补充。  
> 范围：`feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/processor/multi_factor/` 24 个 `*_factor_gen.cpp`（attract、attraction、bq_future、cp_ltr、cp_ltv、cp_playtime_efficient、playtime_efficient、playtime_q_depolarize、satisfy、session_ltr、session_ltr_dibar、subcate_future、surprise、slide、sonar_interaction、emo_future、author_future_v2、author_subsequent_value、adc_ltr、adc_rank、minjie_ltr、ltr、ltr_scene、gcf_sim_score），以及 `src/data/rid_tmp_info.h` 中的多因子字段、`src/processor/base_function.h` 的 GRCGraphFunction 契约。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">图依赖层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`ctr_rank_result` + `sid_info` + `common_info`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">所有 24 个因子生成算子都把 ctr_rank_result 当作输入候选流，用 sid_info 做实验分流，用 common_info 触发上下文。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">业务因子层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`GRCGraphFunction::process()` × 24</div><div style="margin-top:8px;font-size:12px;color:#52606d;">abtest 闸门 → emit state_code(=-1) → 网络结构/权重加载 → 候选循环打分 → state_code 标记成功 → SIA_END。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">输出层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`factor_result` + `factor_state_code_info`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">每个算子至少 emit 两个数据：候选打分结果（vector<RidTmpInfoPtr>）和状态码（int32_t，-1/1），供后续粗排/精排使用。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">前置 abtest</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">加载神经网络</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">候选循环打分</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">写入 rank 字段</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:10px;text-align:center;color:#991b1b;">state_code 上报</div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRC 多因子生成业务流水线（24 个 factor_gen 算子通用范式）
participant "GRC service" as GRC
participant "vertex dispatcher" as VTX
participant "FactorGenFunction" as FG
participant "SidInfo / CommonInfo" as SID
participant "CommonDict" as DICT
participant "RidTmpInfo candidates" as RID
participant "factor_result emit" as EMIT
participant "state_code emit" as STATE
participant "Dapper / SIA" as SIA

GRC -> VTX : run ctr_rank_result → factor_gen_*
VTX -> FG : process()
FG -> SID : read sid_info + common_info
FG -> FG : CHECK_IS_NULL 依赖
FG -> SIA : SIA_START(*_factor_gen_vertex)
FG -> STATE : *_state_code.emit()
STATE -> STATE : *= -1  (默认失败)
FG -> EMIT : *_factor_result.emit()
EMIT -> EMIT : clear() + reserve(size)
loop 每个候选 item
  EMIT -> EMIT : emplace_back(item)
end
FG -> SID : Util::hit_abtest("xxx_exp", *sid_info)
alt 命中实验
  FG -> DICT : GET_COMMON_DICT(权重 dict_key, ...)
  FG -> FG : 构造网络 fcs / nn_activations
  loop 每个候选 item
    FG -> RID : 读 dnn_score_* / refine_multi_*
    FG -> DICT : 读后验统计 vid posterior
    FG -> FG : 算 score = sum(w_i * q_i)
    FG -> RID : item->playtime_efficient_score = score
  end
  FG -> RID : std::stable_sort(by score)
  FG -> RID : 写 *_rank = idx / all_num
  FG -> STATE : *state_code = 1
else 未命中
  FG -> FG : 短路（不写 score / 不排序）
end
FG -> SIA : SIA_ADD(efficient_code, code)
FG -> SIA : SIA_END(*_factor_gen_vertex, 0)
@enduml
```

## 2. 结构信息图

```infographic
infographic hierarchy-structure
data
  title 多因子算子统一契约：5 个共同结构
  desc 24 个 factor_gen 算子共享同一套输入依赖、打分结构、状态码协议
  items
    - label 输入依赖
      desc ctr_rank_result + sid_info + common_info + 业务特征
      children
        - label 候选流
          desc std::vector<RidTmpInfoPtr> 来自 CTR 粗排或前序算子
        - label 实验闸门
          desc sid_info 控制是否进入该因子计算与使用哪套权重
        - label 业务特征
          desc mcvt_user_model / msv_readlist / video_attract_emb / ums_feature 等
    - label 神经网络结构
      desc 每个算子自定 fcs / activations / feature_num
      children
        - label LTR 结构
          desc ltr_factor_gen.cpp:48-49 用 {31,32,1} 三层 + sigmoid
        - label Attract 结构
          desc attract_factor_gen.cpp:102-103 用 {115,64,16,1} 四层 + relu/relu/sigmoid
        - label 权重加载
          desc GET_COMMON_DICT_BY_HASH / GET_COMMON_DICT 加载 KVStrVecDouble
    - label 候选打分
      desc 在 for 循环里逐个 video 计算 score，写回 RidTmpInfo
      children
        - label 读字段
          desc dnn_score_playtime / dnn_score_ctr / refine_multi_voteup 等
        - label 写字段
          desc playtime_efficient_score / bq_future_score / satisfy_ltr_score 等
    - label 状态码
      desc 默认 -1 表示未正确计算，命中实验后改写为 1
      children
        - label emit 协议
          desc *_factor_state_code_info.emit() 返回 Commiter<int32_t>
        - label SIA_ADD
          desc SIA_ADD(efficient_code, code) 同步上报代码返回
    - label 输出排序
      desc 打分后做 stable_sort 并写 *_rank 字段供后续粗排消费
      children
        - label 排序键
          desc playtime_efficient / cp_playtime_efficient / bq_future_score 等
        - label 归一化
          desc *_rank = rank_idx / all_num
theme
  palette #3D5A80 #F59E0B #2D6A4F
```

## 3. 关键机制拆解

### 3.1 24 个因子算子共享同一份“5 段式” process() 骨架

`feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/processor/multi_factor/` 全量 24 个文件都遵循同一结构：
1. `CHECK_IS_NULL` 一组前置依赖（ctr_rank_result / sid_info / common_info 等）。
2. `SIA_START(*_factor_gen_vertex)` 打点。
3. `*_factor_state_code_info.emit()` + `*= -1` 标记默认失败。
4. `*_factor_result.emit()` + `clear()/reserve()` + `emplace_back(item)` 复制候选。
5. `if (Util::hit_abtest(...)) { ... }` 实验闸门内做打分，否则短路。

以 `attract_factor_gen.cpp:55-94` 和 `playtime_efficient_factor_gen.cpp:22-49` 为例，骨架一致，差异只在 abtest 名字、权重 dict_key、网络结构和写入字段。

### 3.2 abtest 闸门是“因子生效”的唯一开关

`attract_factor_gen.cpp:76-83` 与 `ltr_factor_gen.cpp:42-50` 都在最外层用 `Util::hit_abtest("multi_factor_generation", *sid_info)` 或 `Util::hit_abtest("remove_attract_factor", *sid_info)` 控制是否进入真正的打分逻辑。这意味着：
- 未命中实验的请求只会复制候选、把 state_code 设为 -1，不消耗神经网络推理预算；
- 命中实验的请求同时拿到一个 SIA 维度上的实验观察点。

`playtime_efficient_factor_gen.cpp:73-78` 还演示了“二级 abtest 选权重文件”模式：`efficient_huiju_<sid>` 拼接后通过 `GET_COMMON_DICT` 取不同实验下的权重组合，避免为每个 sid 单独写一遍网络。

### 3.3 神经网络结构与权重加载是“算子级静态常量”

`attract_factor_gen.cpp:102-107` 把网络结构硬编码：
```cpp
std::vector<int> fcs = {115, 64, 16, 1};   // 网络结构参数
std::vector<std::string> nn_activations = {"relu", "relu", "sigmoid"};
int feature_num = 0;
for (int i = 1; i < fcs.size(); ++i) {
    feature_num += fcs[i] * (fcs[i - 1] + 1);
}
```
然后用 `feature_num` 比对 `GET_COMMON_DICT_BY_HASH` 取到的 `vecVal.size()`。`ltr_factor_gen.cpp:48-54` 用了三层 `{31, 32, 1}` 加 `sigmoid`/`sigmoid`。两者都把网络结构和激活函数当作类内常量，在进程启动后不会改变——这是“因子生成算子”区别于“动态推理服务”的关键。

### 3.4 候选打分在 `for (auto item : *res)` 循环中完成

`playtime_efficient_factor_gen.cpp:102-158` 演示了典型打分循环：
- 读 DNN 概率：`dnn_score_playtime`、`dnn_score_ctr`、`refine_multi_voteup`、`refine_multi_follow`、`dnn_score_commen_ctr`。
- 读后验统计：通过 `GET_COMMON_DICT(vid_res, ..., (*it_video)->rid, d_microvideo_punish_newcates_nid_part, vid_result)` 拿 6 维 vid posterior。
- 算 13 维加权和 `ces_score`，最终 `playtime_efficient_score = w13 * playtime_q + ces_score / complete_q`。
- 写回 `(*it_video)->playtime_efficient_score`。

注意：所有打分都在 `playtime_efficient_res` 拷贝上完成，所以 `ctr_rank_result` 不会被污染，下游算子拿到的是叠加后的 RidTmpInfo。

### 3.5 排序与归一化字段 *_rank 是粗排的真正输入

`playtime_efficient_factor_gen.cpp:160-172` 用 `std::stable_sort(all_tmp_res->begin(), all_tmp_res->end(), RidTmpInfoEfficient())` 按 score 排序，再 `playtime_efficient_rank = rank_idx / all_num` 写入归一化位次。`src/data/rid_tmp_info.h:3744-3750` 暴露了多个排序函数（RidTmpInfoEfficient / CpRidTmpInfoEfficient 等）供不同因子复用。这意味着下游粗排算子拿到的不是“分数”，而是“位次”，方便按位次交叉合并多个因子。

### 3.6 state_code=-1/1 是因子生效的最终“闸门开关”

`attract_factor_gen.cpp:68-69, 87-89` 显式写出 `*attract_state_code = -1` 起步，命中后改写为 `1`；`playtime_efficient_factor_gen.cpp:29-30, 41-43` 同款。state_code 的 `-1/1` 二值语义让上游监控可以一眼判断某因子是否真正参与了排序。注意：默认 -1 不是"出错"，而是"未命中实验/未正确计算"的统称。

### 3.7 SIA_START/SIA_ADD/SIA_END 把整个 process 串成可观测整体

每个因子算子的 `process()` 都把入口 `SIA_START`、`SIA_ADD(efficient_code, ...)`、出口 `SIA_END` 串起来，配合 `dapper_span.cost_us` 形成 vertex 级别的耗时埋点。`ltr_factor_gen.cpp:46,90,97` 显示 LTR 算子内还会再分支一次 SIA_ADD 用于子阶段上报。

### 3.8 EMIT_DATA 依赖声明决定了算子的图依赖结构

每个 factor_gen 文件末尾的 `GRAPH_FUNCTION_INTERFACE(...)` 块（`playtime_efficient_factor_gen.cpp:179-184`）声明 4 类数据：DEPEND_DATA（输入）、EMIT_DATA（输出）、NAMED_DEPENDENCY（命名依赖）、NAMED_EMIT（命名输出）。`attract_factor_gen.cpp:366-373` 与 `ltr_factor_gen.cpp:386-395` 用同样的宏展开。维护者改依赖时只能在这里加一行，运行时由框架自动注入。

## 4. Pitfalls 卡片

<style>.biz-card{font-family:Inter,Arial,sans-serif;background:#F5F7FA;border-radius:18px;padding:20px;margin:18px 0;border-left:8px solid #3D5A80;color:#1f2937;box-shadow:0 10px 26px rgba(31,41,55,.08)}.biz-card .meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:800}.biz-card h3{font-size:25px;line-height:1.15;margin:8px 0 12px}.biz-card .grid{display:grid;grid-template-columns:2fr 1fr;gap:14px}.biz-card p,.biz-card li{font-size:14px;line-height:1.65}.biz-card ul{margin:0 0 0 18px}.biz-card .side{border-left:3px solid #F59E0B;padding-left:12px;color:#374151}.biz-card code{background:#e8edf3;border-radius:5px;padding:1px 5px}</style><div class="biz-card"><div class="meta">Pitfalls / FactorGen Pipeline</div><h3>多因子算子的“统一骨架”一旦漏掉一段（state_code、reserve、SIA_END），下游粗排就会拿到不完整位次。</h3><div class="grid"><div><ul><li><b>state_code 默认 -1：</b>必须先 <code>*state_code = -1</code>，再决定是否改成 1；否则命中实验也写不进去。</li><li><b>候选拷贝漏 reserve：</b><code>factor_result.emit()</code> 后必须 reserve(ctr_rank_result->size())，否则大流量下反复扩容。</li><li><b>未命中实验被误以为生效：</b>abtest 闸门外的代码务必短路，不要把“算分”放到外面执行。</li><li><b>权重 vecVal 维度不对：</b>13 维 / 6 维后验统计的硬编码必须与字典版本对齐，字典版本升级时同步核对。</li><li><b>排序写入错位：</b><code>*_rank</code> 必须按稳定排序后的位次除以 all_num，否则粗排归一化错乱。</li><li><b>SIA_END 漏写：</b>异常分支也要 SIA_END，否则 vertex 永远卡在 START 状态。</li></ul></div><div class="side">评审口径：每新增一个 factor_gen 算子，先回答“5 段骨架是否齐全？”、“abtest 名字是否唯一？”、“EMIT_DATA 字段是否冲突？”。</div></div></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 多因子算子调试 checklist
  desc 从实验闸门、打分链路、状态码、排序四个维度排查
  items
    - label 校验 abtest 是否命中
      desc sid_info 是否进入 Util::hit_abtest("xxx_exp", *sid_info) 的命中分支
      done true
    - label 校验依赖是否 emit
      desc ctr_rank_result / sid_info / common_info 是否在 EMIT 列表中可读
      done true
    - label 校验 state_code 转换
      desc 默认 -1 是否被覆盖为 1，命中分支是否漏写
      done true
    - label 校验权重 vecVal 维度
      desc feature_num 与 vecVal.size() 是否匹配，网络 fcs 与权重个数对齐
      done true
    - label 校验打分循环完整性
      desc 所有 *_score 字段是否在 for 循环里被赋值，没有遗漏 item
      done true
    - label 校验排序与 *_rank 归一化
      desc stable_sort 后是否按位次 / all_num 写入 *_rank
      done true
    - label 校验 SIA 配对
      desc SIA_START 与 SIA_END 是否配对，异常分支是否补 SIA_END
      done true
theme
  palette #3D5A80 #F59E0B #2D6A4F
```

## 6. 证据来源

- `feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/processor/multi_factor/`：24 个 `*_factor_gen.cpp` 文件，统一范式。
- `attract_factor_gen.cpp:50-94`：AttractFactorGenFunction process 骨架 + state_code + abtest。
- `attract_factor_gen.cpp:102-107`：115/64/16/1 四层网络 + relu/relu/sigmoid 激活。
- `attract_factor_gen.cpp:366-373`：GRAPH_FUNCTION_INTERFACE 依赖声明。
- `attract_factor_gen.cpp:374`：REGISTER_GRC_FUNCTION 注册点。
- `ltr_factor_gen.cpp:42-50`：multi_factor_generation 实验闸门。
- `ltr_factor_gen.cpp:48-54`：31/32/1 三层网络 + sigmoid 激活。
- `ltr_factor_gen.cpp:386-397`：LTR 接口与 REGISTER_GRC_FUNCTION。
- `playtime_efficient_factor_gen.cpp:17-49`：5 段式 process 骨架。
- `playtime_efficient_factor_gen.cpp:73-78`：efficient_huiju_<sid> 二级 abtest 选权重。
- `playtime_efficient_factor_gen.cpp:102-158`：13 维权重 + 6 维 vid 后验打分循环。
- `playtime_efficient_factor_gen.cpp:160-172`：stable_sort + *_rank 归一化。
- `playtime_efficient_factor_gen.cpp:179-184`：GRAPH_FUNCTION_INTERFACE + REGISTER_GRC_FUNCTION。
- `src/data/rid_tmp_info.h:1467-1469, 2262, 2265, 3722, 3744, 3750`：多因子字段定义与排序比较器。
- `src/processor/base_function.h:34-44`：GRCGraphFunction init/process 二次收紧契约。