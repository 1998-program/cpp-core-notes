# protobuf::Arena —— 把 Protobuf 反序列化做成"一次性分配"的内存竞技场

> 2026-06-26 · C++ 核心模块笔记 #37
> 关键词：`Arena`、`AllocateAligned`、bump-pointer、`Owns`、ng-framework 在线服务、brpc RPC 反序列化

---

## 0. 为什么 Arena 是大流量 Protobuf 服务的"命门"

在以 brpc + Protobuf 为骨架的 ng-framework 推荐在线服务里，每条 RPC 请求都会做一次 `request.ParseFromZeroCopyStream()` 或 `request.ParseFromArray()`，紧接着构造一个甚至几个 `response` 协议对象。

朴素 Protobuf 的代价：

- 每个 `Message`、每个嵌套子 message、每个 `string` 字段、每个 `repeated` 元素都是独立 `new`。
- 一个 1KB 的 PB 包，往往触发 **几十次 malloc/free**。
- QPS 上万时，jemalloc 也扛不住 —— 锁、page split、free 链表抖动全暴露。

`google::protobuf::Arena` 就是为了把这一切 **合并成一次大块分配** 而生：所有子对象在 Arena 上线性 bump，整个 Message 树释放时只需 `delete arena;` 一次。

---

## 1. 核心数据结构（v3.x / v25 行为）

```
Arena
 ├─ SerialArena * thread_local        // 每线程独立链
 │   ├─ ArenaBlock (head)             // 当前块：[ptr, limit)
 │   ├─ ArenaBlock (prev)             // 已写满，串成链表
 │   └─ cleanup_list                  // 析构回调（非平凡对象）
 └─ ArenaOptions
     ├─ initial_block_size  (默认 256B → 翻倍至 8KB)
     ├─ max_block_size      (默认 8KB)
     └─ block_alloc / block_dealloc   // 可注入 jemalloc / tcmalloc
```

关键点：
- **bump-pointer**：`ptr += size; if (ptr > limit) NewBlock();` —— 纯算术，无锁。
- **SerialArena 走 thread_local**：多线程并发 `Arena::Create<T>()` 完全无竞争；只有 NewBlock 才回 Arena 总池抢一次轻量原子。
- **块大小指数增长**：避免短消息频繁开块，又给冷请求一个 256B 的友好起点。

---

## 2. 关键 API 与"两个世界"

| API | 行为 | 何时用 |
|---|---|---|
| `Arena::Create<T>(arena, args...)` | 在 arena 上构造 T；非平凡 → 登记 cleanup | 任意 PB 对象 |
| `Arena::CreateMessage<M>(arena)`   | 专为 Message，绑定 arena | 顶层 Request/Response |
| `msg->New(arena)` | 同上，运行时多态版 | 反射/动态消息 |
| `arena.Own(ptr)` | 把堆对象交给 arena 在销毁时一并 delete | 接管第三方资源 |
| `arena.SpaceUsed()` / `SpaceAllocated()` | 度量水位 | 调参、监控 |

> ⚠️ "两个世界"：堆世界（默认）和 arena 世界。**子消息必须与父消息同一 arena**，否则 PB 内部会走 `MergeFrom` 而非"指针接管"，反而劣化。`msg->mutable_xxx()` 自动遵守这一约束。

---

## 3. 在 brpc + ng-framework 中的实战收益

ng-framework 的 service handler 通常长这样：

```cpp
void Recall::Recommend(google::protobuf::RpcController* cntl_base,
                       const RecommendRequest* request,
                       RecommendResponse* response,
                       google::protobuf::Closure* done) {
    brpc::ClosureGuard done_guard(done);
    // request / response 由 brpc 在 Arena 上构造（见下）
    ...
}
```

brpc 通过 `Server::AddService(svc, brpc::SERVER_DOESNT_OWN_SERVICE)` 时，`ServerOptions::auto_arena = true`（默认）会让每个请求自带一个 per-call Arena：
- `request` 反序列化结果全部落 arena；
- `response` 通过 `*response = *Arena::CreateMessage<Resp>(arena)` 也落 arena；
- 整个 RPC 完成（Closure 调用后）arena 整体析构 —— **几十次 free 合并为 1 次大 free**。

实测在召回融合层（recall-fusion，单包 ~8KB、单机 12k QPS）：
- 关闭 Arena：jemalloc lg-dirty-mult 抖动 ~3%，p99 +180μs；
- 开启 Arena：CPU 降 6~9%，p99 -150μs，allocator counter 下降一个量级。

---

## 4. 与 jemalloc / brpc::IOBuf 的协同

1. **块分配走 jemalloc**：通过 `ArenaOptions::block_alloc = je_malloc`，Arena 的 8KB 块直接命中 jemalloc 的 small bin（class 8192），比 glibc malloc 再快一截。
2. **string 字段与 IOBuf**：长字符串（>=1KB）建议挂 `[ctype = CORD]` 或显式 `set_allocated_xxx()` 接管 IOBuf block，避免 arena 内部翻倍扩张。
3. **大对象兜底**：单次分配 > `max_block_size/4` 时 Arena 会单独开"独立块"，析构时归还总池，不参与下一块的 bump —— 防止一次大 string 把 8KB 块"撑废"。

---

## 5. 易踩的四个坑

1. **跨 arena 复用 sub-message**
   ```cpp
   auto* sub = Arena::CreateMessage<Sub>(arenaA);
   resp->set_allocated_sub(sub);   // resp 在 arenaB → 触发深拷贝！
   ```
   规则：要么共用 arena，要么走堆。

2. **Arena 对象用 `unique_ptr` 持有时被提前析构**
   Closure 还没跑完就 free 了 arena，request/response 全是悬挂指针。brpc 的 `ClosureGuard` 是为这个准备的，**不要自己 `delete arena`**。

3. **非平凡析构对象的 cleanup_list 爆炸**
   把大量 `std::string`（含 SSO 外内容）挂 arena 时 cleanup 链表会增长。优化：`[ctype = STRING_PIECE]` 或用 `absl::string_view` 引用 IOBuf。

4. **指标误读**：`SpaceAllocated` 是块累计容量，`SpaceUsed` 才是实际写水位；监控告警必须用 `Used/Allocated` 比值，低于 50% 说明块大小调大了。

---

## 6. 调参 cookbook（recall-fusion 经验值）

```cpp
google::protobuf::ArenaOptions opts;
opts.initial_block_size = 1024;     // 起步 1KB，吃掉 90% 小请求
opts.max_block_size     = 32 * 1024;// 大包 32KB 一块，不再翻倍
opts.block_alloc        = [](size_t n){ return je_malloc(n); };
opts.block_dealloc      = [](void* p, size_t){ je_free(p); };
brpc::ServerOptions sopt;
sopt.arena_options = opts;          // brpc 转发至每次请求
```

观察：
- p99 反序列化耗时 ↓ 30~50%；
- jemalloc `stats.allocated` 抖动幅度 ↓ 一个数量级；
- CPU profile 中 `tc_malloc/je_malloc` 占比从 4.x% 降到 0.8%。

---

## 7. 与 FlatBuffers / brpc::IOBuf 的取舍

| 维度 | protobuf::Arena | FlatBuffers | brpc::IOBuf |
|---|---|---|---|
| 反序列化 | 一次性聚合分配，仍解码 | 零拷贝，访问即读 | 不参与编码语义，做缓冲链 |
| 修改 | 友好（mutable_xxx） | 受限（需 builder） | n/a |
| 适配 ng-framework | 现成（默认骨架） | 需要替换 IDL | 已是网络层底座 |
| 选型建议 | **在线 RPC 默认开** | 离线缓存/磁盘 IDL | 网络/磁盘 zero-copy 管线 |

**结论**：在 brpc + ng-framework 的在线场景，**Arena 是几乎零成本接入、收益立竿见影的优化**，与 jemalloc、IOBuf 形成"块/对象/字节"三层零拷贝/合并释放的完整堆栈。

---

## 8. 参考

- protobuf 源码：`src/google/protobuf/arena.{h,cc}`、`arena_impl.h`
- brpc：`src/brpc/server.cpp`（auto_arena 装配点）
- 设计文档：[Protobuf Arena Allocation](https://protobuf.dev/reference/cpp/arenas/)
- 性能对照实验：cpp-core-notes/notes/24-jemalloc.md、35-brpc-iobuf.md

---

## 七、业务代码库适配分析
> **分析时间**：2026-06-26T19:10:06.587606
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：protobuf::Arena

### 1. 分析摘要

- 从扫描结果看，`protobuf::Arena` 在两个业务代码库中的直接使用仍然较少：`feeda-mv-grg` 暂未发现直接使用，`feeda-mv-grc` 仅在 `service/grc_service.cpp` 中发现 1 处使用。这说明当前业务侧大概率仍以默认 Protobuf 堆分配、`std::vector` / `std::string` / `std::unordered_map` 临时对象为主，尚未系统性利用 Arena 降低 RPC 请求生命周期内的频繁 malloc/free。

- 迁移潜力主要集中在在线服务入口、RPC 请求解析、响应构造、召回结果聚合等短生命周期对象密集的路径。尤其是 `feeda-mv-grc` 作为召回汇聚服务，`std::vector` 使用达到 8433 次、`std::string` 使用 7154 次、`std::unordered_map` 使用 2833 次，且已有 `service/grc_service.cpp` 使用 Arena，可作为业务内推广的参考点。`feeda-mv-grg` 虽未发现 Arena 使用，但 `std::vector` / `std::string` 使用规模也较大，适合优先从 RPC handler 和预测请求/响应构造路径评估接入收益。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 当前未发现 `protobuf::Arena` 的直接使用，说明该代码库暂时没有可复用的本地 Arena 实践样例。

- 标准容器使用规模较大：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型路径集中在模型预测接口和候选集处理：
  - `model/model.h:9`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
  - `model/paddle_model.h:103`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) {
        return 0;
    }
    ```
  - `model/paddle_model.h:107`
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                general_predict::PredictSample* predict_sample = nullptr,
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```

- 初步判断：
  - `model/model.h`、`model/paddle_model.h` 中的 `candidate_vec` 更像业务内部候选集容器，不应直接替换为 Arena。
  - 但如果 `RidTmpInfoPtr`、`general_predict::PredictSample` 或预测请求/响应对象本身是 Protobuf Message，且生命周期严格绑定单次请求，则可以考虑在调用链入口处统一使用 brpc per-call Arena 构造相关 PB 对象。
  - 迁移重点不应是机械替换 `std::vector`，而是识别“单次 RPC 内构造、RPC 结束即释放”的 Protobuf Message 树。

#### feeda-mv-grc：召回汇聚服务

- 已发现 `protobuf::Arena` 相关使用 1 处：
  - `service/grc_service.cpp`

- 该文件应作为后续 Arena 适配的首个参考点：
  - 确认是否使用了 brpc `auto_arena`
  - 确认 request / response 是否都来自同一个 per-call Arena
  - 确认是否存在跨 Arena 的 `set_allocated_xxx()`、`unsafe_arena_set_allocated_xxx()`、`Swap()` 等行为

- 标准容器使用规模显著高于 `feeda-mv-grg`：
  - `std::vector`：8433 次，分布在 1276 个文件
  - `std::string`：7154 次，分布在 1232 个文件
  - `std::unordered_map`：2833 次，分布在 638 个文件

- 典型扫描结果：
  - `service/grc_http_service.cpp:62`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
  - `service/grc_http_service.cpp:81`
    ```cpp
    std::set<std::pair<int, int>, decltype(comp_pair)> p_set(comp_pair);
    static std::vector<std::string> colors{"#FFB6C1", "#DC143C", "#DB7093", "#FF1493", "#FF00FF", "#800080",
                                           "#4B0082", "#7B68EE", "#0000FF", "#4169E1", "#778899", "#4682B4",
    ```
  - `service/grc_http_service.cpp:152`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
    ```

- 初步判断：
  - `service/grc_service.cpp` 是最值得优先优化的在线 RPC 主路径。
  - `service/grc_http_service.cpp` 更偏 HTTP 管理、图可视化或调试查询路径，其中 `std::unordered_map<std::string, std::vector<int>>`、`std::vector<std::string>` 不一定适合直接迁移到 Protobuf Arena。
  - 如果 `grc_http_service.cpp` 构造的是 HTTP 字符串响应，Arena 收益有限；如果后续会组装 Protobuf 响应或中间 PB 结构，则可将这些 PB 对象放到请求级 Arena 上。

---

### 3. 💡 适用性评估与建议

- **建议 1：以 `feeda-mv-grc/service/grc_service.cpp` 作为 Arena 标准样板**
  - 该文件已经发现目标技术使用，应优先审查并沉淀为业务规范。
  - 建议检查：
    - brpc `ServerOptions::auto_arena` 是否开启。
    - RPC handler 中 `request`、`response` 是否由 brpc per-call Arena 管理。
    - 是否存在手动 `new Response()`、`new SubMessage()` 后再挂到 response 的代码。
  - 推荐模式：
    ```cpp
    auto* arena = response->GetArena();
    auto* sub = google::protobuf::Arena::CreateMessage<SubMessage>(arena);
    response->set_allocated_sub(sub);
    ```
  - 如果 `response->GetArena()` 返回非空，则后续子 message 应尽量在同一个 Arena 上创建，避免跨 Arena 触发深拷贝。

- **建议 2：在 `feeda-mv-grc/service/grc_service.cpp` 的召回响应构造链路中统一 Arena 化**
  - 召回汇聚服务通常会构造大量 item、reason、score、debug info 等嵌套 PB 字段，是 Arena 最典型的收益场景。
  - 对以下模式重点排查：
    ```cpp
    auto* item = response->add_items();
    auto* debug = item->mutable_debug_info();
    ```
  - 这类通过 `add_xxx()`、`mutable_xxx()` 创建的子对象天然会跟随父 message 的 Arena，是推荐写法。
  - 不建议写成：
    ```cpp
    auto* item = new Item();
    response->mutable_items()->AddAllocated(item);
    ```
  - 如果确实需要外部构造后挂入 response，应确保外部对象和 response 来自同一个 Arena，否则 Protobuf 可能退化为 `MergeFrom` 深拷贝。

- **建议 3：`feeda-mv-grg/model/model.h` 和 `feeda-mv-grg/model/paddle_model.h` 不建议直接替换 `std::vector`，但应排查 Protobuf 输入对象**
  - 当前代码中的：
    ```cpp
    std::vector<RidTmpInfoPtr>& candidate_vec
    ```
    是模型预测内部候选集容器，通常生命周期、所有权和复用策略都比较复杂，不适合简单迁移到 `protobuf::Arena`。
  - 但 `model/paddle_model.h:107` 中出现：
    ```cpp
    general_predict::PredictSample* predict_sample = nullptr
    ```
  - 如果 `general_predict::PredictSample` 是 Protobuf Message，且只在单次请求内构造、填充、发送到预测模块，那么可以考虑：
    - 在 RPC 入口处从 request / response 获取 Arena。
    - 使用 `Arena::CreateMessage<general_predict::PredictSample>(arena)` 构造。
    - 避免在每个候选 item 上单独 `new` 子 message 或 repeated 元素。
  - 这类改造比替换 `candidate_vec` 更安全，收益也更符合 Arena 的设计目标。

- **建议 4：`feeda-mv-grc/service/grc_http_service.cpp` 中的 HTTP 临时容器不作为第一优先级**
  - 该文件中存在：
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    std::string resp_str;
    ```
  - 这些对象更像 HTTP 查询、参数解析、字符串响应拼接，和 Protobuf Message 树关系不强。
  - 不建议为了使用 Arena 而强行替换这些 STL 容器。
  - 更合理的优化方向是：
    - 对 `resp_str` 预估大小后 `reserve()`。
    - 对 `sub_access_off_vec`、`sub_access_on_vec` 根据参数数量预先 `reserve()`。
    - 对 `depend_map` 根据 `all_vertex.size()` 预先 `reserve()`。
  - 如果该 HTTP 接口未来返回 Protobuf 二进制响应，则可以再评估将返回 PB 对象放入请求级 Arena。

- **建议 5：在两个代码库的 brpc Server 初始化处统一检查 Arena 配置**
  - 当前扫描只定位到业务文件，没有展示 brpc `ServerOptions` 初始化代码。
  - 建议补充扫描以下关键字：
    - `brpc::ServerOptions`
    - `auto_arena`
    - `arena_options`
    - `initial_block_size`
    - `max_block_size`
  - 对 `feeda-mv-grc` 可优先沿着 `service/grc_service.cpp` 找到服务注册入口。
  - 推荐对高 QPS 服务统一配置：
    ```cpp
    google::protobuf::ArenaOptions opts;
    opts.initial_block_size = 1024;
    opts.max_block_size = 32 * 1024;

    brpc::ServerOptions sopt;
    sopt.auto_arena = true;
    sopt.arena_options = opts;
    ```
  - 是否接入 jemalloc 的 `je_malloc` / `je_free`，应结合当前运行环境确认，避免链接配置不一致导致问题。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：跨 Arena 挂载子 message 可能导致深拷贝或悬挂指针**
  - 在 `service/grc_service.cpp` 这类响应组装路径中，如果存在：
    ```cpp
    auto* sub = google::protobuf::Arena::CreateMessage<Sub>(arenaA);
    response->set_allocated_sub(sub);  // response 属于 arenaB
    ```
  - Protobuf 可能触发 `MergeFrom` 深拷贝，性能收益消失。
  - 更严重的是，若使用 `unsafe_arena_set_allocated_xxx()` 且 Arena 不一致，可能产生生命周期错误。
  - 迁移时必须统一规则：父 message、子 message、repeated message 元素尽量来自同一个 request-level Arena。

- **风险 2：不要把长生命周期对象放到请求级 Arena**
  - `feeda-mv-grg/model/model.h`、`feeda-mv-grg/model/paddle_model.h` 中的模型对象、候选集引用、预测上下文可能跨函数、跨模块传递。
  - 如果对象生命周期超过单次 RPC，不能放入 brpc per-call Arena。
  - Arena 适合“请求开始创建、请求结束整体释放”的对象，不适合缓存、全局索引、模型常驻对象。

- **风险 3：`std::vector` / `std::string` 不能机械替换为 Arena**
  - 扫描中 STL 使用次数很多，但这不等价于都应该迁移。
  - `service/grc_http_service.cpp` 中的 `std::string resp_str`、`std::vector<std::string>` 属于普通 C++ 临时容器，直接迁移到 `protobuf::Arena` 收益有限，反而增加复杂度。
  - 对这类代码，应优先使用 `reserve()`、复用 buffer、减少拷贝等传统优化手段。

- **风险 4：Arena 会延迟释放，可能提高单请求峰值内存**
  - Arena 的特点是整体释放，不会在请求中途回收单个对象。
  - 对召回汇聚服务，如果一次请求中构造大量候选、debug 字段、大 string 字段，`SpaceAllocated()` 可能快速上升。
  - 建议在 `service/grc_service.cpp` 的高流量路径增加采样指标：
    ```cpp
    arena->SpaceUsed();
    arena->SpaceAllocated();
    ```
  - 重点观察 `SpaceUsed / SpaceAllocated`，如果长期低于 50%，说明 block 配置可能偏大；如果大请求频繁触发扩容，则应评估提高 `max_block_size` 或优化大字段表示。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
