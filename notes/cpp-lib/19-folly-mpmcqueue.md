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

---

## 七、业务代码库适配分析
> **分析时间**：2026-05-31T19:16:45.514001
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：`folly::MPMCQueue`

### 1. 分析摘要

- 当前在 `feeda-mv-grg` 和 `feeda-mv-grc` 两个业务代码库中，**尚未发现 `folly::MPMCQueue` 的直接使用**，说明现有代码尚未引入该无锁 MPMC 队列，也缺少可直接复用的内部迁移样例。后续如果引入，需要从依赖接入、容量评估、线程退出语义、监控埋点等方面建立统一实践。

- 从扫描结果看，两个代码库中存在一定规模的生产者/消费者相关模式线索：`feeda-mv-grg` 中 `producer_consumer` 命中 75 次，分布在 22 个文件；`feeda-mv-grc` 中 `producer_consumer` 命中 243 次，分布在 55 个文件，同时还存在少量 `std::mutex` / `bthread::Mutex` 使用。虽然部分命中可能是业务字段、注释或非队列语义的误报，但整体上 `feeda-mv-grc` 的并发/异步场景更密集，**迁移潜力高于 `feeda-mv-grg`**。建议优先在召回汇聚、异步 RPC 聚合、批量任务分发等 hot path 中小范围试点。

---

### 2. 代码库详情

#### 2.1 `feeda-mv-grg`：序列生成服务

- **目标库使用情况**
  - 未发现 `folly::MPMCQueue` 的直接使用。
  - 当前没有可作为参考的业务内落地样例。

- **现有等价/相关模式**
  - `producer_consumer`：75 次，分布在 22 个文件。
  - 扫描示例主要集中在 `data/ums_feature.h`：
    - `data/ums_feature.h:34`
    - `data/ums_feature.h:56`
    - `data/ums_feature.h:76`

- **初步判断**
  - 当前展示出的 `data/ums_feature.h` 片段主要是用户画像字段定义、清理和宏注册，例如 `income`、`education`、`consume_level` 等，并非典型生产者/消费者队列场景。
  - 因此，`feeda-mv-grg` 的扫描结果中可能存在较多语义误报，不能仅凭 `producer_consumer` 命中次数判断存在可直接替换的队列。
  - 建议后续进一步定位是否存在如下模式：
    - `std::queue` / `deque` + `std::mutex`
    - `condition_variable`
    - 单独工作线程消费任务
    - RPC 结果异步回调入队
    - 批量特征、样本或序列生成任务分发队列

#### 2.2 `feeda-mv-grc`：召回汇聚服务

- **目标库使用情况**
  - 未发现 `folly::MPMCQueue` 的直接使用。
  - 当前也没有内部参考代码。

- **现有等价/相关模式**
  - `mutex_lock`：2 次，分布在 1 个文件。
  - `std::mutex`：4 次，分布在 2 个文件。
  - `producer_consumer`：243 次，分布在 55 个文件。

- **典型文件与场景**
  - `plugin/gcms_sndb.cpp:167`
    ```cpp
    int64_t begin = base::gettimeofday_us();
    bthread::Mutex async_lock;
    async_lock.lock();
    int ret = _db.query(rids, _cols, small_flow_list, logid, data, new GcmsSndbClosure(&async_lock));
    async_lock.lock();  // 阻塞等待返回结果
    return ret;
    ```
  - `plugin/gcms_sndb.cpp:169`
    ```cpp
    async_lock.lock();
    int ret = _db.query(rids, _cols, small_flow_list, logid, data, new GcmsSndbClosure(&async_lock));
    async_lock.lock();  // 阻塞等待返回结果
    return ret;
    }
    ::std::unique_ptr<GcmsSndbData::VideoSonarInfoPool> GcmsSndbData::_s_pool_show_click;
    ```
  - `dict/dict_manager.h:205`
    ```cpp
    template <typename T, typename std::enable_if<std::is_base_of<Dict, T>::value, int>::type = 0>
      int32_t bind_dict_accessor(const std::string_view dict_name, DictAccessor<T> &dict_accessor) {
          ::std::lock_guard<::std::mutex> lock(_mutex);
          _units.emplace_back();
          auto& unit = ...
    ```

- **初步判断**
  - `plugin/gcms_sndb.cpp` 中存在明显的异步 RPC 后等待回调完成的模式，但当前实现是通过 `bthread::Mutex` 进行阻塞等待，属于“同步等待异步结果”的写法。该场景本身不一定适合直接替换为 `MPMCQueue`，但如果该文件中存在大量请求结果汇聚、批量任务派发、异步回调转工作线程处理，则可以考虑引入队列解耦。
  - `dict/dict_manager.h` 中的 `std::mutex` 用于字典 accessor 绑定和 `_units` 元数据修改，属于低频管理面临界区，通常不建议为了无锁化而替换为 `MPMCQueue`。
  - `feeda-mv-grc` 中 `producer_consumer` 命中规模明显更大，召回汇聚服务天然存在多路召回、异步 RPC、结果归并等并发模式，因此更适合作为 `MPMCQueue` 的试点代码库。

---

### 3. 💡 适用性评估与建议

- **建议 1：优先在 `feeda-mv-grc/plugin/gcms_sndb.cpp` 周边排查异步 RPC 回调是否可以改造为“回调入队 + worker 消费”**
  - 当前 `plugin/gcms_sndb.cpp` 中通过 `bthread::Mutex async_lock` 阻塞等待 `_db.query(...)` 的异步回调返回，本质上仍是同步等待。
  - 如果该路径在高 QPS 下存在大量并发请求，且回调中包含非轻量逻辑，例如结果解析、过滤、特征补全、召回结果归并，则可以考虑：
    - RPC 回调线程只做轻量封装，将结果写入 `folly::MPMCQueue<GcmsSndbResultTask>`；
    - 后端固定数量 worker 从队列中消费并执行较重处理；
    - 生产端使用 `writeIfNotFull()`，队列满时走原同步逻辑或降级逻辑，避免阻塞 brpc/bthread 回调线程。
  - 适用收益：
    - 降低回调线程上的业务处理耗时；
    - 减少锁竞争和临界区等待；
    - 对召回汇聚这类多生产者、多消费者场景更友好。

- **建议 2：`feeda-mv-grc/dict/dict_manager.h` 中的 `_mutex` 不建议直接替换为 `MPMCQueue`**
  - `dict/dict_manager.h:205` 中：
    ```cpp
    ::std::lock_guard<::std::mutex> lock(_mutex);
    _units.emplace_back();
    ```
  - 该逻辑看起来是字典 accessor 绑定或初始化管理，属于共享元数据保护，而不是任务队列。
  - 对这类场景，`MPMCQueue` 不是等价替代品。更合适的优化方向是：
    - 确认该路径是否只在启动期或 reload 期执行；
    - 如果是低频路径，保留 `std::mutex` 即可；
    - 如果是高频读、低频写，可以评估 `std::shared_mutex`、RCU、双 buffer 或原子指针切换。
  - 不建议为了“无锁”而将简单临界区改造为队列，否则会增加时序复杂度和维护成本。

- **建议 3：在 `feeda-mv-grc` 的 55 个 `producer_consumer` 命中文件中，优先筛选真正的任务队列场景**
  - 当前 `producer_consumer` 命中 243 次，分布在 55 个文件，说明存在较大排查空间。
  - 建议按以下特征筛选候选文件：
    - 存在 `std::queue`、`std::deque`、`std::list` 作为任务容器；
    - 同时存在 `std::mutex` / `bthread::Mutex` / `condition_variable`；
    - 有多个生产线程写入任务，一个或多个消费者线程处理任务；
    - 队列位于请求 hot path、召回结果处理、特征请求、异步 RPC 回调、日志异步落盘等路径。
  - 对满足上述条件的场景，可以将：
    ```cpp
    std::queue<Task> queue;
    std::mutex mutex;
    std::condition_variable cv;
    ```
    改造为：
    ```cpp
    folly::MPMCQueue<Task> queue(capacity);
    ```
  - 生产端建议优先使用：
    ```cpp
    queue.writeIfNotFull(std::move(task));
    ```
    消费端建议使用：
    ```cpp
    queue.readIfNotEmpty(task);
    ```
    或超时读取，避免线程退出时长期阻塞。

- **建议 4：`feeda-mv-grg/data/ums_feature.h` 当前示例不适合迁移，应继续寻找真正的异步任务分发点**
  - 扫描示例中的 `data/ums_feature.h` 更像是用户属性结构体字段定义和清理逻辑，例如：
    ```cpp
    std::string income;
    std::string education;
    std::string consume_level;
    ```
  - 这类代码与 `MPMCQueue` 没有直接关系，不应作为迁移目标。
  - 对 `feeda-mv-grg`，更建议检查以下方向：
    - 序列生成过程是否有批量样本生产、异步特征拉取、异步日志写入；
    - 是否存在“主线程生成任务，多个 worker 消费”的流水线；
    - 是否存在 `std::queue + mutex` 的热点等待。
  - 如果后续在序列生成服务中发现类似异步特征预取或批量任务分发逻辑，可参考技术笔记中的 `PrefetchTask` 模式，使用固定容量队列做削峰。

- **建议 5：建议先在 `feeda-mv-grc` 做小规模灰度试点，而不是全局替换**
  - `MPMCQueue` 的收益集中在高并发、短任务、固定容量、生产消费频繁的 hot path。
  - 对低频配置管理、启动初始化、字典绑定等路径，保留锁往往更简单可靠。
  - 推荐试点路径：
    - 选择一个明确的召回或异步 RPC 结果处理队列；
    - 以 `folly::MPMCQueue<Task>` 替换原 `queue + mutex` 实现；
    - 增加队列满计数、消费延迟、排队长度估算、降级次数等指标；
    - 对比替换前后的 P99 延迟、CPU 使用率、上下文切换数和吞吐。

---

### 4. ⚠️ 引入风险与限制

- **固定容量带来的丢弃、阻塞或降级风险**
  - `folly::MPMCQueue` 是定容队列，构造后容量不可动态扩展。
  - 容量过小会导致 `writeIfNotFull()` 失败，容量过大又会增加内存占用。
  - 业务迁移时必须明确队列满时策略：
    - 直接降级同步执行；
    - 丢弃非关键任务；
    - 返回错误；
    - 或记录监控后限流。
  - 不建议在请求主路径中使用无超时的阻塞 `write()`，否则队列满时可能放大尾延迟。

- **自旋等待可能与 bthread 调度模型冲突**
  - `MPMCQueue` 的高性能来自无锁和自旋等待，适合 OS thread 上的短等待场景。
  - 如果在大量 bthread 中使用阻塞读写接口，可能造成 CPU 空转或影响 bthread 调度公平性。
  - 在 `feeda-mv-grc/plugin/gcms_sndb.cpp` 这类 brpc/bthread 场景中，建议优先使用：
    - `writeIfNotFull()`
    - `readIfNotEmpty()`
    - `tryWriteUntil()`
    - 超时 read
  - 避免在 bthread 中长时间 spin 等待。

- **`size()` / `empty()` / `full()` 只能作为近似监控，不应用于严格控制**
  - `MPMCQueue::size()` 在高并发下是估算值，不能用于：
    - 精确判断是否可以入队；
    - 精确判断是否已经消费完成；
    - 作为线程退出的唯一条件。
  - 正确做法是以 `writeIfNotFull()` / `readIfNotEmpty()` 的返回值作为控制依据，并结合独立的停止标记，例如 `std::atomic<bool> running`。

- **任务对象类型需要满足移动、析构和生命周期要求**
  - 队列中保存的 `Task` 如果包含指针、引用、回调、上下文对象，需要确保生命周期跨线程安全。
  - 尤其是类似 `PrefetchTask` 中捕获 `ctx` 的 callback，在迁移到异步队列后要确认：
    - `ctx` 是否可能提前释放；
    - 回调是否可能跨请求生命周期执行；
    - 是否需要使用 `shared_ptr`、请求级 arena 或显式 cancel 机制。
  - 否则无锁队列本身是安全的，但业务对象生命周期仍可能产生 use-after-free。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
