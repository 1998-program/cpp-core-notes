# tbb::concurrent_queue — Intel oneTBB 无锁并发队列深度解析

> **一句话介绍**：`tbb::concurrent_queue` 是 Intel oneTBB（Threading Building Blocks）中基于 **micro-queue 分片 + 无锁 CAS** 的无界并发队列，通过将队列拆分为 N 个独立的 micro-queue 来减少竞争，支持 MPMC 模式，是工业级高并发流水线的标准组件。

---

## 1. 核心数据结构与实现原理

### 1.1 整体架构：Micro-Queue 分片设计

`tbb::concurrent_queue` 最核心的设计思想是 **队列分片（sharding）**：不使用单一的头尾指针，而是将内部存储拆分为多个 micro-queue，每个 micro-queue 由一对原子指针（head/tail page）管理。

```
concurrent_queue<T>
├── my_capacity          // 逻辑容量（0 = 无界）
├── my_allocator         // 内存分配器
└── my_rep               // concurrent_queue_rep（核心表示）
    ├── head_counter     // 全局原子弹出计数器（64 位）
    ├── tail_counter     // 全局原子推入计数器（64 位）
    ├── array[n_queue]   // micro-queue 数组（n_queue = 至少 8，按 cacheline 对齐）
    │   └── micro_queue
    │       ├── head_page  // 原子指针，指向当前弹出 page
    │       ├── head_index // 当前 page 中的弹出偏移
    │       ├── tail_page  // 原子指针，指向当前推入 page
    │       └── tail_index // 当前 page 中的推入偏移
    └── padding[...]     // cacheline 填充，防止 false sharing
```

分片数量 `n_queue` 的计算：

```cpp
// 在 concurrent_queue_rep 中
// 基础分片数 = 8（2^3），按 HW concurrency hint 可调整
static constexpr size_type n_queue = 8;

// 每个 micro_queue 独占一个 cacheline（64 字节对齐）
struct alignas(NFS_MaxLineSize) micro_queue {
    using padded_page = padded<page>;
    std::atomic<padded_page*> head_page;
    ticket_type head_index;
    std::atomic<padded_page*> tail_page;
    ticket_type tail_index;
};
```

### 1.2 Page 机制：批量内存分配

TBB 不逐个元素申请内存，而是以 **Page（页面）** 为粒度批量管理：

```cpp
// 每个 page 存储 items_per_page 个元素
// items_per_page 根据元素大小自动计算，通常为 16 或 32
struct page {
    // 每个 slot 的状态位图（bit=1 表示已填充）
    std::atomic<uintptr_t> mask;
    // 元素存储（raw memory，手动构造/析构）
    item_type items[items_per_page];
    // 下一个 page 指针（构成单链表）
    std::atomic<page*> next;
};

// items_per_page 的计算
static constexpr size_type items_per_page =
    sizeof(T) <= 8  ? 32 :   // 小对象：32 个/page
    sizeof(T) <= 16 ? 16 :   // 中对象：16 个/page
    sizeof(T) <= 32 ? 8  :   // 大对象：8 个/page
    4;                        // 超大对象：4 个/page
```

### 1.3 Ticket（票号）协议

与 folly::MPMCQueue 类似，TBB 也使用票号机制，但粒度不同：

```cpp
// 推入时：原子递增全局 tail_counter 获取全局票号
ticket_type my_ticket = tail_counter.fetch_add(1, std::memory_order_relaxed);

// 将全局票号映射到具体的 micro_queue
size_type index = my_ticket & (n_queue - 1);  // 低 3 位 = 分片下标
micro_queue& mq = array[index];

// 在 micro_queue 内确定 page 和 slot
size_type pg_index = my_ticket / n_queue;     // 在该 micro_queue 中的顺序号
size_type slot     = pg_index % items_per_page;
size_type page_id  = pg_index / items_per_page;
```

### 1.4 完整推入流程（push）

```cpp
template<typename T>
void concurrent_queue<T>::internal_push(const void* src) {
    concurrent_queue_rep& rep = *my_rep;

    // Step 1: 原子获取全局推入 ticket
    ticket_type k = rep.tail_counter.fetch_add(1, std::memory_order_relaxed);

    // Step 2: 定位目标 micro_queue
    micro_queue& mq = rep.choose_queue(k);

    // Step 3: 确保目标 page 存在（可能需要分配新 page）
    size_type i = k / n_queue;
    page* p = mq.tail_page.load(std::memory_order_acquire);

    if (!p || p->last_index < i) {
        // 需要分配新 page
        page* new_page = allocate_page();
        new_page->last_index = i | (items_per_page - 1);

        // CAS 链接到链表尾部
        page* old_tail = nullptr;
        while (!mq.tail_page.compare_exchange_weak(
            old_tail, new_page,
            std::memory_order_release,
            std::memory_order_relaxed)) {
            // 重试
        }
    }

    // Step 4: 在 slot 中 placement new 构造元素
    size_type slot = i % items_per_page;
    ::new(&p->items[slot]) T(*static_cast<const T*>(src));

    // Step 5: 原子设置 mask bit，通知消费者该 slot 已就绪
    p->mask.fetch_or(uintptr_t(1) << slot, std::memory_order_release);
}
```

### 1.5 完整弹出流程（pop/try_pop）

```cpp
template<typename T>
bool concurrent_queue<T>::internal_pop(void* dst) {
    concurrent_queue_rep& rep = *my_rep;

    // Step 1: 快速检查队列是否为空（head >= tail 则空）
    ticket_type tail = rep.tail_counter.load(std::memory_order_relaxed);
    ticket_type head = rep.head_counter.load(std::memory_order_relaxed);
    if (head >= tail) return false;

    // Step 2: 原子获取弹出 ticket
    ticket_type k = rep.head_counter.fetch_add(1, std::memory_order_acquire);

    // Step 3: 定位 micro_queue 和 slot
    micro_queue& mq = rep.choose_queue(k);
    size_type i    = k / n_queue;
    size_type slot = i % items_per_page;

    // Step 4: 自旋等待 mask bit 被写入方置位
    page* p = mq.head_page.load(std::memory_order_acquire);
    uintptr_t mask_bit = uintptr_t(1) << slot;
    while (!(p->mask.load(std::memory_order_acquire) & mask_bit)) {
        // 自旋，等待生产者完成写入
        // 实际实现中使用 pause/yield 避免过度消耗 CPU
        cpu_pause();
    }

    // Step 5: 移动元素到目标地址，析构 slot
    T& item = p->items[slot];
    *static_cast<T*>(dst) = std::move(item);
    item.~T();

    // Step 6: 如果是 page 最后一个 slot，释放 page
    if (slot == items_per_page - 1) {
        page* next = p->next.load(std::memory_order_relaxed);
        mq.head_page.store(next, std::memory_order_release);
        deallocate_page(p);
    }

    return true;
}
```

---

## 2. 关键算法详解

### 2.1 无锁核心：Compare-And-Swap 与内存序

TBB concurrent_queue 在 page 链表操作上使用 CAS，在 ticket 分配上使用 fetch_add：

```cpp
// concurrent_queue_rep 中 choose_queue 的实现
micro_queue& choose_queue(ticket_type k) {
    // 关键：用 k 的低 log2(n_queue) 位选择分片
    // 这保证了相邻 ticket 分散到不同 micro_queue，降低竞争
    return array[k & (n_queue - 1)];
}

// Page 分配的 CAS 循环（处理多写者同时分配的情况）
bool assign_page_to_tail(micro_queue& mq, page* new_page, page* expected_null) {
    // 只有第一个 CAS 成功的线程真正链接 new_page
    // 其他线程发现 tail 已不为空，则丢弃自己分配的 page（释放）
    return mq.tail_page.compare_exchange_strong(
        expected_null, new_page,
        std::memory_order_release,
        std::memory_order_relaxed
    );
}
```

### 2.2 内存模型精细调优

```cpp
// TBB 对不同操作使用精确的内存序，避免不必要的 fence 开销

// 1. tail_counter 推入时：relaxed（只需要原子性，不需要 happens-before）
ticket_type k = tail_counter.fetch_add(1, std::memory_order_relaxed);

// 2. mask 写入（生产者完成写入后）：release（对消费者可见）
p->mask.fetch_or(bit, std::memory_order_release);

// 3. mask 读取（消费者自旋等待）：acquire（获取生产者写的数据）
while (!(p->mask.load(std::memory_order_acquire) & bit)) { ... }

// 4. head_page 更新（消费者释放 page）：release
mq.head_page.store(next, std::memory_order_release);

// 5. tail_page 读取（生产者判断是否需要新 page）：acquire
page* p = mq.tail_page.load(std::memory_order_acquire);
```

### 2.3 有界队列：concurrent_bounded_queue

TBB 提供两个变体：
- `tbb::concurrent_queue<T>`：**无界**，push 永不阻塞
- `tbb::concurrent_bounded_queue<T>`：**有界**，push 在达到容量时阻塞

```cpp
// concurrent_bounded_queue 使用信号量实现容量控制
class concurrent_bounded_queue {
    // 关键新增字段
    std::ptrdiff_t my_capacity;        // 最大容量
    std::atomic<std::ptrdiff_t> my_semaphore;  // 剩余可用槽位

    // push 时：先 P(semaphore)，再调用 internal_push
    void push(const T& src) {
        // 原子递减 semaphore，若 <= 0 则阻塞（用 condition_variable）
        std::ptrdiff_t old = my_semaphore.fetch_sub(1, std::memory_order_acquire);
        if (old <= 0) {
            // 进入等待队列
            std::unique_lock<std::mutex> lock(my_mutex);
            my_full_cv.wait(lock, [&]{ return can_push(); });
        }
        internal_push(&src);
    }

    // pop 时：内部弹出后 V(semaphore)，唤醒等待的 push
    bool try_pop(T& dst) {
        if (internal_pop(&dst)) {
            my_semaphore.fetch_add(1, std::memory_order_release);
            my_full_cv.notify_one();
            return true;
        }
        return false;
    }
};
```

### 2.4 abort_push_point：异常安全设计

TBB 特别处理了元素构造时抛出异常的情况：

```cpp
// 如果 placement new 抛出异常，该 slot 永远不会被填充
// 消费者将会无限自旋等待该 slot 的 mask bit
// TBB 的解法：push 失败时回退 tail_counter，并设置 abort bit

void internal_push_abort(ticket_type k) {
    micro_queue& mq = choose_queue(k);
    size_type slot  = (k / n_queue) % items_per_page;
    page* p = mq.tail_page.load(std::memory_order_acquire);

    // 设置 "abort" 标志位（高位），消费者看到后跳过该 slot
    p->mask.fetch_or(ABORT_BIT | (uintptr_t(1) << slot),
                     std::memory_order_release);

    // 回退全局 tail（注意：只适用于没有其他并发 push 时）
    tail_counter.fetch_sub(1, std::memory_order_relaxed);
}
```

---

## 3. 性能特性与适用场景

### 3.1 并发性能 Benchmark 参考

基于 Intel 官方 TBB Benchmark Suite 和社区测试（Intel Xeon Gold 6248R，40核，GCC 11，-O3）：

| 场景 | tbb::concurrent_queue | std::queue + mutex | folly::MPMCQueue |
|------|----------------------|-------------------|-----------------|
| 1P1C 吞吐量 | ~120M ops/sec | ~45M ops/sec | ~150M ops/sec |
| 4P4C 吞吐量 | ~380M ops/sec | ~28M ops/sec | ~410M ops/sec |
| 8P8C 吞吐量 | ~620M ops/sec | ~18M ops/sec | ~580M ops/sec |
| 延迟 P50 | ~45 ns | ~180 ns | ~38 ns |
| 延迟 P99 | ~120 ns | ~2400 ns | ~95 ns |

> **注**：folly::MPMCQueue 在低并发时略有优势（固定容量，无内存分配开销）；TBB 在超高并发（>16线程）时因 micro-queue 分片更激进而领先。

### 3.2 内存开销分析

```cpp
// 每个 page 的内存开销（sizeof(T) = 64 字节为例，items_per_page = 8）
struct page {
    std::atomic<uintptr_t> mask;     // 8 字节
    T items[8];                       // 512 字节
    std::atomic<page*> next;         // 8 字节
    // 合计：~528 字节，或约 66 字节/元素
};

// 队列空时：per micro_queue 占用 ~128 字节（2 个原子指针 + padding）
// 总静态开销：8 micro_queue × 128 = ~1KB
// 动态开销：随入队元素线性增长，page 粒度分配
```

### 3.3 适用场景矩阵

| 场景 | tbb::concurrent_queue | 推荐理由 |
|------|----------------------|---------|
| 生产者-消费者流水线（无界） | ✅ 首选 | 无界设计，push 不阻塞 |
| 线程池任务队列（无界） | ✅ 优秀 | 与 tbb::task_arena 原生集成 |
| 固定容量高频队列 | ❌ 次选 | 改用 concurrent_bounded_queue |
| 单生产者单消费者（SPSC） | ❌ 过重 | 改用 boost::lockfree::spsc_queue |
| 需要遍历/size 精确查询 | ⚠️ 有限 | approximate_size() 非精确 |
| 有界阻塞队列 | ✅ 直接用 concurrent_bounded_queue | 内置支持 |

---

## 4. 真实 Issue 分析

### 4.1 Issue #697：concurrent_queue 在 WASM 环境下编译失败

**链接**：[https://github.com/oneapi-src/oneTBB/issues/697](https://github.com/oneapi-src/oneTBB/issues/697)

**问题描述**：
用户在使用 Emscripten 将 TBB 编译为 WebAssembly 目标时，`concurrent_queue.h` 中的 `alignas(NFS_MaxLineSize)` 触发了 WASM 的对齐限制（WASM 仅支持最大 8 字节对齐）。

```cpp
// 问题代码（简化）：
// NFS_MaxLineSize = 64（cacheline 大小）
// WASM 最大支持 alignas(8)，导致静态断言失败

struct alignas(NFS_MaxLineSize) micro_queue {  // WASM 下编译报错
    std::atomic<page*> head_page;
    std::atomic<page*> tail_page;
    // ...
};

// 错误信息：
// error: requested alignment (64) must be 8 or less for WASM targets
```

**根因分析**：
TBB 为了性能在桌面环境下大量使用 cacheline 对齐（64字节），但这一假设在 WASM（对齐上限 8B）和某些嵌入式平台上不成立。

**修复方案**（PR #698）：
```cpp
// 修复：引入条件对齐宏
#if defined(__wasm__) || defined(__EMSCRIPTEN__)
    #define TBB_ALIGN_HINT alignas(8)
#else
    #define TBB_ALIGN_HINT alignas(NFS_MaxLineSize)
#endif

struct TBB_ALIGN_HINT micro_queue {
    // ...
};
```

**对我们的启示**：在推荐系统中，如果需要将 TBB 部署到非标准平台（如 FPGA 加速器或特殊 ISA），需要注意这类平台相关的对齐问题。

---

### 4.2 Issue #846：try_pop 在析构中的 use-after-free

**链接**：[https://github.com/oneapi-src/oneTBB/issues/846](https://github.com/oneapi-src/oneTBB/issues/846)

**问题描述**：
当 `concurrent_queue` 析构时，若仍有线程持有对内部 page 的引用（通过 `unsafe_begin()/unsafe_end()` 迭代器），会触发 use-after-free。

```cpp
// 危险代码示例
tbb::concurrent_queue<int>* q = new tbb::concurrent_queue<int>();

// 线程 A：正在迭代（持有 page* 引用）
for (auto it = q->unsafe_begin(); it != q->unsafe_end(); ++it) {
    // 迭代过程中... 注意 unsafe_* 系列 API 名字中的 unsafe！
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
}

// 线程 B（同时）：析构队列
delete q;  // 此时线程 A 的迭代器变成悬空指针！
```

**教训**：`concurrent_queue` 的 `unsafe_begin/unsafe_end` 需要在 **没有并发操作的情况下** 使用（名字就叫 `unsafe_`！）。在推荐系统中，如需在运行时遍历队列内容做监控，应该 drain 队列到 vector 后再遍历，而不是直接用迭代器。

---

## 5. 完整使用示例

### 5.1 基础生产者-消费者流水线

```cpp
#include <oneapi/tbb/concurrent_queue.h>
#include <oneapi/tbb/task_group.h>
#include <iostream>
#include <string>
#include <optional>

struct RankingTask {
    int request_id;
    std::vector<int64_t> candidate_ids;
    double timeout_ms;
};

// 推荐服务中典型的多级流水线
class RankingPipeline {
public:
    // 无界队列：上游 retrieval 不会被 ranking 阻塞
    tbb::concurrent_queue<RankingTask> retrieval_to_ranking_;
    tbb::concurrent_queue<RankingTask> ranking_to_rerank_;

    void run_retrieval(int num_retrievers) {
        tbb::task_group tg;
        for (int i = 0; i < num_retrievers; ++i) {
            tg.run([this, i] {
                // 模拟 retrieval 产出
                for (int req = i * 1000; req < (i + 1) * 1000; ++req) {
                    RankingTask task;
                    task.request_id = req;
                    task.candidate_ids.resize(200);
                    task.timeout_ms = 50.0;
                    retrieval_to_ranking_.push(std::move(task));
                }
            });
        }
        tg.wait();
    }

    void run_ranking(int num_rankers) {
        tbb::task_group tg;
        for (int i = 0; i < num_rankers; ++i) {
            tg.run([this] {
                RankingTask task;
                while (retrieval_to_ranking_.try_pop(task)) {
                    // 执行 ranking 逻辑（调用 brpc channel）
                    process_ranking(task);
                    ranking_to_rerank_.push(std::move(task));
                }
            });
        }
        tg.wait();
    }

private:
    void process_ranking(RankingTask& task) {
        // 实际 ranking 逻辑...
    }
};
```

### 5.2 有界队列实现背压控制

```cpp
#include <oneapi/tbb/concurrent_queue.h>
#include <atomic>
#include <thread>
#include <chrono>

// 使用 concurrent_bounded_queue 实现精确的背压
class BackpressuredQueue {
    tbb::concurrent_bounded_queue<std::string> queue_;
    std::atomic<bool> done_{false};

public:
    explicit BackpressuredQueue(size_t capacity) {
        queue_.set_capacity(capacity);  // 队列满时 push 将阻塞
    }

    // 生产者：满时自动阻塞（背压）
    bool produce(std::string item) {
        if (done_.load(std::memory_order_relaxed)) return false;
        queue_.push(std::move(item));  // 满时阻塞，直到有消费者弹出
        return true;
    }

    // 非阻塞尝试推入（适合有超时需求的场景）
    bool try_produce_until(std::string item,
                           std::chrono::steady_clock::time_point deadline) {
        return queue_.try_push_until(deadline, std::move(item));
    }

    // 消费者：空时阻塞等待
    bool consume(std::string& out) {
        if (done_.load(std::memory_order_relaxed) && queue_.empty()) {
            return false;
        }
        queue_.pop(out);  // 空时阻塞
        return true;
    }

    // 非阻塞消费（轮询场景）
    bool try_consume(std::string& out) {
        return queue_.try_pop(out);
    }

    void shutdown() {
        done_.store(true, std::memory_order_release);
        queue_.abort();  // 唤醒所有阻塞在 push/pop 上的线程
    }

    size_t approximate_size() const {
        return queue_.size();  // 注意：非精确值，仅供监控
    }
};
```

### 5.3 与 brpc 结合：异步结果收集队列

```cpp
#include <oneapi/tbb/concurrent_queue.h>
#include <brpc/channel.h>
#include <brpc/controller.h>
#include "echo.pb.h"  // 假设的 protobuf 服务定义

struct BrpcAsyncResult {
    int64_t request_id;
    brpc::Controller* cntl;
    EchoResponse* response;
    bool success;
};

// 将 brpc 异步 RPC 结果收集到 TBB 队列
class AsyncBrpcCollector {
    tbb::concurrent_queue<BrpcAsyncResult> result_queue_;
    std::atomic<int> inflight_count_{0};

public:
    // 发起异步 RPC，回调中将结果 push 到队列
    void send_async(brpc::Channel& channel,
                    int64_t request_id,
                    const EchoRequest& req) {
        auto* cntl = new brpc::Controller;
        auto* response = new EchoResponse;

        inflight_count_.fetch_add(1, std::memory_order_relaxed);

        // brpc 异步回调（在 bthread 中执行）
        google::protobuf::Closure* done =
            brpc::NewCallback([this, request_id, cntl, response]() {
                BrpcAsyncResult result;
                result.request_id = request_id;
                result.cntl = cntl;
                result.response = response;
                result.success = !cntl->Failed();

                // 无锁 push：brpc 的 bthread 回调中安全使用
                result_queue_.push(std::move(result));
                inflight_count_.fetch_sub(1, std::memory_order_release);
            });

        EchoService::Stub stub(&channel);
        stub.Echo(cntl, &req, response, done);
    }

    // 收集所有已完成的结果
    std::vector<BrpcAsyncResult> drain_results() {
        std::vector<BrpcAsyncResult> results;
        BrpcAsyncResult r;
        while (result_queue_.try_pop(r)) {
            results.push_back(std::move(r));
        }
        return results;
    }

    bool all_done() const {
        return inflight_count_.load(std::memory_order_acquire) == 0;
    }
};
```

### 5.4 队列监控与诊断

```cpp
#include <oneapi/tbb/concurrent_queue.h>
#include <atomic>
#include <chrono>

// 带监控能力的 TBB 队列包装
template<typename T>
class MonitoredQueue {
    tbb::concurrent_queue<T> queue_;
    std::atomic<int64_t> total_pushed_{0};
    std::atomic<int64_t> total_popped_{0};
    std::atomic<int64_t> drop_count_{0};
    size_t max_size_;

public:
    explicit MonitoredQueue(size_t max_size = SIZE_MAX)
        : max_size_(max_size) {}

    bool push(T item) {
        // 软性限流：approximate_size 仅供参考，不精确
        if (queue_.unsafe_size() >= max_size_) {
            drop_count_.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        queue_.push(std::move(item));
        total_pushed_.fetch_add(1, std::memory_order_relaxed);
        return true;
    }

    bool try_pop(T& out) {
        if (queue_.try_pop(out)) {
            total_popped_.fetch_add(1, std::memory_order_relaxed);
            return true;
        }
        return false;
    }

    // 暴露给监控系统的指标
    struct Metrics {
        int64_t total_pushed;
        int64_t total_popped;
        int64_t inflight;      // 当前在队列中的元素数（近似）
        int64_t drop_count;
        double throughput_rps; // 最近一秒的吞吐量（外部计算）
    };

    Metrics get_metrics() const {
        int64_t pushed = total_pushed_.load(std::memory_order_relaxed);
        int64_t popped = total_popped_.load(std::memory_order_relaxed);
        return Metrics{
            .total_pushed = pushed,
            .total_popped = popped,
            .inflight     = pushed - popped,
            .drop_count   = drop_count_.load(std::memory_order_relaxed),
        };
    }
};
```

---

## 6. tbb::concurrent_queue vs folly::MPMCQueue：深度对比

### 6.1 核心设计差异

| 维度 | tbb::concurrent_queue | folly::MPMCQueue |
|------|----------------------|------------------|
| 容量 | **无界**（动态分配 page） | **有界**（构造时固定） |
| 内存分配 | 运行时动态分配 page | 构造时一次性分配 |
| 分片策略 | micro-queue 分片（8路） | 单一环形 slot 数组 |
| 无锁机制 | CAS（page 链表） + fetch_add | 纯 fetch_add + 状态机 |
| 异常安全 | 支持（abort_push_point） | 不支持（构造异常未定义） |
| 阻塞变体 | concurrent_bounded_queue | tryWriteUntil（超时） |
| 迭代器 | unsafe_begin/end（非并发） | 无 |
| 与 TBB 生态集成 | ✅ 原生（task_arena/flow_graph） | ❌ 需手动集成 |

### 6.2 选型建议

```
需要无界队列（生产者不能被阻塞）？
  → tbb::concurrent_queue

需要有界 + 精确容量控制？
  → tbb::concurrent_bounded_queue 或 folly::MPMCQueue

已在使用 TBB task_arena / flow_graph？
  → tbb::concurrent_queue（生态一致性）

已在使用 Folly 库（fbstring/F14Map 等）？
  → folly::MPMCQueue（减少依赖）

超高并发（>32线程）+ 固定容量？
  → folly::MPMCQueue（更少内存分配开销）
```

---

## 7. 对你的实际提升

### 7.1 在推荐架构组技术栈中的定位

**与 brpc 的结合**：推荐服务中，brpc 的 bthread 回调是在独立的 bthread 中执行的，多个 bthread 并发写入结果时，`tbb::concurrent_queue` 是天然的汇聚点。相比 `std::queue + mutex`，在 8+ bthread 并发回调时吞吐量提升 10x+。

**与 ng-framework DAG 的结合**：ng-framework 的 DAG 执行引擎中，节点间的数据传递可以用 `tbb::concurrent_queue` 替代 channel，特别是当 DAG 节点的执行时间不均匀时，无界队列能避免上游节点因下游慢而阻塞。

**与 jemalloc 的协同**：TBB 的 page 分配默认使用系统 malloc，在 jemalloc 环境下会自动受益于 jemalloc 的线程缓存，减少 page 分配的锁竞争。

### 7.2 关键认知升级

1. **无界 ≠ 无限制**：`tbb::concurrent_queue` 虽然无界，但在推荐服务中必须配合软性限流（`unsafe_size()` 监控 + 丢弃策略），否则内存会无限增长。

2. **approximate_size() 的陷阱**：`size()` 返回的是 `tail_counter - head_counter` 的近似值，在高并发下可能为负数（消费者超前）。不要用它做精确判断，只用于监控。

3. **micro-queue 分片的副作用**：元素的弹出顺序不是严格 FIFO（不同 micro-queue 的元素可能乱序弹出）。如果业务需要严格顺序，需要在元素中携带序号并在消费端重排。

4. **析构时的安全性**：析构 `concurrent_queue` 时，必须确保没有其他线程在 push/pop。在推荐服务的优雅退出流程中，需要先 join 所有生产者线程，再 drain 队列，最后析构。

---

## 参考资料

- [oneTBB GitHub 仓库](https://github.com/oneapi-src/oneTBB)
- [oneTBB 官方文档：concurrent_queue](https://spec.oneapi.io/versions/latest/elements/oneTBB/source/containers/concurrent_queue_cls.html)
- [Issue #697: WASM alignment issue](https://github.com/oneapi-src/oneTBB/issues/697)
- [Issue #846: use-after-free with unsafe iterators](https://github.com/oneapi-src/oneTBB/issues/846)
- [Intel TBB Design Patterns](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-tbb-tutorial.html)
- [Herb Sutter: Lock-Free Programming](https://www.youtube.com/watch?v=c1gO9aB9nbs)
