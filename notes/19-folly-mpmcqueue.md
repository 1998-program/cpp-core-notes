# folly::MPMCQueue — 多生产者多消费者无锁队列深度解析

> **一句话介绍**：`folly::MPMCQueue` 是 Facebook Folly 库中基于 ticket-based 算法和 slot 状态机的定容无锁 MPMC 队列，通过消除共享锁竞争，在高并发场景下比 `std::queue + mutex` 吞吐量高出数倍。

---

## 1. 核心数据结构与实现原理

### 1.1 整体布局

```
MPMCQueue<T>
├── capacity_           // 队列容量（固定，不可动态扩展）
├── pushTicket_         // 原子写入 ticket，每次 write 递增
├── popTicket_          // 原子读取 ticket，每次 read 递增
└── slots_[]            // 大小为 capacity_ 的 Slot 数组（环形）
    └── Slot
        ├── item_       // 存储的元素（原始内存，手动构造/析构）
        ├── spinCount_  // 自旋等待计数
        └── sequenceNumber_ // 状态序号（状态机核心）
```

### 1.2 Ticket-Based 协议

MPMCQueue 采用"号码牌"分配机制，而非传统的头尾指针加 CAS：

```cpp
// 写入时：原子获取一个写 ticket
uint64_t ticket = pushTicket_.fetch_add(1);
size_t slot_idx = ticket % capacity_;

// 该 slot 只有当 sequenceNumber_ == ticket 时才可写入
// 写完后设置 sequenceNumber_ = ticket + 1，解锁读者
```

```cpp
// 读取时：原子获取一个读 ticket
uint64_t ticket = popTicket_.fetch_add(1);
size_t slot_idx = ticket % capacity_;

// 等待 sequenceNumber_ == ticket + 1（写者已完成）
// 读完后设置 sequenceNumber_ = ticket + capacity_，解锁下一轮写者
```

### 1.3 Slot 状态机

每个 Slot 的 `sequenceNumber_` 驱动一个严格的状态循环：

```
初始状态: seq = 0
写者持有: seq == pushTicket (写入中)
可读状态: seq == pushTicket + 1
读者持有: seq == popTicket  (读取中)
可写状态: seq == popTicket + capacity_  (循环复用)
```

这个设计确保了无 ABA 问题：`sequenceNumber_` 单调递增，每轮 +1，永不回头。

### 1.4 对齐与 False Sharing 规避

```cpp
// Slot 按 cacheline 对齐，防止相邻 slot 互相污染
struct alignas(detail::kFalseSharingRange) Slot {
    // ...
};

// pushTicket_ 和 popTicket_ 各自独占一个 cacheline
alignas(detail::kFalseSharingRange) std::atomic<uint64_t> pushTicket_;
alignas(detail::kFalseSharingRange) std::atomic<uint64_t> popTicket_;
```

---

## 2. 关键 API

### 2.1 阻塞接口

```cpp
folly::MPMCQueue<Task> queue(1024); // 固定容量 1024

// 阻塞写：队列满时自旋等待
queue.write(std::move(task));

// 阻塞读：队列空时自旋等待
Task task;
queue.read(task);
```

### 2.2 非阻塞/限时接口

```cpp
// 非阻塞写：队列满立即返回 false
bool ok = queue.write(std::move(task));  // 重载版本，失败直接返回

// 尝试写：超时版
bool ok = queue.tryWriteUntil(
    std::chrono::steady_clock::now() + std::chrono::milliseconds(10),
    std::move(task)
);

// 非阻塞读
Task task;
bool ok = queue.read(task);  // 队列空立即返回 false

// 仅当队列未满时写入
bool ok = queue.writeIfNotFull(std::move(task));

// 仅当队列非空时读取
bool ok = queue.readIfNotEmpty(task);
```

### 2.3 容量查询

```cpp
size_t cap = queue.capacity();      // 固定容量
ssize_t sz = queue.size();          // 当前元素数（近似值，非精确）
bool empty = queue.isEmpty();       // 是否为空（近似）
bool full = queue.isFull();         // 是否满（近似）
```

> ⚠️ `size()` 仅为估算值，在高并发下存在瞬态不准确，不应用于精确控制。

---

## 3. 性能特性

### 3.1 与主流方案对比

| 方案 | 吞吐量（相对） | 延迟稳定性 | 适用场景 |
|------|----------------|-----------|----------|
| `std::queue + mutex` | 1x（基准） | 差（锁竞争） | 低并发/非 hot path |
| `tbb::concurrent_queue` | ~3x | 中 | 无界，不关心容量 |
| `folly::MPMCQueue` | **~5-8x** | **好（无锁）** | 高并发，已知容量 |
| `folly::UnboundedQueue` | ~4x | 中（分段锁） | 动态容量需求 |
| `DPDK::rte_ring` | ~10x | 极好（SPSC/MPSC） | 内核旁路，纯数据平面 |

实测数据（来自 Folly benchmark，8 core，每队列 32 线程并发）：
- `MPMCQueue`：~50M ops/sec
- `std::queue + mutex`：~8M ops/sec
- `DPDK rte_ring`（非 NUMA 跨节点）：~80M ops/sec

### 3.2 性能关键点

1. **Ticket 竞争替代指针 CAS**：`fetch_add` 是最轻量的原子操作，无 CAS 重试
2. **自旋而非 futex**：短暂等待通过 `std::this_thread::yield()` 或 pause 指令，避免内核切换
3. **内存预分配**：构造时一次性分配所有 slot，运行时零分配，jemalloc 的 arena 对此友好
4. **Cacheline 对齐**：ticket 和 slot 各自独占 cacheline，读写互不干扰

---

## 4. 真实工程场景：推荐系统异步特征预取队列

### 背景

在 ng-framework DAG 计算图中，特征预取（Feature Prefetch）节点需要异步触发远端 KV 拉取，避免阻塞主计算图。典型模式：主线程生成 `PrefetchTask`，若干 IO 线程执行 brpc 异步 RPC。

### 代码示例

```cpp
// 定义预取任务
struct PrefetchTask {
    std::string feature_key;
    uint64_t request_id;
    std::function<void(const FeatureValue&)> callback;
};

// 全局预取队列（推荐场景下并发度 ~32，容量按 QPS * 最大延迟估算）
// 64 * (1ms / 1000ms * QPS) ≈ 64 * 0.001 * 50000 = 3200
static folly::MPMCQueue<PrefetchTask> g_prefetch_queue(4096);

// ========== 生产者：DAG 计算节点 ==========
class FeaturePrefetchNode : public NgFrameworkNode {
public:
    int Execute(NgContext* ctx) override {
        PrefetchTask task;
        task.feature_key = ctx->GetFeatureKey();
        task.request_id  = ctx->request_id();
        task.callback    = [ctx](const FeatureValue& val) {
            ctx->SetFeature(val);
        };

        // writeIfNotFull 避免阻塞 DAG 主线程
        // 队列满时降级为同步 RPC（牺牲延迟，保证正确性）
        if (!g_prefetch_queue.writeIfNotFull(std::move(task))) {
            BVAR_DEFINE(bvar::Adder<int64_t>, prefetch_queue_full_counter);
            prefetch_queue_full_counter << 1;
            // 降级：同步执行
            ExecuteSyncRPC(ctx);
        }
        return 0;
    }
};

// ========== 消费者：IO 线程池 ==========
class PrefetchWorker {
public:
    void Run() {
        PrefetchTask task;
        while (running_.load()) {
            // 10ms 超时，避免线程在退出时卡死
            if (!g_prefetch_queue.tryReadUntil(
                    std::chrono::steady_clock::now() + std::chrono::milliseconds(10),
                    task)) {
                continue;
            }
            // 发起 brpc 异步 RPC
            DoAsyncRPC(task);
        }
    }

private:
    std::atomic<bool> running_{true};
};
```

### 为什么选 MPMCQueue 而非其他方案

- **brpc bthread 环境**：bthread 是 M:N 协程，`std::queue + mutex` 会触发内核锁，导致 bthread 调度抖动；MPMCQueue 的自旋避免了内核切换
- **jemalloc 友好**：队列槽位一次性分配，运行时无动态申请，天然适配 jemalloc 的 thread cache 分配模式
- **容量可估算**：特征预取场景下 QPS 和延迟上限已知，固定容量足够；无需 UnboundedQueue 的分段锁开销

---

## 5. 对你的实际提升

### 5.1 在 brpc 服务中替换 std::queue + mutex

ng-framework 的 DAG 节点之间通过任务队列传递中间结果。当前很多异步 pipeline 使用 `std::queue + mutex`，在 QPS 峰值时互斥锁竞争明显（perf 可见 `futex_wait` 占比高）。换成 `MPMCQueue` 后：

- 消除 futex 竞争，DAG p99 延迟可降低 10~30%
- bthread 调度器不被内核锁打断，任务调度更均匀

### 5.2 与 jemalloc 内存管理的协同

`MPMCQueue` 在构造时通过单次 `new[]` 分配所有 slot，这正好命中 jemalloc 的 tcache（thread cache）大块分配路径。与频繁动态分配的无界队列相比，内存使用更可预测，也便于通过 `MALLOC_CONF=prof:true` 分析内存水位。

### 5.3 异步特征预取与 Protobuf 解码解耦

在推荐请求中，Protobuf 解码（`proto::Arena` + `ParseFromString`）和特征 RPC 拉取可以通过 MPMCQueue 解耦：主线程 parse 完 proto 后立即投入 prefetch 任务，IO 线程并发处理。这样 Protobuf Arena 的生命周期与 RPC 回调的生命周期可以独立管理，不再需要引用计数或 shared_ptr。

### 5.4 可观测性建议

```cpp
// 在 DAG 节点中埋点，监控队列水位
bvar::Status<size_t> queue_size_bvar(
    "prefetch_queue_size", g_prefetch_queue.size());
bvar::Status<bool> queue_full_bvar(
    "prefetch_queue_full", g_prefetch_queue.isFull());
```

---

## 6. 注意事项与常见坑

### 6.1 容量固定，不可动态扩展

```cpp
// ❌ 错误：MPMCQueue 构造后容量不可变
folly::MPMCQueue<Task> q(100);
// q.resize(200);  // 不存在此方法

// ✅ 正确：容量按照高水位预留
// 公式：capacity = max_producer_rate(tasks/s) * max_latency(s) * safety_factor(2~3)
folly::MPMCQueue<Task> q(max_rate * max_latency_sec * 3);
```

### 6.2 阻塞语义的陷阱

```cpp
// ⚠️ write() 会无限自旋等待，队列满时会占满 CPU
// 在 bthread 中直接调用 write() 可能导致调度饥饿
queue.write(task);  // 危险！

// ✅ 推荐：使用 writeIfNotFull 或 tryWriteUntil
if (!queue.writeIfNotFull(std::move(task))) {
    // 降级处理
}
```

### 6.3 内存预分配的代价

```cpp
// MPMCQueue 构造时立即分配全部内存
// 100万容量 * sizeof(Slot) ≈ 100M * ~64B = 6.4GB
folly::MPMCQueue<LargeStruct> q(1000000);  // ⚠️ 立即占用大量内存

// ✅ 对于不确定负载，考虑 folly::UnboundedQueue（分段，按需分配）
// 或者多个小容量 MPMCQueue 做分片
```

### 6.4 size() 不精确，不可用于流量控制

```cpp
// ❌ 错误：依赖 size() 做流量控制
if (queue.size() > threshold) {
    throttle();
}

// ✅ 正确：用专用的 atomic 计数器，或直接捕获 writeIfNotFull 返回值
```

### 6.5 T 必须可移动，析构必须安全

```cpp
// MPMCQueue 内部用 placement new 构造 T，read 时显式析构
// 如果 T 的析构抛异常，行为未定义
// ✅ 确保 T 的析构是 noexcept
struct Task {
    ~Task() noexcept { /* 安全清理 */ }
};
```

---

## 7. 接入方式

### CMake（vcpkg / folly 已安装）

```cmake
find_package(folly REQUIRED)
target_link_libraries(your_target PRIVATE Folly::folly)
```

### Bazel（百度内部 BCLOUD）

```python
cc_library(
    name = "your_lib",
    deps = [
        "//third_party/folly:folly",
    ],
)
```

### 头文件引入

```cpp
#include <folly/MPMCQueue.h>
```

---

## 8. 总结

`folly::MPMCQueue` 是 MPMC 场景下的工程级利器，其 ticket-based + slot 状态机设计在正确性和性能之间取得了极佳平衡：

- **适合**：高 QPS 的任务分发、特征预取、异步 pipeline 解耦，容量可预估的场景
- **不适合**：动态负载、超大容量（内存代价高）、需要 FIFO 严格保序的单线程场景（开销不必要）
- **与推荐栈的契合点**：brpc 无锁调度 + jemalloc 大块分配 + ng-framework DAG 节点间通信，三者结合后能让预取延迟降低一个量级

掌握 MPMCQueue 的底层机制，有助于在 DAG 节点设计时正确选型，避免引入不必要的锁竞争。
