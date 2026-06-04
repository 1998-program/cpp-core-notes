# folly::ConcurrentHashMap —— 分段锁并发哈希表深度解析

> 版本参考：folly 2024.x，源码路径 `folly/concurrency/ConcurrentHashMap.h`  
> 作者：C++ 核心笔记系列 #21 | 日期：2026-06-04

---

## 目录

1. [模块概览](#1-模块概览)
2. [核心架构](#2-核心架构)
3. [源码剖析](#3-源码剖析)
4. [并发安全机制](#4-并发安全机制)
5. [性能分析](#5-性能分析)
6. [工程实践](#6-工程实践)
7. [典型应用场景](#7-典型应用场景)
8. [总结](#8-总结)

---

## 1. 模块概览

### 1.1 设计背景

`folly::ConcurrentHashMap` 是 Facebook 开源的 Folly 库中提供的线程安全哈希表实现，位于 `folly/concurrency/` 目录下。它以 **Java `ConcurrentHashMap` 的设计哲学** 为蓝本，结合 C++ 的内存模型和现代 CPU 架构特性，提供了一个在高并发场景下性能优秀、接口友好的键值存储容器。

与同库的 `folly::AtomicHashMap` 相比，`ConcurrentHashMap` 做出了不同的设计权衡：

| 特性 | ConcurrentHashMap | AtomicHashMap |
|------|-------------------|---------------|
| 容量 | 动态扩容 | 固定容量，初始化时确定 |
| 锁机制 | 分段锁（Bucket-level lock） | 无锁（CAS + 线性探测） |
| 键类型支持 | 任意可哈希类型 | 整数或简单类型 |
| 删除操作 | 支持真删除 | 仅逻辑删除（tombstone） |
| 迭代器 | 支持稳定迭代 | 有限迭代支持 |
| 内存效率 | 链地址法，有指针开销 | 开放地址法，缓存友好 |

### 1.2 适用场景

- 读写比例均衡或写多读少的共享状态管理
- 需要运行时动态扩容的缓存结构
- 键类型复杂（如 `std::string`、自定义对象）
- 需要支持范围迭代或快照遍历

### 1.3 头文件与命名空间

```cpp
#include <folly/concurrency/ConcurrentHashMap.h>

// 主类型别名
using folly::ConcurrentHashMap;

// 基本使用
ConcurrentHashMap<std::string, int> map;
map.insert("hello", 42);
auto it = map.find("hello");
if (it != map.cend()) {
    // it->second == 42
}
```

### 1.4 版本演进

- **folly early 版本**：基础分段锁实现，API 较为原始
- **2019 重构**：引入 `ConcurrentHashMapSIMD` 变体，利用 SSE 指令加速查找
- **2021+ 版本**：改进迭代器稳定性，支持 `assign_if_equal`（CAS 语义的更新）
- **当前版本**：Hazard Pointer 集成，解决迭代时的内存安全问题

---

## 2. 核心架构

### 2.1 分段锁设计

`ConcurrentHashMap` 的核心思想是将整个哈希表划分为若干个**独立的子段（Segment）**，每个子段拥有独立的锁。对某个键的操作只需锁住对应的子段，不同子段上的操作可以完全并行。

```
┌─────────────────────────────────────────────────────────┐
│                  ConcurrentHashMap<K, V>                 │
│                                                         │
│  segments_[0]     segments_[1]     segments_[N-1]       │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐        │
│  │ mutex_   │     │ mutex_   │     │ mutex_   │        │
│  │ buckets_ │     │ buckets_ │     │ buckets_ │        │
│  │  [0]     │     │  [0]     │     │  [0]     │        │
│  │  [1]─────┼─►  │  [1]     │     │  [1]     │        │
│  │  ...     │     │  ...     │     │  ...     │        │
│  │  [M]     │     │  [M]     │     │  [M]     │        │
│  └──────────┘     └──────────┘     └──────────┘        │
│                                                         │
│  每个 Segment 内部是独立的链地址法哈希表                   │
└─────────────────────────────────────────────────────────┘
```

**段数（segment count）** 的选择至关重要：
- 默认段数为 `hardware_concurrency() * 4`，通常是 CPU 核数的 4 倍
- 段数必须是 2 的幂，以便用位运算快速定位段索引
- 段数过少：锁竞争激烈；段数过多：内存开销增大

```cpp
// 源码路径：folly/concurrency/detail/ConcurrentHashMap-detail.h
// 段索引计算
size_t getSegment(size_t hash) const {
    return (hash >> (sizeof(size_t) * 8 - segmentPower_)) & segmentMask_;
}
```

### 2.2 内存布局

每个 Segment 内部采用**链地址法（Chaining）**处理哈希冲突，桶数组指向单链表节点：

```
Segment:
  mutex_: SharedMutex (8 bytes)
  buckets_: std::atomic<BucketRoot*>
  size_: std::atomic<size_t>
  capacity_: size_t

BucketRoot[N]:          NodeT链表:
  ┌──────────┐           ┌────────────┐
  │ next_ ───┼──────────►│ key_       │
  └──────────┘           │ value_     │
  ┌──────────┐           │ next_ ─────┼──► ...
  │ next_ ───┼──► null   └────────────┘
  └──────────┘
  ...
```

**内存分配细节：**

```cpp
// NodeT 的内存布局（简化）
template <typename KeyType, typename ValueType>
struct NodeT {
    std::atomic<NodeT*> next_{nullptr};
    // 使用 aligned storage 避免不必要的构造
    aligned_storage_t<sizeof(value_type)> item_;

    // 实际的 key/value 通过 placement new 构造
    value_type* getItem() {
        return reinterpret_cast<value_type*>(&item_);
    }
};
```

### 2.3 扩容机制

当负载因子（load factor）超过阈值（默认 1.05）时，触发**单段扩容**：

```
扩容流程：
1. 对目标 Segment 获取写锁
2. 分配新的 bucket 数组（容量翻倍）
3. 遍历所有旧 bucket 的链表节点，rehash 到新 bucket
4. 原子地替换 buckets_ 指针
5. 释放旧 bucket 数组（延迟回收，等待读者退出）
```

关键：扩容是**逐段进行**的，不会锁住整个表。正在访问其他段的线程不受影响。

---

## 3. 源码剖析

### 3.1 关键数据结构

```cpp
// folly/concurrency/ConcurrentHashMap.h （简化版）

template <
    typename KeyType,
    typename ValueType,
    typename HashFn = std::hash<KeyType>,
    typename KeyEqual = std::equal_to<KeyType>,
    typename Allocator = std::allocator<uint8_t>,
    uint8_t ShardBits = 8,  // 2^8 = 256 个段
    template <typename> class Atom = std::atomic,
    class Mutex = folly::SharedMutex>
class ConcurrentHashMap {
public:
    using key_type    = KeyType;
    using mapped_type = ValueType;
    using value_type  = std::pair<const KeyType, ValueType>;
    using size_type   = std::size_t;

    // 迭代器类型（包装内部 hazard pointer 保护的迭代器）
    class ConstIterator;

private:
    // 每个 Segment 的实现
    using SegmentT = detail::ConcurrentHashMapSegment<
        KeyType, ValueType, ShardBits, HashFn, KeyEqual, Allocator, Atom, Mutex>;

    // 段数组，使用 aligned storage 避免 false sharing
    // 每个 Segment 对齐到 cache line（通常 64 bytes）
    alignas(folly::hardware_destructive_interference_size)
    SegmentT segments_[NumShards];

    static constexpr size_t NumShards = (1 << ShardBits);

    HashFn hasher_;
};
```

```cpp
// Segment 内部结构
template <typename K, typename V, ...>
class ConcurrentHashMapSegment {
private:
    // 读写锁，保护整个段
    Mutex mutex_;

    // bucket 数组的原子指针（支持 RCU 风格的无锁读）
    std::atomic<BucketRoot*> buckets_;

    // 当前元素数量
    std::atomic<size_t> element_count_{0};

    // 当前 bucket 数量
    size_t bucket_count_;

    // 负载因子阈值
    static constexpr float kLoadFactor = 1.05f;
};
```

### 3.2 插入流程

```cpp
// insert 操作的完整流程
template <typename K, typename V, ...>
std::pair<ConstIterator, bool>
ConcurrentHashMap<K, V, ...>::insert(const key_type& k, const mapped_type& v) {
    // 1. 计算哈希值
    auto hash = hasher_(k);

    // 2. 定位到对应 Segment（高位 bits 作为段索引）
    auto segment_idx = (hash >> (64 - ShardBits)) & (NumShards - 1);
    auto& segment = segments_[segment_idx];

    // 3. 委托给 Segment 处理
    return segment.insert(k, v, hash);
}

// Segment 内部的 insert
std::pair<ConstIterator, bool>
ConcurrentHashMapSegment::insert(const K& k, const V& v, size_t hash) {
    // 4. 加写锁
    std::unique_lock<Mutex> lock(mutex_);

    // 5. 计算桶索引（低位 bits）
    auto bucket_idx = hash & (bucket_count_ - 1);
    auto* bucket = &buckets_.load(std::memory_order_relaxed)[bucket_idx];

    // 6. 遍历链表，检查是否已存在
    for (auto* node = bucket->load(std::memory_order_relaxed);
         node != nullptr;
         node = node->next_.load(std::memory_order_relaxed)) {
        if (key_equal_(node->getItem()->first, k)) {
            // key 已存在，返回 false
            return {makeIterator(node), false};
        }
    }

    // 7. 分配新节点
    auto* new_node = allocateNode(k, v);

    // 8. 头插法插入链表
    new_node->next_.store(
        bucket->load(std::memory_order_relaxed),
        std::memory_order_relaxed);
    bucket->store(new_node, std::memory_order_release);  // release 保证可见性

    // 9. 更新计数，检查是否需要扩容
    auto count = element_count_.fetch_add(1, std::memory_order_relaxed) + 1;
    if (count > bucket_count_ * kLoadFactor) {
        rehash(bucket_count_ * 2);  // 仍在写锁保护下
    }

    return {makeIterator(new_node), true};
}
```

### 3.3 查找与删除

**查找（find）** —— 只需读锁，高并发下读不阻塞读：

```cpp
ConstIterator ConcurrentHashMapSegment::find(const K& k, size_t hash) const {
    // 1. 加读锁（SharedMutex 允许多读者并发）
    std::shared_lock<Mutex> lock(mutex_);

    auto bucket_idx = hash & (bucket_count_ - 1);
    auto* buckets = buckets_.load(std::memory_order_acquire);  // acquire 读
    auto* node = buckets[bucket_idx].load(std::memory_order_relaxed);

    // 2. 线性遍历链表
    while (node != nullptr) {
        if (key_equal_(node->getItem()->first, k)) {
            // 3. 找到节点，通过 Hazard Pointer 保护，防止迭代器失效
            return makeIterator(node, std::move(lock));
        }
        node = node->next_.load(std::memory_order_relaxed);
    }

    return cend();
}
```

**删除（erase）** —— 需要写锁，从链表摘除节点：

```cpp
size_t ConcurrentHashMapSegment::erase(const K& k, size_t hash) {
    std::unique_lock<Mutex> lock(mutex_);

    auto bucket_idx = hash & (bucket_count_ - 1);
    auto* bucket = &buckets_.load(std::memory_order_relaxed)[bucket_idx];
    auto* prev = bucket;  // 前驱指针（实际是 atomic<NodeT*>*）

    for (auto* node = bucket->load(std::memory_order_relaxed);
         node != nullptr;) {
        auto* next = node->next_.load(std::memory_order_relaxed);
        if (key_equal_(node->getItem()->first, k)) {
            // 从链表中摘除
            prev->store(next, std::memory_order_release);
            element_count_.fetch_sub(1, std::memory_order_relaxed);

            // 延迟释放：节点可能被其他线程的迭代器引用
            // 使用 Hazard Pointer 回收机制，确保安全释放
            scheduleForDeletion(node);
            return 1;
        }
        prev = &node->next_;
        node = next;
    }
    return 0;  // 未找到
}
```

**条件更新（assign_if_equal）** —— CAS 语义：

```cpp
// 仅当 value == expected 时才更新为 desired（原子的 compare-and-swap）
bool assign_if_equal(
    const key_type& k,
    const mapped_type& expected,
    const mapped_type& desired) {

    auto hash = hasher_(k);
    auto& segment = getSegment(hash);

    std::unique_lock<Mutex> lock(segment.mutex_);
    auto* node = segment.findNode(k, hash);
    if (node && node->getItem()->second == expected) {
        node->getItem()->second = desired;
        return true;
    }
    return false;
}
```

### 3.4 迭代器设计

`ConcurrentHashMap` 的迭代器是其最复杂的部分，需要同时解决：
1. 迭代期间其他线程可能删除节点
2. 迭代期间可能发生扩容（bucket 数组重分配）
3. 需要支持跨段迭代

```cpp
class ConstIterator {
public:
    // 迭代器内部状态
    struct State {
        const ConcurrentHashMap* map_;
        size_t segment_idx_;           // 当前所在段
        size_t bucket_idx_;            // 当前桶索引
        NodeT* node_;                  // 当前节点
        hazptr_holder<Atom> hazptr_;   // Hazard Pointer，保护 node_ 不被释放
        std::shared_lock<Mutex> lock_; // 持有当前段的读锁
    };

    reference operator*() const {
        return *state_.node_->getItem();
    }

    ConstIterator& operator++() {
        // 移动到链表下一节点
        auto* next = state_.node_->next_.load(std::memory_order_acquire);
        if (next) {
            // 更新 Hazard Pointer 到新节点
            state_.hazptr_.reset(next);
            state_.node_ = next;
        } else {
            // 当前桶遍历完，移到下一个非空桶
            advanceBucket();
        }
        return *this;
    }

private:
    void advanceBucket() {
        // 在持有读锁的情况下，安全地扫描后续桶
        while (true) {
            ++state_.bucket_idx_;
            if (state_.bucket_idx_ >= state_.map_->getBucketCount(state_.segment_idx_)) {
                // 当前段遍历完，移到下一段
                state_.lock_.unlock();
                advanceSegment();
                return;
            }
            auto* node = /* 当前桶头节点 */;
            if (node) {
                state_.hazptr_.reset(node);
                state_.node_ = node;
                return;
            }
        }
    }
};
```

**迭代器的内存安全保证：**

```cpp
// Hazard Pointer 的使用确保：
// 1. 迭代器持有 node_ 的 hazard pointer 后，该节点不会被 free
// 2. 即使其他线程调用 erase 删除了该节点，内存不会立即释放
// 3. 等到所有 hazard pointer 都不再引用该节点时，才真正释放内存

// 使用示例：安全的并发迭代
ConcurrentHashMap<int, std::string> map;
// 在一个线程插入数据
// ...

// 在另一个线程安全迭代
for (auto it = map.cbegin(); it != map.cend(); ++it) {
    // it->first, it->second 始终有效
    // 即使此时其他线程正在删除某些 key
    process(it->first, it->second);
}
```

---

## 4. 并发安全机制

### 4.1 分段锁 vs. 无锁对比

#### 分段锁方案（ConcurrentHashMap）

```cpp
// 写操作：独占单个段
void writeExample(ConcurrentHashMap<int, int>& map, int k, int v) {
    // 内部：lock(segments_[k_hash >> ShardBits].mutex_)
    map.insert_or_assign(k, v);
    // 内部：unlock
}

// 读操作：共享单个段
int readExample(const ConcurrentHashMap<int, int>& map, int k) {
    // 内部：shared_lock(segments_[k_hash >> ShardBits].mutex_)
    auto it = map.find(k);
    // 内部：返回时迭代器持有 shared_lock
    return it != map.cend() ? it->second : -1;
}
```

**优点：**
- 锁粒度细，竞争概率 = 1/NumShards（默认 256 分之一）
- 支持复杂值类型（non-trivially-copyable）
- 真正的删除（不需要 tombstone）
- 动态扩容，无需预分配容量

**缺点：**
- 持有锁期间不能做耗时操作（否则阻塞其他线程）
- 锁本身有开销（即使无竞争时 mutex 的 acquire/release 约 10-20ns）
- 迭代器持有读锁，迭代期间写入同一段会被阻塞

#### 无锁方案（AtomicHashMap）

```cpp
// 无锁：完全依赖 CAS 操作
void writeNoLock(AtomicHashMap<int, int>& map, int k, int v) {
    // 内部：linear probing + CAS
    map.insert({k, v});
}
```

**对比总结：**

| 维度 | ConcurrentHashMap (分段锁) | AtomicHashMap (无锁) |
|------|--------------------------|---------------------|
| 写延迟（无竞争） | ~20-50ns（锁开销） | ~5-15ns（纯 CAS） |
| 写延迟（高竞争） | 线性退化，但有界 | 可能 CAS 反复失败 |
| 扩容 | 支持 | 不支持 |
| 真删除 | 支持 | 不支持（tombstone） |
| 高负载性能 | 稳定 | 显著下降（探测距离增大）|
| 键类型 | 任意 | 受限（需要 trivially copyable）|

### 4.2 内存顺序保证

`ConcurrentHashMap` 使用了精心设计的内存顺序组合：

```cpp
// 插入时：store 使用 release，保证 key/value 写入对读者可见
bucket->store(new_node, std::memory_order_release);

// 查找时：load 使用 acquire，保证能看到 release 之前的所有写入
auto* node = bucket->load(std::memory_order_acquire);

// 计数器：使用 relaxed，只需最终一致性
element_count_.fetch_add(1, std::memory_order_relaxed);

// 桶数组指针更新（扩容时）：
// 写端（扩容，持有写锁）：
new_buckets_ptr.store(new_buckets, std::memory_order_release);
// 读端（无锁路径）：
auto* buckets = buckets_.load(std::memory_order_acquire);
```

**Release-Acquire 语义的作用：**

```
线程 A（写入 k=1）                线程 B（查找 k=1）
─────────────────────             ─────────────────
node->value = 42                  
node->key = 1                     
bucket.store(node, release) ──────► bucket.load(acquire)
                                   // 此时 B 看到的 node->value 一定是 42
                                   // acquire 保证了 release 之前的所有写入可见
```

### 4.3 Hazard Pointer 集成

为防止迭代器持有的节点指针在节点被删除后悬空（use-after-free），`ConcurrentHashMap` 集成了 `folly::hazptr`：

```cpp
#include <folly/synchronization/Hazptr.h>

// 迭代器获取节点时，先注册 hazard pointer
ConstIterator makeIterator(NodeT* node) {
    hazptr_holder<Atom> hp;
    // 循环：先 load 节点指针，再通过 hazard pointer 保护，再验证指针未变
    while (true) {
        auto* n = bucket->load(std::memory_order_acquire);
        hp.reset_protection(n);  // 声明：我正在使用 n，不要释放它
        // 验证：在我保护期间，节点未被替换
        if (bucket->load(std::memory_order_acquire) == n) {
            return ConstIterator(n, std::move(hp));
        }
        // 节点已被替换，重试
    }
}

// 删除节点时，不立即 free，而是放入退休队列
void scheduleForDeletion(NodeT* node) {
    node->retire();  // 等所有 hazard pointer 都不再引用时，才真正释放
}
```

---

## 5. 性能分析

### 5.1 Benchmark 数据

以下数据基于 folly 官方 benchmark（`folly/concurrency/test/ConcurrentHashMapBench.cpp`），测试环境：

- CPU：Intel Xeon E5-2680 v4 @ 2.40GHz，14 核，28 线程
- 内存：128GB DDR4
- 编译：GCC 11，`-O3 -march=native`
- 测试规模：10M 条目，均匀分布键

#### 读密集场景（95% read / 5% write）

| 容器 | 线程数 | 吞吐量 (Mops/s) | P99 延迟 (ns) |
|------|--------|----------------|---------------|
| `ConcurrentHashMap` | 1 | 28.4 | 45 |
| `ConcurrentHashMap` | 8 | 187.3 | 62 |
| `ConcurrentHashMap` | 28 | 521.8 | 95 |
| `AtomicHashMap` | 1 | 35.2 | 28 |
| `AtomicHashMap` | 8 | 241.5 | 38 |
| `AtomicHashMap` | 28 | 498.2 | 105 |
| `std::unordered_map`+mutex | 28 | 12.4 | 8200 |
| `absl::flat_hash_map`+mutex | 28 | 15.1 | 6800 |

#### 写密集场景（50% read / 50% write）

| 容器 | 线程数 | 吞吐量 (Mops/s) | P99 延迟 (ns) |
|------|--------|----------------|---------------|
| `ConcurrentHashMap` | 1 | 18.7 | 68 |
| `ConcurrentHashMap` | 8 | 98.4 | 85 |
| `ConcurrentHashMap` | 28 | 267.3 | 130 |
| `AtomicHashMap` | 1 | 22.1 | 45 |
| `AtomicHashMap` | 8 | 89.3 | 78 |
| `AtomicHashMap` | 28 | 178.5 | 280 |

**观察：**
- 在读多写少时，`AtomicHashMap` 在低并发下略优（无锁优势）
- 随着写比例上升，`ConcurrentHashMap` 的稳定性更好
- 28 线程高写压力下，`ConcurrentHashMap` 吞吐量超过 `AtomicHashMap`（后者 CAS 冲突严重）

#### 扩容场景性能

```
初始容量 1024，插入 10M 元素（触发多次 rehash）：

ConcurrentHashMap: 平均插入 52ns（含 rehash 均摊）
AtomicHashMap:     初始容量不足时直接抛出异常，需预分配

说明：ConcurrentHashMap 的分段 rehash 使得最差情况下
      只有 1/256 的请求会遇到扩容延迟（约 500-2000ns），
      而非全局停顿。
```

### 5.2 与主要竞品对比

```cpp
// 测试代码框架
#include <folly/concurrency/ConcurrentHashMap.h>
#include <folly/AtomicHashMap.h>
#include <absl/container/flat_hash_map.h>
#include <tbb/concurrent_hash_map.h>
#include <benchmark/benchmark.h>

// 测试：并发查找性能
static void BM_Find_ConcurrentHashMap(benchmark::State& state) {
    folly::ConcurrentHashMap<int64_t, int64_t> map;
    // 预填充 1M 条目
    for (int i = 0; i < 1'000'000; ++i) map.insert(i, i * 2);

    for (auto _ : state) {
        auto it = map.find(state.range(0) % 1'000'000);
        benchmark::DoNotOptimize(it);
    }
}

static void BM_Find_TBB(benchmark::State& state) {
    tbb::concurrent_hash_map<int64_t, int64_t> map;
    for (int i = 0; i < 1'000'000; ++i) {
        tbb::concurrent_hash_map<int64_t, int64_t>::accessor a;
        map.insert(a, {i, i * 2});
    }

    for (auto _ : state) {
        tbb::concurrent_hash_map<int64_t, int64_t>::const_accessor a;
        bool found = map.find(a, state.range(0) % 1'000'000);
        benchmark::DoNotOptimize(found);
    }
}
```

**综合对比表：**

| 特性 | folly::ConcurrentHashMap | folly::AtomicHashMap | absl::flat_hash_map | tbb::concurrent_hash_map | std::unordered_map |
|------|--------------------------|----------------------|---------------------|-------------------------|--------------------|
| 线程安全 | ✅ 内置 | ✅ 内置 | ❌ 需外部锁 | ✅ 内置 | ❌ 需外部锁 |
| 动态扩容 | ✅ | ❌ | ✅ | ✅ | ✅ |
| 无锁读 | 部分（持 shared_lock）| ✅ | N/A | ❌（全程加锁）| N/A |
| 真删除 | ✅ | ❌ | ✅ | ✅ | ✅ |
| 内存效率 | 中（链表指针）| 高（开放地址）| 极高（平坦布局）| 中 | 低 |
| 复杂键支持 | ✅ | 受限 | ✅ | ✅ | ✅ |
| 单线程性能 | 中 | 高 | 极高 | 低（锁开销）| 高 |
| 32线程读吞吐 | **521 Mops/s** | 498 Mops/s | ~60 Mops/s | 380 Mops/s | ~8 Mops/s |

---

## 6. 工程实践

### 6.1 何时选 ConcurrentHashMap vs. AtomicHashMap

**选 `ConcurrentHashMap` 当：**

```cpp
// 1. 键类型是字符串或复杂对象
ConcurrentHashMap<std::string, UserProfile> user_cache;

// 2. 需要运行时扩容（不知道数据量上限）
ConcurrentHashMap<int64_t, SessionData> active_sessions;
// 随着用户增长，map 自动扩容，无需预分配

// 3. 需要删除操作（如 LRU 淘汰）
ConcurrentHashMap<CacheKey, CacheValue> lru_store;
auto evict = [&](const CacheKey& k) {
    lru_store.erase(k);  // 真实删除，不留 tombstone
};

// 4. 写操作比例较高（>10%）
// 分段锁在高写压力下比无锁 CAS 更稳定
```

**选 `AtomicHashMap` 当：**

```cpp
// 1. 数据量固定已知，且键是整数
AtomicHashMap<int32_t, std::atomic<int64_t>> counters(1024);

// 2. 极致读性能，写操作极少
// 例如：配置项缓存，启动时写入，运行时只读

// 3. 不需要删除操作
// tombstone 不影响你的使用场景
```

**决策树：**

```
需要线程安全的哈希表？
├── 键是整数 & 容量固定 & 几乎只读？
│   └── → folly::AtomicHashMap
├── 写比例 > 5% 或键类型复杂？
│   └── → folly::ConcurrentHashMap
├── 单线程 & 性能敏感？
│   └── → absl::flat_hash_map（配合外部 RWLock）
└── 需要 Java-style 事务 accessor？
    └── → tbb::concurrent_hash_map
```

### 6.2 常见陷阱

#### 陷阱 1：迭代器持有读锁导致死锁

```cpp
// ❌ 错误：在迭代同一 segment 时尝试写入
ConcurrentHashMap<int, int> map;
map.insert(1, 1);

for (auto it = map.cbegin(); it != map.cend(); ++it) {
    if (it->first == 1) {
        // 这里 it 持有 segment[x] 的读锁
        // insert 需要 segment[x] 的写锁 → 死锁！
        map.insert(it->first + 100, it->second);  // 可能死锁
    }
}

// ✅ 正确：先收集，再修改
std::vector<std::pair<int,int>> to_insert;
for (auto it = map.cbegin(); it != map.cend(); ++it) {
    to_insert.emplace_back(it->first + 100, it->second);
}
for (auto& [k, v] : to_insert) {
    map.insert(k, v);
}
```

#### 陷阱 2：长时间持有迭代器阻塞写入

```cpp
// ❌ 问题：迭代器持有 shared_lock，长时间处理阻塞同段的写操作
for (auto it = map.cbegin(); it != map.cend(); ++it) {
    // 这里进行了耗时操作（如网络 IO、文件操作）
    expensiveOperation(it->second);  // 期间所有对此 segment 的写入被阻塞！
}

// ✅ 改进：先拷贝出来，再处理
std::vector<std::pair<int,int>> snapshot;
snapshot.reserve(map.size());
for (auto it = map.cbegin(); it != map.cend(); ++it) {
    snapshot.emplace_back(it->first, it->second);
}
// 释放所有迭代器（读锁释放）之后，再进行耗时处理
for (auto& [k, v] : snapshot) {
    expensiveOperation(v);
}
```

#### 陷阱 3：误用 find 后的迭代器

```cpp
// ❌ 问题：find 返回的迭代器生命期
ConcurrentHashMap<int, std::string> map;
map.insert(1, "hello");

auto it = map.find(1);
// it 持有 segment 的 shared_lock

{
    // 另一个线程（或当前线程）试图写入同 segment
    // 如果在同一线程：对同一 segment 的写会 = 等待（可能 deadlock 取决于 Mutex 类型）
}

it->second;  // OK，迭代器仍有效

// 显式释放迭代器
it = map.cend();  // 释放 shared_lock

// 或者用局部作用域
if (auto it2 = map.find(1); it2 != map.cend()) {
    process(it2->second);
}  // it2 在此析构，释放锁
```

#### 陷阱 4：size() 的近似性

```cpp
// ConcurrentHashMap 的 size() 是各段 atomic 计数的总和
// 不持有任何锁，所以是近似值（可能因为并发操作而瞬间不准）
size_t sz = map.size();  // 近似值，不是精确快照

// 对 size() 的正确使用：
// ✅ 用于容量估算、日志输出
LOG(INFO) << "map size: ~" << map.size();

// ❌ 不要依赖 size() 做精确控制
if (map.size() == 0) {
    // 此时 map 可能已经被另一个线程插入了数据！
}
```

#### 陷阱 5：自定义哈希函数不满足要求

```cpp
// ❌ 不好的哈希函数：碰撞严重，退化为 O(N)
struct BadHash {
    size_t operator()(const std::string& s) const {
        return s.length();  // 所有同长度字符串映射到同一桶
    }
};
ConcurrentHashMap<std::string, int, BadHash> bad_map;

// ✅ 使用高质量哈希函数
// folly 默认使用 FNV-1a 或城市哈希（取决于键类型）
// 对于自定义类型，推荐使用 folly::Hash 或 absl::Hash
struct GoodHash {
    size_t operator()(const MyKey& k) const {
        return folly::hash::hash_combine(k.id, k.name);
    }
};
```

### 6.3 配置优化

#### 段数调优

```cpp
// 默认 ShardBits = 8，即 256 段
// 对于高并发场景，可以增加段数
using HighConcurrencyMap = folly::ConcurrentHashMap<
    std::string,
    int,
    std::hash<std::string>,
    std::equal_to<std::string>,
    std::allocator<uint8_t>,
    10  // ShardBits=10，即 1024 段，适合 100+ 并发线程
>;

// 对于低并发场景，减少段数节省内存
using LowConcurrencyMap = folly::ConcurrentHashMap<
    int,
    int,
    std::hash<int>,
    std::equal_to<int>,
    std::allocator<uint8_t>,
    4   // ShardBits=4，即 16 段，适合 < 8 线程
>;
```

#### 预留容量

```cpp
// 如果大致知道数据量，预先 reserve 避免 rehash
ConcurrentHashMap<int64_t, Data> map;
map.reserve(100'000);  // 预分配约 100K 容量

// reserve 内部：对每个 segment 分配 100K/256 ≈ 390 个 bucket
// 减少初始 rehash 次数
```

#### 自定义内存分配器

```cpp
// 使用 jemalloc 或自定义池分配器
#include <folly/memory/JemallocHugePageAllocator.h>

// 对于大表，使用大页内存减少 TLB miss
using HugePageMap = folly::ConcurrentHashMap<
    int64_t,
    CacheEntry,
    std::hash<int64_t>,
    std::equal_to<int64_t>,
    folly::JemallocHugePageAllocator<uint8_t>  // 自定义分配器
>;
```

#### 使用 SIMD 变体

```cpp
// ConcurrentHashMapSIMD：在 x86 上使用 SSE4.2 的 PCMPESTRI 指令
// 一次比较 16 个字节，加速字符串键的哈希查找
#include <folly/concurrency/ConcurrentHashMap.h>

// ConcurrentHashMapSIMD 与 ConcurrentHashMap API 完全兼容
// 对字符串键的查找性能提升约 15-30%
using FastStringMap = folly::ConcurrentHashMapSIMD<std::string, int>;
```

---

## 7. 典型应用场景

### 7.1 高并发缓存层

```cpp
#include <folly/concurrency/ConcurrentHashMap.h>
#include <optional>
#include <functional>

template <typename K, typename V>
class ConcurrentCache {
public:
    explicit ConcurrentCache(size_t capacity)
        : capacity_(capacity), map_() {
        map_.reserve(capacity);
    }

    std::optional<V> get(const K& key) {
        auto it = map_.find(key);
        if (it != map_.cend()) {
            return it->second;
        }
        return std::nullopt;
    }

    void set(const K& key, V value) {
        // insert_or_assign：key 存在则更新，不存在则插入
        map_.insert_or_assign(key, std::move(value));
    }

    // 带 loader 的 get-or-load 模式
    V getOrLoad(const K& key, std::function<V(const K&)> loader) {
        auto it = map_.find(key);
        if (it != map_.cend()) {
            return it->second;
        }
        // 注意：这里可能有重复 load 的情况（多线程同时 miss）
        // 对于允许重复计算的场景这是 OK 的
        V value = loader(key);
        map_.insert(key, value);  // 如果已被其他线程插入，此次 insert 会失败（返回 false）
        return value;
    }

    size_t size() const { return map_.size(); }

private:
    size_t capacity_;
    folly::ConcurrentHashMap<K, V> map_;
};

// 使用示例
ConcurrentCache<std::string, UserData> user_cache(10000);

// 多线程安全使用
std::vector<std::thread> threads;
for (int i = 0; i < 16; ++i) {
    threads.emplace_back([&user_cache, i]() {
        for (int j = 0; j < 1000; ++j) {
            std::string key = "user_" + std::to_string(i * 1000 + j);
            auto data = user_cache.getOrLoad(key, [](const std::string& k) {
                return loadFromDatabase(k);  // 只在 cache miss 时调用
            });
            process(data);
        }
    });
}
```

### 7.2 分布式计数器

```cpp
// 使用 ConcurrentHashMap 实现线程安全的词频统计
class WordCounter {
public:
    void add(const std::string& word, int64_t count = 1) {
        // assign_if_equal 实现原子自增（CAS 循环）
        while (true) {
            auto it = counts_.find(word);
            if (it == counts_.cend()) {
                // 首次插入
                auto [inserted_it, ok] = counts_.insert(word, count);
                if (ok) return;
                // 竞争失败，重试
                continue;
            }
            int64_t old_val = it->second;
            // CAS 更新：只有当前值仍为 old_val 时才更新
            if (counts_.assign_if_equal(word, old_val, old_val + count)) {
                return;
            }
            // 被其他线程修改了，重试
        }
    }

    std::optional<int64_t> get(const std::string& word) const {
        auto it = counts_.find(word);
        if (it != counts_.cend()) return it->second;
        return std::nullopt;
    }

    // 获取 top-N 词汇（近似，非精确快照）
    std::vector<std::pair<std::string, int64_t>> topN(size_t n) const {
        std::vector<std::pair<std::string, int64_t>> all;
        for (auto it = counts_.cbegin(); it != counts_.cend(); ++it) {
            all.emplace_back(it->first, it->second);
        }
        std::partial_sort(all.begin(),
                          all.begin() + std::min(n, all.size()),
                          all.end(),
                          [](const auto& a, const auto& b) {
                              return a.second > b.second;
                          });
        all.resize(std::min(n, all.size()));
        return all;
    }

private:
    folly::ConcurrentHashMap<std::string, int64_t> counts_;
};
```

### 7.3 连接池管理

```cpp
// 用 ConcurrentHashMap 管理数据库连接池状态
struct ConnectionInfo {
    std::shared_ptr<DbConnection> conn;
    std::chrono::steady_clock::time_point last_used;
    std::atomic<bool> in_use{false};
};

class ConnectionPool {
public:
    std::shared_ptr<DbConnection> acquire(const std::string& host) {
        auto it = pool_.find(host);
        if (it != pool_.cend()) {
            auto& info = it->second;
            bool expected = false;
            if (info.in_use.compare_exchange_strong(expected, true)) {
                info.last_used = std::chrono::steady_clock::now();
                return info.conn;
            }
        }
        // 创建新连接
        auto conn = createConnection(host);
        pool_.insert_or_assign(host, ConnectionInfo{conn,
            std::chrono::steady_clock::now()});
        return conn;
    }

    void release(const std::string& host) {
        auto it = pool_.find(host);
        if (it != pool_.cend()) {
            it->second.in_use.store(false);
        }
    }

    // 清理超时连接
    void evictExpired(std::chrono::seconds timeout) {
        auto now = std::chrono::steady_clock::now();
        std::vector<std::string> to_remove;
        for (auto it = pool_.cbegin(); it != pool_.cend(); ++it) {
            if (!it->second.in_use &&
                now - it->second.last_used > timeout) {
                to_remove.push_back(it->first);
            }
        }
        for (const auto& key : to_remove) {
            pool_.erase(key);
        }
    }

private:
    folly::ConcurrentHashMap<std::string, ConnectionInfo> pool_;
};
```

### 7.4 事件路由表

```cpp
// 高性能事件分发：多写者注册 handler，多读者查找并调用
using EventHandler = std::function<void(const Event&)>;

class EventRouter {
public:
    void subscribe(int event_type, EventHandler handler) {
        handlers_.insert_or_assign(event_type, std::move(handler));
    }

    void unsubscribe(int event_type) {
        handlers_.erase(event_type);
    }

    void dispatch(const Event& event) {
        auto it = handlers_.find(event.type);
        if (it != handlers_.cend()) {
            it->second(event);  // 调用 handler
        }
    }

private:
    folly::ConcurrentHashMap<int, EventHandler> handlers_;
};

// 使用场景：服务路由表
EventRouter router;
router.subscribe(EVENT_USER_LOGIN, [](const Event& e) {
    handleUserLogin(e);
});
router.subscribe(EVENT_ORDER_CREATED, [](const Event& e) {
    handleOrderCreated(e);
});

// 多线程并发分发事件
std::vector<std::thread> workers;
for (int i = 0; i < 8; ++i) {
    workers.emplace_back([&router]() {
        while (auto* event = eventQueue.pop()) {
            router.dispatch(*event);  // 线程安全
        }
    });
}
```

---

## 8. 总结

### 8.1 设计要点回顾

`folly::ConcurrentHashMap` 的核心设计哲学是**以可控的锁开销换取通用性和稳定性**：

1. **分段锁**：将竞争范围缩小到 1/N（默认 N=256），使锁竞争概率极低
2. **链地址法**：支持任意键类型，支持真正的删除操作
3. **SharedMutex**：读操作不互斥，多读者可并发访问同一段
4. **Hazard Pointer**：解决迭代器与删除操作之间的内存安全问题
5. **分段扩容**：扩容影响范围最小化，不阻塞全局操作
6. **Release-Acquire 内存顺序**：精确控制可见性，避免不必要的 fence

### 8.2 选型建议

```
                    ┌── 容量固定 & 键为整数 & 读多写极少 → AtomicHashMap
                    │
是否需要并发访问？──── 是 ──── 需要动态扩容/复杂键 → ConcurrentHashMap
                    │          │
                    │          └── 需要事务级 accessor → tbb::concurrent_hash_map
                    │
                    └── 否 → absl::flat_hash_map（单线程最优）
```

### 8.3 关键数字速查

| 指标 | 数值 |
|------|------|
| 默认段数 | 256（ShardBits=8） |
| 负载因子阈值 | 1.05 |
| 扩容倍数 | 2x |
| 无竞争 find 延迟 | ~30-50ns |
| 无竞争 insert 延迟 | ~50-80ns |
| 32线程读吞吐量（10M条目）| ~520 Mops/s |
| 头文件路径 | `folly/concurrency/ConcurrentHashMap.h` |
| 详细实现路径 | `folly/concurrency/detail/ConcurrentHashMap-detail.h` |

### 8.4 延伸阅读

- [folly 官方文档](https://github.com/facebook/folly/blob/main/folly/concurrency/ConcurrentHashMap.h)
- Doug Lea, *A Scalable, Correct Time-Stamped Stack* — 并发容器设计经典论文
- Paul McKenney, *Is Parallel Programming Hard?* — 内存模型与并发原语深度解析
- Cliff Click, *A Lock-Free Hash Table* — 无锁哈希表的另一条路
- Facebook Engineering Blog: *Introducing ConcurrentHashMap for C++*

---

*C++ 核心笔记系列 #21 | 2026-06-04*  
*GitHub: https://github.com/1998-program/cpp-core-notes*
