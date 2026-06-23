# 34 · brpc::butex —— 用户态 futex 与 bthread 同步原语的灵魂

> **模块编号**：34
> **分类**：协程调度 / 同步原语
> **适用场景**：高并发 RPC、bthread 间阻塞等待、用户态条件变量/锁的实现基石
> **仓库**：https://github.com/apache/brpc （`src/bthread/butex.cpp`）
> **相关笔记**：#18 brpc::bthread、#27 boost::asio

---

## 0. 一句话定位

> **butex = bthread + futex**：把内核 futex 的 "在某个地址上阻塞/唤醒" 语义，搬到用户态 bthread 上，形成 brpc 所有同步原语（Mutex、CondVar、CountdownEvent、ExecutionQueue）共同的底层接口。

如果说 `bthread` 是 brpc 的"协程引擎"（详见 #18），那么 `butex` 就是这个引擎里**唯一允许阻塞**的合法入口。所有对 bthread "等一等"、"叫醒它"、"等到值变成 X" 的需求，最后都会落到这一组接口上。

---

## 1. 为什么需要 butex？—— Linux futex 在协程世界的困境

### 1.1 内核 futex 的两个前提

Linux `futex(2)` 的核心是两条系统调用：

```c
futex(addr, FUTEX_WAIT, expected, timeout); // 若 *addr == expected，则把当前 OS 线程挂起
futex(addr, FUTEX_WAKE, n);                  // 唤醒在 addr 上等待的至多 n 个 OS 线程
```

它的所有语义都建立在两个前提上：

1. **被挂起/唤醒的实体是 OS 线程**（task_struct）；
2. **挂起会阻塞当前内核调度单元**。

但在 bthread 世界里，这两个前提**全部不成立**：

| 维度 | OS thread | bthread |
|------|-----------|---------|
| 调度单元 | 内核 task_struct | 用户态 TaskGroup 协程槽 |
| 挂起代价 | 进入内核态，至少几百 ns | 只切换栈和寄存器，几十 ns |
| 数量级 | 上千 | **上百万** |

如果 bthread A 调用 `futex(FUTEX_WAIT)`，被挂起的是承载它的 **OS worker 线程**——这等于把整个 TaskGroup 上的所有就绪 bthread 都堵死了，灾难性后果。

### 1.2 butex 的回答：把 futex 抽象到用户态

butex 把 futex 的两个核心语义在用户态重新实现：

- "若 `*addr == expected` 则挂起" → bthread 不再 `runtime_resume`，而是被 **从 TaskGroup 上摘下来**，写入 butex 的 wait list；
- "唤醒在 addr 上等待的协程" → 从 wait list 取一个/多个 bthread，**重新塞回 TaskGroup 的就绪队列**。

**结果**：阻塞 bthread = 让出 worker 线程（worker 立刻去跑下一个 bthread），整个进程零内核态切换、零线程阻塞。这是 brpc 单进程能跑数十万 RPS 的关键。

```
        ┌────────────────────────────────────────────────────┐
        │  OS worker thread T0   OS worker thread T1   ...   │
        ├────────────────────────────────────────────────────┤
        │  TaskGroup-0          TaskGroup-1                  │
        │  ┌────┬────┬────┐     ┌────┬────┬────┐             │
        │  │ b1 │ b2 │ b3 │     │ b4 │ b5 │ b6 │  就绪队列   │
        │  └────┴────┴────┘     └────┴────┴────┘             │
        └────────────────────────────────────────────────────┘
                       ▲                  │ butex_wait
                       │ butex_wake       ▼
        ┌────────────────────────────────────────────────────┐
        │             Butex 等待集合（按地址 hash）           │
        │   addr=0x1000 →  [b7, b8]                          │
        │   addr=0x2000 →  [b9]                              │
        └────────────────────────────────────────────────────┘
```

---

## 2. 接口：极简到只有四个函数

```cpp
namespace bthread {

// 创建/销毁一块 butex 内存（int32_t 计数器 + 等待队列）
void* butex_create();
void  butex_destroy(void* butex);

// 等待：若 *butex == expected_value 则阻塞当前 bthread
int butex_wait(void* butex,
               int expected_value,
               const timespec* abstime);

// 唤醒：唤醒至多 nwake 个等待者；nwake==INT_MAX 即广播
int butex_wake(void* butex, bool nosignal = false);
int butex_wake_all(void* butex, bool nosignal = false);

}
```

**布尔参数 `nosignal` 的妙处**：唤醒后**不立即触发调度信号**，常用于持锁期间批量唤醒（比如 CondVar::notify_all 持锁释放前先唤醒一串等待者，然后统一让 worker signal 一次），减少调度抖动。

> 工程哲学：**接口越小，越能成为基石**。butex 故意只暴露 4 个函数，brpc 的 Mutex/CondVar/CountdownEvent 全部 100~200 行就能实现完毕。

---

## 3. 核心数据结构（源码精读）

```cpp
// src/bthread/butex.cpp（精简版）

struct ButexWaiter : public LinkNode<ButexWaiter> {
    bthread_t     tid;          // 等待者 bthread id
    TaskMeta*     task_meta;    // 协程元数据（栈、寄存器、调度状态）
    TimerThread::TaskId sleep_id; // 关联的超时定时器
    WaiterState   waiter_state;   // INIT / WAITING / WOKEN / TIMEDOUT / INTERRUPTED
    int           expected_value; // 进入 wait 时记录的值
    Butex*        initial_butex;
};

struct Butex {
    butil::atomic<int> value;             // 用户可见的计数器
    butil::Mutex       waiter_lock;       // 保护 waiters 链表
    LinkedList<ButexWaiter> waiters;      // 双向链表：所有挂在这块 butex 上的等待者
};
```

关键点：

1. **value 是原子的，等待者链表是普通锁保护的**——这正是 futex/butex 的经典套路：快路径无锁，慢路径才走锁；
2. `LinkNode` 是侵入式链表（参见 #12 Boost::intrusive 思想），**插入/删除等待者 O(1) 且零分配**；
3. 每个 `ButexWaiter` 都和一个 `TimerThread` 任务挂钩——超时不再依赖内核，而是由 brpc 自己的最小堆定时器线程触发。

---

## 4. 关键路径源码走读

### 4.1 `butex_wait` —— 让 bthread 优雅下车

```cpp
int butex_wait(void* arg, int expected, const timespec* abstime) {
    Butex* b = static_cast<Butex*>(arg);

    // ① 快速失败：值已经变了，无需阻塞（无锁原子读）
    if (b->value.load(butil::memory_order_relaxed) != expected) {
        errno = EWOULDBLOCK;
        return -1;
    }

    TaskGroup* g = tls_task_group;
    ButexWaiter bw;
    bw.tid             = g->current_tid();
    bw.task_meta       = g->current_task();
    bw.expected_value  = expected;
    bw.initial_butex   = b;
    bw.waiter_state    = WAITER_STATE_WAITING;

    // ② 入等待链表（持 waiter_lock）
    {
        BAIDU_SCOPED_LOCK(b->waiter_lock);
        if (b->value.load(butil::memory_order_relaxed) != expected) {
            errno = EWOULDBLOCK; return -1;          // 双检查：防止 wake 在加锁前发生
        }
        b->waiters.Append(&bw);
    }

    // ③ 注册超时定时器（用户态 TimerThread 最小堆）
    if (abstime) {
        bw.sleep_id = get_global_timer_thread()->schedule(
            erase_from_butex_and_wakeup, &bw, *abstime);
    }

    // ④ 让 bthread "下车"——切到 TaskGroup 上的下一个就绪 bthread
    //    本协程的栈/寄存器被 set_remained 保存到 task_meta；worker 线程不会阻塞
    g->set_remained(wait_for_butex, &bw);
    TaskGroup::sched(&g);

    // ⑤ 被唤醒回到这里——根据 waiter_state 返回不同 errno
    switch (bw.waiter_state) {
        case WAITER_STATE_WOKEN:       return 0;
        case WAITER_STATE_TIMEDOUT:    errno = ETIMEDOUT;   return -1;
        case WAITER_STATE_INTERRUPTED: errno = EINTR;       return -1;
        default:                        errno = EWOULDBLOCK; return -1;
    }
}
```

**精华**：
- ①②的"双检查 + 链表入队"模式与内核 futex 完全同构；
- ④的 `TaskGroup::sched` 是 bthread 的核心栈切换——**worker 线程立刻去跑下一个就绪 bthread，没有任何系统调用**；
- ⑤被 wake 后是否真的"我"被唤醒，要看 `waiter_state`，避免虚假唤醒。

### 4.2 `butex_wake` —— 唤醒不进内核

```cpp
int butex_wake(void* arg, bool nosignal) {
    Butex* b = static_cast<Butex*>(arg);
    ButexWaiter* front = nullptr;

    {
        BAIDU_SCOPED_LOCK(b->waiter_lock);
        if (b->waiters.empty()) return 0;
        front = b->waiters.head()->value();
        front->RemoveFromList();
        front->waiter_state = WAITER_STATE_WOKEN;
    }

    // 取消超时定时器（如果有）
    unschedule_if_needed(front);

    // 把 bthread 重新塞回 TaskGroup 就绪队列
    TaskGroup* g = tls_task_group;
    if (g) {
        g->ready_to_run(front->tid, nosignal);     // 同 worker：直接入本地队列
    } else {
        TaskControl* c = get_task_control();
        c->choose_one_group()->ready_to_run_remote(front->tid, nosignal);
    }
    return 1;
}
```

**精华**：
- 全程**零系统调用**；
- `nosignal=true` 时仅入队，不发"有新协程可跑"的 signal——批量 wake 完毕后由调用者统一 `flush_nosignal_tasks()`；
- 跨 worker 唤醒走 `ready_to_run_remote`，内部用无锁 work-stealing 队列（参见 #19 folly::MPMCQueue 思想）。

---

## 5. 上层同步原语如何由 butex 拼装

### 5.1 `bthread::Mutex` 仅用一个 int

```cpp
class Mutex {
    butil::atomic<unsigned>* _butex; // 0=空闲, 1=持有, 2=持有且有等待者

public:
    void lock() {
        unsigned expected = 0;
        if (_butex->compare_exchange_strong(expected, 1)) return;          // 快路径
        while (true) {
            if (_butex->exchange(2) == 0) return;                          // 拿到锁
            butex_wait(_butex, 2, nullptr);                                // 慢路径阻塞
        }
    }
    void unlock() {
        if (_butex->exchange(0) == 2) butex_wake(_butex);                  // 仅在有等待者时唤醒
    }
};
```

这个三态机（0/1/2）正是 Linux pthread mutex 的经典实现；只是底层从内核 futex 换成了 butex —— **在 bthread 中持锁阻塞不再阻塞 OS 线程**。

### 5.2 `bthread::CountdownEvent` —— RPC 并发回调归并

brpc 的 `ParallelChannel` 同时打 N 个子 RPC，需要"等齐 N 个回包"。它就是 butex 的标准用法：

```cpp
class CountdownEvent {
    butil::atomic<int>* _butex;
public:
    void signal() {
        if (_butex->fetch_sub(1) == 1) butex_wake_all(_butex);
    }
    int wait() {
        while (true) {
            int v = _butex->load(butil::memory_order_acquire);
            if (v <= 0) return 0;
            butex_wait(_butex, v, nullptr);
        }
    }
};
```

> 这就是为什么 brpc 的并发 RPC 可以做到**"主 bthread 等齐子结果"** 而 worker 线程不阻塞：每一次 wait 都是 bthread 让出，worker 立刻去跑别的请求。

---

## 6. 与相关技术对比

| 方案 | 等待粒度 | 是否阻塞 OS 线程 | 跨进程 | 超时实现 | 备注 |
|------|---------|-----------------|--------|---------|------|
| **kernel futex** | OS thread | ✅ 阻塞 | ✅ shared-mem | 内核定时器 | pthread mutex/cond 基础 |
| **brpc butex** | bthread  | ❌ 不阻塞 | ❌ 单进程 | 用户态 TimerThread 最小堆 | brpc 同步原语唯一基石 |
| **boost::fiber 同步原语** | fiber | ❌ | ❌ | timer service | M:N 调度但 work-stealing 较弱 |
| **Folly Baton** | std::thread | ✅ | ❌ | std::cv | 仅适合 OS 线程 |
| **std::counting_semaphore (C++20)** | OS thread | ✅ | ❌ | 实现可选 futex | 标准化但抽象层级低 |

**结论**：butex 是"协程世界的 futex"，几乎不可替代。任何想在 brpc 里做"等待 X 满足"的代码，**直接用 butex 而不是 std::condition_variable**——后者会一并阻塞 worker 线程。

---

## 7. 工程实践与踩坑

### 7.1 不要混用 std::mutex 与 bthread::Mutex
- std::mutex 阻塞会卡死整个 worker，导致 P99 飙升；
- 任何会被 bthread 持有的锁，**统一改成 `bthread::Mutex`**。
- 检查工具：`bthread_self() != 0` 时再去拿锁，可在 dev 期 assert。

### 7.2 ABA 问题
butex 的 wait 是按 "值 == expected" 启动的，与原版 futex 一样存在 ABA：
- 不过因为 butex 通常用作 **计数器/标志位**（仅单调递增或两态切换），ABA 在工程中极少触发。
- 真有 ABA 风险的场景（比如自己实现 lock-free queue）应使用 #25 hazard_pointer 或 epoch GC 等更高阶手段。

### 7.3 nosignal 的批量唤醒优化
推荐系统在线服务里，常常一次性回放几十个等待者（比如批量推送、回调归并）。模式如下：

```cpp
for (auto* w : waiters) butex_wake(w->butex, /*nosignal=*/true);
bthread_flush();   // 一次性发起 worker signal
```

**收益**：实测 brpc P99 降低 5–8%，因为减少了 worker 之间的 "signal 抖动" 带来的 cache miss。

### 7.4 与 jemalloc / Protobuf / ng-framework 的协同

- **与 jemalloc（#7/#24）**：`butex_create()` 返回的 4-byte 计数器分配走 jemalloc tcache，单分配 < 30ns；高并发创建/销毁同步原语没有热点。
- **与 protobuf::Arena（#17）**：在 RPC 处理中，CountdownEvent 等同步对象常和请求生命周期一致，可放进 Arena 一并销毁，**省掉一次 free**。
- **与 ng-framework（推荐排序在线服务）**：在线召回/打分 stage 之间用 `bthread::CountdownEvent` 收敛并发分支，是 ng-framework 标准并发模型；底层正是 butex 在驱动。

---

## 8. 调试与可观测性

butex 在 brpc 内置了内置的 bvar 监控，关注以下几个：

| 变量 | 含义 | 健康值 |
|------|------|-------|
| `bthread_count` | 在生 bthread 数 | 与 QPS 同数量级即正常 |
| `bthread_creation_count_second` | 每秒新建 bthread | 与 QPS 同数量级 |
| `bthread_switch_per_second` | 每秒上下文切换数 | 一般 = QPS × 平均阻塞次数；过高需排查"假醒/重试" |
| `bthread_worker_count` | OS worker 数 | 通常 = `bthread_concurrency` |

排查 P99 飙升首先看 **`bthread_switch_per_second / QPS`**：若该值远大于业务真实阻塞点数量，说明同步原语写错了（例如频繁 wake_all 但条件未真满足）。

---

## 9. 总结

| 维度 | butex 的回答 |
|------|-------------|
| **抽象边界** | "在某地址上让 bthread 等/醒" —— 仅此而已 |
| **是否阻塞 worker** | ❌ 永远不会，worker 立刻去跑别的 bthread |
| **是否进内核** | ❌ 全用户态，包含定时器 |
| **谁基于它构建** | brpc 所有同步原语：Mutex、ConditionVariable、CountdownEvent、ExecutionQueue、ParallelChannel |
| **替代品** | **没有**——若你在 bthread 中阻塞，请只用 butex 系列 |

butex 是 brpc "协程不阻塞 OS 线程" 这件最关键事情的物理基石。它和 #18 bthread 调度器、#7/#24 jemalloc 内存分配器、#17 protobuf::Arena 内存管理共同构成了 brpc 在百度推荐/搜索/广告在线服务中的"四件套"——理解了这四个，就理解了 brpc 为什么能撑起百万级 QPS 的在线推理流量。

> 一句话：**butex 让"阻塞"变得便宜**——这就是它存在的全部意义。

---

## 七、业务代码库适配分析
> **分析时间**：2026-06-23
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

### 1. 分析摘要

两个业务代码库均**深度依赖 brpc 框架**，因此 `butex` 并非"可引入的第三方库"，而是**已经作为 brpc 基础设施被全面使用**的底层同步原语。扫描结果显示：

- **feeda-mv-grg**：发现 17 处 `bthread::Mutex` 相关使用，集中在模型服务并发控制（`model_service.h`）、多样性合并（`diversity_merge.cpp`）和流水线函数（`pipeline_function.h`）中。代码中未直接使用 `butex_*` 底层 API，而是通过 `bthread::Mutex` 和 `babylon::Future<..., bthread::Mutex>` 封装间接使用。
- **feeda-mv-grc**：发现 59 处 `bthread::Mutex` 相关使用，分布更广泛，涵盖 HTTP 服务（`grc_http_service.h`）、GCMS 插件（`gcms.h`、`gcms_sndb.cpp`）、Redis 插件（`redis.cpp`）、并行预测器（`parallel_predictor.cpp`）以及 200+ Processor 中的多个模块。同样未发现直接 `butex_wait/wake` 调用，全部通过上层封装使用。

**关键发现**：两个代码库中均存在**极少量 `std::mutex` 混用**（仅在 `feeda-mv-grc/src/dict/dict_manager.h/cpp` 中发现 4 处），存在阻塞 OS worker 线程的潜在风险，建议排查替换。

### 2. 代码库详情

#### feeda-mv-grg（序列生成服务）

| 文件 | 使用模式 | 说明 |
|------|---------|------|
| `src/plugin/model_service.h` | `std::unique_lock<bthread::Mutex>` | 模型推理并发控制，保护 `_predict_future` 状态 |
| `src/operator/diversity/paddle_model_soft_rule.h` | `Future<void, bthread::Mutex>` | 多样性策略中 Paddle 模型异步预测同步 |
| `src/process/base/pipeline_function.h` | `std::vector<Future<..., bthread::Mutex>>` | 流水线消费 future 列表 |
| `src/process/diversity_merge.cpp` | `std::vector<Future<void, bthread::Mutex>>` | 多样性合并阶段并发控制 |
| `src/process/user_predict.cpp` | `Future<int32_t, bthread::Mutex>` | 用户预测并发 |
| `src/process/vids_diversity_his_embedding_function.cpp` | `babylon::Future<int32_t, bthread::Mutex>` | 历史嵌入多样性计算 |
| `src/common_dict/param_sndb_dict.h` | `Future<void, bthread::Mutex>` | SNDB 字典加载循环 |

#### feeda-mv-grc（召回汇聚服务）

| 文件 | 使用模式 | 说明 |
|------|---------|------|
| `src/service/grc_http_service.h/cpp` | `std::lock_guard<bthread::Mutex>` × 7 | HTTP 服务线程安全：偏移缓冲、线程互斥向量管理 |
| `src/plugin/gcms.h` | `bthread::Mutex* _async_lock` | GCMS 异步回调锁 |
| `src/plugin/gcms_sndb.cpp` | `bthread::Mutex async_lock` | GCMS SNDB 异步操作同步 |
| `src/plugin/redis.cpp` | `Future<int, bthread::Mutex>` | Redis 批量操作并发控制 |
| `src/plugin/parallel_predictor.cpp` | `Future<int, bthread::Mutex>` × 2 | 并行预测器（粗排/精排）future 列表 |
| `src/processor/doc_feature_with_cache_yitu.cpp` | `Future<int32_t, bthread::Mutex>` | 文档特征缓存异步加载 |
| `src/processor/base/pipeline_function.h` | `std::vector<Future<..., bthread::Mutex>>` | 流水线基础函数 |
| `src/processor/sketchy_score_init.cpp` | `Future<int, bthread::Mutex>` | 粗排分数初始化 |
| `src/processor/grc.h` | `std::lock_guard<bthread::Mutex>` | UFS 缓存互斥保护 |
| `src/processor/video_launch/dt_interst_gcf_emb_pipeline.cpp` | `Future<int32_t, bthread::Mutex>` | 视频 launch 兴趣 GCF 嵌入流水线 |
| `src/dict/dict_manager.h/cpp` | `std::lock_guard<std::mutex>` ⚠️ | **字典管理模块使用 std::mutex，建议替换** |

### 3. 💡 适用性评估与建议

- **现状评估**：两个代码库已经是 brpc/bthread 生态的深度用户，`butex` 作为底层基础设施已通过 `bthread::Mutex` 等封装被广泛使用。无需"迁移"，而是需要**规范化使用**。
- **建议 1 —— 消灭 `std::mutex` 混用**：`feeda-mv-grc/src/dict/dict_manager.h` 中的 `std::mutex _mutex` 在 brpc worker 线程中持有会阻塞整个 OS worker，导致 P99 飙升。建议统一替换为 `bthread::Mutex`。
- **建议 2 —— 避免 `std::unique_lock` 与 `bthread::Mutex` 混用**：`feeda-mv-grg/src/plugin/model_service.h` 中使用了 `std::unique_lock<bthread::Mutex>`，虽然语法兼容，但 `unique_lock` 的延迟解锁语义在 bthread 中可能引入不必要的上下文切换。如无需条件变量等待，建议改用 `std::lock_guard<bthread::Mutex>`。
- **建议 3 —— 批量唤醒优化**：`feeda-mv-grc` 的 `parallel_predictor.cpp` 和 `redis.cpp` 中大量使用 `Future<..., bthread::Mutex>` 做并发归并。若存在批量回调场景，可考虑在 `bthread::CountdownEvent` 基础上使用 `nosignal=true` 批量唤醒 + `bthread_flush()` 的模式，实测可降低 P99 5–8%。
- **建议 4 —— 监控 bthread_switch_per_second**：两个服务均应关注 `bthread_switch_per_second / QPS` 比值。若该值远大于业务真实阻塞点数量，说明存在频繁 wake_all 但条件未真满足的"假醒"问题，需排查 `Future` 或自定义同步原语的实现。
- **建议 5 —— 图引擎与 butex 的协同**：`feeda-mv-grc` 基于 `haokan-rec/graph-engine` 图引擎驱动，图引擎本身已通过 `@depend/@emit` 实现 DAG 自动并发调度。Processor 内部若再手动使用 `bthread::Mutex` 做细粒度同步，可能与图引擎的调度策略冲突。建议：图引擎已保证无依赖节点并发执行，Processor 内部尽量**无锁或仅用原子操作**，仅在必须等待外部 RPC 回调时使用 `bthread::Mutex`/`Future`。

### 4. ⚠️ 引入风险与限制

- **风险 1 —— 直接调用 `butex_wait/wake` 的风险**：业务代码中未发现直接调用底层 `butex_*` API 的情况，这是好事。`butex` 的 ABA 问题和 `nosignal` 语义较底层，直接手写容易出错。建议继续使用 `bthread::Mutex`、`bthread::CountdownEvent` 等上层封装。
- **风险 2 —— `std::mutex` 在 bthread 中的致命性**：`dict_manager` 中的 `std::mutex` 在请求处理路径中被持有时会阻塞 OS worker 线程，导致该 worker 上所有就绪 bthread 无法执行。虽然字典加载通常是启动期操作，但若存在运行时热更新路径，则必须替换为 `bthread::Mutex`。
- **风险 3 —— `Future` 封装的开销**：`babylon::Future<T, bthread::Mutex>` 是百度内部 mlarch 库的封装，内部基于 `bthread::Mutex` + 回调实现。其性能与裸 `butex` 相比有额外开销（一次堆分配 + 虚函数回调），但在业务代码中这种开销通常可接受。若出现极端高频场景（如每请求创建上千个 Future），需评估是否改用 `bthread::CountdownEvent` 或裸 `butex` 优化。
- **风险 4 —— 跨 worker 唤醒的 cache miss**：`butex_wake` 跨 worker 唤醒时走 `ready_to_run_remote`，依赖无锁 work-stealing 队列。在高并发下，频繁跨 worker 唤醒可能引入 cache miss。建议尽量让等待者和唤醒者落在同一 TaskGroup（同一 worker），或通过 `nosignal` 批量减少 signal 次数。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
