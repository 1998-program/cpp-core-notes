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
