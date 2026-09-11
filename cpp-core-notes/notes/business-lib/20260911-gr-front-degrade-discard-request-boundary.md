# 2026-09-11 业务库理解：GRF Degrade/Discard 控制面与请求分发边界

> 日期：2026-09-11  
> 主题选择：未发现当日 weekly/daily plan；本周 fallback 基础主题选定 `gr-front` rframe 启动链后，业务侧按真实代码边界配对到 Degrade/Discard HTTP 控制面、NSHEAD 请求解析、`FlowStrategy` 条件命中与 `FeedClosure` 分支。KU 正文未读取，业务上下文需人工补充；以下结论只依据代码证据。  
> 范围：`service/http_service.cc`、`service/discard_service.cc`、`service/nshead_service.cc`、`dict/flowstrategy.cc`、`dispatch/command_dispatcher.cc`、`dispatch/feed.cc` 与 `flow_strategy.conf`。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:10px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;">控制面</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">/degrade / /discard</div><div style="margin-top:8px;font-size:12px;color:#52606d;">HTTP query 读写降级比例、场景集合和丢弃模式，返回 text/plain 状态。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;">策略状态</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">FlowStrategy 双缓冲</div><div style="margin-top:8px;font-size:12px;color:#52606d;">set 构造 FlowUnit/DiscardUnit，原子切换 index；request path 只读当前快照。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;">请求面</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">NsheadJsonService → FeedClosure</div><div style="margin-top:8px;font-size:12px;color:#52606d;">解析 JSON、公参和 sid/smfw 后，决定是否降级、丢弃或进入 rframe 执行树。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 52px 1fr 52px 1fr 52px 1fr;gap:8px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">action=set/get</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;font-size:13px;">FlowUnit / DiscardUnit</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;font-size:13px;">enable_strategy / do_discard</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">FeedClosure tree</div></div></div>

## 1. 请求与控制面流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRF 控制面与请求面的交汇
actor "Operator" as OP
participant "DegradeService" as DEG
participant "DiscardService" as DIS
participant "FlowStrategy" as FLOW
participant "NsheadJsonService" as NS
participant "CommandDispatcher" as CMD
participant "FeedClosure" as FEED
OP -> DEG : /degrade?action=set&ratio=...
DEG -> FLOW : set_unit(sid, percent, refresh_state/origin/os/scene)
FLOW -> FLOW : build condition + atomic index switch
OP -> DIS : /discard?action=set&discard=...
DIS -> FLOW : set_dis_unit(mode)
FLOW -> FLOW : update DiscardUnit snapshot
NS -> NS : parser + fill_params + init_experiment
NS -> FLOW : enable_strategy(session)
NS -> CMD : dispatch(command)
CMD -> FEED : new_feed_closure + feed()
FEED -> FLOW : do_discard(session)
alt discard hit
  FEED -> FEED : set_discard + return_and_destroy
else normal or degrade path
  FEED -> FEED : choose SampleServerTree / InterleavingTree / ListTree / SetRedisTree
end
@enduml
```

## 2. 控制参数信息图

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title Degrade 与 Discard 的状态边界
  desc 两个 HTTP 控制面共享 FlowStrategy，但写入不同快照结构
  items
    - label Degrade
      desc ratio、refresh_state、origin_channel_id、os_branch、scene、sid 组合成条件表达式和抽样比例
      value FlowUnit
      icon mdi:tune-variant
      children
        - label action=get
          desc 输出 percent、条件集合、sid 和 degrade_off_gr_ad
        - label action=set
          desc 5 秒写入限频，percent 范围 0-100
        - label action=off/on_gr_ad
          desc 远端 IP 权限校验后切换广告降级原子标记
    - label Discard
      desc discard=0..4 映射为自动刷新、readafter、双列引导等丢弃开关组合
      value DiscardUnit
      icon mdi:delete-sweep
      children
        - label action=get
          desc 输出 discard_enable 模式值
        - label action=set
          desc 5 秒写入限频，非法模式返回参数错误
        - label request hit
          desc FeedClosure 开始阶段命中后直接 return_and_destroy
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 Degrade HTTP 接口把 query 参数转换成 FlowUnit

`service/http_service.cc:34-63` 先检查 brpc controller 和 `action` 参数。`action=get` 时，`service/http_service.cc:65-108` 从 `FlowStrategy::get_unit` 读取 percent、refresh_state、origin_channel_id、os_branch、scene、sid，并附带输出 `degrade_off_gr_ad`。

`action=set` 在 `service/http_service.cc:109-208` 读取 `ratio`，要求 0 到 100；可选解析 `refresh_state`、`origin_channel_id`、`os_branch`、`scene`，其中 scene 为空时默认加入 `mainfeed` 和 `lite`。代码里 `sid_value != nullptr` 分支会把 `err_no` 置为参数错误，而 `sid` 为空时才调用 `set_unit`，这个行为需要业务方人工确认是否符合接口预期。

### 3.2 FlowStrategy 用双缓冲快照降低读写冲突

`dict/flowstrategy.cc:143-216` 创建新的 `FlowUnit`，把多个集合转换为 `condition` 字符串，调用 `unit->exp.load(condition)`，再写入 `_m_unit[1-index]` 并原子切换 `_current_index`。`dict/flowstrategy.cc:160-165` 通过 `_last_set_time` 限制 5 秒内重复写入。

请求期 `enable_strategy` 在 `dict/flowstrategy.cc:331-406` 构造 `condition_values`，包含 `hour`、`minute`、`refresh_state`、基于 `cuid + log_id` 的 `percent`、`os_branch`、`origin_channel_id`、`refresh_index`。随后先匹配 `flow_strategy.conf` 中的配置策略，再检查 scene、percent、sid、条件表达式和随机抽样。

### 3.3 Discard 是更直接的早退控制

`service/discard_service.cc:30-75` 同样按 HTTP query 的 `action` 分发；`action=set` 要求 `discard` 参数，调用 `FlowStrategy::set_dis_unit`。`dict/flowstrategy.cc:255-289` 将 `discard=0..4` 映射为 `discard_enable`、`discard_readafter`、`discard_guide_double_list` 的组合，并通过 `_dis_current_index` 切换快照。

`dict/flowstrategy.cc:314-328` 的 `do_discard` 明确了早退条件：自动刷新且非冷启、`refresh_state == 14` 的 readafter、或 `guide_double_list == 1`。`dispatch/feed.cc:84-93` 在 `FeedClosure::feed()` 开始就调用 `do_discard`，命中后 `set_discard(true)` 并直接返回销毁，不再进入后续树。

### 3.4 NSHEAD 请求解析负责把业务字段归一化后交给分发器

`service/nshead_service.cc:32-58` 创建 `GRFSessionContext`，设置 logid、全局配置和 `conf_ctx`，再调用 `parser`。`service/nshead_service.cc:182-213` 中 `parser` 解析 JSON、检查参数、调用 `fill_params`，并写入 `gr_micro_module_from = gr_front`。

`service/nshead_service.cc:236-476` 的 `fill_params` 是请求边界核心：它解析 product、channel、osbranch、refresh_state、sid/smfw、cuid、baiduid、uid、refresh_index、reqnums 等字段，并在部分场景下改写 `channel_id` 与 `command`。例如 `channel_id` 属于新 feed 且非 lite 时可能把 `channel_id` 改为 `TAB_RECOMMENDATION`，同时保留 `origin_channel_id`。

### 3.5 CommandDispatcher 只接受 feed/channel，FeedClosure 再选择执行树

`dispatch/command_dispatcher.cc:22-35` 只对 `command == "feed"` 或 `command == "channel"` 创建 `FeedClosure` 并调用 `feed()`，其他 command 直接返回 `ERR_REQ_OTHER`。`dispatch/feed.cc:95-109` 之后按 `rl_imitation`、interleaving sid 和普通请求选择 `SampleServerTree`、`InterleavingTree` 或 `ListTree`；`dispatch/feed.cc:124-142` 在 list 后按 `enable_dr()` 决定是否进入 `SetRedisTree` 或直接响应并设置 unsend。

## 4. 证据来源

- `service/http_service.cc:34-63`：Degrade 参数与 action 基础校验。
- `service/http_service.cc:65-108`：Degrade get 输出当前策略快照。
- `service/http_service.cc:109-208`：Degrade set 解析 ratio、refresh_state、origin_channel_id、os_branch、scene 并调用 `set_unit`。
- `service/discard_service.cc:30-75`：Discard get/set 控制面。
- `dict/flowstrategy.cc:143-216`：FlowUnit 条件构建、表达式加载和双缓冲切换。
- `dict/flowstrategy.cc:255-328`：DiscardUnit 模式映射、快照切换和请求早退判断。
- `dict/flowstrategy.cc:331-406`：请求期 `enable_strategy` 条件值、配置策略、scene、sid 和抽样判定。
- `service/nshead_service.cc:32-103`：NSHEAD 请求创建 session、解析、实验初始化并调用 dispatcher。
- `service/nshead_service.cc:236-476`：请求字段解析、channel/command 归一化和 reqnums 默认值。
- `dispatch/command_dispatcher.cc:22-35`：只分发 feed/channel 命令。
- `dispatch/feed.cc:84-142`：discard 早退、SampleServer/Interleaving/List/SetRedis 树选择。
- `flow_strategy.conf:1-43`：高峰期 refresh_state、hour、minute、percent 和 sid 组合策略。

## 5. Pitfalls 卡片

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f7fb;border-left:5px solid #3D5A80;padding:14px 16px;margin:16px 0;color:#243b53;"><div style="font-size:13px;font-weight:800;color:#334e68;text-transform:uppercase;">Pitfall 1</div><div style="font-size:18px;font-weight:800;margin-top:4px;color:#102a43;">Degrade set 的 sid 分支需要人工复核</div><div style="font-size:13px;margin-top:8px;color:#52606d;">当前代码在 sid 参数存在时把 err_no 置为参数错误，sid 不存在时才调用 set_unit。若接口文档声称支持 sid 定向降级，这里可能存在协议与实现不一致。</div></div>
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f6fbf8;border-left:5px solid #0F766E;padding:14px 16px;margin:16px 0;color:#243b53;"><div style="font-size:13px;font-weight:800;color:#335c4a;text-transform:uppercase;">Pitfall 2</div><div style="font-size:18px;font-weight:800;margin-top:4px;color:#102a43;">Discard 命中发生在 FeedClosure 开头</div><div style="font-size:13px;margin-top:8px;color:#52606d;">命中后不会进入 ListTree 或 SetRedisTree。调试缺量时要先看 discard 模式、refresh_state、冷启和 guide_double_list，而不是只看下游 GR/Redis 调用。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title GRF 降级与丢弃排查清单
  desc 面向线上缺量、异常降级和请求未进入下游的最短路径
  items
    - label 查询 /degrade?action=get
      desc 记录 percent、条件集合、sid 和 degrade_off_gr_ad 当前值
      done true
    - label 查询 /discard?action=get
      desc 确认 discard=0..4 对应的三个丢弃开关组合
      done true
    - label 对照 flow_strategy.conf
      desc refresh_state、hour、minute、percent 与 sid 命中会优先触发配置降级
      done true
    - label 检查请求归一化
      desc channel_id、origin_channel_id、command、refresh_state 和 reqnums 可能在 fill_params 中被改写
      done true
    - label 检查 FeedClosure 早退
      desc do_discard 命中会直接 return_and_destroy，后续树不会执行
      done true
    - label 检查树选择
      desc rl_imitation、interleaving sid、enable_dr 分别影响 SampleServerTree、InterleavingTree、ListTree 和 SetRedisTree
      done true
 theme
  palette #3D5A80 #0F766E #F59E0B
```
