# 2026-09-15 业务库理解：GRG DiversityMerge 的 reqnum 截断与多流执行边界

> 日期：2026-09-15  
> 主题选择：未发现本日有效 daily-plan；工作区中的旧计划日期为 2026-05-29，且近期笔记已覆盖 actual_reqnum 回传、Deepes quota 和缓存响应，因此回退到邻近未覆盖的 **GRG DiversityMerge reqnum 槽位、插入阈值和 MultiStreamEngine 执行闭环**。KU 正文未读取，业务背景需人工补充；本文只陈述代码可验证的行为。  
> 范围：旧版 `DiversityMergeFunction`、新版 `NewDiversityMergeFunction`、四类输入队列和 `multi_stream_diversity.conf` 的 stream/executor 合同。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.3fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">输入分槽</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">loads / rule / function / effect</div><div style="margin-top:8px;font-size:12px;color:#52606d;">`reqnum` 决定 loads 与 rule 的槽位数量；function/effect 保持单槽输入。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">多流执行</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">MultiStreamEnginePlugin → run</div><div style="margin-top:8px;font-size:12px;color:#52606d;">Graph dependency 绑定外层函数与池化引擎，配置把选择、PK 和 effect 阶段串成执行图。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#102a43;text-transform:uppercase;letter-spacing:.04em;">结果闭环</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">output → append → SIA</div><div style="margin-top:8px;font-size:12px;color:#52606d;">引擎输出先转为业务结果，effect 队列补充未入选资源，再记录成功数、类别数和回滚数。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 48px 1fr 48px 1fr 48px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">reqnum</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:6px;padding:10px;text-align:center;color:#047857;font-size:13px;">queue slots</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">engine output</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:10px;text-align:center;color:#991b1b;font-size:13px;">final result</div></div></div>

**一句话结论：** `reqnum` 在这里既是结果规模提示，也是输入容器的槽位边界；当它小于插入阈值时，rule/function 队列根本不会读取，排查“规则没生效”必须先确认阈值分支，而不是直接检查规则算子。

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRG DiversityMerge reqnum 与 MultiStreamEngine 流程
participant "GRG graph function" as FN
participant "reqnum" as N
participant "loads queue" as LOADS
participant "rule/function queues" as INSERT
participant "effect queue" as EFFECT
participant "MultiStreamEngine" as ENGINE
participant "result vector" as RESULT
participant "SIA" as SIA
FN -> N : read *_reqnum
FN -> FN : allocate loads/rule slots = reqnum
FN -> LOADS : read_queue(loads)
alt reqnum > insert threshold
  FN -> INSERT : read rule + function
else reqnum <= threshold
  FN -> INSERT : skip insert queues / warning
end
FN -> EFFECT : read effect queue
FN -> ENGINE : set_loop_num(max(reqnum, result_num)) / run
ENGINE --> RESULT : selected DynamicStruct items
FN -> RESULT : append effect items not already selected
FN -> SIA : success size / queue counts / category counts
@enduml
```

## 2. 队列与配置结构信息图

```infographic
infographic sequence-snake-steps-simple
data
  title DiversityMerge 的五个处理阶段
  desc 代码输入容器与配置 stream 共同决定候选如何进入引擎和结果
  items
    - label allocate
      desc loads 与 rule 按 reqnum 建槽，function 与 effect 使用单槽
      icon mdi:vector-square
    - label read queues
      desc 先读取 loads；通过阈值后才读取 rule/function；effect 独立读取
      icon mdi:inbox-arrow-down
    - label build engine input
      desc 将 RidTmpInfo 槽位包装成 SelectStreamContainer，恢复 div_index 顺序
      icon mdi:package-variant
    - label execute streams
      desc loads_select、rule_select、function_select、final_select 后进入 PK 阶段
      icon mdi:source-branch
    - label append and emit
      desc 引擎结果转换后补 effect，最后写入 div_succ_size 与 SIA 计数
      icon mdi:playlist-check
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 新旧实现都把 reqnum 转换为输入槽位数

旧版 `src/process/diversity_merge.cpp:251-257` 和新版 `src/process/new_diversity_merge.cpp:203-209` 都创建四类输入：`loads`、`rule` 的 vector 长度为 `rec_num`，`function`、`effect` 为 1。这个结构意味着候选的槽位索引是请求级合同；`reqnum` 过大还会直接扩大容器，必须结合输入上限和内存指标观察。

### 3.2 插入阈值决定 rule/function 是否读入

旧版 `src/process/diversity_merge.cpp:263-273` 在命中 `change_req_num` 实验时把全局插入阈值改为 0，随后只有 `rec_num > FLAGS_recnum_threshold_for_insert` 才读取 rule、news_rule 和 function。新版 `src/process/new_diversity_merge.cpp:214-220` 使用实例配置的 `_insert_recnum_threshold`。二者都在低 `reqnum` 下保留 loads/effect 路径，却跳过插入队列，结果看起来像“规则失效”，实质是输入没有进入引擎。

### 3.3 输入容器要经过排序和生命周期包装

新版 `src/process/new_diversity_merge.cpp:224-259` 先收集非空 loads 槽位，再对每个输入 vector 按 `div_index` 稳定排序；随后把槽位转换为 `SelectStreamContainer` 并保存到 `_containers`，避免临时 container 在引擎运行期间失效。旧版也有相同的容器桥接结构。排查乱序或悬挂引用时，应同时看 `div_index`、`_containers` 和引擎输入 map。

### 3.4 执行 loop 上限不是简单等于 reqnum

`src/process/diversity_merge.cpp:796-803` 与 `src/process/new_diversity_merge.cpp:266-275` 都使用 `result_num = max(rec_num, _result_num)`，然后设置引擎 loop 数并调用 `run()`。这会让配置的 `result_num` 在低请求数场景仍影响引擎循环；性能对比需要固定两者，而不能只比较 reqnum。

### 3.5 引擎结果之后还有 effect 补充阶段

新版 `src/process/new_diversity_merge.cpp:283-320` 把 engine output 转为 `RidTmpInfo`，记录 `div_succ_size`，然后遍历 effect 队列，把尚未设置 `div_status` 的项目标记为 `effect_append` 并追加。最终 SIA 记录插入量、mv/dt/sv/news 类别数。因而 `div_succ_size` 不是最终结果数，最终 size 还包含 effect append。

### 3.6 配置把选择和 PK 拆成不同 executor

`conf/plugins/exec_engine/multi_stream_diversity.conf:8-64` 定义 loads/rule/function/final 四个核心 stream，并定义 `merge_pk` 将 loads、rule、function、effect_pk 的输出组合。`conf/plugins/exec_engine/multi_stream_diversity.conf:687-767` 表明 index PK、score PK 同步执行，而 effect PK 使用独立 cost strategy，可按实验条件选择不同算子。线上修改 executor 时要同步确认阶段顺序和条件表达式。

## 4. 结果对账信息图

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title “输入被跳过”与“引擎选不出”的区分
  desc 两类现象最终都可能表现为结果数下降，但证据位置不同
  items
    - label input gate
      desc reqnum、insert threshold、read_queue size 和 warning 日志
      value 1
      icon mdi:filter-variant
      children
        - label threshold
          desc reqnum <= threshold 时 rule/function 不读
        - label slots
          desc loads/rule 槽位长度由 reqnum 决定
        - label ordering
          desc 输入按 div_index 恢复原始顺序
    - label engine gate
      desc diversity_engine_rpc、div_succ_size、rollback 和 category counters
      value 2
      icon mdi:engine-outline
      children
        - label execution
          desc run ret 与 diversity_engine_rpc 耗时
        - label output
          desc engine output 与 effect_append 的数量差异
        - label final
          desc final result size、div_insert_size、div_msv_num 等计数
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#ffffff;border:1px solid #d7dee8;border-left:6px solid #0f766e;border-radius:8px;padding:18px;margin:18px 0;color:#1f2937;line-height:1.6;"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#0f766e;font-weight:800;">Pitfalls · Diversity Boundary</div><div style="font-size:24px;line-height:1.2;font-weight:900;margin:7px 0 12px;">结果少，不一定是规则算子坏了</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:6px;padding:12px;font-size:14px;"><b>四个高频误读：</b><br>① 忽略 reqnum 阈值，误以为 rule/function 已执行；② 把 div_succ_size 当最终结果数；③ 只看 result_num，忽略 effect append；④ 修改配置后只看 stream 名称，没核对 executor 条件和同步属性。</div><div style="background:#fff7ed;border-top:3px solid #f59e0b;border-radius:6px;padding:12px;font-size:14px;color:#7c2d12;"><b>核对顺序</b><br>reqnum → threshold → queue sizes → div_index → run ret → div_succ_size → effect append → final SIA。</div></div><div style="margin-top:12px;font-weight:900;color:#0f766e;">∎ 先证明候选进了引擎，再评价多样性策略效果。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title DiversityMerge reqnum 排查清单
  desc 适用于规则未插入、结果数不足、顺序错乱和引擎耗时抖动
  items
    - label 固定 reqnum 与 result_num
      desc 同时记录请求 reqnum、函数配置 result_num 和最终 loop 上限
      done true
    - label 检查插入阈值
      desc 区分旧版全局阈值、change_req_num 实验和新版实例阈值
      done true
    - label 对账 queue input size
      desc 分别记录 loads、rule、news_rule、function、effect 的读取数量
      done false
    - label 检查槽位和排序
      desc 核对 vector 长度、非空槽位、div_index 稳定排序和容器生命周期
      done false
    - label 检查引擎执行
      desc 记录 diversity_engine_rpc ret、耗时、loop_num 和 output size
      done false
    - label 区分补充结果
      desc 对比 div_succ_size、effect_append 数量与最终 div_result size
      done false
    - label 对账业务计数
      desc 检查 div_insert_size、类别计数、rollback 和 trace_log_size
      done false
    - label 核对配置条件
      desc 确认 stream、executor、is_syncer、condition 与实验开关一致
      done false
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/process/diversity_merge.cpp:251-273`：旧版 reqnum 槽位和插入阈值分支。
- `src/process/diversity_merge.cpp:796-803`：旧版 loop 上限和 MultiStreamEngine 执行。
- `src/process/diversity_merge.cpp:1138-1162`：旧版回滚、类别计数和 SIA 收束。
- `src/process/new_diversity_merge.cpp:53-75`：新版引擎池获取与 Graph dependency 绑定。
- `src/process/new_diversity_merge.cpp:203-220`：新版 reqnum 槽位和实例插入阈值。
- `src/process/new_diversity_merge.cpp:224-275`：新版排序、容器桥接和引擎执行。
- `src/process/new_diversity_merge.cpp:283-320`：引擎结果、effect append 和最终 SIA。
- `conf/plugins/exec_engine/multi_stream_diversity.conf:8-64`：loads/rule/function/effect stream 结构。
- `conf/plugins/exec_engine/multi_stream_diversity.conf:687-767`：PK executor、同步属性和条件算子。

> KU 正文未读取，需人工补充线上 reqnum 口径、各 ua 的阈值策略、stream cost strategy 约束、结果数基线和规则插入的业务验收指标。
