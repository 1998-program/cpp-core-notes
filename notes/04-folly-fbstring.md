# folly::fbstring · SSO 字符串优化深度解析

> 来源：facebook/folly · 推荐在线架构组技术栈适配版

---

## 一、设计思想

`std::string` 的最大问题是"为平均情况而设计，为最坏情况分配内存"。任何一个字符串，哪怕只有 3 个字符，都会触发堆分配、维护引用计数或 capacity 字段，带来不可忽视的 malloc 压力。

`folly::fbstring` 的核心哲学是：**让短字符串永远不上堆**。它通过 SSO（Small String Optimization）将短于 23 字节的字符串完全内嵌在栈上的对象本身，与此同时对中等字符串和大字符串采用不同的内存布局策略，在三种场景下各自最优。与 `std::string` 的本质区别在于：它把对象本身当作 buffer，而不是永远指向堆。

---

## 二、核心实现：三态内存布局

fbstring 在同一个 24 字节对象中，通过最后两位 bit 区分三种存储类型：

```
┌─────────────────────────────────────────┐
│  fbstring 对象（固定 24 字节）            │
│                                         │
│  [00] Small  : 直接存 data，最后1字节    │
│               低5位=剩余容量             │
│  [10] Medium : 堆分配，无 COW，          │
│               独占 buffer               │
│  [11] Large  : 堆分配，COW（写时复制），  │
│               引用计数 in-place 在堆上   │
└─────────────────────────────────────────┘
```

### Small 布局（≤ 23 字节，最关键）

```cpp
// folly/FBString.h（简化）
struct MediumLarge {
    char*  data_;
    size_t size_;
    size_t capacity_;  // 最低2位用于 tag
};

union {
    char     small_[sizeof(MediumLarge)];  // 24 字节，直接存字符
    MediumLarge ml_;
};
```

Small 模式下，`small_[23]` 的低 5 位存"剩余容量"（maxSmallSize - size），最高位为 0 表示 Small 类型。这样：
- `size()` = `maxSmallSize - (small_[23] >> 2)`，一条位运算搞定
- `data()` = `&small_[0]`，**直接返回对象地址，零堆访问**
- 字符串内容连续存在对象本身，CPU 缓存行命中率极高

### Medium 布局（24 ~ 255 字节）

独立堆 buffer，不做 COW，`capacity` 字段低两位 `= 0b10`。写操作直接修改，无 refcount 开销。

### Large 布局（> 255 字节）

堆 buffer 头部预留 `RefCount`，实现写时复制。`capacity` 低两位 `= 0b11`。适用于大字符串多次只读传递，避免不必要的深拷贝。

---

## 三、性能优化原理

### 优化点 1：SSO 消灭堆分配热路径

推荐系统里大量字符串属于短字符串：特征 key（`feed_type`、`user_id`、`strategy_v2`），Protobuf repeated string 的单个元素，brpc channel name 等，长度通常在 8~20 字节。

fbstring SSO 阈值 23 字节，覆盖了绝大多数此类场景。相比 `std::string`（libstdc++ SSO 阈值仅 15 字节），多覆盖了 8 字节，显著减少 jemalloc `malloc/free` 调用频率。在 ng-framework DAG 图的每次请求处理中，一个算子（Processor）可能构造数十个临时 key 字符串，全部 SSO 意味着**整个算子栈帧内零堆分配**。

### 优化点 2：缓存友好的内存布局

Small 模式下字符串内容和元数据（size/capacity）在同一 24 字节对象内，单个 cache line（通常 64 字节）可容纳 2 个 fbstring 对象。对于 `std::vector<fbstring>` 这类容器，遍历时数据完全连续，prefetcher 可以有效预取。

相比之下，`std::string`（堆模式）的 `data_` 指针指向堆上另一块内存，遍历时产生大量 TLB miss 和 cache miss。在 filter-server 对候选集做批量特征 key 查找时，这个差异会被放大。

### 优化点 3：Large COW 减少大字符串深拷贝

对于 Protobuf message 序列化后的大 payload（通常几百字节到几 KB），fbstring 的 Large 模式通过引用计数实现 COW。在 brpc RPC 框架中，server 端将 response 序列化为 string 后多处传递（log、metrics、response body），COW 保证了只要不修改就不复制，降低了序列化 buffer 的内存峰值。

---

## 四、使用示例

```cpp
#include <folly/FBString.h>
#include <folly/FBVector.h>

// 1. 基本用法，接口与 std::string 完全兼容
folly::fbstring feature_key = "feed_type_v2";  // SSO，在栈上
folly::fbstring user_id = "123456789012345";    // 15字节，SSO
folly::fbstring long_key = "this_is_a_very_long_feature_key_exceed_23";  // Medium/Large

// 2. 与 std::string 互转
std::string std_str = feature_key.toStdString();
folly::fbstring fb_str(std_str);

// 3. 在 Processor 中替换 std::string
class FeatureKeyProcessor {
public:
    void process(RecContext* ctx) {
        // 用 fbstring 代替 std::string 存特征 key
        folly::fbvector<folly::fbstring> keys;
        keys.reserve(32);
        for (auto& item : ctx->candidates()) {
            keys.emplace_back(item.feature_key());  // 短 key 全部 SSO
        }
        // 批量查找，缓存友好
        for (const auto& key : keys) {
            auto it = feature_map_.find(key);
            if (it != feature_map_.end()) {
                ctx->set_feature(key, it->second);
            }
        }
    }
private:
    std::unordered_map<folly::fbstring, float> feature_map_;
};

// 4. 验证 SSO：短字符串不触发堆分配
folly::fbstring s = "hello";
assert(s.isSmall());   // true，在栈上
// s.data() 返回的地址在栈上，不是堆
```

---

## 五、性能基准

以下数据来自 folly benchmark 及社区测试（x86-64, Release 编译）：

| 操作 | `std::string` | `folly::fbstring` | 说明 |
|------|:---:|:---:|------|
| 构造短字符串（≤15字节） | ~8 ns | ~2 ns | std 也有 SSO，但阈值更小 |
| 构造短字符串（16~23字节）| ~25 ns（堆分配）| ~2 ns（SSO）| fbstring 优势区间 |
| 拷贝短字符串 | ~4 ns | ~2 ns | 栈拷贝 vs 可能的堆分配 |
| 遍历 10000 个短字符串 | 基准 | **~30% 更快** | 缓存行命中率差异 |
| 大字符串只读传递 | 深拷贝 O(n) | COW O(1) | Large 模式引用计数 |

---

## 六、适用场景与限制

**适用**：
- 大量短字符串（特征 key、策略标识、枚举值字符串化）
- 需要高吞吐的字符串容器遍历
- Protobuf string 字段的本地缓存/处理

**限制**：
- COW（Large 模式）在多线程写场景有竞争，需注意：**多线程修改同一个 Large fbstring 不安全**
- 与 `std::string_view` 配合需注意生命周期，fbstring 本身有 `string_piece()` 方法返回 `StringPiece`
- 项目中若同时使用 `std::string` 和 `fbstring`，接口边界有转换开销，建议一个模块内统一

---

## 七、在推荐在线架构中的实际落地

### 场景：ng-framework Processor 的特征 key 管理

```cpp
// 当前写法（std::string，每个 key 可能触发堆分配）
std::vector<std::string> feature_keys = {
    "ctr_score", "quality_v3", "user_active_level", "feed_type"
};

// 替换为 fbstring（全部 SSO，零堆分配）
folly::fbvector<folly::fbstring> feature_keys = {
    "ctr_score", "quality_v3", "user_active_level", "feed_type"
};
// 每个 key ≤ 23 字节，整个 vector 的 data 区连续，一个 cache line 能放下 2~3 个元素
```

### 场景：brpc Server 的 response 字符串传递

```cpp
// brpc RpcService::handle() 中
folly::fbstring serialized = proto_msg.SerializeAsString();
// 传给 log 系统、metrics、response body，全部 COW，不发生拷贝
log_service_->record(serialized);    // refcount++
metrics_->track(serialized);         // refcount++
response->set_body(serialized);      // refcount++
// 三处传递，实际内存只有一份
```

### 场景：jemalloc 压力减少

fbstring SSO 减少了 `malloc`/`free` 次数，配合 jemalloc 的 `MALLOC_CONF=prof:true` 做 heap profiling 时，可以明显看到 `fbstring` 替换后 `je_malloc` 调用栈变浅、分配频率下降，适合在 uther 性能平台上对比前后火焰图。

---

*生成时间：2026-05-05 | 序号：04 | 模块：folly::fbstring*
