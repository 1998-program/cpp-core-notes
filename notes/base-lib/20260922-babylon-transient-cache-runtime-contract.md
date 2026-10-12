# 2026-09-22 基础库理解：Babylon `TransientCache` 的加载、过期与生命周期合同

> 日期：2026-09-22（周二）  
> 主题选择：未发现目标日期的有效 daily-plan。近期基础库笔记已经覆盖 `ObjectPool/GraphPool`、`PipelineGraphFunction`、`BaseGraphFunction`、`fork/framework`、`ApplicationContext`、`ReusableRPCProtocol` 与 `SerializedResponse/IOBuf`，且 `babylon::TransientCache` 直接出现在 `feeda-mv-grc` 的 `CacheQueuePlugin`、`cache-api`、`grc/expired_cache` 等多个业务路径，是负载最高的本地进程内缓存原语；因此回退到 **Babylon `TransientCache` 的异步加载/过期/析构合同**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：`TransientCacheOption` 的三档开关、`LoadCallback` 的 epoch 校验、`Node::set/fail/get/run` 状态迁移、`Slice` epoch 推进、`SafeDestruction` 进出计数、`SyncLoadFunction` 与 Executor 版本、`initialize/get/try_get/set` 的合同面，以及 `expired_cache` 包装层如何把 ttl/容量/jitter 注入 `init/run/stop`。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">用户接口</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">get / try_get / set</div><div style="margin-top:8px;font-size:12px;color:#52606d;\">SyncLoadFunction + Executor 注入；Future 驱动多次访问者共享一次 load。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="margin-top:0;font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">核心数据结构</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">Slice × Node × LoadResult</div><div style="margin-top:8px;font-size:12px;color:#52606d;\">每个 Node 持有一个 `LoadResult*`，load 完成时通过 epoch 校验决定是否写回。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="margin-top:0;font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">生命周期</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">GC 线程 + Slice epoch 推进</div><div style="margin-top:8px;font-size:12px;color:#52606d;\">后台线程按 `cache_duration_us * amplification + jitter` 推进 epoch；销毁期 SafeDestruction 计数等待 load 退出。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">get(key)</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">Slice epoch / Node</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef3c7;border:1px solid #fde68a;border-radius:6px;padding:10px;text-align:center;color:#92400e;font-size:13px;">SyncLoadFunction</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fce7f3;border:1px solid #fbcfe8;border-radius:6px;padding:10px;text-align:center;color:#9d174d;font-size:13px;">done(value / code)</div></div></div>

**一句话结论：** Babylon `TransientCache` 的安全前提是“同一 key 的多次访问者只触发一次 load”，每个访问者获得同一份 `std::shared_ptr<const V>`；它的语义不变量是 **Node 状态机 + Slice epoch 守卫 + SafeDestruction 进出计数**，任何直接 `_result` 写回、或在 GC 推进后仍调用 `done` 的代码都会破坏这条不变性。

## 1. 核心调用链

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title TransientCache get/try_get/set 闭环
participant "Caller" as C
participant "TransientCache" as TC
participant "Executor" as EX
participant "SyncLoadFunction" as L
participant "LoadCallback" as LC
participant "Node / Slice" as NS
C -> TC : get(key)
TC -> NS : find / new Node
alt first visitor (pioneer)
  TC -> EX : submit load_function(key, callback)
  EX -> L : invoke
  L -> LC : done(value | code)
  LC -> NS : epoch check + set/fail
else followers
  TC -> NS : monitor(callback)
end
TC --> C : Future<tuple<error, value>>
C -> TC : try_get(key, code, value)
TC -> NS : Node::get
NS --> TC : OK / EMPTY / RUNNING / BUSY
TC --> C : return code
@enduml
```

## 2. 异步加载与切片策略信息图

```infographic
infographic infographic sequence-ascending-steps
data
  title TransientCache 的五个不变量
  desc 不变量由接口、状态机、epoch 与 SafeDestruction 共同保证
  items
    - label 接口
      desc get/try_get/set 仅返回 OK / BUSY / EMPTY / RUNNING，错误信息只通过 LoadFunction 的 error_code 传
      icon mdi:application-cog-outline
    - label 状态机
      desc Node::_result 一旦被 set/fail 即被 SEALED_HEAD 标记，新访问者不会再重复 load
      icon mdi:state-machine
    - label epoch
      desc GC 推进 Slice 的 epoch，LoadCallback::done 用 epoch 过滤旧回调，避免 ABA 覆盖新值
      icon mdi:timer-sand
    - label 析构
      desc SAFE_DESTRUCTION 选项下 LoadCallback 在 Slice 上 enter/leave，析构期等待所有 load 退出再释放
      icon mdi:shield-lock-outline
    - label 复用
      desc 已过期 Node 走 Move 语义把 _result/_head 迁移到新 Node，旧对象析构时仍可触发未 ready 的 callback
      icon mdi:recycle-variant
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 `TransientCacheOption` 把生命周期策略集中在一处

`cache.h:31-50` 把三档开关编码成位掩码：`SAFE_DESTRUCTION=0x1` 让 LoadCallback 在 Slice 上 `enter/leave` 计数（用于销毁期等待 load 退出）；`SAFE_EXPIRATION=0x2` 把内存序从 `memory_order_acquire` 提升到 `memory_order_seq_cst`，使得过期淘汰更安全但会降低高频 get 吞吐；`FORCE_EXPIRATION=0x4` 在超过 ttl 后不再等待 visitor，强制释放旧值但有潜在 crash 风险。`DEFAULT=0x0` 是常驻 cache 的安全档。

`cache.h:142-159` 声明 `get(const K&)` 返回 `Future<tuple<int32_t, std::shared_ptr<const V>>`，返回 false（`valid()==false`）仅在 GC 延迟时出现；`try_get` 显式返回 OK / BUSY / EMPTY / RUNNING 四种代码。这种把 `BUSY/EMPTY/RUNNING` 拆开的设计让调用方在不进入 Future 链的情况下也能区分“未命中”、“仍在加载”和“GC 抢占失败”。

### 3.2 状态机：Node::_result 与 SEALED_HEAD

`cache.hpp:75-98` 把 `Node` 实现为 `std::atomic<LoadResult*> _result` 与 `std::atomic<ValueCallback*> _head`；Node 拷贝构造直接 `CFATAL_LOG + abort()`，Move 构造用 `exchange(nullptr, memory_order_relaxed)` 把两个字段从旧 Node 迁出。析构时若 `_result != nullptr` 则 `delete result`，并 `run(ERROR, DEFAULT_VALUE)` 触发已挂载的 callback——这是“过期节点复用到新 Slice” 的关键路径。

`cache.hpp:101-119` 的 `Node::set/fail` 把 `LoadResult*` 通过 `compare_exchange_strong(_result, nullptr)` 写入：竞争失败意味着别人先写入 `set`，当前 LoadResult 直接销毁。`cache.hpp:122-132` 的 `Node::get` 仅返回 OK（带 value）或 RUNNING（仍在加载）——这是 try_get 语义的基础。`cache.hpp:135-160` 的 `monitor` 用 SEALED_HEAD 哨兵 + 单链表挂载 callback，确保 set/fail 完成时一次性激活所有 follow-up。

### 3.3 epoch 守卫与 ABA

`cache.hpp:26-68` 实现了 `LoadCallback::done` 的两个重载：先用 `SliceVisitor` 把当前 Slice 标记为正在访问，再用 `_slice.epoch(memory_order)` 读取当前 epoch；若 `_epoch != slice_epoch`，说明 GC 已经推进 slice，LoadCallback 输出 `CWARNING_LOG` 退出，避免把过期值写回新节点。否则把 value 写入 `_node.set(...)` 或 `_node.fail(error_code)`。

`SafeDestruction` 选项同时进入 `_slice.enter()` 与 `_slice.leave()`，配合 `cache.h:121-128` 的 `initialize(Executor&...)` 重载，使得“销毁时还有未完成的 LoadCallback” 这种情况可以等 LoadFunction 退出；用户也可以选择不带 SafeDestruction 的常驻 cache，由服务优雅退出保证时序。

### 3.4 `SyncLoadFunction` 与 Executor 注入

`cache.h:97` 把 `SyncLoadFunction` 显式定义为 `std::function<int32_t(const K&, std::shared_ptr<const V>&)>`，与过时的 `LoadFunction`（带 `LoadCallback`）形成对照。`cache.h:113-128` 的 `initialize` 提供四个重载：无 Executor 版本（load 在首个 get 线程内运行）、带 Executor 版本（load 在 Executor 线程中运行）、左右值引用版本。注释明确说明：“加入 executor 机制后，推荐使用 SyncLoadFunction 版本，由 Executor 来定制异步机制”。

这意味着新代码不应该再调用旧的 `get(key, callback)`，否则它会触发 `__attribute__((deprecated))` 警告（`cache.h:215-217`）。`feed-mlarch/babylon/test/test_cache.cpp:19-25` 的测试用 `TransientCacheOption::SAFE_DESTRUCTION` 与 `FORCE_EXPIRATION` 验证不同档位的语义，确认 `cache.hpp:43-46` 的 leave 计数在两档下都会触发。

### 3.5 包装层 `expired_cache` 把 ttl/容量/jitter 注入

`feed-gr/feeda-mv-grc/src/plugin/expired_cache_mgr.h:46-85` 的 `init` 把 `comcfg::Configure` 的 `cache_name / ttl_us / cache_size / jitter` 转成 `ExpiredCache::init(ttl_us, jitter, cache_name, cache_size)`，并立即 `cache.run()` 启动后台 GC 线程；`cache_set` 集合防止同名 cache 重复初始化。`expired_cache_mgr.cpp:12-31` 的 `init()` 把 `path/conf_file` 默认值指向 `./conf/expired_cache.conf`，加载失败直接 `LOG(FATAL)`，确保缓存没有降级路径。

`feed-gr/feeda-mv-grc/conf/expired_cache.conf:1-5` 把 `no_decay_cube_cache` 的 ttl 配成 300s、容量 500k、jitter 0.5。`initializer/global.h:229-233` 把 `ExpiredCacheMgr::get_instance().init()` 列为强一致初始化阶段；失败时调用 `exit(1)` 而不是返回错误——这条不变量意味着任何对 `_no_decay_cube_cache` 的访问者都不需要判空，只需关注 cache 自身的“miss/hit” 语义。

### 3.6 `cache-api` 把同一份原语包装为同步 client

`feed-mlarch/cache-api/src/cache_util.cc:24-46` 的初始化读取 `cache_api` 段（`cache_expire_time_ms / write_timeout_ms / bns / write_bns / lb / backup_request_ms`），与 `cache_api.h:35-49` 的 `get/put` 接口配合；`cache_api.cc:27-44` 通过 `CacheService_Stub` 调用 cache service，把请求体序列化后投递到对应 channel。这条路径对 TransientCache 是透明 client，所以“缓存服务端是否使用同一个 cache 实现” 是排查关键——若服务端使用另一份实现，本地缓存命中与服务端回源会出现对账偏差。

## 4. 故障对账信息图

```infographic
infographic infographic compare-binary-horizontal-underline-text-vs
data
  title 看起来都是“没拿到值”，定位路径不同
  desc 先用 try_get 把 BUSY/EMPTY/RUNNING 拆开，再继续往下追
  items
    - label EMPTY
      desc cache 中没有该 key 的记录；调用方应直接发起 load，或把请求旁路到下一层
      icon mdi:tray-remove
    - label RUNNING
      desc 有别的访问者正在触发 load；要么用 get 拿 Future 等结果，要么退避重试
      icon mdi:progress-clock
    - label BUSY
      desc GC 抢占 Slice，新访问者无法进入处理路径；上层需 sleep + retry，不要直接放弃
      icon mdi:alert-outline
    - label load 失败
      desc Node::fail 把 error_code 写回 _result，try_get 返回 OK 但 code 非 0；上层必须自己解析 error_code
      icon mdi:close-circle-outline
    - label epoch 失效
      desc LoadCallback::done 检查 epoch 不匹配，CWARNING_LOG 后丢弃结果；说明这次 load 太慢、GC 已推进
      icon mdi:timer-off-outline
theme
  palette #3D5A80 #0F766E #DC2626
```

## 5. Pitfalls

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0;color:#263238;"><div style="border-left:4px solid #d97706;background:#fff7ed;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#9a3412;text-transform:uppercase;">不要绕过 LoadCallback</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">直接修改 Node::_result 会绕开 epoch 守卫与 SafeDestruction 计数，是 TransientCache 的“非法写”。</div></div><div style="border-left:4px solid #2563eb;background:#eff6ff;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#1d4ed8;text-transform:uppercase;">不要混用新旧 API</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">过时的 `get(key, callback)` 与新 `get(key) → Future` 行为不同；混用会破坏 follower 共享 load 的不变量。</div></div><div style="border-left:4px solid #dc2626;background:#fef2f2;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#991b1b;text-transform:uppercase;">不要忽略 BUSY</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">BUSY 不是错误，是 GC 抢占的退避信号。上层应退避后重试，而不是把它当成 empty 跳过。</div></div><div style="border-left:4px solid #059669;background:#ecfdf5;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#047857;text-transform:uppercase;">正确选择 SAFE_DESTRUCTION</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">常驻 cache 走 DEFAULT 即可；只在销毁期仍可能触发 LoadFunction 时打开 SAFE_DESTRUCTION，否则 enter/leave 计数会成为热点。</div></div></div>

## 6. 调试 checklist

```infographic
infographic infographic list-column-done-list
data
  title TransientCache 排查清单
  items
    - label Option 档位
      desc 确认使用了 SAFE_DESTRUCTION / SAFE_EXPIRATION / FORCE_EXPIRATION 中的哪一档，初始化日志中应有 cache_name 与 ttl
      done false
    - label 接口版本
      desc 优先使用 get(key) 返回 Future 的版本，避免 deprecated 的 callback 重载
      done false
    - label 返回码
      desc try_get 必须区分 OK / BUSY / EMPTY / RUNNING，并把 BUSY 当成“退避后重试”而不是“失败”
      done false
    - label load 失败
      desc Node::fail 写入 error_code 后，访问者需解析 error_code 字段，不能只看 try_get 是否返回 OK
      done false
    - label epoch 警告
      desc CWARNING_LOG 输出 `ignore legacy callback at epoch X < Y` 时，确认 load 时延是否超过 ttl × amplification
      done false
    - label 销毁
      desc SAFE_DESTRUCTION 模式下确认服务优雅退出；非 SAFE_DESTRUCTION 模式禁止在还有访问者时析构 cache
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `cache.h:31-50`：`TransientCacheOption` 三档位 + DEFAULT。
- `cache.h:97-160`：SyncLoadFunction、LoadCallback、get/try_get/set 的返回码合同。
- `cache.h:215-217`：过时的 `get(key, callback)` 用 `__attribute__((deprecated))` 标记。
- `cache.hpp:18-68`：LoadCallback 构造与两个 done 重载，epoch 守卫与 SafeDestruction 计数。
- `cache.hpp:75-98`：Node 构造/析构，Move 把 `_result/_head` 从旧节点迁出。
- `cache.hpp:101-132`：Node::set/fail/get 的状态机与 LoadResult 生命周期。
- `cache.hpp:135-176`：Node::monitor + set_result，SEALED_HEAD 哨兵 + CAS 链。
- `feed-mlarch/babylon/test/test_cache.cpp:19-25`：DEFAULT/SAFE_DESTRUCTION/FORCE_EXPIRATION 三档位的使用示例。
- `feed-gr/feeda-mv-grc/src/plugin/expired_cache_mgr.h:46-85`：把 ttl/容量/jitter 注入 `ExpiredCache::init`，并 `_cache_set` 去重。
- `feed-gr/feeda-mv-grc/src/plugin/expired_cache_mgr.cpp:12-31`：默认 `./conf/expired_cache.conf`，加载失败 LOG(FATAL)。
- `feed-gr/feeda-mv-grc/conf/expired_cache.conf:1-5`：`table_no_decay_cube_cache` ttl=300s / cache_size=500k / jitter=0.5。
- `feed-gr/feeda-mv-grc/src/initializer/global.h:229-233`：`ExpiredCacheMgr::get_instance().init()` 失败时 `exit(1)`。
- `feed-mlarch/cache-api/src/cache_util.cc:24-46` + `cache_api.cc:27-44`：同一份原语在 cache-api 路径下的同步 client 封装。
