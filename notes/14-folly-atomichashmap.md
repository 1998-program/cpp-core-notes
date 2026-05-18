# #14 · folly::AtomicHashMap — 无锁哈希表设计解析

> **仓库**: [facebook/folly](https://github.com/facebook/folly) · `folly/AtomicHashMap.h`  
> **定位**: 高并发 int32/int64 key 哈希表，find() 完全 wait-free，insert 无全局锁，比 tbb::concurrent_hash_map 快 2~4x

---

## 一句话价值

**write-once、insert 无锁、find wait-free**——为「大量并发读、偶尔写」场景设计，代价是 key 只能是整型、erase 不回收内存、初始容量估不准则性能线性退化。

---

## 核心数据结构

### 两层架构：AHArray + AHMap

```
AtomicHashMap (AHMap)
  └── subMaps_[16]  (std::atomic<SubMap*>)
        ├── [0] AtomicHashArray  ← 主 submap，初始容量
        ├── [1] AtomicHashArray  ← 扩容时懒分配
        ├── [2] AtomicHashArray
        └── ... 最多 16 个
```

- **AHArray**：固定大小连续内存块，线性探测，find() 完全 wait-free
- **AHMap**：包装多个 AHArray，实现有限增长（最多 ~18x 初始容量）
- submap 用 CAS 懒分配：`compare_exchange_strong(nullptr, kLockedPtr_)`

### 关键常量

```cpp
static const uint32_t kNumSubMapBits_    = 4;    // 最多 16 个 submap
static const uint32_t kSecondaryMapBit_  = 1u << 31;
static const uint32_t kSubMapIndexShift_ = 27;   // 高5位编码 submap 编号
static const uintptr_t kLockedPtr_       = 0x88ULL << 48; // CAS 锁哨兵
```

### findAt() 的 32-bit 稳定引用

```
uint32_t idx = encodeIndex(submap_i, cell_j)
              = (submap_i << 27) | cell_j
```

一旦写入，cell 不移动，idx 永久有效——可以当对象 ID 用。

---

## find() 为什么 wait-free

```cpp
// AHArray 线性探测核心（简化）
for (size_t i = 0; i < capacity_; ++i) {
    size_t idx = (hash + i) % capacity_;
    KeyT k = cells_[idx].first.load(std::memory_order_relaxed);
    if (k == kEmptyKey_)  return end();   // 探测到空槽，key 不存在
    if (equalFn_(k, key)) return makeIter(idx);  // 命中
    if (k == kErasedKey_) continue;       // 跳过已删除
}
```

- **纯读路径**：只用 `memory_order_relaxed` load，无 CAS，无 mutex
- **不会 starvation**：探测长度有界（maxLoadFactor 控制）
- 代价：key 写入时要经历 `kLockedKey_ → 真实 key` 两步，find 可能短暂看到 locked 状态，需跳过

---

## insert() 无全局锁原理

```cpp
// 写入一个 cell 的原子序列
1. CAS(cell.key, kEmptyKey_, kLockedKey_)  // 抢占空槽
   ├── 失败 → 别人先写，检查是否 key 碰撞
   └── 成功 → 独占写入权
2. 原地构造 value（placment new）
3. cell.key.store(真实key, memory_order_release)  // 解锁，对 find 可见
```

只在 cell 级别用 CAS，**不需要 bucket 锁或全表锁**。

### 扩容时的 submap 分配

```cpp
bool tryLockMap(unsigned int idx) {
    SubMap* val = nullptr;
    return subMaps_[idx].compare_exchange_strong(
        val, (SubMap*)kLockedPtr_,  // 0x88ULL<<48，非法指针
        std::memory_order_acquire);
}
// 只有一个线程 CAS 成功，负责分配；其他线程自旋等待 ptr != kLockedPtr_
```

---

## 性能数据（官方 benchmark）

8 线程，100万 `<int64_t, int64_t>`，4核 2.5GHz：

| Load Factor | 内存利用率 | Insert (µs) | Find (µs) |
|-------------|-----------|------------|----------|
| 50%         | 50%       | 0.19       | **0.05** |
| 85%         | 85%       | 0.20       | 0.06     |
| 90%         | 90%       | 0.23       | 0.08     |
| 95%         | 95%       | 0.27       | 0.10     |

对比：tbb::concurrent_hash_map 同场景约 0.20~0.40 µs/Find，AHM 快 2~4x。

---

## 关键 API 与使用模式

### 基本用法

```cpp
// 必须预估容量，性能与此强相关
folly::AtomicHashMap<int64_t, MyValue> map(1'000'000);

// insert：key 碰撞返回 false，不覆盖
auto [iter, inserted] = map.insert(42, MyValue{...});
if (!inserted) {
    // key 已存在，iter 指向已有元素
}

// find：wait-free
auto it = map.find(42);
if (it != map.end()) {
    // it->second 是 value
}
```

### findAt() — 32-bit 稳定引用（对象存储场景）

```cpp
// insert 后拿到稳定 index
auto [iter, ok] = map.insert(key, value);
uint32_t idx = iter.getIndex();  // 永久有效，cell 不移动

// 之后直接按 index 查，比 find(key) 还快（跳过 hash 计算）
auto it = map.findAt(idx);
```

**典型场景**：把 idx 存进其他数据结构（如 skip list node），需要 O(1) 回查 value。

### size() 为什么比较慢

```cpp
// ThreadCachedInt：每个线程本地 counter，size() 时聚合
// 需要对每个 submap 加锁+累加所有 TLS counter
size_t sz = map.size();  // 不建议高频调用
```

---

## 使用陷阱

### ❌ 陷阱1：key 类型不是 int32/int64

```cpp
// 不支持 string / 指针 key（需要显式转换）
folly::AtomicHashMap<std::string, int> map;  // ❌ 编译失败

// 需要先把 string hash 成 int64
int64_t hashed_key = folly::hash::fnv64(str);
map.insert(hashed_key, value);  // ✅
```

### ❌ 陷阱2：初始容量估低了

```cpp
// 初始 capacity=1000，实际插入 100万
folly::AtomicHashMap<int64_t, int> map(1000);  // ❌
// 会分配 16 个 submap，每次 find 要串行搜索所有 submap
// 性能退化为 O(numSubMaps)，约慢 16 倍

// 正确：一次性估准
folly::AtomicHashMap<int64_t, int> map(1'200'000);  // 留 20% 余量
```

### ❌ 陷阱3：erase 不回收内存

```cpp
map.erase(key);  // cell 标记为 kErasedKey_，内存不释放
// 大量 erase 后 map 依然占用原始内存
// 需要回收 → 只能 clear()（非线程安全）后重建
```

### ❌ 陷阱4：operator[] 不存在

```cpp
map[key] = value;  // ❌ 未实现
// 原因：operator[] 的语义是「不存在则默认构造」，
// 对于多线程场景容易产生竞态误解
// 使用 insert() 或 emplace() 替代
```

---

## 适用 vs 不适用场景

| 场景 | 适用？ | 原因 |
|------|--------|------|
| 高并发读（查缓存、ID 映射） | ✅ | find wait-free，极低延迟 |
| 写入后不删的数据（intern 池） | ✅ | erase 不回收无影响 |
| 需要稳定 32-bit 引用的对象存储 | ✅ | findAt() 设计初衷 |
| 频繁 delete/回收内存 | ❌ | erase 不释放内存 |
| 任意类型 key（string/struct） | ❌ | 只支持 int32/int64 |
| 容量难以预估的场景 | ❌ | submap 扩容导致性能退化 |
| 需要遍历所有元素（有序） | ❌ | 无序，迭代器跨 submap 串行 |

---

## 关键文件索引

```
folly/
├── AtomicHashMap.h         # 主接口，AHMap 模板定义
├── AtomicHashMap-inl.h     # insert/find/emplace 实现
├── AtomicHashArray.h       # 底层 AHArray，cell 级 CAS 逻辑
├── AtomicHashArray-inl.h   # 线性探测、wait-free find 实现
├── ThreadCachedInt.h       # size() 依赖的 TLS counter
└── test/AtomicHashMapTest.cpp  # benchmark + 边界测试
```

---

*自动生成 · 2026-05-18 · OpenClaw Daily Task*
