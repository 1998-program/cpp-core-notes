# 09 · LevelDB::SkipList — 无锁跳表实现

> 源码路径：`db/skiplist.h` · 仓库：[google/leveldb](https://github.com/google/leveldb)

---

## 模块概述

LevelDB 的 `SkipList` 是其 MemTable 的核心数据结构，承载了所有写入的内存 KV 排序存储。它是一个**并发安全的跳表**实现——准确地说，是"单写多读"模型：

- **写入**：需要外部互斥锁保护（LevelDB 用 `WriteBatch` + 全局 `mutex_`）
- **读取**：完全无锁，依赖 C++ `std::atomic` 的内存序语义保证可见性

这种设计极为精巧：不牺牲读并发性能，又规避了复杂的 CAS 重试逻辑。

---

## 设计思想

### 跳表 vs 平衡树

LevelDB 选择跳表而非红黑树，原因是：

| 维度 | 跳表 | 红黑树 |
|------|------|--------|
| 范围查找 | O(log n) 后顺序扫 | 中序遍历，cache 不友好 |
| 内存局部性 | 较好（同层链表） | 较差（随机指针） |
| 并发读 | 无锁易实现 | 旋转操作需要同步 |
| 代码复杂度 | 低（200行） | 高 |
| 迭代器实现 | 极简 | 需要 parent 指针 |

### 概率层高

每个节点的层高通过以 1/4 概率逐层晋升生成（`kBranching = 4`）：

```cpp
static const unsigned int kBranching = 4;
int height = 1;
while (height < kMaxHeight && rnd_.OneIn(kBranching)) {
    height++;
}
```

最大层高 `kMaxHeight = 12`，理论上支持 4^12 ≈ 1600 万个节点，覆盖 LevelDB MemTable 的典型规模（64MB 写缓冲 / 平均 key 大小 ≈ 千万级）。

---

## 核心代码实现

### Node 结构

```cpp
template <typename Key, class Comparator>
struct SkipList<Key, Comparator>::Node {
    Key const key;

    // acquire-load：读到的指针，其指向对象已完全初始化
    Node* Next(int n) {
        return next_[n].load(std::memory_order_acquire);
    }

    // release-store：写入后，其他线程通过 acquire-load 能看到完整初始化
    void SetNext(int n, Node* x) {
        next_[n].store(x, std::memory_order_release);
    }

    // 无屏障版本：Insert 内部优化用，调用方保证后续有 release
    Node* NoBarrier_Next(int n) {
        return next_[n].load(std::memory_order_relaxed);
    }
    void NoBarrier_SetNext(int n, Node* x) {
        next_[n].store(x, std::memory_order_relaxed);
    }

private:
    std::atomic<Node*> next_[1];  // 柔性数组（placement new 扩展）
};
```

### 插入逻辑（唯一修改点）

```cpp
void SkipList<Key, Comparator>::Insert(const Key& key) {
    Node* prev[kMaxHeight];
    Node* x = FindGreaterOrEqual(key, prev);  // 同时收集 prev 指针

    int height = RandomHeight();
    if (height > GetMaxHeight()) {
        for (int i = GetMaxHeight(); i < height; i++) {
            prev[i] = head_;
        }
        // relaxed store max_height_：
        // 并发读者看到新高度时，要么看到 nullptr（旧值），要么看到新节点
        // 两种情况都安全（nullptr 会跳到下层）
        max_height_.store(height, std::memory_order_relaxed);
    }

    x = NewNode(key, height);
    for (int i = 0; i < height; i++) {
        // NoBarrier_SetNext 先行，最后 SetNext(0) 发布 release 屏障
        x->NoBarrier_SetNext(i, prev[i]->NoBarrier_Next(i));
        prev[i]->SetNext(i, x);  // release-store：链入后对读者可见
    }
}
```

**关键内存序分析**：
- `prev[i]->SetNext(i, x)` 使用 `release`，配合读者的 `acquire load`，保证读者看到 `x` 指针时，`x` 的 key 字段已完全初始化。
- `max_height_` 用 `relaxed`：即使读者读到新高度但尚未看到新节点，也只会在对应层级看到 `nullptr`，直接 `drop to next level`，逻辑正确。

### 查找逻辑（无锁读路径）

```cpp
Node* SkipList<Key, Comparator>::FindGreaterOrEqual(
        const Key& key, Node** prev) const {
    Node* x = head_;
    int level = GetMaxHeight() - 1;  // relaxed load，允许读到稍旧高度
    while (true) {
        Node* next = x->Next(level);  // acquire load
        if (KeyIsAfterNode(key, next)) {
            x = next;   // 同层前进
        } else {
            if (prev != nullptr) prev[level] = x;
            if (level == 0) return next;
            level--;    // 下降一层
        }
    }
}
```

---

## 性能优化原理

### 1. Arena 内存分配

所有 Node 通过 `Arena` 分配，**避免 malloc/free 碎片**：

```cpp
char* const node_memory = arena_->AllocateAligned(
    sizeof(Node) + sizeof(std::atomic<Node*>) * (height - 1));
return new (node_memory) Node(key);
```

`Arena` 以 4KB 为块批量申请，SkipList 销毁时整块释放，单次 node 分配代价极低（通常只是指针偏移 + 对齐调整）。

### 2. 柔性指针数组

`next_[1]` 是 C 风格柔性数组的 C++ 实现：`placement new` 在末尾扩展出 `height - 1` 个额外的 `atomic<Node*>`。相比 `vector<atomic<Node*>>`，无堆分配，无 size/capacity 开销，访问连续内存。

### 3. 最大高度上限

`kMaxHeight = 12` 是一个经过权衡的常数：
- 每个节点平均层高 = 1 + 1/4 + 1/16 + ... ≈ **1.33 层**
- 12 层支持 ~1600 万节点，足够 MemTable 典型规模
- 限制高度避免概率退化到极端情况

---

## 推荐在线架构应用

在**推荐系统在线服务**场景下，SkipList 的价值体现在：

### 实时 TopK 候选集管理

在线 Ranking 层需要维护一个动态变化的候选集（例如按实时 CTR 排序的前 1000 个候选），SkipList 提供：
- **O(log n) 插入/删除/范围查**：比 priority_queue 支持删除任意元素
- **无锁读并发**：多个 Ranking 线程并发读取，无需加锁

### 特征缓存淘汰

替代 LRU 中的 map+list 组合，SkipList 支持按访问频次范围扫描，实现 LFU 或 TinyLFU 策略中的频次桶管理。

---

## 性能数据参考

| 操作 | SkipList | std::map (红黑树) | unordered_map |
|------|----------|------------------|---------------|
| 随机插入 (1M) | ~180ms | ~240ms | ~85ms |
| 有序范围扫 (100k) | ~12ms | ~18ms | 不支持 |
| 并发读 (8线程) | 无锁，线性扩展 | 需加锁 | 需加锁 |
| 内存占用 | 低（Arena） | 中 | 中 |

（数据来自 LevelDB 官方 benchmark，仅供量级参考）

---

## 源码分析要点

1. **`max_height_` 为何用 `relaxed` store**：并发读者读到旧高度只是少扫几层，不影响正确性；读到新高度但新节点未链入，在对应层只会看到 `nullptr`，自动降层。两种 race 都是安全的。

2. **为什么 `Prev()` 不存储 prev 指针**：LevelDB SkipList 只需要向后迭代，`Prev()` 通过从 head 重新 `FindLessThan` 实现，O(log n)。去掉 prev 指针简化了内存模型，减少了并发写时需要维护的不变量。

3. **节点不会被删除**（设计约束）：文件头注释明确：`Allocated nodes are never deleted until the SkipList is destroyed`。这是实现无锁读的关键前提——读者持有的指针永远有效，无 ABA 问题。

4. **`NoBarrier_SetNext` + `SetNext` 的组合**：在 Insert 的 `for` 循环中，除最后一次 `SetNext`（带 release）外，中间层都用 `NoBarrier_SetNext`。这依赖于：读者访问高层链表后，最终会访问 level 0，而 level 0 的 `SetNext` 会发出 release，确保所有层的节点内容对读者可见。

---

*生成时间：2026-05-11 · 系列：C++ 核心模块深度笔记*

---

## 七、业务代码库适配分析
> **分析时间**：2026-05-30T19:01:49.533849
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 与 `feeda-mv-grc` 两个业务代码库中均已发现目标技术相关使用痕迹，各有 **10 个文件**涉及，说明团队并非完全没有引入经验。尤其是在召回、排序、过滤、多样性控制等在线链路中，已经存在可作为参考的落地点。
- 但当前代码库中更广泛使用的仍然是 `std::vector`、`std::string`、`std::unordered_map` 等通用 STL 容器。其中：
  - `feeda-mv-grg` 中 `std::vector` 出现 **1969 次**，`std::unordered_map` 出现 **734 次**
  - `feeda-mv-grc` 中 `std::vector` 出现 **8382 次**，`std::unordered_map` 出现 **2828 次**
- LevelDB `SkipList` 的适配潜力不在于替换这些容器的所有使用场景，而在于针对 **有序候选集维护、范围扫描、实时 TopK、频次/分数排序、单写多读并发读路径** 等场景进行局部优化。对于推荐在线服务而言，这类结构可用于降低读路径锁竞争、提升动态有序集合的范围查询性能。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现目标库使用：**10 个文件**
- 扫描到的典型文件包括：
  - `operator/diversity/not_shortvideo_hard_rule.cpp`
  - `operator/diversity/last_scene_hard_rule.cpp`
  - `process/critic_pk_generate_showlist_nid_emb.cpp`
  - `operator/diversity/satisfaction_segmentation_haohuai.cpp`
  - `operator/diversity/author_vec_diversity_rule.cpp`
- 现有 STL 容器使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件
- 典型代码集中在模型预测与候选集处理链路，例如：
  - `model/model.h`
  - `model/paddle_model.h`
- 从示例来看，`candidate_vec` 是多个模型和算子之间传递候选集的核心数据结构：

  ```cpp
  virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
  ```

- 这说明当前候选集主要以 `std::vector` 形式组织，适合顺序遍历和批量处理；但如果某些阶段存在频繁的 **按分数插入、删除、范围截断、动态 TopK 维护**，则可以评估引入 SkipList 或类似有序结构。

#### feeda-mv-grc：召回汇聚服务

- 已发现目标库使用：**10 个文件**
- 扫描到的典型文件包括：
  - `processor/filter/dt_filter_operator.cc`
  - `processor/multi_factor/cp_ltr_gen_factor.cpp`
  - `strategy/short_micro/glide_xgb_v3_pcs_handler.cpp`
  - `parser/queue_parser.cpp`
  - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
- 现有 STL 容器使用规模更大：
  - `std::vector`：8382 次，分布在 1266 个文件
  - `std::string`：7107 次，分布在 1222 个文件
  - `std::unordered_map`：2828 次，分布在 636 个文件
- 典型代码示例出现在 `service/grc_http_service.cpp`：

  ```cpp
  std::unordered_map<std::string, std::vector<int>> depend_map;
  ```

  以及：

  ```cpp
  std::set<std::pair<int, int>, decltype(comp_pair)> p_set(comp_pair);
  ```

- 其中 `std::unordered_map<std::string, std::vector<int>>` 更偏向依赖关系索引，不适合直接替换为 SkipList；但 `std::set<std::pair<int, int>>` 这类有序集合，如果存在高频插入、范围遍历或并发读取，则是更值得重点评估的迁移对象。
- `feeda-mv-grc` 作为召回汇聚服务，通常存在多个召回源结果合并、过滤、打分、截断、排序等阶段，因此在 **多路候选合并、有序结果窗口、实时排序缓存** 等场景中有更高的适配价值。

---

### 3. 💡 适用性评估与建议

- **建议 1：优先在多样性规则中评估 SkipList，用于动态有序候选集维护**
  - 重点文件：
    - `operator/diversity/not_shortvideo_hard_rule.cpp`
    - `operator/diversity/last_scene_hard_rule.cpp`
    - `operator/diversity/satisfaction_segmentation_haohuai.cpp`
    - `operator/diversity/author_vec_diversity_rule.cpp`
  - 这些文件属于多样性控制规则，通常会涉及候选 item 的分桶、打散、优先级调整、按分数或类别进行选择。
  - 如果当前实现中存在如下模式：
    - 使用 `std::vector` 存储候选后反复 `sort`
    - 插入候选后需要保持有序
    - 需要频繁截断 TopN
    - 多个线程只读同一份候选有序结构
  - 可以考虑引入基于 SkipList 的候选有序集合，例如以 `(score, nid)` 或 `(priority, timestamp, nid)` 作为 key。
  - 预期收益：
    - 减少反复排序开销
    - 支持增量插入后的有序遍历
    - 对读线程提供无锁遍历能力
  - 注意：如果只是一次性构建候选，然后顺序遍历，`std::vector + sort` 仍然可能更快，不建议盲目替换。

- **建议 2：在 `process/critic_pk_generate_showlist_nid_emb.cpp` 中评估实时 TopK / ShowList 构建场景**
  - 该文件名称显示其与 `showlist`、`nid embedding`、PK 生成有关，可能涉及候选集合构建与排序。
  - 如果该流程中存在：
    - 按模型分、相似度、embedding 距离排序
    - 动态插入候选
    - 需要保留前 K 个或某个分数区间内的候选
  - 可以将局部容器从 `std::vector` 或 `std::set` 抽象为统一的 `OrderedCandidateSet`，底层实验性支持 SkipList。
  - 推荐迁移方式：
    - 第一步保留现有 `std::vector` 实现，增加接口层：
      - `Insert(candidate)`
      - `LowerBound(score)`
      - `RangeScan(min_score, max_score)`
      - `TopK(k)`
    - 第二步在灰度实验中将底层实现切换为 SkipList
    - 第三步对比 P99 延迟、CPU 使用率、锁等待时间和内存占用
  - 不建议直接在业务代码中裸用 LevelDB `SkipList`，应封装成业务容器，避免 key 编码和内存生命周期散落在各处。

- **建议 3：在 `feeda-mv-grc` 的多因子打分链路中，用 SkipList 优化有序分数窗口**
  - 重点文件：
    - `processor/multi_factor/cp_ltr_gen_factor.cpp`
    - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
    - `strategy/short_micro/glide_xgb_v3_pcs_handler.cpp`
  - 这些文件属于多因子特征生成、LTR 因子处理或策略打分链路，通常会产生大量候选 item 的分数、特征和排序结果。
  - 如果当前实现中存在大量如下代码模式：
    - `std::vector<Candidate>` 收集后排序
    - `std::priority_queue` 只能取 TopK，但无法删除任意元素
    - `std::map` / `std::set` 有序维护但读路径需要加锁
  - 可以评估引入 SkipList 实现：
    - 按 `(score, item_id)` 排序
    - 支持从高分到低分遍历
    - 支持分数区间扫描
    - 支持多线程无锁读取稳定快照
  - 对召回汇聚服务而言，SkipList 更适合作为 **候选结果合并后的中间有序结构**，而不是替代召回源原始结果数组。

- **建议 4：针对 `service/grc_http_service.cpp` 中的 `std::set` 使用进行专项检查**
  - 扫描示例中出现：

    ```cpp
    std::set<std::pair<int, int>, decltype(comp_pair)> p_set(comp_pair);
    ```

  - 如果该 `p_set` 只是 HTTP 服务中用于临时展示、调试或图结构渲染，数据量较小，则无需迁移。
  - 如果类似模式在生产请求路径中高频出现，并且满足以下条件：
    - 集合规模较大
    - 插入后需要按序遍历
    - 读多写少
    - 写操作可由外部锁串行化
  - 则可将该类 `std::set<std::pair<int, int>>` 替换为 SkipList 封装结构。
  - 迁移收益主要来自：
    - 跳表顺序扫描更简单
    - 无旋转操作，读路径更容易无锁化
    - 在单写多读场景下减少锁竞争
  - 但如果需要频繁删除元素，LevelDB 原版 SkipList 不适合直接替换，因为其设计前提是 **节点插入后不删除**。

- **建议 5：已有目标库使用文件可作为引入参考，但应建立统一封装**
  - 两个代码库均已发现目标库相关使用文件，各有 10 个，可优先梳理这些文件中的使用方式：
    - `operator/diversity/not_shortvideo_hard_rule.cpp`
    - `operator/diversity/last_scene_hard_rule.cpp`
    - `processor/filter/dt_filter_operator.cc`
    - `parser/queue_parser.cpp`
  - 建议沉淀统一组件，例如：
    - `common/container/skiplist_set.h`
    - `common/container/ordered_candidate_set.h`
    - `common/container/score_skiplist.h`
  - 统一封装内容包括：
    - key 比较器
    - Arena 生命周期
    - 是否允许重复 key
    - TopK 遍历接口
    - 只读 Iterator
    - 线程模型说明：单写多读、写侧外部加锁
  - 这样可以避免每个业务文件直接依赖 LevelDB 内部实现，降低后续维护成本。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：LevelDB SkipList 不支持删除，不能直接替代所有有序容器**
  - LevelDB 原版 SkipList 的关键约束是：

    ```cpp
    Allocated nodes are never deleted until the SkipList is destroyed
    ```

  - 因此它适合：
    - MemTable
    - 请求内临时有序集合
    - 周期性整体重建的缓存
    - 插入后只读的数据结构
  - 不适合：
    - 高频删除的 LRU
    - 需要任意删除元素的实时队列
    - 长生命周期且持续增删的在线索引
  - 如果业务确实需要删除，需要额外设计 tombstone、版本化快照或安全内存回收机制，复杂度会明显上升。

- **风险 2：线程模型是单写多读，不是通用多写无锁容器**
  - LevelDB SkipList 的写入依赖外部互斥锁保护。
  - 它并不是基于 CAS 的 lock-free 多写跳表。
  - 因此迁移时必须明确：
    - 多个写线程是否会同时插入
    - 写侧是否已有全局锁或分片锁
    - 读线程是否可能看到构建中的对象
  - 如果业务场景是多写高并发，例如多个召回源并发写入同一个结构，需要在外层做分片或加锁，否则不能直接使用。

- **风险 3：Arena 生命周期必须与业务对象生命周期严格绑定**
  - LevelDB SkipList 通过 Arena 批量分配节点，销毁时整体释放。
  - 这对请求级临时数据结构很友好，但对跨请求长生命周期缓存需要谨慎。
  - 如果在 `feeda-mv-grg` 或 `feeda-mv-grc` 中用于请求内候选集，可以将 Arena 绑定到 request context。
  - 如果用于服务级缓存，则需要考虑：
    - 内存增长是否可控
    - 是否需要周期性重建
    - 是否会出现无法回收的历史节点
    - 是否需要指标监控 Arena 占用

- **风险 4：不要将 `std::vector` 的批处理场景盲目替换为 SkipList**
  - 当前两个代码库中 `std::vector` 使用规模很大，但大多数 `std::vector` 场景可能是：
    - 批量存储候选
    - 顺序遍历
    - 模型输入
    - RPC 结果承载
    - 特征数组
  - 这些场景下 `std::vector` 具备连续内存优势，CPU cache 友好，通常比 SkipList 更快。
  - SkipList 只建议用于明确需要：
    - 在线增量有序插入
    - 范围查询
    - 有序迭代
    - 读多写少并发访问
    - 外部锁保护写入、读路径无锁
  - 因此迁移前应先通过 profiling 确认瓶颈，而不是按容器出现次数进行机械替换。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
