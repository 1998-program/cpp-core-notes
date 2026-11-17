# LevelDB BloomFilterPolicy：位数组、探测与"可能存在"

## 场景

为一批键生成一个小摘要，之后先用它判断"某个键是否可能在这批键里"，避免直接去做更贵的查找。本篇只讲 `FilterPolicy` 接口和这个内建 Bloom 编码本身，不涉及表文件里过滤器如何组织、何时被查询。

## 它是什么

`FilterPolicy` 是三个纯虚函数的接口：`Name()`、`CreateFilter(keys, n, dst)`、`KeyMayMatch(key, filter)`。

契约很明确：键确实在传给 `CreateFilter` 的列表里时，`KeyMayMatch` 必须返回 true；键不在列表里时，可以返回 true 也可以返回 false，但应当以高概率返回 false。也就是说 true 只代表"没被排除"，false 才是可靠的否定。

这条"不产生假阴性"的前提是有条件的：传给 `KeyMayMatch` 的必须是同一策略产生的、完整且未被改动的过滤器。对损坏或不匹配的输入，接口没有给出任何保证。接口还要求只往 `dst` 尾部追加，不改它已有的内容。

## 三个关键机制

**编码。** `bits = n * bits_per_key`；小于 64 位时抬到 64 位（注释说小 n 时假阳性率会很高，用最小长度修正）；`bytes = (bits + 7) / 8`，再把 `bits` 改写成 `bytes * 8`；目标字符串扩出这么多零字节，最后追加一个字节记录探测次数 k。所以过滤器就是"位数组 + 末尾 1 字节 k"。

**双哈希。** 每个键算一次哈希得 `h`，`delta` 是 `h` 循环右移 17 位；每轮先用 `h % bits` 定位并置位，再执行 `h += delta`；查询侧以相同顺序检查这些位。构造时 `k_ = bits_per_key * 0.69`（约 ln2，且刻意向下取整减少探测开销），夹在 1 到 30 之间。这一步只对正常参数成立；本例只用正数 10，负数或极端取值的定义不在本版本接口说明范围内，本文不做推广。

**查询短路。** 过滤器长度小于 2 字节返回 false；末尾字节解出的 k 大于 30 视为保留编码返回 true；否则逐位探测，任一位为 0 立即 false，全命中才 true。读侧用末尾字节里的 k，源码注释说明这样能读取用不同参数生成的过滤器。

## 示例

```cpp
// 阅读示例：仅在内存中操作数据并打印结果；尚未编译运行。
// 只使用公开 API；需要链接已编译的 leveldb 库。
#include <cstdio>
#include <string>
#include <vector>

#include <leveldb/filter_policy.h>
#include <leveldb/slice.h>

int main() {
  const leveldb::FilterPolicy* policy = leveldb::NewBloomFilterPolicy(10);
  if (policy == nullptr) {
    std::printf("policy=null\n");
    return 1;
  }

  // 接口要求 keys 按比较器排序；这里按字节序排列。
  const std::vector<std::string> keys = {"alpha", "beta", "delta", "epsilon",
                                         "gamma"};
  std::vector<leveldb::Slice> slices;
  slices.reserve(keys.size());
  for (size_t i = 0; i < keys.size(); ++i) {
    slices.push_back(leveldb::Slice(keys[i]));
  }

  std::string filter;
  policy->CreateFilter(slices.data(), static_cast<int>(slices.size()), &filter);

  std::printf("policy=%s\n", policy->Name());
  std::printf("keys=%zu\n", slices.size());
  std::printf("filter_bytes=%zu\n", filter.size());

  // 插入过的键回查：接口契约要求返回 true。
  for (size_t i = 0; i < keys.size(); ++i) {
    const bool may = policy->KeyMayMatch(slices[i], leveldb::Slice(filter));
    std::printf("key=%s may_match=%d\n", keys[i].c_str(), may ? 1 : 0);
  }

  delete policy;
  return 0;
}
```


键按字节顺序写成 `alpha`、`beta`、`delta`、`epsilon`、`gamma`，符合接口"按键比较器排序"的前提，而不是依赖实现恰好不在意顺序。按源码推导：5 个键乘 10 位等于 50 位，抬到 64 位即 8 字节，再加末尾 1 字节 k，`filter_bytes` 应为 9；插入过的 5 个键回查应全部为 1，这是契约的直接后果。以上都是源码推导，不是运行结果。

## 边界

不要把过滤器的结论换算成实际查找代价或延迟，也不要据此判断键存在。策略对象由 `NewBloomFilterPolicy` 分配，调用方负责 delete，并且必须在所有使用它的数据库关闭之后。若自定义比较器忽略键的一部分（例如尾部空格），不能直接用内置布隆，需要实现同样忽略该部分的策略。

## 固定来源

- 仓库与固定提交：`google/leveldb` @ `bb74ef739973a70ca9f0d90788a1da89b788c0ad`
- 本篇引用文件：
  - `util/bloom.cc` — https://github.com/google/leveldb/blob/bb74ef739973a70ca9f0d90788a1da89b788c0ad/util/bloom.cc
  - `include/leveldb/filter_policy.h` — https://github.com/google/leveldb/blob/bb74ef739973a70ca9f0d90788a1da89b788c0ad/include/leveldb/filter_policy.h
  - `util/hash.h` — https://github.com/google/leveldb/blob/bb74ef739973a70ca9f0d90788a1da89b788c0ad/util/hash.h

## 验证状态

源码核对，示例尚未编译运行。
