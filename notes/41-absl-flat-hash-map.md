# absl::flat_hash_map — Swiss Table 高性能哈希表

> **编号**: 41  
> **日期**: 2026-07-01  
> **模块**: `absl::flat_hash_map` (Google Abseil)  
> **关联技术栈**: brpc (butil), Protobuf, ng-framework 在线服务  
> **源码**: https://github.com/abseil/abseil-cpp

---

## 一、为什么需要 Swiss Table

### 1.1 std::unordered_map 的痛点

`std::unordered_map` 的经典实现是**链式桶（chaining）**：

```
桶数组 (bucket array)
├─ [0] → 节点1 → 节点2 → nullptr
├─ [1] → nullptr
├─ [2] → 节点3 → nullptr
└─ [3] → 节点4 → 节点5 → 节点6 → nullptr
```

每次 `find()` 操作的平均路径：
1. **哈希计算** → 得到桶索引
2. **指针跳转 1** → 桶数组元素（大概率 cache miss）
3. **指针跳转 2** → 链表节点（必然 cache miss）
4. **键值比较** → 判断相等

> 实验证明：把 `std::hash` 换成 wyhash、xxhash 甚至 SipHash，`find` 延迟不会有数量级变化。瓶颈不在哈希质量，而在**内存访问模式**。

### 1.2 生产场景中的影响

在 brpc/ng-framework 在线服务中，哈希表常用于：
- **请求路由表**（key: user_id → value: shard_id）
- **连接池管理**（key: endpoint → value: Channel）
- **缓存索引**（key: request_hash → value: response）
- **指标聚合**（key: metric_name → value: Counter）

这些场景的共同特征：**高频查找、低延迟敏感、内存敏感**。std::unordered_map 的二次指针跳转成为性能瓶颈。

---

## 二、Swiss Table 核心设计

### 2.1 内存布局革命

Swiss Table 采用**开放寻址法（Open Addressing）+ 紧凑数组存储**，核心创新是将 key-value 直接内联存储在单一数组中：

```
┌─────────────────────────────────────────────────────────┐
│                    flat_hash_map 内存布局                    │
├─────────────┬──────────────────────────┬──────────────────┤
│  Control    │      Slot Array          │  Group Probing   │
│  Bytes      │  (key|value 内联存储)     │  策略            │
├─────────────┼──────────────────────────┼──────────────────┤
│ 1 byte/槽   │ sizeof(K) + sizeof(V)   │ 三角数探测序列    │
│ • Empty     │ 无额外指针，CPU cache     │ 避免聚集          │
│ • Deleted   │ 行对齐优化               │ SIMD 批量比较     │
│ • H2 hash   │                          │                  │
└─────────────┴──────────────────────────┴──────────────────┘
```

**控制字节（Control Byte）编码**：
```
┌───────┬─────────────────┐
│ Bit 7 │  Bits 6..0      │
│ Ctrl  │  H2 hash (7bits) │
└───────┴─────────────────┘
Ctrl = 0:  空槽（Empty）
Ctrl = 1:  已删除（Deleted / Tombstone）
Ctrl >= 2: 已占用，低7位 = H2 hash
```

### 2.2 哈希值分裂：H1 / H2

```cpp
// 64-bit 哈希值拆分
uint64_t hash = Hash(key);

H1 = hash >> 7;          // 高 57 位：决定元素在数组中的起始位置
H2 = hash & 0x7F;        // 低  7 位：存入控制字节（指纹）

// H2 用于 SIMD 预筛选，期望冲突率 < 1/128
// 但控制字节中的 H2 仅 7bit，因此每 128 个不同 H1 约有 1 个 H2 冲突
```

> **关键洞察**：H2 指纹不是防碰撞的，而是**快速排除**的。通过 SIMD 同时比较 16 个槽的 H2，只让真正可能匹配的槽（期望 < 0.11 个）进入键值比较阶段。

### 2.3 SIMD 查找：一次过滤 16 个槽

```cpp
// SSE2 实现（16 字节并行比较）
MatchResult Match(h2_t hash, const ctrl_t* metadata) {
    __m128i match = _mm_set1_epi8(hash);           // 广播目标 H2 到 128-bit
    __m128i block = _mm_loadu_si128(metadata);      // 加载 16 个控制字节
    __m128i eq = _mm_cmpeq_epi8(match, block);      // 16 路并行比较
    return _mm_movemask_epi8(eq);                   // 提取 16-bit mask
}
```

**查找流程**：
```
1. H1 → 确定起始组（Group）
2. 加载该组的 16 个控制字节
3. SSE2 _mm_cmpeq_epi8 → 16 路并行 H2 比较
4. _mm_movemask_epi8 → 得到候选位图（期望 0~1 个候选）
5. 对候选槽做精确键值比较
6. 未找到 → 三角数步长探测下一组
```

### 2.4 三角数探测（Triangular Probing）

```cpp
// 避免主聚集（Primary Clustering）
size_t probe(size_t h1, size_t i) {
    // 步长序列: 1, 3, 6, 10, 15, 21... (三角数)
    // offset = (i * (i + 1)) / 2
    return (h1 + i * (i + 1) / 2) & capacity_mask;
}
```

> **数论基础**：三角数步长保证了在 2^n 容量的表中，探测序列能遍历所有槽位而不重复（当容量为 2 的幂时）。这比线性探测的聚集效应小得多。

---

## 三、源码级实现解析

### 3.1 核心模板结构

```cpp
// absl/container/flat_hash_map.h
// 继承自 raw_hash_map，基于 raw_hash_set

template <class Key, class Value,
          class Hash = absl::container_internal::hash_default_hash<Key>,
          class Eq = absl::container_internal::hash_default_eq<Key>,
          class Alloc = std::allocator<std::pair<const Key, Value>>>
class flat_hash_map
    : public container_internal::raw_hash_map<
          container_internal::FlatHashMapPolicy<Key, Value>,
          Hash, Eq, Alloc> {
    // FlatHashMapPolicy: 定义 key/value 提取策略
    // - key 从 pair<const Key, Value> 中提取 first
    // - value 提取 second
    // - 支持原地构造优化
};
```

### 3.2 控制字节布局（container/internal/raw_hash_set.h）

```cpp
// control byte 的编码值
static constexpr ctrl_t kEmpty = -128;      // 0b10000000
static constexpr ctrl_t kDeleted = -2;      // 0b11111110
static constexpr ctrl_t kSentinel = -1;     // 0b11111111

// H2 有效范围: 0 ~ 126（7 位，最高位留作控制标记）
// 实际存储时，已占用槽的 ctrl = H2（>= 0）
// 空槽 = kEmpty, 删除槽 = kDeleted
```

### 3.3 Group 结构（SIMD 操作单元）

```cpp
// 一个 Group = 16 个控制字节（SSE2）或 8 个（ARM NEON）
template <size_t N>
struct Group {
    // 加载 16-byte 控制字节块
    static Group Load(const ctrl_t* pos);
    
    // 匹配目标 H2，返回位掩码
    uint32_t Match(h2_t hash) const;
    
    // 查找空槽（用于插入）
    uint32_t MatchEmpty() const;
    
    // 查找空或已删除（用于插入）
    uint32_t MatchEmptyOrDeleted() const;
};

// SSE2 特化
template <>
struct Group<16> {
    __m128i ctrl_;  // 16 x int8
    
    uint32_t Match(h2_t hash) const {
        auto match = _mm_set1_epi8(static_cast<char>(hash));
        return _mm_movemask_epi8(_mm_cmpeq_epi8(match, ctrl_)) & 0xFFFF;
    }
};
```

### 3.4 插入路径优化

```cpp
// 1. 计算 H1/H2
// 2. 从 H1 起始位置开始组探测
// 3. 每组用 SIMD MatchEmptyOrDeleted() 找可插入位置
// 4. 插入时先写控制字节（标记为 H2），再构造 key-value

// 关键优化：延迟构造（Lazy Construction）
template <class... Args>
std::pair<iterator, bool> emplace(Args&&... args) {
    // 先检查 key 是否已存在（避免不必要构造）
    // 只有确认不存在时，才构造 value_type
    // 这对 heavy value（如 std::string）意义重大
}
```

### 3.5 增长策略：加倍 + 重排

```cpp
// 负载因子控制：最大 7/8 = 87.5%
// 当 元素数 > 容量 * 7/8 时触发 rehash
// 容量始终为 2 的幂（方便位运算取模）

void resize(size_t new_capacity) {
    // 1. 分配新控制字节数组（容量 + 16 个 sentinel）
    // 2. 分配新 slot 数组
    // 3. 遍历旧表：非空槽重新计算 H1，插入新表
    // 4. 释放旧内存
    
    // 注意：rehash 后元素地址改变 → flat_hash_map 不保证指针稳定性
}
```

> **与 std::unordered_map 的关键区别**：
> - `flat_hash_map` 元素地址在 insert/erase 后可能改变（无指针稳定性）
> - `node_hash_map` 提供指针稳定性（每个元素独立分配，类似 std）
> - 推荐：`flat_hash_map<std::unique_ptr<T>>` 替代 `node_hash_map<T>`

---

## 四、性能对比：生产环境基准

### 4.1 官方基准测试（Google 内部，CppCon 2017）

| 操作 | std::unordered_map | absl::flat_hash_map | 提升 |
|------|-------------------|---------------------|------|
| 随机查找 | 基准 | **~2-3x** | 2-3x |
| 随机插入 | 基准 | **~2.3x** | 2.3x |
| 顺序迭代 | 基准 | **~5-10x** | 5-10x |
| 内存占用 | 基准 | **-35%** | 节省 35% |

### 4.2 延迟分解（find 操作）

```
std::unordered_map find:
  ├─ 哈希计算:       ~5ns
  ├─ 桶数组访问:     ~15ns (cache miss)
  ├─ 链表节点跳转:   ~50ns (cache miss)
  ├─ 键值比较:       ~10ns
  └─ 总延迟:        ~80ns (平均)

absl::flat_hash_map find:
  ├─ 哈希计算:       ~5ns
  ├─ H1 定位组:      ~3ns
  ├─ SIMD H2 比较:   ~2ns (一次过滤 16 槽)
  ├─ 候选槽比较:     ~5ns (期望 0~1 个候选)
  └─ 总延迟:        ~15ns (平均)
```

> **5x 延迟降低**的核心：将"链表遍历"转化为"SIMD 批量预筛选"。

---

## 五、brpc/ng-framework 生产实战

### 5.1 brpc 中的应用场景

brpc 内部多处使用 Swiss Table 风格的设计思想：

```cpp
// butil/containers/flat_map.h — brpc 自研的 flat map
// 原理类似 Swiss Table，但针对 RPC 场景优化：
// - 支持 copy-on-write（多线程读安全）
// - 支持 wait-free 读路径

// brpc::bvar 中的指标聚合
// 大量 Counter/Gauge 的 name → value 映射
// 使用 flat_map 存储，支持每秒百万次读取

// brpc::Server 中的 service 路由表
// service_name → Service 对象指针
// 请求到达时 O(1) 路由，无锁读
```

### 5.2 ng-framework 在线服务中的应用

```cpp
// 推荐系统的特征索引（典型 ng-framework 应用）
class FeatureIndex {
    // key: feature_name_hash (uint64)
    // value: FeatureMeta { offset, type, dim }
    absl::flat_hash_map<uint64_t, FeatureMeta> index_;
    
public:
    const FeatureMeta* Find(uint64_t hash) const {
        auto it = index_.find(hash);
        return it != index_.end() ? &it->second : nullptr;
    }
    // 单次查找 < 15ns，支撑每秒 10万+ 特征查询
};

// Protobuf FieldDescriptor 缓存
// 在反序列化高频消息时，缓存 field_number → FieldDescriptor*
// 避免重复查表，提升解析速度 10%+
```

### 5.3 与 Protobuf 的协同

```cpp
// Protobuf Arena 分配 + flat_hash_map 索引 = 极致性能
// 
// 场景：在线请求中解析大量小消息，需要快速字段访问

struct FieldCache {
    // Arena 分配所有 FieldMeta，保证生命周期
    google::protobuf::Arena arena;
    
    // flat_hash_map 索引 Arena 中的对象（存指针，保证稳定性）
    absl::flat_hash_map<int, const FieldMeta*> field_map;
    
    // insert 时：
    // 1. Arena::Create<FieldMeta>() — 分配在 Arena 上
    // 2. field_map[field_number] = ptr — 索引指针（稳定！）
};
```

> **Arena + flat_hash_map 的组合优势**：
> - Arena 批量分配释放，替代 malloc/free
> - flat_hash_map 存储 Arena 对象指针，获得指针稳定性
> - 请求结束时一次 Arena::Reset()，O(1) 回收所有内存

### 5.4 jemalloc 协同优化

```cpp
// Swiss Table 与 jemalloc 的完美配合
// 
// 1. 控制字节数组和 slot 数组分别分配
//    jemalloc 的 size class 对齐减少内部碎片
// 
// 2. 小表（< 256 元素）完全 fits in L2 cache
//    jemalloc 的 tcache 提供 < 50ns 的分配延迟
//
// 3. rehash 时批量分配/释放
//    jemalloc 的 arena 批量释放策略减少碎片

// 实际配置建议：
// export MALLOC_CONF="tcache:true,lg_tcache_max:15"
// 使 32KB 以下的表分配走 tcache 快速路径
```

---

## 六、API 进阶与最佳实践

### 6.1 异构查找（Heterogeneous Lookup）

```cpp
// C++20 特性：用不同类型的 key 进行查找，避免构造临时对象

struct StringHash {
    using is_transparent = void;  // 标记支持透明查找
    
    size_t operator()(absl::string_view sv) const {
        return absl::Hash<absl::string_view>{}(sv);
    }
};

struct StringEq {
    using is_transparent = void;
    
    bool operator()(absl::string_view a, absl::string_view b) const {
        return a == b;
    }
};

absl::flat_hash_map<std::string, int, StringHash, StringEq> map;

// 查找时直接传 string_view，无需构造 std::string
absl::string_view key = "feature_name";
auto it = map.find(key);  // 零拷贝查找！
```

> **生产价值**：在 ng-framework 中，从请求中解析出的 key 往往是 `absl::string_view`（引用外部缓冲区），异构查找避免了不必要的 `std::string` 构造和内存拷贝。

### 6.2 try_emplace：避免值拷贝

```cpp
// 如果 key 已存在，不构造 value
// 对 heavy value 意义重大

map.try_emplace(key, expensive_constructor_args...);

// vs insert/emplace:
// insert 会先构造 pair，即使 key 已存在也会构造后丢弃
// try_emplace 先查 key，不存在时才构造
```

### 6.3 预分配：避免 rehash

```cpp
// 已知大致规模时，预分配容量
map.reserve(expected_size);

// reserve 计算：
// 内部容量 = ceil(expected_size / 0.875) 向上取整到 2 的幂
// 即保证负载因子 <= 7/8

// 大规模批量插入时，预分配可将性能提升 30%+
```

### 6.4 自定义分配器：与内存池结合

```cpp
// 使用 ArenaAllocator 将 flat_hash_map 绑定到特定内存池
template <class T>
class ArenaAllocator {
    google::protobuf::Arena* arena_;
public:
    T* allocate(size_t n) {
        return static_cast<T*>(arena_->AllocateBytes(n * sizeof(T)));
    }
    void deallocate(T*, size_t) {}  // Arena 不单独释放
};

absl::flat_hash_map<Key, Value, Hash, Eq, 
    ArenaAllocator<std::pair<const Key, Value>>> arena_map;
```

---

## 七、限制与注意事项

### 7.1 指针稳定性

```cpp
absl::flat_hash_map<int, std::string> map;
map[1] = "hello";
auto& val = map[1];

map.insert({2, "world"});  // 可能触发 rehash
// insert 后 val 引用可能失效！
```

**解决方案**：
```cpp
// 方案 1: 使用 node_hash_map（有指针稳定性）
// 方案 2: 使用 flat_hash_map + unique_ptr
absl::flat_hash_map<int, std::unique_ptr<Data>> map;
// rehash 时 unique_ptr 被 move，但指向的 Data 对象地址不变
```

### 7.2 迭代器失效

```cpp
// 任何 insert/erase 都可能导致迭代器失效
// 因为开放寻址 + rehash 会移动元素

auto it = map.begin();
map.erase(it++);  // OK: post-increment 返回旧位置的迭代器
// map.erase(it); it++;  // 危险！erase 后 it 可能失效
```

### 7.3 不支持非 movable/copyable 类型

```cpp
// flat_hash_map 要求 value_type 至少 movable
// 因为 rehash 时需要移动元素
struct NonMovable {
    NonMovable() = default;
    NonMovable(NonMovable&&) = delete;  // 不可移动！
};

// absl::flat_hash_map<int, NonMovable> map;  // 编译失败
// 改用 absl::node_hash_map（元素不移动）
```

---

## 八、总结与选型指南

| 容器 | 内存布局 | 指针稳定性 | 查找延迟 | 适用场景 |
|------|---------|-----------|---------|---------|
| `std::unordered_map` | 链式桶 + 节点分配 | ✅ 稳定 | ~80ns | 兼容性优先 |
| `absl::flat_hash_map` | 开放寻址 + 内联存储 | ❌ 不稳定 | ~15ns | **默认首选** |
| `absl::node_hash_map` | 开放寻址 + 节点分配 | ✅ 稳定 | ~25ns | 需稳定性时 |
| `folly::F14Map` | 类似 Swiss Table | 可选 | ~15ns | Facebook 生态 |
| `tsl::robin_map` | 开放寻址 + 线性探测 | ❌ 不稳定 | ~12ns | 极致性能 |

### brpc/ng-framework 生产建议

1. **默认选择**：`absl::flat_hash_map` — 综合最优
2. **需要指针稳定性**：`absl::flat_hash_map<Key, std::unique_ptr<V>>` 或 `node_hash_map`
3. **极致性能 + 已知 workload**：考虑 `tsl::robin_map`（但失去 Abseil 生态支持）
4. **与 Arena 配合**：存储 Arena 分配对象的指针，获得稳定性 + 批量释放

---

## 参考

- [Swiss Tables Design Notes](https://abseil.io/about/design/swisstables)
- [Abseil Containers Guide](https://abseil.io/docs/cpp/guides/container)
- Matt Kulukundis, "Designing a Fast, Efficient, Cache-friendly Hash Table", CppCon 2017
- brpc `butil/containers/flat_map.h`
- jemalloc `arena.c` — 批量分配/释放策略

---

> 写于 2026-07-01，配合 brpc::bthread + protobuf::Arena + jemalloc 使用效果更佳 🦞
