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
> **分析时间**：2026-05-16T18:09:02.488494
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

### 分析摘要
在两个业务代码库中，共发现 5016 处 `std::vector` 声明、4147 处 `push_back/emplace_back` 调用、980 处 `reserve` 调用。
目前主服务（feeda-mv-grg / feeda-mv-grc）尚未直接使用 InlinedVector。

#### feeda-mv-grg

**std::vector 使用统计**：
- `vector_declarations`：924 次，分布在 194 个文件
  - `operator/diversity/scatter_context.cpp`：67 次
  - `process/set2set_predict_function.cpp`：52 次
  - `data/rid_tmp_info.h`：42 次
  - `util/util.h`：41 次
  - `process/newhot_replace.cpp`：31 次
- `push_back_calls`：1058 次，分布在 130 个文件
  - `util/util.h`：275 次
  - `operator/diversity/scatter_context.cpp`：138 次
  - `process/set2set_predict_function.cpp`：113 次
  - `process/response_function.cpp`：74 次
  - `process/user_model_service_input_function_gen_v5.cpp`：28 次
- `reserve_calls`：150 次，分布在 55 个文件
  - `operator/diversity/scatter_context.cpp`：13 次
  - `process/diversity_merge.cpp`：11 次
  - `process/post_mark.cpp`：10 次
  - `process/tagcf_weight_function.cpp`：10 次
  - `process/vids_gcf_embedding_function.cpp`：8 次

**InlinedVector 使用情况**：尚未在主服务中直接使用

**典型使用示例**（前 3 个）：

1. `model/paddle_model.h:118`
   ```cpp
   bool is_from_cube = true) const {
        std::vector<std::vector<float>> outputs;
        std::vector<uint64_t> instance_id_vec;
        std::vector<T> candidate_input_vec;
        outputs.reserve(candidate_vec.size());
        instance_id_vec.reserve(candidate_vec.size());
   ```

2. `model/paddle_model.h:119`
   ```cpp
   std::vector<std::vector<float>> outputs;
        std::vector<uint64_t> instance_id_vec;
        std::vector<T> candidate_input_vec;
        outputs.reserve(candidate_vec.size());
        instance_id_vec.reserve(candidate_vec.size());
        candidate_input_vec.reserve(candidate_vec.size());
   ```

3. `model/paddle_model.h:197`
   ```cpp
   std::string _model_name;
    std::vector<FieldAccessor> _score_fields;
    const PredictClient* _predict_client{nullptr};

    Context* _context{nullptr};
   ```

#### feeda-mv-grc

**std::vector 使用统计**：
- `vector_declarations`：4092 次，分布在 763 个文件
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`：132 次
  - `processor/new_adjust/precise_score_init.cpp`：104 次
  - `data/base.h`：94 次
  - `processor/reddot/dibar_reddot_rank_query_words.cpp`：92 次
  - `processor/video_launch/dibar/dibar_precise_fusion.cpp`：87 次
- `push_back_calls`：3089 次，分布在 386 个文件
  - `processor/new_adjust/precise_score_init.cpp`：177 次
  - `processor/video_launch/dibar/dibar_precise_fusion.cpp`：127 次
  - `processor/video_launch/response_for_grg.cpp`：111 次
  - `processor/reddot/dibar_reddot_rank_query_words.cpp`：98 次
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`：77 次
- `reserve_calls`：830 次，分布在 229 个文件
  - `processor/video_launch/dibar/dibar_precise_fusion.cpp`：33 次
  - `dict/kv_dict_parse.h`：28 次
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`：28 次
  - `processor/new_adjust/precise_score_init.cpp`：26 次
  - `strategy/reddot/reddot_xgb_sort.cpp`：25 次

**InlinedVector 使用情况**：尚未在主服务中直接使用

**典型使用示例**（前 3 个）：

1. `service/grc_http_service.cpp:152`
   ```cpp
   std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
   ```

2. `service/grc_http_service.cpp:153`
   ```cpp
   std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
    if (sub_access_off_vec_str != nullptr) {
   ```

3. `service/grc_service.cpp:370`
   ```cpp
   GraphData* after_adjust_rank_data = graph->find_data("AfterAdjustRankResult");
        if (after_adjust_rank_data) {
            std::vector<RidTmpInfoPtr> const* items = after_adjust_rank_data->cvalue<std::vector<RidTmpInfoPtr>>();
            if (items && !items->empty()) {
                //前置条件检查通过，开始打印日志
                thread_local std::string log_str;
   ```

### 💡 适用性评估与建议

- **优先替换场景**：函数局部临时容器（如存储 feature id 列表、候选 item score数组），元素数通常 ≤4~16，使用 InlinedVector 可避免堆分配

- **渐进式迁移**：先从热点文件（如 `process/response_function.cpp`、`processor/response.cpp`）中的局部 vector 开始替换，观察 CPU 和延迟变化

- **reserve 模式优化**：对于已经使用 `reserve` 的 vector，如枚举大小固定且小于 16，直接替换为 InlinedVector 可省去 reserve 调用

- **注意栈帧大小**：InlinedVector 增加栈帧大小，对于深层递归或高并发线程，需测试 stack overflow 风险

### ⚠️ 引入风险与限制

- InlinedVector 增加对象大小，在大量存储于容器的容器（如 `std::vector<InlinedVector<T, N>>`）中可能导致内存浪费

- 与 brpc/protobuf 等第三方库接口兼容性需验证，部分第三方 API 只接受 `std::vector`

- 团队学习成本：InlinedVector API 与 vector 高度兼容，但内存布局差异可能影响 debug 和性能调试

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
