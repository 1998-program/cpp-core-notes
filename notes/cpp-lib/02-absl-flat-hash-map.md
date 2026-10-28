# absl::flat_hash_map · SIMD 哈希探测
<!-- topic-nav:start -->
> 主题：`cpp/swiss-table` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](41-absl-flat-hash-map.md)
<!-- topic-nav:end -->


> 阅读分工：共同原理统一见[主题主文](41-absl-flat-hash-map.md)；本文保留补充场景与示例。被合并的旧讲解可在[原始版本](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/02-absl-flat-hash-map.md)查阅。

> **库**：[abseil/abseil-cpp](https://github.com/abseil/abseil-cpp) ⭐ 15k+
> **日期**：2026-05-03
> **主题**：Swiss Table 设计、SIMD 并行探测、内存布局优化、tombstone 消除

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

---

## 七、业务代码库适配分析
> **分析时间**：2026-05-16T18:33:40.973799
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

### 分析摘要
在两个业务代码库中，共发现 3525 处 `std::unordered_map` 声明和 1108 处 `std::unordered_set` 声明。
目前主服务（feeda-mv-grg / feeda-mv-grc）已直接使用 `absl::flat_hash_map`，
但已有 7 个文件使用 `absl::flat_hash_set`。

#### feeda-mv-grg

**Hash Map 使用统计**：
- `unordered_map`：726 次，分布在 202 个文件
  - `data/base.h`：112 次
  - `process/set2set_predict_function.cpp`：27 次
  - `operator/diversity/scatter_context.cpp`：21 次
  - `util/util.h`：21 次
  - `process/response_function.cpp`：20 次
- `unordered_set`：240 次，分布在 91 个文件
  - `process/user_model_service_input_function_gen_v5.cpp`：14 次
  - `operator/diversity/quota_modi_test1.cpp`：12 次
  - `process/newhot_replace.cpp`：11 次
  - `process/set2set_predict_function.cpp`：11 次
  - `operator/diversity/scatter_context.cpp`：8 次

**absl::flat_hash_map 使用情况**：已直接使用，涉及文件：
- `plugin/sid.h`

**absl::flat_hash_set 使用情况**：已使用，涉及文件：
- `model/paddle_model.h`
- `data/base.h`
- `process/user_model_service_input_function_gen_v4.cpp`
- `process/user_model_service_input_function_gen_longterm_v1.cpp`
- `process/pk_generate_candidate_nid_emb_function_v4.cpp`
- `process/feasign_cube.cpp`

**典型使用示例**（前 3 个）：

1. `plugin/follow_service.h:115`
   ```cpp
   baidu::rpc::Channel* _channel{nullptr};

    static std::unordered_map<int, std::string> _relation_type_map;
};

}
   ```

2. `plugin/graph_parser.cpp:75`
   ```cpp
   GraphVertexBuilder& vertex_builder) {

    std::unordered_map<std::string, std::string> depend_condition_map;
    // 添加使用的引擎的依赖    
    GRG_CHECK_RET(parse_engine_depend(config_unit["option"], depend_condition_map), "engine_depend parse fail!");
   ```

3. `plugin/sid.h:73`
   ```cpp
   //std::string _dict_file;
    //每个path和path index的 map
    std::unordered_map<std::string, uint32_t> _hash_path;
    // 每个小流量号对应的path以及path的val
    SidMapPtr _sid_map_ptr{nullptr};
    // 每个path和path index的string view map
   ```

#### feeda-mv-grc

**Hash Map 使用统计**：
- `unordered_map`：2799 次，分布在 633 个文件
  - `data/base.h`：342 次
  - `processor/prepare_lcn_info.cpp`：53 次
  - `strategy/short_micro/mcv_satisfaction_predict_xgb_model_pcs_handler.cpp`：53 次
  - `processor/pursuit_analyzer.cpp`：47 次
  - `processor/reddot/reddot_data.h`：36 次
- `unordered_set`：868 次，分布在 289 个文件
  - `data/base.h`：54 次
  - `strategy/short_micro/mcv_satisfaction_predict_xgb_model_pcs_handler.cpp`：38 次
  - `processor/reddot/reddot_data.h`：28 次
  - `util/util.hpp`：23 次
  - `strategy/diversity_with_price.cpp`：16 次

**absl::flat_hash_map 使用情况**：已直接使用，涉及文件：
- `dict/sid_new_dict.h`

**absl::flat_hash_set 使用情况**：已使用，涉及文件：
- `data/base.h`

**典型使用示例**（前 3 个）：

1. `user_data/pcs_precise_parallel_commented.cpp:507`
   ```cpp
   if (msv_readlist_info != nullptr){
                // 获取mcv深度特征映射（从readlist中提取的统计特征）
                ::std::unordered_map<std::string, float> mcv_deepes_features_map = 
                    msv_readlist_info->mcv_deepes_features_map;
                
                // 定义要提取的特征key列表（共20个）
   ```

2. `user_data/pcs_precise_parallel_commented.cpp:559`
   ```cpp
   if (ertiao_dt_readlist_info != nullptr){
                // 获取dt深度特征映射
                ::std::unordered_map<std::string, float> dt_deepes_features_map = 
                    ertiao_dt_readlist_info->dt_deepes_features_map;
                
                // 定义要提取的特征key列表（共9个）
   ```

3. `service/grc_service.cpp:321`
   ```cpp
   dynamic_timeout_cntl, (sctx._end_us - sctx._begin_us) / 1000);

    std::unordered_map<std::string, size_t> res_len_map;
    if (ret == 0) {
        // lijiang01 todo: 目前brpc Run之后会析构，所以下面CopyFrom基本是必须的
        // 那这里的Swap意义不大，考虑到下次response输出时可以重用PB的话，这里做拷贝更好
   ```

### 💡 适用性评估与建议

- **高优先级替换场景**：`data/base.h` 中大量 `unordered_map<uint64_t, double>` 用于实时打分（`instant_interest_nid_map`、`ltv_nid_map` 等），key 为整数时 absl hash 性能显著优于 std

- **string_view 场景**：`plugin/sid.h` / `dict/sid_new_dict.h` 已用 `flat_hash_map<string_view, uint32_t>`，可作为团队最佳实践推广

- **渐进式迁移**：先从热路径中的局部临时 map（如 `process/set2set_predict_function.cpp`、`processor/prepare_lcn_info.cpp`）开始替换，观察 CPU 和延迟变化

- **reserve 模式优化**：对于已经调用 `reserve` 的 unordered_map，直接替换为 flat_hash_map 可省去额外预留逻辑

### ⚠️ 引入风险与限制

- flat_hash_map 要求 key 支持 `absl::Hash`，自定义类型需特化 hash 函数

- 迭代器在 rehash 后失效，代码中若保存了 map 迭代器需特别注意

- 与 brpc/protobuf 等第三方库接口兼容性需验证，部分 API 只接受 `std::unordered_map`

- 节点不单独分配内存，不适合需要稳定指针/引用的场景（如返回内部节点指针）

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
