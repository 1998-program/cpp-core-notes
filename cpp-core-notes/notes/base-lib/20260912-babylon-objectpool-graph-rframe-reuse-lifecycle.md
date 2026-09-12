# 2026-09-12 基础库理解：Babylon ObjectPool 与 Graph/rframe 复用生命周期

> 日期：2026-09-12  
> 主题选择：未发现当日有效 daily-plan；历史计划中的 Saturday 主题 DynamicTimeOutPlugin 已有覆盖，邻近未覆盖主题回退为 **Babylon ObjectPool 的线程缓存、PooledObject 所有权与 Graph/rframe 对象复用生命周期**。KU 正文未读取，业务背景需人工补充；以下结论只依据代码证据。  
> 范围：`baidu/feed-mlarch/babylon/pool.h`、`pool.hpp`、`baidu/feed-general/framework/include/graph_engine.h`、`graph_engine.cpp`、`baidu/elitefeed/rframe/src/io/io.h`、`nshead_io.h`。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">池化抽象</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">ObjectPool&lt;T&gt;</div><div style="margin-top:8px;font-size:12px;color:#52606d;">预建对象、线程本地缓存、全局无锁栈和可选重建策略共同控制获取与回收成本。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">所有权边界</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">PooledObject → release()</div><div style="margin-top:8px;font-size:12px;color:#52606d;">移动语义转移持有权；析构自动回池。池耗尽时 get() 可现场创建，try_get() 则返回空句柄。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">上层消费者</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">Graph / NsheadIO / IO</div><div style="margin-top:8px;font-size:12px;color:#52606d;">GraphPool 复用整张执行图；NsheadIO 复用 request/response 消息，并在 reset 中清空请求态。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 48px 1fr 48px 1fr 48px 1fr;gap:8px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">initialize / config</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;font-size:13px;">local cache</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;font-size:13px;">global head</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">clear / reconstruct / return</div></div></div>

**一句话结论：** Graph/rframe 的低分配成本不是“永不清理”，而是把对象生命周期交给 `PooledObject`，把请求态清理交给消费者的 `reset()`/clear 约定。

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title ObjectPool 获取、使用与回收时序
participant "GraphEngine / rframe" as CLIENT
participant "ObjectPool<T>" as POOL
participant "ThreadCache" as TC
participant "Global head" as GH
participant "PooledObject" as PO
participant "T::reset / clear" as CLR

CLIENT -> POOL : get() / try_get()
POOL -> TC : try_pop_local()
alt local cache hit
  TC --> PO : node -> object
else local miss
  POOL -> GH : try_pop_from(cacheable/global head)
  alt pooled node hit
    GH --> PO : node -> object
  else pool exhausted
    alt get()
      POOL -> POOL : _create_function()
      POOL --> PO : non-pooled object
    else try_get()
      POOL --> CLIENT : empty PooledObject
    end
  end
end
CLIENT -> PO : use graph / IO / message
CLIENT -> PO : reset consumer state before reuse
PO -> PO : destructor or explicit release()
PO -> CLR : optional clear/reconstruct policy
CLR --> POOL : push(node)
POOL -> TC : push_local(node)
POOL -> GH : overflow or non-local return
@enduml
```

## 2. 池配置与行为信息图

```infographic
infographic list-grid-badge-card
data
  title ObjectPool 的四类配置旋钮
  desc 配置同时影响内存预热、线程局部命中、池耗尽行为和回收后的状态隔离
  items
    - label reserve / pool_size
      desc 初始化时预建对象数量；GraphUnit 默认 pool_size 为 10，配置缺失时 reserve_global 回退为 pool_size
      icon mdi:counter
    - label cache_per_thread / cached_size
      desc 每线程缓存上限，优先从本地缓存取对象，减少全局原子栈竞争
      icon mdi:account-multiple
    - label reserve_global
      desc 保留给全局池的对象数量，不进入线程缓存，用于跨线程或缓存耗尽时补给
      icon mdi:server-network
    - label get vs try_get
      desc get() 池耗尽会现场创建；try_get() 池耗尽返回空句柄，调用方必须检查 get() 是否为 nullptr
      icon mdi:call-split
    - label reconstruct_before_return_pool
      desc 回池前销毁并重建实体，适合残留状态昂贵或难以完整清理的对象
      icon mdi:refresh
    - label life_cycle
      desc 设置池化对象最大使用周期，超过周期可被强制销毁，避免长期复用同一实例
      icon mdi:timer-outline
```

## 3. 关键机制拆解

### 3.1 `PooledObject` 是真实的所有权句柄

`pool.h:40-78` 将 `PooledObject` 设计成不可拷贝、可移动的类 unique_ptr：内部同时记录 `_object`、所属 `_pool` 和 `_node`。移动构造会清空源句柄，析构统一调用 `release()`。这使 GraphEngine 可以按值返回池化图对象，而调用方不需要显式记住“归还图”的动作。

`pool.hpp:78-95` 的 `release()` 有两条路径：池化对象通过 `pool->push(node)` 回池；池耗尽时由 `get()` 创建的非池化对象没有 `_pool`，最终直接 `delete`。因此不能把 `get()` 返回的对象一律当作来自固定池容量的实体。

### 3.2 获取路径优先线程缓存，再走全局结构

`pool.hpp:383-399` 说明 `get()` 和 `try_get()` 都先调用 `try_pop()`，区别只在池耗尽时的策略。`pool.hpp:414-432` 进一步展示获取顺序：线程缓存 → cacheable 全局 head → 普通全局 head。`pool.hpp:436-457` 用线程本地 `ThreadCacheAccessor` 和带版本的原子 head 实现低竞争弹出。

这解释了两个排障现象：线程本地命中率下降时，全局池访问会增加；全局也耗尽时，`get()` 的 `new` 计数增加而 `try_get()` 的空句柄计数由调用方体现。

### 3.3 GraphUnit 把“建图”和“取图”分开

`graph_engine.h:52-88` 中，`GraphUnit` 持有 `GraphBuilder` 和 `unique_ptr<GraphPool>`，同时提供 `get()`、`try_get()` 和非池化 `create()`。`graph_engine.cpp:51-65` 直接把这三种语义映射到池获取、池尝试获取和 builder 建图。

`graph_engine.cpp:198-301` 的 `init_pool()` 先读取 `pool_size`、`cached_size`、`reserve_global`、`life_cycle` 和 `reconstruct_before_return_pool`，再创建 `GraphPool`、设置重建/生命周期策略，最后以 `_builder.build()` 作为工厂初始化池。也就是说配置驱动的是运行时实例策略，不是图拓扑本身。

### 3.4 GraphEngine 的 product/channel 映射决定取哪个池

`graph_engine.cpp:343-374` 的 `try_get(product_id, channel_id, ...)` 先查 product，再按 channel 选择 graph_name，最终进入按名称获取。`create_if_pool_empty=true` 时调用 `get()`，允许现场构造；设为 false 时调用 `try_get()`，池耗尽会返回空句柄并打印 warning。

因此容量问题要先确认 graph_name/product/channel 路由，再看池计数；把所有 graph 的获取失败都归因于同一个全局池是不准确的。

### 3.5 rframe IO 将消息清理放在消费者层

`rframe/io/nshead_io.h:27-42` 为 request/response 各持有一个 `ObjectPool<NsheadMessage>::PooledObject`，构造时立即 `Clear()`。`nshead_io.h:63-67` 的 `reset()` 先调用 `IO::reset()`，再清空两条消息；`io.h:36-95` 则负责 controller、时间戳、channel 和 callback 状态。

这体现了清晰分工：ObjectPool 负责实例所有权与回收，NsheadIO/IO 负责协议对象和 RPC controller 的请求级状态。只调 `PooledObject::release()` 而不调 IO 的 reset，会把上一请求状态带入下一次使用。

## 4. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#f5f7fa;border:1px solid #d8e0ea;border-left:8px solid #3d5a80;border-radius:16px;padding:20px;margin:18px 0;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800;">Pitfalls · Pool Lifecycle</div><div style="font-size:26px;line-height:1.15;font-weight:900;margin:8px 0 12px;">“回池”不等于“状态已清空”</div><div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;"><div style="background:#fff;border-top:4px solid #3d5a80;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;"><b>三类风险：</b><br>① `try_get()` 返回空句柄，调用方若不检查会在后续解引用处崩溃；② `get()` 池耗尽会现场创建，导致 bvar 中 new 计数和延迟抖动上升；③ Graph、NsheadMessage、Controller 的业务状态需要消费者显式 reset/clear，不能只依赖句柄析构。</div><div style="background:#fff7ed;border-top:4px solid #f59e0b;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;color:#7c2d12;"><b>评审顺序</b><br>获取 API → 空句柄处理 → request reset → graph reset → 回池策略 → local/global/new 计数。</div></div><div style="margin-top:12px;font-weight:900;color:#3d5a80;">∎ 先证明生命周期闭合，再讨论把池子调大。</div></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title ObjectPool / GraphPool 排查清单
  desc 从配置、获取路径、请求态清理到监控计数逐层确认
  items
    - label 核对 graph_name 与路由
      desc 确认 product_id、channel_id、graph_name 是否选中了预期 GraphUnit
      done true
    - label 区分 get 与 try_get
      desc 明确池耗尽时是允许现场创建，还是必须快速失败并走降级
      done true
    - label 检查空句柄
      desc 对 try_get() 返回值执行 get() 判空，避免把容量不足变成随机崩溃
      done true
    - label 对齐 pool 参数
      desc 记录 pool_size、cached_size、reserve_global、life_cycle 与 reconstruct 开关
      done true
    - label 检查消费者 reset
      desc Graph、IO、NsheadMessage、Controller 的请求态都要在复用前清理
      done true
    - label 观察 bvar 计数
      desc 对比 cached、pooled、new 三类计数，定位线程缓存失配还是池容量不足
      done false
    - label 检查外部引用
      desc PooledObject 释放后不得继续持有 Graph、message 或内部 node 的裸指针
      done false
```

## 6. 证据来源

- `baidu/feed-mlarch/babylon/src/baidu/feed/mlarch/babylon/pool.h:34-78`：ObjectPool 与 PooledObject 所有权接口。
- `baidu/feed-mlarch/babylon/src/baidu/feed/mlarch/babylon/pool.h:92-178`：初始化、配置、get/try_get 与清理/重建配置。
- `baidu/feed-mlarch/babylon/src/baidu/feed/mlarch/babylon/pool.h:192-279`：Node、ThreadCache、原子 head、bvar 与生命周期字段。
- `baidu/feed-mlarch/babylon/src/baidu/feed/mlarch/babylon/pool.hpp:78-113`：release、移动句柄与 de_manage。
- `baidu/feed-mlarch/babylon/src/baidu/feed/mlarch/babylon/pool.hpp:383-457`：获取路径、线程缓存和全局原子栈。
- `baidu/feed-mlarch/babylon/src/baidu/feed/mlarch/babylon/pool.hpp:461-479`：回池前重建实现。
- `baidu/feed-general/framework/include/graph_engine.h:35-84`：GraphPool、GraphUnit 与 GraphEngine 接口。
- `baidu/feed-general/framework/src/graph_engine.cpp:198-317`：GraphPool 参数解析、工厂构建与 bvar 暴露。
- `baidu/feed-general/framework/src/graph_engine.cpp:343-374`：product/channel 到 graph_name 与 get/try_get 分支。
- `baidu/elitefeed/rframe/src/io/io.h:36-112`：IO controller 状态与 IO buffer pool 初始化宏。
- `baidu/elitefeed/rframe/src/io/nshead_io.h:27-81`：NsheadMessage 池化、构造清理和 reset。

> KU 正文未读取，需人工补充 Babylon/ObjectPool 的线上容量基线、GraphPool 推荐参数和 rframe 发布环境约束。
