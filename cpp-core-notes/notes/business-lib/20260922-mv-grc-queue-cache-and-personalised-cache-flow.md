# 2026-09-22 业务库理解：MV-GRC 队列缓存读/写回与个性化缓存异步落库边界

> 日期：2026-09-22（周二）  
> 主题选择：未发现目标日期的有效 daily-plan。最近业务笔记已覆盖 `video_launch` 响应组装、`NewResponseFunction` ExtMsg、Deepes 推理池、gr-rank ContextDNN、MultiStream 等，MV-GRC 的 “队列缓存” 与 “个性化缓存异步落库” 这两条业务侧缓存路径尚未单独成文；因此回退到 **`CacheQueuePlugin` 的 Redis 读/写 + `QueueCacheProcessor` 重组召回结果 + `DibarReddotCacheData` 回写 + `WritePersonalisedCacheProcessor` 异步压缩落 Dalton**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：`CacheQueuePlugin::get/set/gen_query_cache_key/parse_cache_data`、`QueueCacheProcessor` 的 mark/重命名规则和召回替换、`DibarReddotCacheData` 的兜底写回路径、`WritePersonalisedCacheProcessor` 的 Gzip + base64 + DaltonClient 写入以及与 `personalize_cache_limit / personalize_cache_max / ttl` 的协作。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7faf5;border:1px solid #d9e2d0;border-radius:8px;padding:16px;margin:16px 0;color:#243126;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">召回前缓存读</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">QueueCacheProcessor</div><div style="margin-top:8px;font-size:12px;color:#52615a;\">从 RecallStrategyConf 拿物理队列 mark，标记重命名后用 CacheQueuePlugin 读 Redis。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="margin-top:0;font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">队列协议</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">CacheQueuePlugin</div><div style="margin-top:8px;font-size:12px;color:#52615a;\">`query_<fork>_<cuid>` 键 + RedisReqRes，cache_item.id/mark 写回 mark_and_nids。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="margin-top:0;font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">个性化缓存</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">WritePersonalisedCache</div><div style="margin-top:8px;font-size:12px;color:#52615a;\">截断超出 reqnum 的 RidTmpInfo，Gzip + base64 后通过 DaltonClientPlugin 异步写 Dalton。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">recall queue conf</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:6px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">Redis GET/SET EX 36000</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef3c7;border:1px solid #fde68a;border-radius:6px;padding:10px;text-align:center;color:#92400e;font-size:13px;">RidTmpInfo 注入</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fce7f3;border:1px solid #fbcfe8;border-radius:6px;padding:10px;text-align:center;color:#9d174d;font-size:13px;">个人化缓存 Dalton</div></div></div>

**一句话结论：** MV-GRC 的缓存不是单一组件，而是“召回前的 Redis 队列缓存 → 召回后的兜底回写 → 超量召回的个性化缓存异步压缩落库”三段链路；任何一段的 key/序列化/容量不变量被破坏，下游会同时表现为 “召回量不足但日志显示命中” 或 “个人化缓存写不进去但响应正常”。

## 1. 核心时序

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title MV-GRC 队列缓存与个人化缓存链路
participant "QueueCacheProcessor" as QC
participant "CacheQueuePlugin" as CQ
participant "Redis" as RDS
participant "RecallStrategyConf" as RC
participant "DibarReddotCacheData" as DR
participant "WritePersonalisedCache" as WP
participant "DaltonClientPlugin" as DAL
QC -> RC : recall_conf.physical_queues[fork_type].mark_vec
QC -> QC : 追加 1801/1802/1803 重命名桶
QC -> CQ : get_cache_data(channel, fork, cuid, mark_and_nids)
CQ -> RDS : GET query_<fork>_<cuid>
RDS --> CQ : reply string (QueueCache pb)
CQ -> QC : ParsePartialFromString -> mark_and_nids[id]=nid
QC -> QC : cache_result->result[mark].emplace_back(RidTmpInfo)
QC -> QC : cache_state = is_cache_valid
RC --> DR : QueryQueueCacheData (兜底回写)
DR -> CQ : set_cache_data(channel, fork, cuid, cache_data)
CQ -> RDS : SET query_<fork>_<cuid> ... EX 36000
WP -> WP : 截断 res_dep_result_p[recnums..] 限 600 条
WP -> WP : 至少 100 条才压缩 GzipOutputStream + base64
WP -> DAL : put(microvideo_dr_data, msv_dr_<ua>_<cuid>, b64, ttl=86400s)
@enduml
```

## 2. 队列缓存读/写信息图

```infographic
infographic infographic sequence-snake-steps-simple
data
  title QueueCacheProcessor 与 CacheQueuePlugin 的六个合同面
  desc 读路径把物理队列 mark 重命名后注入 RidTmpInfo，写路径使用相同 key 与 EX 36000 TTL
  items
    - label 物理队列
      desc RecallStrategyConf 提供 physical_queues[fork_type].mark_vec，作为初始 mark 列表
      icon mdi:format-list-numbered
    - label 重命名
      desc 额外追加 1801/1802/1803 三个空桶，保证 mark_and_nids 在缓存 miss 时也能维持稳定结构
      icon mdi:rename-box
    - label key
      desc query_<fork_type>_<cuid> 是 cache key；fork_type 与 cuid 同时影响读和写
      icon mdi:key-variant
    - label parse
      desc QueueCache pb 通过 ParsePartialFromString 解析，cache_item.id 进 nid_vec、mark 决定桶归属
      icon mdi:code-json
    - label 回写
      desc DibarReddotCacheData 在 QueryCacheData 非空时调用 set_cache_data，TTL 通过 Redis SET EX 36000 强制
      icon mdi:content-save-outline
    - label 状态位
      desc cache_state 标记是否命中；下游依据 cache_state 决定是否走“纯缓存” 路径
      icon mdi:check-decagram-outline
theme
  palette #2D6A4F #3D5A80 #F59E0B
```

## 3. 关键机制拆解

### 3.1 key 与 mark 重命名规则

`cache_queue.cpp:53-55` 的 `gen_query_cache_key(fork_type, cuid)` 直接拼成 `query_<fork>_<cuid>`，没有哈希或 namespace 前缀；任何对 `fork_type` 的修改都会让历史 Redis 缓存全部失效，需要配合 `cuid` 维度主动清理。`cache_queue.cpp:42-51` 的 `gen_get_data_request` 把 `GET %s` 命令塞进 `RedisReqRes[0]`，由 `RedisPlugin::call` 投递；这是 cache_queue 与 redis 协议的最小接入点。

`queue_cache.cpp:78-89` 在物理队列 mark 之外又追加了 `cache_rename_mark = {1801, 1802, 1803}`，并通过 `mark_and_nids.find(rename_mark) == mark_and_nids.end()` 判断是否需要新建空桶。这条逻辑保证即物物理队列完全空，cache_result 输出结构仍然稳定；下游读取 `cache_result->result[1801]` 等桶只会得到空 vector，不会引起崩溃或 nullptr 解引用。

### 3.2 缓存 reply 的多重防护与 ParsePartialFromString

`cache_queue.cpp:62-87` 在解析前先做 reply_size/is_error/is_nil/is_string 四道防线，任意一种不满足都 `continue`，最终 `ret = true` 仅在至少一项成功解析后才置位。`cache_reply = reply.data().data()` 取得二进制值，再用 `QueueCache pb` 的 `ParsePartialFromString` 解析，失败也只是 warning 不抛错。

`ParsePartialFromString` 而不是 `ParseFromString` 是有意为之：缓存写端在升级字段后，旧的 cache 字符串可以继续被部分解析，缺失字段走默认值，避免“升级后第一次部署窗口内命中失效” 引起回源雪崩。

### 3.3 命中后注入 RidTmpInfo 的字段语义

`queue_cache.cpp:102-121` 把 `mark_and_nids[mark]` 中的每个 nid 包装成 `RidTmpInfo`：设置 `rid / mark / type / type_vec.emplace_back(mark) / rec_type = mark`。注意 `mark / type / rec_type` 都用同一个 `mark` 填充；后续算子如果以 `type` 或 `rec_type` 区分召回源，缓存命中会与正常召回语义保持一致，因为两者的 mark 都来自 `physical_queues` 配置。

`queue_cache.cpp:122-127` 把 `cache_state` 设为 `is_cache_valid`（只要有一个 mark 桶非空就算命中），并把 SIA 的 `query_from_cache` 加 1。该值会被 `IsQueueDataFromCache` 上层 vertex 读取，影响下游是否触发回源兜底。

### 3.4 兜底写回路径

`queue_cache.cpp:96-101` 在 `get_cache_data` 失败时仍返回 0（不抛错），靠 `cache_state=false` 让上层自行决定是否发起正常召回。这避免了一次 Redis 抖动就引发全量回源。

`dibar_reddot_cache_data.cpp:29-72` 是反向链路：仅当 `query_cache_data` 非空、cuid 非空、channel 可用时才调用 `set_cache_data`。`cache_data_tmp.id/mark/query` 三字段都来自 `QueryQueueCacheData`，`set_cache_data` 通过 `gen_set_data_request` 把 `QueueCache pb` 序列化后用 `SET %s %s EX 36000` 写入（`cache_queue.cpp:132`），TTL 强制为 36000 秒。Redis 写入失败在业务侧不抛错，但日志会留 `redis plugin set queue cache failed` 警告。

### 3.5 个性化缓存的容量与压缩门槛

`write_personalised_cache.cpp:12-13` 定义 `personalize_cache_limit=100`（压缩门槛）与 `personalize_cache_max=600`（最大写入条目数）。`conf/gflags.conf:34` 把 limit 覆盖为 300，覆盖方式仍是命令行 flag。`write_personalised_cache.cpp:57-78` 仅在 `is_need_person_rz_data && res_dep_result_p->size() > recnums` 时进入“超量召回” 路径，然后逐个写入 `vid / mark(type_vec[0]) / type=VIDEO_TYPE`。

随后 `write_personalised_cache.cpp:80-110` 走 “压缩 → base64 → 写 Dalton” 三步：只有当 `cache_msg.video_personalize_cache_items_size() >= personalize_cache_limit` 时才压缩，否则直接走 “size lt limit” 警告并返回 -3。这种“低于门槛不写” 的策略避免了少量超量召回占用缓存，又在调用方显式记录 ret 状态，便于 SLA 对账。

### 3.6 压缩、key 与 TTL

`write_personalised_cache.cpp:84-93` 使用 `GzipOutputStream + SerializeToZeroCopyStream` 写入到 `personalize_cache_binary_str`，再用 `babylon::base64_encode` 编码到 `personalize_cache_b64_str`。压缩失败 ret=-1、b64 大小为 0 则 ret=-2，两者均不调用 DaltonClient。

`write_personalised_cache.cpp:96-101` 用 key 模板 `"%s_%d_%s"` 生成 `<dr_key_prefix>_<ua>_<cuid>` 形式的 key（`_dr_key_preix = "msv_dr"`, `_tablename = "microvideo_dr_data"`），expire_time = `gettimeofday_ms() + _ttl=86400000`（24h）。`SIA_START(put_dr_data_rpc) ... SIA_END(put_dr_data_rpc, ret)` 把这次 RPC 单独打点，便于排查个性化缓存的写入尾延迟。

### 3.7 与上游顶点的耦合

`grc_service.cpp:308-309` 在 run graph 时把 `IsWritePersonalisedCacheSucc` 加入 end 依赖，确保只有 `WritePersonalisedCacheProcessor` 返回成功（或显式失败但被吞掉）才继续往下走。`write_personalised_cache.cpp:47-49` 把 `IsWritePersonalisedCacheSucc` 默认设为 true，只有真正的 ret<0 路径才会把它置 false——但当前实现并未在 ret<0 时显式覆盖该值，说明 graph 把 true 当作“已尝试” 的弱不变量。后续若需要把“写入失败” 反馈到 graph 主链，需要在该 vertex 末尾增加 `*is_success = (ret == 0)`。

## 4. 三段链路对账信息图

```infographic
infographic infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title “缓存写不进去” 但响应正常时的三层定位
  desc 由外到内确认配置、序列化、远程写入
  items
    - label 队列缓存
      desc confirm fork_type 与 cuid、Redis 是否可达、`queue_cache` pb 是否带新字段
      value 1
      icon mdi:database-search-outline
    - label 兜底回写
      desc confirm handle_name 与 ChannelGroup 是否正确、QueryCacheData 是否被 fill_meta 写入
      value 2
      icon mdi:database-cog-outline
    - label 个性化
      desc confirm recnums 与 res_dep_result_p 大小、limit 是否覆盖、is_need_person_rz_data 是否开启
      value 3
      icon mdi:archive-lock-outline
theme
  palette #2D6A4F #3D5A80 #DC2626
```

## 5. Pitfalls

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0;color:#263238;"><div style="border-left:4px solid #d97706;background:#fff7ed;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#9a3412;text-transform:uppercase;">不要让 fork_type 自由变动</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">key 直接拼 `query_<fork>_<cuid>`，fork_type 改名会让历史 Redis 缓存全部失效，且无主动清理路径。</div></div><div style="border-left:4px solid #2563eb;background:#eff6ff;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#1d4ed8;text-transform:uppercase;">不要混淆 hit 与有效</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">`cache_state = is_cache_valid` 需要至少一个 mark 桶非空才算命中；空桶不等于命中，需要回源。</div></div><div style="border-left:4px solid #dc2626;background:#fef2f2;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#991b1b;text-transform:uppercase;">不要忽略压缩门槛</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">`personalize_cache_limit` 之下走 “size lt limit” 警告，不写 Dalton；不要把这种正常跳过当成错误排查。</div></div><div style="border-left:4px solid #059669;background:#ecfdf5;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#047857;text-transform:uppercase;">正确处理 b64 空串</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">压缩成功但 b64 空串是 ret=-2；b64 写入失败时 ret 直接反映到 SIA，不要把它当成 OK 吞掉。</div></div></div>

## 6. 调试 checklist

```infographic
infographic infographic list-column-done-list
data
  title MV-GRC 缓存链路排查清单
  items
    - label 配置
      desc 核对 vertex 的 handle_name / fork_type 与 ChannelGroup 是否一致，gflags.conf 是否覆盖 limit
      done false
    - label 命中
      desc 同时记录 mark_and_nids 各桶大小、cache_state 与 SIA query_from_cache 计数
      done false
    - label 解析
      desc 保留 reply_size / is_error / is_nil / is_string 四道防线的日志，以及 ParsePartialFromString 失败次数
      done false
    - label 回写
      desc 核对 gen_set_data_request 的 TTL（EX 36000）和 SET 命令中 cache_data_str 是否非空
      done false
    - label 个性化
      desc 记录 res_dep_result_p->size() 与 recnums 的差值、items_size 是否达到 limit、b64 size 与 ret 状态
      done false
    - label 写入
      desc 检查 DaltonClient put 返回值与 put_dr_data_rpc 的 SIA，确认 ttl=86400 与 key 模板正确
      done false
theme
  palette #2D6A4F #0F766E #F59E0B
```

## 7. 证据来源

- `feeda-mv-grc/src/plugin/cache_queue.cpp:15-55`：`get_cache_data`、`gen_get_data_request`、`gen_query_cache_key` 三个最小合同面。
- `feeda-mv-grc/src/plugin/cache_queue.cpp:57-104`：reply_size / is_error / is_nil / is_string 四道防线 + `QueueCache pb` 的 `ParsePartialFromString`。
- `feeda-mv-grc/src/plugin/cache_queue.cpp:106-157`：`gen_set_data_request` / `set_cache_data` 与 `SET ... EX 36000`。
- `feeda-mv-grc/src/plugin/cache_queue.h:16-50`：`CacheQueuePlugin` 接口与 `_redis_plugin` autowire。
- `feeda-mv-grc/src/processor/reddot/queue_cache.cpp:11-39`：setup 解析 handle_name / fork_type，并从 ChannelGroup 取 channel。
- `feeda-mv-grc/src/processor/reddot/queue_cache.cpp:42-131`：process 主体，包括 1801/1802/1803 重命名与 RidTmpInfo 注入。
- `feeda-mv-grc/src/processor/reddot/dibar_reddot_cache_data.cpp:29-72`：兜底回写路径，cache_ret 与 QueryCacheData 三件套。
- `feeda-mv-grc/src/processor/write_personalised_cache.cpp:12-13`：`personalize_cache_limit / personalize_cache_max` 的默认值。
- `feeda-mv-grc/src/processor/write_personalised_cache.cpp:42-114`：process 主流程、压缩 + base64 + DaltonClient put。
- `feeda-mv-grc/src/processor/write_personalised_cache.cpp:116-121`：`_tablename / _ttl / _dr_key_preix` 默认值。
- `feeda-mv-grc/conf/gflags.conf:34`：`microvideo_personalize_cache_limit=300` 的命令行覆盖。
- `feeda-mv-grc/src/service/grc_service.cpp:308-309`：`IsWritePersonalisedCacheSucc` 与 graph run 的 end 耦合。
