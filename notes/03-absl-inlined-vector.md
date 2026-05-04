# absl::InlinedVector · 栈上优化小容器

> **库**：[abseil/abseil-cpp](https://github.com/abseil/abseil-cpp) ⭐ 15k+
> **日期**：2026-05-04
> **主题**：SSO 内联存储、动态扩容策略、移动语义优化、零堆分配路径

---

## 一、设计思想

`std::vector` 的核心问题在于**哪怕只存一个元素，也必须在堆上分配内存**。一次 `new` 调用在 Linux 上的开销约 50~200ns（含锁竞争），而在推荐系统的热路径上，一个请求可能产生数千个小 vector（存 feature id、存候选 item 列表、存临时 score 数组），堆分配的累积开销非常可观。

`absl::InlinedVector<T, N>` 的设计思路：**在对象内部内联预分配 N 个元素的栈空间**。只要实际元素数量 ≤ N，整个容器完全不碰堆；超过 N 后，自动 fallback 到堆分配，行为退化为标准 `std::vector`。

关键权衡：
- N 越大，栈帧越大，可能引发 cache miss（栈帧挤出 L1）
- N 越小，fallback 越频繁，失去优化效果
- **经验值：N=4~16 对大多数推荐系统小容器场景效果最好**

---

## 二、核心实现

### 内存布局

```cpp
template <typename T, size_t N>
class InlinedVector {
 private:
  // 关键：union 让内联存储和堆指针共用同一块内存
  union Storage {
    // 内联模式：直接在对象内部存数据（无堆分配）
    alignas(T) char inlined[sizeof(T) * N];

    // 堆分配模式：存指针+容量
    struct {
      T* data;
      size_t capacity;
    } heap;
  } storage_;

  // 低位 bit 标记当前是内联模式(0)还是堆模式(1)
  // 高位存 size（内联模式下 size ≤ N，用 1 byte 足够）
  size_t metadata_;  // [size | is_heap_bit]
};
```

### 状态判断与访问

```cpp
// 判断当前是否在使用内联存储
bool is_inlined() const {
  return (metadata_ & 1) == 0;  // 最低位为0表示内联
}

size_t size() const {
  return metadata_ >> 1;  // 高位是 size
}

T* data() {
  if (is_inlined()) {
    // 内联模式：直接返回内部数组地址，无间接寻址
    return reinterpret_cast<T*>(storage_.inlined);
  } else {
    // 堆模式：一次指针解引用
    return storage_.heap.data;
  }
}
```

### push_back 的快路径

```cpp
void push_back(const T& value) {
  size_t s = size();

  if (ABSL_PREDICT_TRUE(is_inlined() && s < N)) {
    // 快路径：内联存储未满，直接 placement new，零堆分配
    ::new (static_cast<void*>(data() + s)) T(value);
    set_size(s + 1);
    return;
  }

  // 慢路径：需要扩容或切换到堆模式
  GrowAndPushBack(value);
}

void GrowAndPushBack(const T& value) {
  if (is_inlined()) {
    // 首次溢出：从内联切换到堆
    // 1. 在堆上分配 2*N 空间
    // 2. 把内联数据 move 过去（对 trivially_movable 类型直接 memcpy）
    // 3. 更新 metadata_ 的 is_heap_bit
    size_t new_cap = N * 2;
    T* heap_data = static_cast<T*>(::operator new(sizeof(T) * new_cap));
    absl::MoveRange(data(), data() + size(), heap_data);  // move-construct
    storage_.heap.data = heap_data;
    storage_.heap.capacity = new_cap;
    set_heap_bit();
  }
  // ... 正常 vector 扩容逻辑（1.5x 或 2x）
}
```

### 移动语义优化

```cpp
// 移动构造：内联模式下必须逐元素 move（不能直接 memcpy 指针）
InlinedVector(InlinedVector&& other) noexcept {
  if (other.is_inlined()) {
    // 内联数据必须 move-construct 每个元素（地址不可复用）
    absl::MoveRange(other.data(), other.data() + other.size(), data());
    set_size(other.size());
    other.clear();
  } else {
    // 堆模式：直接偷指针，O(1)
    storage_.heap = other.storage_.heap;
    set_heap_size_and_bit(other.size(), other.capacity());
    other.storage_.heap.data = nullptr;
    other.set_size(0);
  }
}
```

---

## 三、性能优化原理

### 🔥 零堆分配路径：消除 allocator 锁竞争

在多线程推荐服务中，`operator new` 内部有全局锁（或 arena 竞争）。`InlinedVector` 在内联模式下完全绕过 allocator：
- `push_back` 退化为一次 placement new + 写内存，约 **2~5ns**
- 对比 `std::vector::push_back`（含 `malloc`）：约 **50~200ns**（高并发下更糟）

### 🔥 缓存局部性：数据与控制信息同驻一个 cache line

内联的 `InlinedVector<int, 8>` 对象大小 = `8 * sizeof(int) + sizeof(metadata_)` = 36 bytes，**整个对象塞进 1 个 cache line（64 bytes）**。访问元素时不需要额外的内存跳转。

对比 `std::vector`：对象本身 24 bytes（pointer + size + capacity），元素在堆上另一个 cache line，访问必然触发 1 次额外 miss。

### 🔥 Trivially Relocatable 优化

对 `T` 满足 `absl::is_trivially_relocatable`（如 `int`、`float`、指针、大多数 POD 类型），从内联切换到堆时直接 `memcpy`，跳过逐元素析构/构造，比 `std::move` 快约 **3~10 倍**（取决于 N）。

---

## 四、使用示例

```cpp
#include "absl/container/inlined_vector.h"

// 典型用法：存小列表，N=8 覆盖绝大多数情况
absl::InlinedVector<int64_t, 8> item_ids;
item_ids.push_back(123456);
item_ids.push_back(789012);
// size=2 ≤ N=8，全程无堆分配

// 存 feature 值（推荐系统常见）
absl::InlinedVector<float, 16> feature_values;
for (int i = 0; i < 12; ++i) {
  feature_values.push_back(score[i]);  // 12 ≤ 16，零分配
}

// 存 Processor 输出的候选 item
absl::InlinedVector<ItemInfo, 4> top_candidates;
top_candidates.emplace_back(id, score, reason);

// 与标准算法完全兼容
std::sort(feature_values.begin(), feature_values.end(), std::greater<float>());

// 转换为 std::vector（需要传给不接受 InlinedVector 的接口时）
std::vector<int64_t> ids(item_ids.begin(), item_ids.end());

// 预分配（超过 N 时提前告知，避免二次 realloc）
absl::InlinedVector<std::string, 4> labels;
labels.reserve(10);  // 超过 N=4，提前在堆上分配，避免多次扩容
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

## 五、N 的选择指南

| 使用场景 | 推荐 N | 理由 |
|---------|--------|------|
| feature id 列表 | 8~16 | 单请求 feature 数通常 < 16 |
| 候选 item 池（精排前） | 4~8 | 精排前 top-k 通常 4~8 个 |
| 标签/reason 字符串 | 4 | 标签数量少，string 本身有 SSO |
| RPC 地址列表 | 4 | 大多数服务实例数 ≤ 4 |
| 不确定大小的列表 | 1 | N=1 仍比 vector 省一次分配（小 size 情况下）|

**规则**：用 p95 实际元素数量作为 N，既保证大多数情况零分配，又不让栈帧过大。

---

## 六、适用场景与限制

**✅ 适合**
- 热路径上频繁创建/销毁的小容器（元素数 < 20）
- 元素为值类型（int、float、指针、小 struct）
- 容器生命周期短（函数栈帧内，不跨 RPC）

**❌ 不适合**
- 元素数量不确定且可能很大（直接用 `std::vector`）
- 需要稳定的元素地址（内联模式下 move 会改变地址）
- 元素是复杂对象且 move 代价高（内联→堆切换需逐元素 move）

---

## 七、推荐在线架构场景

在 `gr-convergence` / `eden-grc` 等服务的热路径中：

```cpp
// 旧写法（每次创建都堆分配）
std::vector<int64_t> selected_ids;
selected_ids.reserve(8);

// 替换为（N=8 覆盖 p95，零堆分配）
absl::InlinedVector<int64_t, 8> selected_ids;

// FuncExecutor 并发调用结果收集（通常 4~8 个并发 RPC）
absl::InlinedVector<RecallResult, 8> recall_results;
for (auto& future : futures) {
  recall_results.push_back(future.get());
}
```

---

*下一篇：[folly::fbstring · SSO 字符串优化](./04-folly-fbstring.md)*

---

## 七、业务代码库适配分析
> **分析时间**：2026-05-04T21:23:34.920209
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

### 分析摘要
InlinedVector 分析尚未实现完整逻辑

### 📋 下一步行动

1. **POC 验证**：选取 `processor/response.cpp` 或 `process/response_function.cpp` 中的单个 protobuf 消息，用 FlatBuffers 重写 schema 并对比延迟
2. **brpc 协议适配**：调研 brpc 对 FlatBuffers 的支持程度，评估是否需要自定义 Protocol
3. **灰度策略**：从内部服务通信（grg-grc）开始试点，逐步扩展到 client-facing 接口

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
