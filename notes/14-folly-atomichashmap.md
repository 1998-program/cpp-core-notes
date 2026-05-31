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

---

## 七、业务代码库适配分析
> **分析时间**：2026-05-31T19:08:23.510924
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 和 `feeda-mv-grc` 两个业务代码库中**已经存在目标库使用经验**：两个仓库均发现 10 个相关使用文件，说明项目在引入 folly 组件或类似高性能容器方面已有一定基础，可优先参考现有调用方式、编译依赖和工程接入方式进行增量迁移。

- 从潜在替换规模看，两个仓库中 `std::unordered_map` 使用非常广泛：`feeda-mv-grg` 中有 734 次、分布在 205 个文件；`feeda-mv-grc` 中有 2828 次、分布在 636 个文件。考虑到 `folly::AtomicHashMap` 适合 **int32/int64 key、write-once、读多写少、高并发查询** 的场景，迁移不应面向所有 `unordered_map`，而应优先筛选召回、过滤、候选集索引、特征缓存、ID 到对象映射等热点路径。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- **目标库已有使用**
  - 扫描发现 10 个文件已使用目标库或相关 folly 能力，已列出的典型文件包括：
    - `operator/diversity/microvideo_llm_disu_suppress_soft_rule.cpp`
    - `process/pk_generate_candidate_nid_emb_function_v4.cpp`
    - `operator/diversity/deepes_kl_soft_rule.cpp`
    - `process/pk_generate_candidate_nid_emb_function_v5.cpp`
    - `process/new_author_cb2cf_data_function.cpp`

- **std 等价物使用规模**
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- **初步判断**
  - `feeda-mv-grg` 主要是序列生成与候选处理链路，文件名中出现较多 `candidate`、`nid_emb`、`author_cb2cf` 等场景，通常会存在大量以 `nid`、`author_id`、`rid` 等整型 ID 为 key 的映射。
  - 如果这些映射在请求内或批处理周期内呈现“构建一次、多次查询、不频繁删除”的模式，比较适合评估替换为 `folly::AtomicHashMap<int64_t, Value>`。
  - 已有目标库使用文件可作为工程接入参考，尤其是：
    - `process/pk_generate_candidate_nid_emb_function_v4.cpp`
    - `process/pk_generate_candidate_nid_emb_function_v5.cpp`
    - `process/new_author_cb2cf_data_function.cpp`

#### feeda-mv-grc：召回汇聚服务

- **目标库已有使用**
  - 扫描发现 10 个文件已使用目标库或相关 folly 能力，已列出的典型文件包括：
    - `processor/filter/high_show_audit_filter_operator.cc`
    - `operator/adjuster/sketchy/collection_new_ltv_adjuster.cpp`
    - `operator/adjuster/sketchy/heji_tgi_ltv_adjuster.cpp`
    - `strategy/virtual_mark_select.cpp`
    - `processor/compute_readlist_itemtype_prefer.cpp`

- **std 等价物使用规模**
  - `std::vector`：8382 次，分布在 1266 个文件
  - `std::string`：7107 次，分布在 1222 个文件
  - `std::unordered_map`：2828 次，分布在 636 个文件

- **初步判断**
  - `feeda-mv-grc` 的 `std::unordered_map` 使用规模明显更大，且召回汇聚服务往往存在高并发、多路召回结果合并、过滤、打分、调权等读密集场景，因此整体迁移收益潜力高于 `feeda-mv-grg`。
  - 但扫描样例中也存在 `std::unordered_map<std::string, std::vector<int>>`，例如：
    - `service/grc_http_service.cpp:62`
  - 该类 string key 场景**不能直接替换**为 `folly::AtomicHashMap`，除非业务能够接受将 string 稳定映射为 `int64_t` hash key，并处理 hash 冲突风险。

---

### 3. 💡 适用性评估与建议

- **建议 1：优先评估 `feeda-mv-grg` 中候选 ID 到特征/embedding 的映射**
  - 重点文件：
    - `process/pk_generate_candidate_nid_emb_function_v4.cpp`
    - `process/pk_generate_candidate_nid_emb_function_v5.cpp`
  - 适用场景：
    - 如果代码中存在类似 `std::unordered_map<int64_t, Embedding>`、`std::unordered_map<int64_t, Feature>`、`std::unordered_map<uint64_t, CandidateInfo>` 的结构，并且 key 是 `nid`、`rid`、`item_id` 等整型 ID。
    - 如果流程是先批量构建候选映射，后续多次按 ID 查询 embedding 或候选属性。
  - 建议替换方向：
    - 将只增不改或少量写入、多次查询的映射替换为：
      ```cpp
      folly::AtomicHashMap<int64_t, Value> map(estimated_size);
      ```
    - 初始化容量建议按候选规模预估后增加 20%~50% 余量，避免扩容后多个 submap 串行查找导致性能退化。
  - 预期收益：
    - 降低高频 `find()` 开销。
    - 在多线程候选处理场景下减少 `std::unordered_map` 外层加锁或分片锁的必要性。

- **建议 2：在 `feeda-mv-grg` 的多样性/去重规则中评估替换只读索引表**
  - 重点文件：
    - `operator/diversity/microvideo_llm_disu_suppress_soft_rule.cpp`
    - `operator/diversity/deepes_kl_soft_rule.cpp`
  - 适用场景：
    - 多样性规则中常见按 `author_id`、`category_id`、`cluster_id`、`nid` 进行去重、抑制、计数或查表。
    - 若当前使用 `std::unordered_map<int64_t, RuleState>`、`std::unordered_map<int32_t, Score>` 作为规则执行期临时表，并且生命周期内基本不删除元素，可考虑替换。
  - 注意：
    - 如果 value 是计数器，并且需要并发自增，`AtomicHashMap` 只解决 key 到 value 的定位问题，不自动保证 `value` 内部更新线程安全。
    - 对于 value 内部的计数，可使用 `std::atomic<int>` 或请求线程本地聚合后再合并。

- **建议 3：`feeda-mv-grc` 的过滤算子适合作为首批 A/B 试点**
  - 重点文件：
    - `processor/filter/high_show_audit_filter_operator.cc`
  - 适用场景：
    - 高曝光审核过滤通常会涉及大量 item/user 维度的黑白名单、审核状态、历史曝光状态查询。
    - 如果其中存在以 `item_id`、`content_id`、`author_id` 为 key 的 `std::unordered_map`，并且查询频率远高于插入频率，适合迁移到 `folly::AtomicHashMap`。
  - 建议方式：
    - 先选取局部热点 map 做灰度替换，不建议一次性替换整个文件内所有 `unordered_map`。
    - 对比指标包括：
      - 单请求过滤耗时
      - p99 延迟
      - CPU 使用率
      - map 初始化耗时
      - 内存占用变化

- **建议 4：`feeda-mv-grc` 的调权/召回融合模块可评估 ID 映射表优化**
  - 重点文件：
    - `operator/adjuster/sketchy/collection_new_ltv_adjuster.cpp`
    - `operator/adjuster/sketchy/heji_tgi_ltv_adjuster.cpp`
    - `strategy/virtual_mark_select.cpp`
    - `processor/compute_readlist_itemtype_prefer.cpp`
  - 适用场景：
    - 这些模块通常会维护 item、collection、用户偏好、item type、召回来源等映射关系。
    - 若存在 `std::unordered_map<int64_t, float>`、`std::unordered_map<int64_t, double>`、`std::unordered_map<int32_t, int>` 等轻量 value 表，`AtomicHashMap` 的收益会比较明显。
  - 建议：
    - 对于构建后只读的调权参数表，优先考虑 `folly::AtomicHashMap`。
    - 对于每个请求临时构建的小 map，如果元素数量很少，例如几十个以内，替换收益可能不明显，反而会增加初始化成本，应谨慎评估。

- **建议 5：`service/grc_http_service.cpp` 中 string key map 不建议直接迁移**
  - 现有示例：
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    ```
  - 判断：
    - `folly::AtomicHashMap` 仅适合 int32/int64 key，不直接支持 `std::string` key。
    - 该场景是 HTTP 服务中 graph 依赖关系构建，key 为字符串，直接替换不可行。
  - 可选优化：
    - 如果 graph name、vertex name、depend name 可以提前离线编码为 int64 ID，可改为：
      ```cpp
      folly::AtomicHashMap<int64_t, std::vector<int>> depend_map;
      ```
    - 但必须建立可靠的 string 到 ID 映射，并处理 hash 冲突或维护全局唯一 ID，否则不建议迁移。

---

### 4. ⚠️ 引入风险与限制

- **key 类型限制明显，不能机械替换所有 `std::unordered_map`**
  - `folly::AtomicHashMap` 主要面向 `int32_t` / `int64_t` key。
  - 对以下类型不适合直接替换：
    - `std::unordered_map<std::string, T>`
    - `std::unordered_map<const char*, T>`
    - `std::unordered_map<复杂结构体, T>`
  - 例如 `service/grc_http_service.cpp` 中的：
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    ```
    不应直接迁移。

- **容量预估错误会导致性能退化**
  - `AtomicHashMap` 对初始容量非常敏感。
  - 如果初始容量明显低于实际插入规模，会触发多个 submap 分配，`find()` 需要顺序搜索多个 submap，最坏可能接近 16 倍退化。
  - 建议迁移前统计：
    - 单请求 map 平均元素数
    - p95 / p99 元素数
    - 峰值元素数
  - 初始化容量建议至少按 p99 规模预留 20%~50% 空间。

- **erase 不回收内存，不适合高频删除场景**
  - `erase()` 只是将 cell 标记为 erased，不会释放内存，也不会让该槽位恢复成完全空闲状态。
  - 如果业务中存在大量增删，例如在线状态表、滑动窗口缓存、实时淘汰缓存，不建议使用 `AtomicHashMap`。
  - 这类场景应继续使用：
    - `std::unordered_map` + 锁
    - 分片 map
    - `folly::F14FastMap`
    - LRU/TTL cache 结构

- **value 的并发修改需要单独保证线程安全**
  - `AtomicHashMap` 保证的是 key 插入和查找路径的并发安全，不等价于 value 内部字段的并发安全。
  - 如果迁移后出现如下模式：
    ```cpp
    auto it = map.find(key);
    if (it != map.end()) {
        it->second.count++;
    }
    ```
    在多线程下仍然可能产生数据竞争。
  - 可选方案：
    - value 内部字段使用 `std::atomic`
    - value 只读，不做原地修改
    - 使用线程本地 map 聚合后合并
    - 在 value 层增加细粒度锁

---

### 5. 推荐落地路径

- **第一阶段：只做热点识别，不立即全量替换**
  - 在 `feeda-mv-grc` 优先扫描以下文件中的 `std::unordered_map<int64_t, T>` / `std::unordered_map<int32_t, T>`：
    - `processor/filter/high_show_audit_filter_operator.cc`
    - `operator/adjuster/sketchy/collection_new_ltv_adjuster.cpp`
    - `operator/adjuster/sketchy/heji_tgi_ltv_adjuster.cpp`
    - `processor/compute_readlist_itemtype_prefer.cpp`
  - 在 `feeda-mv-grg` 优先扫描：
    - `process/pk_generate_candidate_nid_emb_function_v4.cpp`
    - `process/pk_generate_candidate_nid_emb_function_v5.cpp`
    - `process/new_author_cb2cf_data_function.cpp`

- **第二阶段：选择一个读多写少、整型 key、无删除的 map 做试点**
  - 替换前后对比：
    - 构建耗时
    - 查询耗时
    - p99 请求耗时
    - CPU 消耗
    - 内存占用
  - 若 value 较大，建议存储指针、索引或轻量结构，避免 cell 过大导致 cache miss。

- **第三阶段：沉淀封装工具类**
  - 可封装业务侧别名，降低直接依赖成本：
    ```cpp
    template <class V>
    using Int64AtomicMap = folly::AtomicHashMap<int64_t, V>;
    ```
  - 对容量估算、插入失败处理、find 封装统一规范，避免各业务模块重复踩坑。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
