# 18. brpc::bthread — M:N 协程调度引擎深度解析

## 1. 核心定位

bthread 是 brpc 内置的 M:N 用户态线程库：**M 个 bthread 映射到 N 个 pthread（N = CPU 核数）**，通过 work-stealing 调度实现接近零开销的上下文切换（~200ns），是 brpc 高并发低延迟的根基。

---

## 2. 架构拆解

### 2.1 核心数据结构

```
TaskGroup (per pthread)
├── run_queue: WorkStealingQueue<bthread_t>  // 本地可运行队列
├── remote_rq: RemoteTaskQueue              // 外部线程提交入口
├── main_stack: Stack                        // 当前栈帧
└── current_task: TaskMeta*                 // 正在运行的 bthread

TaskMeta
├── tid: bthread_t                           // 唯一 ID（含版本号）
├── stack: ContextualStack*                  // 栈（大/小/主栈）
├── attr: bthread_attr_t                     // 优先级、栈大小等
├── fn + arg                                 // 入口函数
└── local_storage: bthread_local_t[]         // bthread-local 存储

ContextualStack
├── context: bthread_fcontext_t             // 寄存器上下文（x86_64 汇编）
├── stacktype: STACK_TYPE_SMALL/NORMAL/LARGE
└── storage: mprotect-guarded memory
```

### 2.2 Work-Stealing 调度

```
pthread-0 TaskGroup          pthread-1 TaskGroup
┌─────────────────┐          ┌─────────────────┐
│ run_queue: [A,B]│  steal →  │ run_queue: []   │
│ current: C      │          │ current: D      │
└─────────────────┘          └─────────────────┘
         ↑ push                      ↑ submit from remote
    bthread_start_urgent         bthread_start_background
```

**关键路径**：
- `bthread_start_urgent`：立即抢占当前 bthread，插入本地队列头部（LIFO，缓存热点友好）
- `bthread_start_background`：插入尾部异步执行
- Steal 时从其他 TaskGroup 的队列**尾部**窃取（与本地 LIFO 相反，减少竞争）

### 2.3 上下文切换（汇编级）

```asm
; bthread_fcontext_swap (x86_64, src/bthread/context.cpp)
; 保存调用者寄存器 → 切换栈指针 → 恢复目标寄存器
push rbp; push rbx; push r12..r15   ; 保存 callee-saved
mov  [rdi], rsp                      ; 保存当前 SP 到 from->context
mov  rsp, [rsi]                      ; 切换到 to->context 的 SP
pop  r15; pop r14; ...; pop rbx; pop rbp
ret                                  ; 跳转到 to 的 IP
```

**切换代价**：仅保存/恢复 6 个寄存器 + 栈指针，约 8 条指令，实测 ~200ns（vs pthread context switch ~5µs）。

### 2.4 栈内存管理

| 栈类型 | 大小 | 用途 |
|--------|------|------|
| SMALL  | 32KB | 默认，适合大多数 bthread |
| NORMAL | 1MB  | `BTHREAD_ATTR_NORMAL` |
| LARGE  | 8MB  | 类 pthread 深调用栈 |
| MAIN   | —    | 复用 pthread 原始栈 |

栈内存通过 `StackFactory<N>` 对象池管理，避免 mmap/munmap 系统调用开销。Guard page（mprotect）检测栈溢出。

---

## 3. 核心使用场景

### 3.1 场景一：brpc Server 中的并发请求处理

每个 RPC 请求在独立 bthread 中执行，阻塞操作（DB、下游 RPC）自动挂起当前 bthread，pthread 切换到其他就绪 bthread：

```cpp
#include <brpc/server.h>
#include <bthread/bthread.h>

class RecommendServiceImpl : public RecommendService {
public:
    void GetRecommend(google::protobuf::RpcController* cntl_base,
                      const RecommendRequest* req,
                      RecommendResponse* resp,
                      google::protobuf::Closure* done) override {
        brpc::ClosureGuard done_guard(done);

        // 当前已在 bthread 中，可以直接使用同步风格
        // 下游 brpc 调用会自动 yield 当前 bthread 而不阻塞 pthread
        brpc::Channel channel;
        channel.Init("recall-service:8000", nullptr);

        RecallRequest recall_req;
        RecallResponse recall_resp;
        brpc::Controller recall_cntl;

        RecallService_Stub stub(&channel);
        stub.GetRecall(&recall_cntl, &recall_req, &recall_resp, nullptr);
        // ↑ 底层：bthread_id_lock → epoll wait → bthread_id_unlock 唤醒
        // pthread 在此期间处理其他 bthread，无阻塞

        BuildResponse(recall_resp, resp);
    }
};
```

### 3.2 场景二：ParallelChannel 并发多路召回

```cpp
#include <brpc/parallel_channel.h>

void ParallelRecall(const QueryContext& ctx, RecommendResponse* resp) {
    brpc::ParallelChannel pchan;
    brpc::ParallelChannelOptions opts;
    opts.timeout_ms = 50;  // 整体超时
    pchan.Init(&opts);

    // 添加多个召回源
    for (const auto& source : recall_sources_) {
        pchan.AddChannel(source.channel.get(),
                         brpc::DOESNT_OWN_CHANNEL,
                         nullptr, nullptr);
    }

    // 所有下游并发发起，任一完成不阻塞其他
    // 底层：每路请求各一个 bthread，主 bthread 在 butex_wait 上等待
    brpc::Controller cntl;
    pchan.CallMethod(nullptr, &cntl, &req_, &resp_, nullptr);
}
```

**bthread 视角**：`ParallelChannel` 为每个子 Channel 创建一个 bthread，主 bthread 在 `butex_wait` 上等待所有子任务完成，pthread 无阻塞地继续调度其他就绪 bthread。

### 3.3 场景三：DAG 计算图节点并发执行

结合 ng-framework DAG 模型，bthread 天然适合节点间的并发调度：

```cpp
#include <bthread/bthread.h>
#include <bthread/countdown_event.h>

// DAG 节点并发执行示例
struct DagNodeCtx {
    std::function<void()> fn;
    bthread::CountdownEvent* latch;
};

void* RunDagNode(void* arg) {
    auto* ctx = static_cast<DagNodeCtx*>(arg);
    ctx->fn();
    ctx->latch->signal();
    delete ctx;
    return nullptr;
}

void ExecuteDagLayer(const std::vector<DagNode*>& nodes) {
    bthread::CountdownEvent latch(nodes.size());
    std::vector<bthread_t> tids(nodes.size());

    for (size_t i = 0; i < nodes.size(); ++i) {
        auto* ctx = new DagNodeCtx{
            [&, i] { nodes[i]->Execute(); },
            &latch
        };
        // start_urgent: 尽快调度，适合关键路径节点
        bthread_start_urgent(&tids[i], nullptr, RunDagNode, ctx);
    }

    latch.wait();  // 当前 bthread yield，等待所有节点完成
}
```

**对比 std::thread**：DAG 图中节点数可达数百，bthread 创建成本（~1µs）远低于 pthread（~10µs），且调度延迟更低。

### 3.4 场景四：bthread-local 存储替代 thread-local

```cpp
#include <bthread/bthread.h>

// 问题：同一 pthread 串行执行多个 bthread，thread_local 在 bthread 间共享
// 解决：使用 bthread_key_t 实现真正的 bthread-local

bthread_key_t g_trace_key;

struct TraceContext {
    std::string trace_id;
    uint64_t start_us;
    std::vector<std::string> spans;
};

// 初始化（全局一次）
void InitBthreadLocal() {
    bthread_key_create(&g_trace_key, [](void* data) {
        delete static_cast<TraceContext*>(data);
    });
}

TraceContext* GetTraceCtx() {
    auto* ctx = static_cast<TraceContext*>(bthread_getspecific(g_trace_key));
    if (!ctx) {
        ctx = new TraceContext();
        bthread_setspecific(g_trace_key, ctx);
    }
    return ctx;
}

// 在 RPC handler 中使用
void HandleRequest(const Request* req) {
    GetTraceCtx()->trace_id = req->trace_id();
    GetTraceCtx()->start_us = butil::gettimeofday_us();
    // ...每个 bthread 有独立的 TraceContext
}
```

---

## 4. 性能数据

| 指标 | bthread | pthread | 说明 |
|------|---------|---------|------|
| 创建耗时 | ~1µs | ~10µs | 复用栈对象池 |
| 上下文切换 | ~200ns | ~3-5µs | 纯用户态，8条汇编 |
| 内存占用（32K栈） | 32KB | 8MB (默认) | 栈默认差256x |
| 并发上限 | 10M+ | ~10K (系统限制) | bthread 受堆限制 |
| 吞吐（echo QPS） | ~200万/s | ~50万/s | 4核，brpc benchmark |

**brpc 官方 benchmark**（4核，128B payload）：
- bthread echo server：~2M QPS，P99 < 500µs  
- pthread-per-request：~500K QPS，P99 > 2ms（上下文切换过多）

---

## 5. 与同类工具对比

| 特性 | bthread | Golang goroutine | C++20 coroutine | seastar fiber |
|------|---------|------------------|-----------------|---------------|
| 调度模型 | M:N work-stealing | M:N work-stealing | 协作式（无调度器） | 单线程 per-core |
| 上下文切换 | ~200ns | ~100-300ns | ~ns（内联） | ~50ns |
| 栈管理 | 固定大小池化 | 动态增长（segment） | 无栈（heap frame） | 固定大小 |
| 阻塞系统调用 | 需 bthread 版 API | 自动拦截 | 需手动 co_await | 需 seastar API |
| 与 pthread 互操作 | 好（butex/fd_guard） | 好 | 好 | 差（需绑定 reactor） |
| brpc 集成 | 原生 | 无 | 部分（brpc 在推进） | 无 |
| 生产规模 | 百度内大规模 | Google/字节等 | 标准库，新生态 | ScyllaDB |

**关键差异**：bthread 与 brpc IO 事件循环深度集成，`butex`（bthread mutex）与 epoll 共用同一唤醒机制，其他方案在 brpc 生态中需额外适配。

---

## 6. 接入方式

### 6.1 CMake 引入

```cmake
# 方式1：使用系统安装的 brpc（包含 bthread）
find_package(brpc REQUIRED)
target_link_libraries(my_service brpc::brpc)
# bthread 头文件随 brpc 一起安装到 /usr/include/bthread/

# 方式2：通过 FetchContent 源码编译
include(FetchContent)
FetchContent_Declare(
    brpc
    GIT_REPOSITORY https://github.com/apache/brpc.git
    GIT_TAG        1.9.0
)
FetchContent_MakeAvailable(brpc)
target_link_libraries(my_service brpc-static)
```

### 6.2 bazel 引入

```python
# WORKSPACE
load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

http_archive(
    name = "brpc",
    urls = ["https://github.com/apache/brpc/archive/refs/tags/1.9.0.tar.gz"],
    strip_prefix = "brpc-1.9.0",
    sha256 = "...",  # 填写实际 sha256
)

# BUILD 文件
cc_binary(
    name = "recommend_server",
    srcs = ["server.cc"],
    deps = [
        "@brpc//:brpc",  # 包含 bthread
    ],
)
```

### 6.3 关键头文件

```cpp
#include <bthread/bthread.h>           // bthread_start_*, bthread_join
#include <bthread/butex.h>             // 低层 futex-like 原语
#include <bthread/countdown_event.h>   // CountdownEvent
#include <bthread/mutex.h>             // bthread::Mutex（可被 bthread yield）
#include <bthread/condition_variable.h>// bthread::ConditionVariable
```

---

## 7. 限制与注意事项

### 7.1 不能在 bthread 中调用阻塞系统调用

```cpp
// ❌ 错误：sleep/read/write 会阻塞整个 pthread，导致其他 bthread 饥饿
void WrongUsage() {
    sleep(1);                    // 阻塞 pthread！
    int fd = open("file", O_RDONLY);
    read(fd, buf, size);         // 阻塞 pthread！
}

// ✅ 正确：使用 bthread 版本
void CorrectUsage() {
    bthread_usleep(1000000);     // yield 当前 bthread，不阻塞 pthread
    // 文件 I/O 应异步化，或在专用 blocking pthread 中执行
    bthread_start_background(&tid, &BTHREAD_ATTR_PTHREAD, blocking_fn, arg);
}
```

### 7.2 `thread_local` 陷阱

```cpp
// ❌ 危险：多个 bthread 共享同一 pthread，thread_local 在切换后被另一 bthread 修改
thread_local int request_id = 0;

// ✅ 使用 bthread_key_t（见场景四）
```

### 7.3 `bthread::Mutex` vs `std::mutex`

```cpp
// ❌ std::mutex 会挂起整个 pthread
std::mutex mu;
std::lock_guard<std::mutex> lk(mu);  // 如果锁被持有，阻塞 pthread

// ✅ bthread::Mutex 只挂起当前 bthread，pthread 继续调度其他 bthread
bthread::Mutex bmu;
std::lock_guard<bthread::Mutex> lk(bmu);
```

### 7.4 栈大小默认 32KB

递归深度深、局部数组大的函数需要显式指定大栈：

```cpp
bthread_attr_t attr = BTHREAD_ATTR_NORMAL;
attr.stack_size = 1 * 1024 * 1024;  // 1MB
bthread_start_urgent(&tid, &attr, my_fn, arg);
```

### 7.5 Work-stealing 下的 CPU 亲和性

bthread 可能在不同 CPU 核上迁移，对 NUMA 架构的内存访问局部性有影响。大内存操作（如大型 Protobuf 解析）建议在固定 bthread 绑定的 `TaskGroup` 上执行（通过 `bthread_attr_t::keytable_pool` 控制）。

---

## 8. 总结

bthread 是 brpc 高性能的核心引擎，通过 M:N work-stealing 调度实现了**接近系统线程数量级的 I/O 并发，同时保持同步代码风格**。

**对推荐架构组技术栈的实际提升**：

| 场景 | bthread 带来的收益 |
|------|-------------------|
| brpc Server 并发请求 | 每个请求独立 bthread，pthread 数固定为核数，无 C10K 问题 |
| 多路并发召回（ParallelChannel） | 子请求各一 bthread，主 bthread yield 等待，P99 延迟显著降低 |
| ng-framework DAG 节点并发 | 节点级 bthread 并发，创建成本低，适合 DAG 的扇出扇入模式 |
| jemalloc 集成 | bthread 栈通过对象池 + jemalloc 分配，减少 mmap 碎片 |
| Protobuf Arena + bthread | 每个 bthread 持有独立 Arena，结合 bthread_key_t 实现零竞争 |

**关键记忆点**：
1. 上下文切换 ~200ns（8条汇编，仅保存 callee-saved 寄存器）
2. 绝不在 bthread 中调用阻塞系统调用（用 `bthread_usleep`/`BTHREAD_ATTR_PTHREAD`）
3. `thread_local` → `bthread_key_t`，`std::mutex` → `bthread::Mutex`
4. `bthread_start_urgent`（立即抢占）vs `bthread_start_background`（后台异步）

---

## 七、业务代码库适配分析
> **分析时间**：2026-05-31T19:13:23.904104
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：brpc::bthread

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 和 `feeda-mv-grc` 两个业务代码库中已经存在对目标技术库的使用痕迹，各发现 **10 个相关文件**。这说明业务侧并非完全从零引入 `bthread`，已有一定的落地基础，可优先复用现有使用方式作为迁移参考，例如：
  - `feeda-mv-grg/plugin/ums_parser.cpp`
  - `feeda-mv-grg/operator/diversity/new_hot_diversity_rule.cpp`
  - `feeda-mv-grc/operator/adjuster/precise/comment_sign_xgb_adjust.cpp`
  - `feeda-mv-grc/processor/reddot/reddot_data.h`

- 从业务形态看，两个代码库都属于推荐/召回链路中的高并发服务，存在大量候选集处理、召回聚合、过滤、排序、图执行等场景。`bthread` 的主要价值不在于替换 `std::vector`、`std::string`、`std::unordered_map` 等容器，而在于优化 **并发执行模型**：将部分 `std::thread`、线程池任务、同步 RPC 等阻塞式逻辑迁移为 bthread 友好的同步风格，从而降低线程切换开销，提升高并发下的吞吐与尾延迟稳定性。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现目标库使用：**10 个文件**
  - `plugin/ums_parser.cpp`
  - `operator/diversity/new_hot_diversity_rule.cpp`
  - `strategy/diversity/rule/interest_diversity_rule.cpp`
  - `operator/diversity/quality_selection_soft_rule.cpp`
  - `operator/diversity/kanju_days_lt_adjust_soft_rule.cpp`

- 现有 `std` 基础类型使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型代码集中在模型预测、候选集处理和多策略规则执行上，例如：
  - `model/model.h`
  - `model/paddle_model.h`
  - `operator/diversity/*`
  - `strategy/diversity/rule/*`

- 从代码形态看，`model/model.h`、`model/paddle_model.h` 中大量以 `std::vector<RidTmpInfoPtr>& candidate_vec` 作为候选集输入，说明服务内部存在明显的批量候选处理路径。该类路径如果存在多模型、多特征、多规则并行执行需求，适合进一步评估使用 `bthread` 将独立计算单元并发化。

---

#### feeda-mv-grc：召回汇聚服务

- 已发现目标库使用：**10 个文件**
  - `operator/adjuster/precise/comment_sign_xgb_adjust.cpp`
  - `processor/reddot/reddot_data.h`
  - `operator/diversity/new_hot_diversity_rule.cpp`
  - `processor/filter/boutique_filter_opratore.cc`
  - `processor/filter/item_cold_filter_operator.cc`

- 现有 `std` 基础类型使用规模更大：
  - `std::vector`：8382 次，分布在 1266 个文件
  - `std::string`：7107 次，分布在 1222 个文件
  - `std::unordered_map`：2828 次，分布在 636 个文件

- 典型代码显示该服务中存在 DAG/图执行、HTTP 请求处理、召回依赖关系构建等场景，例如：
  - `service/grc_http_service.cpp:62`
    - 使用 `std::unordered_map<std::string, std::vector<int>> depend_map`
    - 遍历 `graph_engine->get_vertexs_message(graph_name)` 构建节点依赖关系
  - `service/grc_http_service.cpp:81`
    - 使用静态颜色列表生成图可视化信息
  - `service/grc_http_service.cpp:152`
    - 解析 HTTP query 参数，包括 `off`、`on` 等开关参数

- `feeda-mv-grc` 的业务特征更贴近 `bthread` 的典型收益场景：多召回源并发、图节点并发执行、过滤器/调整器流水线并发、下游 RPC 并发请求等。相比 `feeda-mv-grg`，该代码库具备更高的并发调度优化潜力。

---

### 3. 💡 适用性评估与建议

- **建议 1：以已有 bthread 使用文件作为迁移参考，先统一封装并发执行工具**
  - 参考文件：
    - `feeda-mv-grg/operator/diversity/new_hot_diversity_rule.cpp`
    - `feeda-mv-grg/strategy/diversity/rule/interest_diversity_rule.cpp`
    - `feeda-mv-grc/operator/diversity/new_hot_diversity_rule.cpp`
    - `feeda-mv-grc/operator/adjuster/precise/comment_sign_xgb_adjust.cpp`
  - 建议先梳理这些文件中对 bthread 的使用方式，包括：
    - 是否直接调用 `bthread_start_urgent`
    - 是否使用 `bthread::CountdownEvent`
    - 是否存在手写任务上下文结构体
    - 是否存在异常路径未 `signal()` 的风险
  - 建议沉淀一个统一工具，例如：
    - `BthreadTaskGroup`
    - `RunParallelTasks`
    - `ParallelForCandidates`
  - 避免每个 operator/filter/rule 中重复手写 bthread 创建、参数传递、等待和资源释放逻辑。

- **建议 2：在 `feeda-mv-grc/service/grc_http_service.cpp` 的图依赖处理路径中评估 DAG 节点并发化**
  - `service/grc_http_service.cpp:62` 附近存在对 `graph_engine->get_vertexs_message(graph_name)` 的遍历，并构建 `depend_map`。
  - 如果该文件不仅用于图展示，还参与在线执行图构建或调试执行，建议进一步评估：
    - 同一层无依赖节点是否可以并发执行
    - 每个 vertex 的处理是否包含 RPC、特征读取、过滤、召回等阻塞操作
    - 当前是否存在串行遍历导致的耗时累加
  - 对于同层节点执行，可采用：
    - `bthread_start_urgent`
    - `bthread::CountdownEvent`
    - 分层 DAG 调度
  - 适合的改造模式是：
    - 每一层 DAG 节点并发提交 bthread
    - 当前主 bthread 使用 `CountdownEvent::wait()` 等待
    - 下游 RPC 或 IO 阻塞时让出 pthread，提升整体并发度

- **建议 3：在 `feeda-mv-grg/model/paddle_model.h` 的批量预测路径中评估候选集分片并行**
  - 当前接口示例：
    - `model/model.h`
      ```cpp
      virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
      ```
    - `model/paddle_model.h`
      ```cpp
      int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                                    general_predict::PredictSample* predict_sample = nullptr,
                                    bool is_from_cube = true) const
      ```
  - 如果 `candidate_vec` 较大，且预测前后的特征构建、打分、后处理可以按候选 item 分片独立执行，可以考虑使用 bthread 做分片并行。
  - 适合场景：
    - 单次请求候选数较多
    - 每个候选的特征计算相互独立
    - 当前 CPU 计算耗时明显
    - 不涉及共享模型对象的非线程安全写操作
  - 不建议直接对模型推理本身盲目并发化，尤其是 Paddle/TensorRT/自研预测器内部可能已有线程池或 batch 优化。更推荐并行化模型调用前后的轻量逻辑，例如候选过滤、特征拼装、结果 merge。

- **建议 4：在 `feeda-mv-grc/processor/filter/*` 中评估过滤器并发执行或分片执行**
  - 可优先关注：
    - `processor/filter/boutique_filter_opratore.cc`
    - `processor/filter/item_cold_filter_operator.cc`
  - 这些过滤器通常对候选集合进行遍历判断，若单个请求候选量大，且过滤逻辑依赖的数据只读，可以将候选集切分为多个 shard，通过 bthread 并行处理。
  - 推荐方式：
    - 每个 bthread 处理一段 `[begin, end)` 候选区间
    - 每个任务写入自己的局部结果容器
    - 主 bthread 汇总结果，避免多个 bthread 竞争写同一个 `std::vector`
  - 需要注意：
    - 不要在多个 bthread 中同时修改共享 `std::unordered_map`
    - 不要并发修改候选对象中的共享字段，除非有明确的同步策略
    - 对小候选集不建议并发化，避免调度开销大于收益

- **建议 5：在召回聚合路径优先使用 brpc 同步风格 + bthread，而不是业务侧手写 pthread 阻塞等待**
  - `feeda-mv-grc` 是召回汇聚服务，天然存在多下游召回源并发请求场景。
  - 如果当前代码中存在：
    - 多个下游 RPC 串行调用
    - 使用 `std::thread` 或普通线程池并发调用
    - 主线程使用 `std::future::get()`、`condition_variable` 等阻塞 pthread
  - 建议迁移为：
    - brpc `ParallelChannel`
    - 或每个召回源一个 bthread
    - 主 bthread 使用 `CountdownEvent` / butex 等 bthread 友好等待方式
  - 这样可以保持同步代码风格，同时避免阻塞底层 pthread，提高高并发场景下的资源利用率。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：不能把所有阻塞操作都默认视为 bthread 友好**
  - brpc RPC、bthread mutex、butex 等是 bthread 友好的，阻塞时会让出 pthread。
  - 但普通系统调用、第三方 SDK、同步文件 IO、阻塞数据库客户端、`pthread_mutex` 等可能直接阻塞 pthread。
  - 如果在 bthread 中调用这些阻塞接口，可能导致整个 worker pthread 被占用，削弱 M:N 调度收益。
  - 迁移前需要重点排查：
    - DB client
    - Redis client
    - 本地文件读写
    - 第三方模型推理 SDK
    - 使用 `pthread_mutex` / `std::mutex` 的共享资源

- **风险 2：`thread_local` 在 bthread 场景下语义不等价**
  - bthread 是 M:N 调度，一个 pthread 会连续执行多个 bthread。
  - 如果业务代码中使用 `thread_local` 保存 trace、请求上下文、用户状态、临时 buffer，可能出现不同 bthread 之间共享同一个 pthread local 数据的问题。
  - 建议排查：
    - trace context
    - AB 实验上下文
    - 请求级 cache
    - 日志 MDC/NDC
  - 对请求级数据应优先使用：
    - 显式参数传递
    - request context
    - `bthread_key_t` / bthread-local storage

- **风险 3：bthread 栈较小，深递归或大栈对象可能溢出**
  - bthread 默认小栈通常远小于 pthread 默认栈。
  - 如果业务代码中存在：
    - 大型局部数组
    - 深层递归
    - 复杂模板对象在栈上构造
    - 大 `std::vector` / `std::string` 对象的栈上组合结构
  - 需要改为堆分配，或为特定任务使用更大的 bthread 栈属性。
  - 重点关注模型、图执行和复杂规则文件，例如：
    - `model/paddle_model.h`
    - `service/grc_http_service.cpp`
    - `operator/diversity/*`

- **风险 4：并发化后需要重新审视容器和对象的线程安全**
  - 扫描结果显示两个代码库大量使用：
    - `std::vector`
    - `std::string`
    - `std::unordered_map`
  - 这些容器本身不是线程安全的。
  - 如果将原本串行逻辑改为多个 bthread 并发执行，需要避免：
    - 多个 bthread 同时 `push_back` 同一个 `std::vector`
    - 多个 bthread 同时写同一个 `std::unordered_map`
    - 多个 bthread 修改同一个候选对象字段
  - 推荐模式是：
    - 每个 bthread 使用局部结果
    - 主 bthread 统一 merge
    - 共享只读数据可以直接访问
    - 共享可写数据必须加锁或分片隔离

---

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
