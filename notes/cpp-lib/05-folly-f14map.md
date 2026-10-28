# 05 - folly::F14Map：向量化哈希表

> **来源库**：[facebook/folly](https://github.com/facebook/folly)  
> **归档日期**：2026-05-08

---

## 设计思想

`folly::F14Map` 是 Facebook Folly 库中专为现代 CPU 设计的高性能哈希表，其核心创新在于充分利用 SIMD（单指令多数据）向量化指令集（SSE2/AVX2）。传统哈希表每次探测一个桶，而 F14Map 将多个哈希标签（tag）打包进一个 16 字节的向量寄存器，通过一条 SIMD 比较指令同时检测 14 个候选槽位（"F14" 即 Fourteen），大幅降低了 cache miss 与分支预测失败的代价。

F14 系列提供三种变体：`F14ValueMap`（值直接存储在桶中，适合小对象）、`F14NodeMap`（堆分配节点，迭代器稳定）、`F14VectorMap`（值连续存储，迭代性能最优）。`F14Map` 本身是根据键值大小自动选择最优变体的适配器，兼顾通用性与极致性能。其负载因子控制在 87.5% 以内，在高吞吐场景下查找性能普遍优于 `std::unordered_map` 2～5 倍，也强于 `absl::flat_hash_map`。

---

## 核心实现（带注释）

```cpp
#include <folly/container/F14Map.h>
#include <folly/container/F14Set.h>
#include <string>
#include <iostream>

// ------------------------------------------------------------------
// F14Map 内部：每个"chunk"存储 14 个 tag（各 1 字节）+ 14 个 KV 槽
// tag 格式：0x00=空, 0x80=墓碑(deleted), 0x01~0x7F=hash高7位
// 查找时用 SIMD 将目标 tag 广播到向量寄存器，与整个 chunk 一次比较
// ------------------------------------------------------------------

void f14map_basics() {
    // F14Map：自动选择 ValueMap / NodeMap / VectorMap
    folly::F14FastMap<std::string, int> wordCount;

    // 插入：与 std::unordered_map 接口完全兼容
    wordCount["hello"] = 1;
    wordCount.emplace("world", 2);
    wordCount.insert({"folly", 3});

    // 查找：O(1) 均摊，SIMD 加速 tag 比较
    if (auto it = wordCount.find("hello"); it != wordCount.end()) {
        std::cout << "hello -> " << it->second << "\n";  // 1
    }

    // try_emplace：不存在则插入，存在则返回已有迭代器（避免重复构造）
    auto [it, inserted] = wordCount.try_emplace("hello", 99);
    std::cout << "inserted=" << inserted << " val=" << it->second << "\n"; // 0, 1

    // prefetch：显式预取，适合批量查找前的流水线优化
    // wordCount.prefetch("hello");
}

void f14map_variants() {
    // F14ValueMap：KV 直接内联在 chunk，适合 sizeof(V) <= 24 的小对象
    folly::F14ValueMap<int, int> valueMap;
    valueMap.reserve(1024);  // 预分配，避免 rehash

    // F14NodeMap：节点堆分配，迭代器/指针在 rehash 后仍有效
    folly::F14NodeMap<int, std::string> nodeMap;

    // F14VectorMap：所有 KV 连续存储在独立数组，迭代局部性最优
    // 但 erase 会使迭代器失效（用 swap-with-last 删除）
    folly::F14VectorMap<int, double> vecMap;
    vecMap.insert({1, 3.14});
    vecMap.insert({2, 2.71});

    // 批量删除：erase_if（C++20 风格，F14 原生支持）
    folly::erase_if(vecMap, [](const auto& kv) { return kv.second < 3.0; });
}

void f14set_example() {
    // F14FastSet：同样基于向量化 chunk，用于高频去重场景
    folly::F14FastSet<uint64_t> visited;
    visited.reserve(100000);

    for (uint64_t i = 0; i < 50000; ++i) {
        visited.insert(i * 2);  // 插入偶数
    }

    std::cout << "contains 100: " << visited.count(100) << "\n";  // 1
    std::cout << "contains 101: " << visited.count(101) << "\n";  // 0
}
```

---

## 3 个优化点

### 1. 预分配容量，消除动态 rehash 开销

```cpp
// ❌ 反例：频繁 rehash，每次触发全量数据迁移
folly::F14FastMap<int, int> m;
for (int i = 0; i < 1000000; ++i) m[i] = i;

// ✅ 优化：已知上界时提前 reserve
folly::F14FastMap<int, int> m;
m.reserve(1000000);  // 一次性分配，负载因子控制在 87.5% 以下
for (int i = 0; i < 1000000; ++i) m[i] = i;
// 吞吐提升约 30%，内存分配次数从 O(log N) 降至 1
```

### 2. 用 `try_emplace` / `emplace` 替代 `operator[]` 避免默认构造

```cpp
// ❌ 反例：operator[] 对不存在的 key 会默认构造 value，再赋值
// 对复杂对象产生一次无效构造 + 一次移动赋值
m["key"] = ExpensiveObject(args...);

// ✅ 优化：try_emplace 就地构造，无多余临时对象
m.try_emplace("key", args...);
// 对 std::string value 节省一次堆分配
```

### 3. 选择正确变体匹配访问模式

```cpp
// 场景 A：高频随机访问，value 较小（int/float/小 struct）
// → F14ValueMap：value 与 tag 同 chunk，减少 cache line 跳转
folly::F14ValueMap<uint32_t, float> scoreMap;

// 场景 B：需要稳定迭代器（存储指针/引用传外部）
// → F14NodeMap：rehash 不移动节点，指针永久有效
folly::F14NodeMap<std::string, MyObject> registry;

// 场景 C：批量遍历（如序列化、统计）
// → F14VectorMap：连续内存，向量化遍历，比 NodeMap 快 3-5x
folly::F14VectorMap<int, Record> batch;
```

---

## 使用示例：词频统计 + Top-K

```cpp
#include <folly/container/F14Map.h>
#include <algorithm>
#include <vector>
#include <string_view>
#include <sstream>

// 统计文本词频，返回 Top-K 高频词
std::vector<std::pair<std::string, int>> topKWords(
    std::string_view text, int k)
{
    folly::F14FastMap<std::string, int> freq;
    freq.reserve(4096);

    // 分词 + 计数
    std::istringstream ss{std::string(text)};
    std::string word;
    while (ss >> word) {
        ++freq[word];  // F14 在高冲突下仍保持 ~100ns 级别查找
    }

    // 转换为 vector 并按频次排序（F14VectorMap 迭代更快，此处用 FastMap 演示）
    std::vector<std::pair<std::string, int>> result(freq.begin(), freq.end());
    std::partial_sort(result.begin(),
                      result.begin() + std::min(k, (int)result.size()),
                      result.end(),
                      [](const auto& a, const auto& b) {
                          return a.second > b.second;
                      });
    result.resize(std::min(k, (int)result.size()));
    return result;
}

int main() {
    std::string text = "the quick brown fox jumps over the lazy dog "
                       "the fox and the dog are friends the end";
    auto top5 = topKWords(text, 5);
    for (auto& [w, c] : top5) {
        std::cout << w << ": " << c << "\n";
    }
    // 输出（按频次降序）：
    // the: 5
    // fox: 2
    // dog: 2
    // quick: 1
    // brown: 1
    return 0;
}
```

---

## 关键对比

| 特性 | `std::unordered_map` | `absl::flat_hash_map` | `folly::F14FastMap` |
|------|---------------------|----------------------|---------------------|
| 内部结构 | 链式拉链 | 开放寻址（Robin Hood） | SIMD chunk（14槽） |
| 查找每次比较 | 1（+链表遍历） | 1～若干 | **14 个并行** |
| 迭代器稳定性 | ✅（NodeMap变体） | ❌ | 取决于变体 |
| 内存局部性 | 差 | 好 | 极好 |
| 典型查找延迟 | ~200ns | ~60ns | **~35ns** |

---

*自动归档 by OpenClaw · 2026-05-08*

---

## 七、业务代码库适配分析
> **分析时间**：2026-05-25T19:01:34.369400
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：`folly::F14Map`

### 1. 分析摘要

- 从扫描结果看，`folly::F14Map / F14Set` 在两个业务代码库中已经有一定落地基础：`feeda-mv-grg` 和 `feeda-mv-grc` 均已发现 **10 个文件**使用目标库，说明工程依赖、编译链路和基础用法大概率已经打通，可以优先从已有文件周边进行增量推广，而不是从零引入。

- 两个代码库中 `std::unordered_map` 使用规模较大，具备明显迁移潜力：
  - `feeda-mv-grg`：`std::unordered_map` 使用 **734 次**，分布在 **205 个文件**
  - `feeda-mv-grc`：`std::unordered_map` 使用 **2799 次**，分布在 **633 个文件**

- 从业务形态看，`feeda-mv-grg` 偏序列生成，常见场景包括候选集特征聚合、去重、计数、规则打分；`feeda-mv-grc` 偏召回汇聚，存在大量 parser、filter、adjuster 逻辑，通常会有高频 key-value 查找、特征字典访问、召回结果合并等场景。上述场景与 `folly::F14FastMap` / `F14FastSet` 的优化方向高度匹配，尤其适合替换热点路径中的 `std::unordered_map`。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现目标库使用：**10 个文件**
  - `operator/diversity/author_days_ltv_rgh_qwlx_soft_rule.cpp`
  - `process/msv_readlist_parse_function.cpp`
  - `operator/diversity/quality_selection_soft_rule.cpp`
  - `operator/diversity/heji_rhythm_soft_rule.cpp`
  - `process/pk_generate_candidate_nid_emb_function_v5.cpp`

- 现有 STL 容器使用情况：
  - `std::vector`：**1969 次**，分布在 **356 个文件**
  - `std::string`：**2443 次**，分布在 **425 个文件**
  - `std::unordered_map`：**734 次**，分布在 **205 个文件**

- 观察结论：
  - 该代码库中 `std::unordered_map` 使用面较广，但目标库已经存在使用样例，迁移阻力相对较低。
  - `operator/diversity/*_soft_rule.cpp` 一类文件通常属于在线规则链路，可能存在大量按 author、nid、category、tag、bucket 等维度的查找和去重，适合优先评估 `F14FastMap` / `F14FastSet`。
  - `process/msv_readlist_parse_function.cpp`、`process/pk_generate_candidate_nid_emb_function_v5.cpp` 这类 process 逻辑可能涉及候选集解析、特征映射、embedding 数据组织，如果内部存在临时哈希表，适合替换为 F14 并配合 `reserve()`。

#### feeda-mv-grc：召回汇聚服务

- 已发现目标库使用：**10 个文件**
  - `parser/recall_parser_new.cpp`
  - `operator/adjuster/precise/author_interest.cpp`
  - `parser/searchc_nid_ernie_parser.cpp`
  - `operator/adjuster/precise/author_rgh_factor.cpp`
  - `processor/filter/sparkle_black_queue_filter_operator.cc`

- 现有 STL 容器使用情况：
  - `std::vector`：**8330 次**，分布在 **1258 个文件**
  - `std::string`：**7099 次**，分布在 **1219 个文件**
  - `std::unordered_map`：**2799 次**，分布在 **633 个文件**

- 观察结论：
  - `feeda-mv-grc` 的 `std::unordered_map` 使用规模明显更大，是更具收益潜力的迁移目标。
  - parser 类文件如 `parser/recall_parser_new.cpp`、`parser/searchc_nid_ernie_parser.cpp` 往往存在字符串 key、nid key、特征名 key 到结构化字段的映射，F14 在查找密集场景中收益较明显。
  - adjuster / filter 类文件如 `operator/adjuster/precise/author_interest.cpp`、`operator/adjuster/precise/author_rgh_factor.cpp`、`processor/filter/sparkle_black_queue_filter_operator.cc` 通常位于在线召回后处理链路，延迟敏感，适合优先进行小范围 A/B 性能验证。

---

### 3. 💡 适用性评估与建议

- **建议 1：优先替换在线热点路径中的 `std::unordered_map` 为 `folly::F14FastMap`**
  - 适用文件：
    - `feeda-mv-grg/operator/diversity/author_days_ltv_rgh_qwlx_soft_rule.cpp`
    - `feeda-mv-grg/operator/diversity/quality_selection_soft_rule.cpp`
    - `feeda-mv-grg/operator/diversity/heji_rhythm_soft_rule.cpp`
    - `feeda-mv-grc/operator/adjuster/precise/author_interest.cpp`
    - `feeda-mv-grc/operator/adjuster/precise/author_rgh_factor.cpp`
  - 适用场景：
    - author 维度计数
    - nid / rid 到特征对象的映射
    - category / tag 维度打分缓存
    - 召回结果聚合时的去重和累加
  - 推荐替换方式：
    ```cpp
    // 原逻辑
    std::unordered_map<uint64_t, float> score_map;

    // 推荐
    folly::F14FastMap<uint64_t, float> score_map;
    ```
  - 如果 value 是小对象，例如 `int`、`float`、`double`、小型 struct，可进一步明确使用：
    ```cpp
    folly::F14ValueMap<uint64_t, float> score_map;
    ```

- **建议 2：对临时构建的大 map / set 统一增加 `reserve()`**
  - 适用文件：
    - `feeda-mv-grg/process/msv_readlist_parse_function.cpp`
    - `feeda-mv-grg/process/pk_generate_candidate_nid_emb_function_v5.cpp`
    - `feeda-mv-grc/parser/recall_parser_new.cpp`
    - `feeda-mv-grc/parser/searchc_nid_ernie_parser.cpp`
  - 适用场景：
    - 解析召回结果时构建 `nid -> item` 映射
    - 批量候选生成时构建 `rid -> embedding` 或 `nid -> feature` 映射
    - 按请求临时构建去重集合
  - 推荐写法：
    ```cpp
    folly::F14FastMap<uint64_t, ItemFeature> feature_map;
    feature_map.reserve(candidate_vec.size());

    for (const auto& item : candidate_vec) {
        feature_map.try_emplace(item.nid, item.feature);
    }
    ```
  - 对于 `F14FastSet`：
    ```cpp
    folly::F14FastSet<uint64_t> dedup_set;
    dedup_set.reserve(candidate_vec.size());

    for (const auto& item : candidate_vec) {
        if (!dedup_set.insert(item.nid).second) {
            continue;
        }
        // process item
    }
    ```

- **建议 3：去重集合场景使用 `folly::F14FastSet` 替代 `std::unordered_set`**
  - 适用文件：
    - `feeda-mv-grc/processor/filter/sparkle_black_queue_filter_operator.cc`
    - `feeda-mv-grc/parser/recall_parser_new.cpp`
    - `feeda-mv-grg/process/msv_readlist_parse_function.cpp`
  - 适用场景：
    - 黑名单过滤
    - 已处理 nid / rid 去重
    - 作者、队列、召回源去重
  - 推荐写法：
    ```cpp
    folly::F14FastSet<uint64_t> black_nids;
    black_nids.reserve(black_queue.size());

    for (auto nid : black_queue) {
        black_nids.insert(nid);
    }

    if (black_nids.contains(item.nid)) {
        return false;
    }
    ```
  - 如果当前代码仍使用 `count(x) > 0`，可在 C++20 或 Folly 支持场景下逐步改为 `contains(x)`，表达更清晰。

- **建议 4：字符串 key 的特征字典访问可优先试点 F14**
  - 适用文件：
    - `feeda-mv-grc/user_data/pcs_precise_parallel_commented.cpp`
    - `feeda-mv-grc/parser/recall_parser_new.cpp`
    - `feeda-mv-grc/parser/searchc_nid_ernie_parser.cpp`
  - 扫描示例中存在大量 `std::vector<std::string>` 特征 key 列表，例如：
    - `yitiao_latest_tgi_fea_key`
    - `yitiao_3days_tgi_fea_key`
  - 如果这些 key 后续会用于从特征 map 中取值，例如：
    ```cpp
    std::unordered_map<std::string, double> feature_map;
    ```
    可替换为：
    ```cpp
    folly::F14FastMap<std::string, double> feature_map;
    ```
  - 若查找时使用的是 `std::string_view`，建议确认 hash / equal 是否支持透明查找，避免因临时构造 `std::string` 抵消 F14 的性能收益。

- **建议 5：已有 F14 使用文件可作为迁移参考样板**
  - `feeda-mv-grg` 可参考：
    - `operator/diversity/author_days_ltv_rgh_qwlx_soft_rule.cpp`
    - `operator/diversity/quality_selection_soft_rule.cpp`
    - `process/pk_generate_candidate_nid_emb_function_v5.cpp`
  - `feeda-mv-grc` 可参考：
    - `parser/recall_parser_new.cpp`
    - `operator/adjuster/precise/author_interest.cpp`
    - `processor/filter/sparkle_black_queue_filter_operator.cc`
  - 建议在这些文件中沉淀统一写法，例如：
    - 使用 `folly::F14FastMap` 作为默认替代
    - 对小 value 使用 `folly::F14ValueMap`
    - 对需要稳定引用 / 指针的场景使用 `folly::F14NodeMap`
    - 对批量遍历型统计结果使用 `folly::F14VectorMap`

---

### 4. ⚠️ 引入风险与限制

- **迭代器、引用、指针稳定性需要重点检查**
  - `std::unordered_map` 在某些场景下节点地址相对稳定，但 `F14FastMap` 可能选择 value / vector 变体，rehash 后元素地址和迭代器可能失效。
  - 如果业务代码中存在以下模式：
    ```cpp
    auto* ptr = &map[key];
    // 后续 map 继续 insert / rehash
    ```
    应优先使用：
    ```cpp
    folly::F14NodeMap<Key, Value>
    ```
  - 建议重点检查：
    - `operator/adjuster/precise/author_interest.cpp`
    - `operator/adjuster/precise/author_rgh_factor.cpp`
    - `process/pk_generate_candidate_nid_emb_function_v5.cpp`

- **遍历顺序不能依赖**
  - `std::unordered_map` 本身也不保证稳定顺序，但一些业务代码可能在实践中隐式依赖当前实现的遍历顺序。
  - F14 替换后，遍历顺序大概率变化，可能影响：
    - Top-K 平分时的 tie-break
    - 日志输出顺序
    - 序列生成结果的稳定性
  - 对 `feeda-mv-grg` 的序列生成链路尤其需要关注，例如：
    - `operator/diversity/heji_rhythm_soft_rule.cpp`
    - `operator/diversity/quality_selection_soft_rule.cpp`

- **小规模 map 不一定有显著收益**
  - 如果 map 元素数量很小，例如小于 8 或 16 个，F14 的 SIMD chunk 优势不一定明显。
  - 对短生命周期、小容量、低频访问的局部 map，不建议机械替换。
  - 建议优先迁移：
    - 请求级大 map
    - 候选集级 map / set
    - parser 中复用频繁的特征字典
    - filter / adjuster 热路径中的查找结构

- **依赖与编译配置需要统一**
  - 虽然两个代码库已经发现目标库使用，但仍需确认所有子模块都能稳定 include：
    ```cpp
    #include <folly/container/F14Map.h>
    #include <folly/container/F14Set.h>
    ```
  - 如果存在独立编译单元、特殊 Bazel/CMake target 或轻量工具模块，需要检查 Folly 依赖是否已透传。
  - 建议先在已使用 F14 的模块附近试点，再扩大到 `std::unordered_map` 使用密集的 205 / 633 个文件范围。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
