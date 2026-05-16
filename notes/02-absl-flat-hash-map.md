# absl::flat_hash_map · SIMD 哈希探测

> **库**：[abseil/abseil-cpp](https://github.com/abseil/abseil-cpp) ⭐ 15k+
> **日期**：2026-05-03
> **主题**：Swiss Table 设计、SIMD 并行探测、内存布局优化、tombstone 消除

---

## 一、设计思想

`std::unordered_map` 的性能瓶颈本质上是**指针追逐**：每个 bucket 是一条链表，插入的元素在堆上分配，查找时从 bucket 跳到链表节点，每跳一次就可能触发一次 cache miss。在现代 CPU 上，一次 L3 cache miss 约 100ns，比 ALU 运算慢 100 倍以上。

absl::flat_hash_map 基于 Google 的 **Swiss Table** 算法，核心设计哲学是：**把哈希表的控制元数据（每个槽位是否占用）和实际数据分离，让 SIMD 指令一次比较 16 个槽位**，将查找的平均 cache miss 次数从 O(load_factor) 降到接近 O(1)。

关键洞察：
- 传统开放寻址需要逐个槽位检查，探测序列长
- Swiss Table 把 128 个 slot 的 7-bit 元数据打包进一个连续的 `ctrl` 数组，SSE2/NEON 一条指令比较 16 个，**探测等价于位运算而非内存访问**

---

## 二、核心实现

### 内存布局

```
┌─────────────────────────────────────────────────────┐
│  ctrl[0..N-1]  │  slots[0..N-1]                     │
│  (1 byte each) │  (key-value pairs, flat)            │
└─────────────────────────────────────────────────────┘

ctrl[i] 的含义：
  0b10000000 (0x80) = kEmpty    槽位空
  0b11111110 (0xFE) = kDeleted  已删除（tombstone）
  0b0xxxxxxx        = H2        低7位存 hash 的高7位
```

### SIMD 探测核心路径

```cpp
// 简化的查找逻辑（实际在 raw_hash_set.h）
iterator find(const K& key) {
  size_t hash = Hash{}(key);
  uint64_t h1 = H1(hash);   // 高57位，用于定位 bucket 起始位置
  uint8_t  h2 = H2(hash);   // 低7位，存在 ctrl 数组里做快速筛选

  // 用 h1 定位到起始 group（每 group 16 个 slot）
  size_t pos = h1 & capacity_;

  while (true) {
    // ① 加载 16 个 ctrl 字节到 SIMD 寄存器
    Group g{ctrl_ + pos};

    // ② SIMD 并行比较：找出所有 ctrl[i] == h2 的位置
    //    SSE2: _mm_cmpeq_epi8(g, _mm_set1_epi8(h2))
    //    结果是一个 bitmask，有几个候选就有几个 bit 置1
    for (int i : g.Match(h2)) {
      // ③ 只对匹配 h2 的槽位做完整 key 比较（大概率1次即命中）
      if (ABSL_PREDICT_TRUE(slots_[pos + i].key == key))
        return iterator{ctrl_ + pos + i, slots_ + pos + i};
    }

    // ④ 如果有 Empty 槽，说明 key 不存在（开放寻址终止条件）
    if (ABSL_PREDICT_TRUE(g.MatchEmpty()))
      return end();

    pos = (pos + 16) & capacity_; // 跳到下一个 group
  }
}
```

### Group::Match 的 SIMD 实现

```cpp
// x86 SSE2 版本
struct GroupSse2Impl {
  __m128i ctrl;

  // 返回所有 ctrl[i] == h2 的位置 bitmask
  BitMask<uint32_t, 16> Match(uint8_t h2) const {
    auto match = _mm_set1_epi8(static_cast<char>(h2));
    // _mm_cmpeq_epi8: 16路并行字节比较，相等则该字节全1(0xFF)，否则全0
    return BitMask<uint32_t, 16>{
        static_cast<uint32_t>(_mm_movemask_epi8(_mm_cmpeq_epi8(ctrl, match)))
    };
  }

  BitMask<uint32_t, 16> MatchEmpty() const {
    // kEmpty = 0x80，高位为1；H2 值高位永远为0
    // _mm_movemask_epi8 提取每个字节的最高位
    return BitMask<uint32_t, 16>{
        static_cast<uint32_t>(_mm_movemask_epi8(ctrl))
    };
  }
};
```

### tombstone 消除（无 kDeleted 设计）

Swiss Table 通过 **rehash on full-group-deleted** 策略避免 tombstone 积累：当一个 group 内删除比例过高时，触发局部重整而非全量 rehash，保持探测序列干净。

---

## 三、性能优化原理

### 🔥 SIMD 并行探测：16路比较降低探测步数

传统线性探测平均需要 `1 / (1 - load_factor)` 次探测；Swiss Table 每步比较 16 个槽位，等效探测步数缩短 **16 倍**。在 load_factor=0.875 时（Swiss Table 默认），平均实际内存访问次数 ≈ 1.07 次，接近理论下限。

### 🔥 Flat 布局消除指针追逐

`slots_` 数组直接存 key-value（不是指针），查找命中后直接访问元素，无二次解引用。相比 `std::unordered_map` 的链表节点，减少 1~2 次 cache miss：

```
std::unordered_map 查找路径：
  bucket[h % N]  →  node*  →  node.key/value   (2次潜在 miss)

flat_hash_map 查找路径：
  ctrl[pos..pos+15] (SIMD)  →  slots[pos+i]    (1次 miss，且 prefetch 友好)
```

### 🔥 H2 早期筛选：99.6% 的槽位比较在 SIMD 层终止

7-bit H2 作为过滤器，两个不同 key 的 H2 碰撞概率约 1/128 ≈ 0.78%。绝大多数情况下 SIMD Match 返回 0 或 1 个候选，真正执行 `key ==` 比较的次数极少，避免了重量级的 key 比较开销（尤其对 string key）。

---

## 四、使用示例

```cpp
#include "absl/container/flat_hash_map.h"
#include "absl/container/flat_hash_set.h"

// 基本用法与 std::unordered_map 兼容
absl::flat_hash_map<std::string, int> word_count;
word_count["hello"] += 1;
word_count.emplace("world", 42);

// 查找
if (auto it = word_count.find("hello"); it != word_count.end()) {
  // it->second == 1
}

// 性能关键：预分配容量，避免 rehash
absl::flat_hash_map<int64_t, std::vector<int>> index;
index.reserve(1'000'000);  // 预分配100万槽，一次性完成内存布局

// 自定义 hash（对性能敏感的场景）
struct MyHash {
  size_t operator()(int64_t id) const {
    // 用 absl 的混合函数，比默认 std::hash 分布更均匀
    return absl::Hash<int64_t>{}(id);
  }
};
absl::flat_hash_map<int64_t, Item, MyHash> items;

// 遍历（flat 布局，cache 友好）
for (const auto& [key, value] : word_count) {
  process(key, value);
}
```

### 在 BCLOUD 中引入

```python
# BCLOUD 文件
CONFIGS('baidu/third-party/abseil-cpp@stable')
```

```cpp
// BUILD 依赖
deps = ["//baidu/third-party/abseil-cpp:absl_container"]
```

---

## 五、性能基准

| 操作 | absl::flat_hash_map | std::unordered_map | absl::node_hash_map |
|------|--------------------|--------------------|---------------------|
| 查找（命中） | **~30ns** | ~80ns | ~45ns |
| 查找（未命中）| **~20ns** | ~70ns | ~40ns |
| 插入 | **~35ns** | ~120ns | ~60ns |
| 内存占用 | **低**（flat） | 高（链表节点） | 中（稳定指针） |
| 迭代 | **极快**（线性内存）| 慢（指针跳跃）| 中 |

> 数据来源：[abseil 官方 benchmark](https://abseil.io/docs/cpp/guides/container)，测试环境 Intel Skylake，load_factor≈0.8

**与 `std::unordered_map` 相比查找快 2~3 倍，迭代快 4~5 倍**。

---

## 六、适用场景与限制

**✅ 适合**
- 高频查找的热路径（如推荐系统的特征 cache、item 索引）
- key-value 均为值类型（int、string、小 struct）
- 需要高效迭代的场景（flat 内存布局 prefetch 友好）
- 替换现有 `std::unordered_map`（API 基本兼容）

**❌ 不适合**
- 需要**稳定指针/引用**（插入/rehash 后已有指针失效）→ 用 `absl::node_hash_map`
- 存储大型 value（flat 布局会拷贝 value，可改存指针/index）
- 需要按 key 有序遍历 → 用 `absl::btree_map`

---

## 七、推荐在线架构场景

在 `uni-que` / `gr-convergence` 等服务中，典型的 item 索引结构如：

```cpp
// 旧写法（潜在性能问题）
std::unordered_map<int64_t, ItemInfo> item_map;

// 替换为（查找热路径快 2-3x）
absl::flat_hash_map<int64_t, ItemInfo> item_map;
item_map.reserve(expected_size * 1.2);  // 预留20%空间避免 rehash
```

---

*下一篇：[absl::InlinedVector · 栈上优化小容器](./03-absl-inlined-vector.md)*

---
