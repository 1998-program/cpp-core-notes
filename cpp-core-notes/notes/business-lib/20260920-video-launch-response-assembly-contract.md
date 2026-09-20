# 2026-09-20 业务库理解：video_launch 合并结果到 GRG 响应与 ExtMsg 透传

> 日期：2026-09-20（周日）  
> 主题选择：未发现 2026-09-20 有效 daily-plan；近期业务笔记已覆盖 quota 计算和 video_launch 合并的基础顺序，因此按相邻未覆盖边界回退到 **DataMerge 输出 → ResponseForGrg attachment/ExtMsg → NewsResponse content 组装与漏斗观测**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：`DataMergeFunction` 的输出 ownership/去重/兜底、`ResponseForGrgFunction` 的依赖图与 attachment/ExtMsg 编码、`NewsResponseFunction` 的 rule/function/effect content 组装、`grg_response.conf` 的条件依赖。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7faf5;border:1px solid #d9e2d0;border-radius:8px;padding:16px;margin:16px 0;color:#243126;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">合并输出层</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">DataMergeFunction</div><div style="margin-top:8px;font-size:12px;color:#52615a;">清空本轮资源，按保量→效果→兜底填充到 total quota，并复制一份供统计。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">响应协议层</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">ResponseForGrgFunction</div><div style="margin-top:8px;font-size:12px;color:#52615a;">按 graph_opt 选择 response 资源，透传 actual_reqnum，并把用户/策略信息编码进 attachment 与 item ext。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">业务内容层</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">NewsResponseFunction</div><div style="margin-top:8px;font-size:12px;color:#52615a;">按规则、功能和效果队列构造 Content，排序效果队列后记录每类输出长度和总数。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">RidTmpInfo vectors</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:6px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">merged_data_result</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">GRCResponse + attachment</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;padding:10px;text-align:center;color:#334155;font-size:13px;">Content + funnel</div></div></div>

**一句话结论：** video_launch 的“合并完成”只表示 `merged_data_result` 已按 quota 形成；真正对外可用还要经过 ResponseForGrg 的 attachment/ExtMsg 透传和 NewsResponse 的 Content 组装，三层任何一层条件不满足都会表现为“结果数量正确但业务字段或展示内容缺失”。

## 1. 合并到响应的核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title video_launch 合并结果到 GRG 响应
participant "DataMergeFunction" as DM
participant "merged_data_result" as MERGED
participant "ResponseForGrgFunction" as RESP
participant "GRCResponse" as PB
participant "NewsResponseFunction" as NEWS
participant "Content / ContentItem" as CONTENT
participant "Funnel + SIA" as OBS
DM -> DM : clear output / ext info
DM -> MERGED : merge_data()
DM -> DM : baoliang first
DM -> DM : effect queue with quota/diversity
DM -> DM : final fallback until total quota
DM -> RESP : response dependency
RESP -> PB : graph_opt selects/reuses response
RESP -> PB : set attachment.actual_reqnum
RESP -> CONTENT : fill item fields and RespItemExt
RESP -> RESP : SerializeToString(UserRecommendFeature)
RESP -> PB : base64 -> attachment.user_recommend_feature
NEWS -> CONTENT : add rule/function content when gate matches
NEWS -> CONTENT : stable_sort effect queue
NEWS -> CONTENT : fill effect content
NEWS -> OBS : SIA total count + per-content length
DM -> OBS : merge counts / data merge SIA
@enduml
```

## 2. 响应边界信息图

```infographic
infographic sequence-ascending-steps
data
  title video_launch 对外响应的四个边界
  desc 从候选集合到最终 Content，每一步都有自己的条件和观测字段
  items
    - label 合并
      desc DataMergeFunction 清空输出，按保量、效果、最终兜底形成 merged_data_result
      icon mdi:merge
    - label 复制与统计
      desc const_merged_data_result 复制合并结果，calc_ext_info 和 print_log 使用只读副本
      icon mdi:content-copy
    - label attachment / ExtMsg
      desc ResponseForGrg 透传 actual_reqnum、用户推荐特征和候选级 RespItemExt 字段
      icon mdi:attachment
    - label Content / funnel
      desc NewsResponse 按队列组装 content，记录 grg_*_res_len 与总输出数
      icon mdi:chart-funnel
theme
  palette #2D6A4F #3D5A80 #F59E0B
```

## 3. 关键机制拆解

### 3.1 DataMergeFunction 的 ownership 是“清空→生成→复制→统计”

`src/processor/video_launch/data_merge.cpp:48-74` 的 `process()` 先检查 quota、效果队列和保量依赖，随后清空 `merged_data_result`、`const_merged_data_result` 与 `merged_data_ext_info`。`merge_data()` 生成主结果后，代码把主结果复制到 const 副本，再释放输出句柄，之后对副本做 `calc_ext_info` 和 `print_log`，最后以副本大小结束 SIA。

这说明下游如果需要稳定统计快照，应消费 `const_merged_data_result`；不能把“主输出已 release”误解为数据已经销毁，也不能在本轮 process 前持有上一轮的 vector 内容。

### 3.2 合并顺序和去重标记决定最终 quota

`src/processor/video_launch/data_merge.cpp:153-178` 固定执行：先 `reserve(*_total_quota)`，再插入保量数据，随后根据低活用户、兴趣 quota 和兴趣信息选择常规或 v2 效果合并，最后直接遍历效果队列兜底填满 quota。兜底阶段不再考虑各类别 quota 和多样性限制，因此最终数量补齐不能证明前置策略全部命中。

`src/processor/video_launch/data_merge.cpp:181-206` 通过 `output_vec.size() >= *_total_quota` 限制容量，通过空指针和 `is_insert_sketchy_rank` 避免重复插入，并在首次插入时补齐 `type_vec`。排查重复内容时应同时看 vector 容量、指针为空和 insert 标记。

### 3.3 类型队列可以并行处理，但必须统一 join

`src/processor/video_launch/data_merge.cpp:400-418` 将不同类型的非保量效果队列封装为多个 `merge_context`，为每个 context 创建 Future 并用 `bthread_async` 调用 `merge_effect_other_data`，随后逐个 `future.get()`。join 完成后才汇总 `merge_<type>_count` 和 `effect_merge_count`。

因此 `merge_effect_other_data` 的内部数据不能假设“所有类型已经完成”；类型间并行结束点在 Future join，而不是在创建 Future 的循环末尾。

### 3.4 ResponseForGrg 由配置声明依赖图，再把结果分成 response 和 attachment

`conf/plugins/graph/video_launch/grg_response.conf:1-18` 声明 `ResponseForGrgFunction`，emit `_response_for_grg` 和 `_truncated_effect_queue_for_pcs`，并依赖新闻 response、loads、IP cluster 等数据。配置还以条件依赖形式接入 Set2set、PCS、channel 等分支；缺少条件数据时，不能简单按完整依赖图理解所有请求都会经过同一条路径。

`src/processor/video_launch/response_for_grg.cpp:178-226` 中，`graph_opt` 命中时将 response ref 到资源创建的 `GRCResponse`，否则清空已有 response；之后写入直播 content、读取策略和 q cache，并将请求的 `reqnum` 写入 `response->mutable_attachment()->set_actual_reqnum`。

### 3.5 UserRecommendFeature 通过 protobuf + base64 放入 attachment

`src/processor/video_launch/response_for_grg.cpp:859-904` 在条件允许时把 set2set sample 复制到 `UserRecommendFeature`，调用 `SerializeToString` 得到二进制字符串，再 base64 编码后写入 `grc_response` attachment 的 `user_recommend_feature`。因此 downstream 读取该字段时需要同时满足：样本条件分支已命中、protobuf 序列化成功、base64 attachment 字段被写入。

同一文件的 `_set_res_ext` 路径会将 `ridinfo` 的 category、channel、recall score、merge score、queue/position、各类 DNN score 等字段写入每个 `RespItemExt`。这些字段是候选级协议，不应与 response attachment 中的全局请求级字段混为一谈。

### 3.6 NewsResponse 负责 Content 分组、排序和输出漏斗

`src/processor/video_launch/news_response.cpp:29-67` 先清空 response；当 UA 不为 85 且 `rec_num` 超过阈值时，按顺序写入 rule 和 function content；随后对 effect queue 稳定排序，写入 effect content。最后遍历 `_res_content_vec`，按每个 content 的 key 写入 `grg_<key>_res_len`，并将总数写入 `kuaixun_grg_res_len`。

这意味着“response protobuf 有内容”与“业务 output funnel 计数正确”是两件事：content 可能因为空指针被跳过，或者 rule/function 分支因 UA/rec_num 条件未执行，但 effect 仍然存在。

## 4. 三层输出对账图

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title 合并、协议、展示三层的故障对账
  desc 先确认候选集合，再确认跨层字段，最后确认 content 与漏斗
  items
    - label merge layer
      desc 关注 total_quota、baoliang_count、merge_effect_other_count、final_doudi_count 和 is_insert_sketchy_rank
      value 1
      icon mdi:database-arrow-up
      children
        - label async join
          desc 每类 merge context 的 Future 必须完成
        - label stable snapshot
          desc const_merged_data_result 用于 ext info 与日志
    - label protocol layer
      desc 关注 graph_opt、actual_reqnum、UserRecommendFeature attachment、RespItemExt 和条件依赖
      value 2
      icon mdi:protocol
      children
        - label global attachment
          desc reqnum/user feature 属于 response 级字段
        - label item ext
          desc score/category/queue/position 属于 candidate 级字段
    - label presentation layer
      desc 关注 rule/function/effect content 的门控、排序和 grg_*_res_len
      value 3
      icon mdi:view-list-outline
      children
        - label gate
          desc UA 与 rec_num 决定 rule/function 是否进入 response
        - label funnel
          desc content key 和 items_size 决定输出观测
theme
  palette #2D6A4F #3D5A80 #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#ffffff;border:1px solid #d6d3ce;border-left:6px solid #2d6a4f;border-radius:8px;padding:18px;margin:18px 0;color:#243126;line-height:1.6;"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#2d6a4f;font-weight:900;">Pitfalls · video_launch response</div><div style="font-size:24px;line-height:1.2;font-weight:900;margin:7px 0 12px;">合并数量正确，不代表响应协议完整</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #2d6a4f;border-radius:6px;padding:12px;font-size:14px;"><b>三个常见误判：</b><br>① 看到 merged_data_result 已满 quota 就跳过 ResponseForGrg 的 attachment 检查；② 把 UserRecommendFeature 的全局 attachment 和 RespItemExt 的候选字段当成同一协议；③ 只看 protobuf items，不看 NewsResponse 的 UA/rec_num 门控和输出漏斗。</div><div style="background:#fff7ed;border-top:3px solid #f59e0b;border-radius:6px;padding:12px;font-size:14px;color:#7c2d12;"><b>核对顺序</b><br>merge quota → insert marker → const snapshot → graph_opt response → actual_reqnum → serialized attachment → item ext → content gate → stable sort → funnel。</div></div><div style="margin-top:12px;font-weight:900;color:#2d6a4f;">∎ 线上排查要区分“候选没生成”“字段没透传”和“Content 没被门控放行”。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title video_launch 响应链排查清单
  desc 从 DataMerge 输出一路核对到 GRG response 和最终 content
  items
    - label 确认输入依赖
      desc total_quota、effect_other_data、merged_baoliang_data 等依赖非空
      done true
    - label 确认输出清空
      desc 本轮 merged_data_result、const snapshot、ext info 没有复用上轮残留
      done true
    - label 确认三段合并
      desc 保量、效果队列和最终兜底的数量分别记录
      done false
    - label 确认去重标记
      desc nullptr 或 is_insert_sketchy_rank 的候选不会再次插入
      done false
    - label 确认异步 join
      desc 每个 merge_context 的 Future get 完成后再读取 output_data 与 merge count
      done false
    - label 确认配置条件依赖
      desc grg_response.conf 中 NewsInVideoImmersionGraph、Set2set、PcsInGrc 等条件与请求场景一致
      done false
    - label 确认 attachment
      desc actual_reqnum、UserRecommendFeature 和 base64 字段是否实际写入
      done false
    - label 确认候选级 ext
      desc RespItemExt 的 score/category/queue/position 是否来源于正确 RidTmpInfo
      done false
    - label 确认 content 门控
      desc UA、rec_num、rule/function/effect 队列和 stable_sort 分支是否符合预期
      done false
    - label 对齐 funnel
      desc grg_<key>_res_len、kuaixun_grg_res_len 与最终 response items_size 一致
      done false
    - label 补齐业务背景
      desc 从 KU/协议平台确认 attachment 字段版本、GRG 消费约定和漏斗告警阈值
      done false
theme
  palette #2D6A4F #3D5A80 #F59E0B
```

## 7. 证据来源

- `src/processor/video_launch/data_merge.cpp:48-74`：输出清空、合并、const snapshot、ext info 和 SIA 收口。
- `src/processor/video_launch/data_merge.cpp:153-206`：保量→效果→兜底顺序、quota 限制和 `is_insert_sketchy_rank` 去重标记。
- `src/processor/video_launch/data_merge.cpp:400-418`：按类型异步 merge 和 Future join。
- `conf/plugins/graph/video_launch/grg_response.conf:1-18`：ResponseForGrgFunction、emit 和基础依赖。
- `conf/plugins/graph/video_launch/grg_response.conf:30-90`：Set2set、PCS、channel 等条件依赖声明。
- `src/processor/video_launch/response_for_grg.cpp:178-226`：graph_opt response 资源、策略初始化和 actual_reqnum attachment。
- `src/processor/video_launch/response_for_grg.cpp:859-904`：UserRecommendFeature 复制、SerializeToString、base64 和 attachment 写出。
- `src/processor/video_launch/response_for_grg.cpp:366-404`：候选级 RespItemExt 的 category、channel、recall score 等字段写出。
- `src/processor/video_launch/response_for_grg.cpp:496-515`：merge score、原始 score、queue/position 和授权字段写出。
- `src/processor/video_launch/news_response.cpp:29-67`：rule/function/effect content 门控、稳定排序、输出数量 SIA。

> KU 正文未读取，需人工补充 ResponseForGrg 的完整 attachment 协议、GRG 对 UserRecommendFeature/actual_reqnum 的消费版本、条件依赖的线上场景覆盖，以及 response funnel 的告警阈值。
