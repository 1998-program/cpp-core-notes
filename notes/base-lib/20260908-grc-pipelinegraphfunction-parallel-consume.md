# 2026-09-08 周一基础库理解：PipelineGraphFunction 并行消费骨架与队列上下文收敛

> 日期：2026-09-08  
> 主题来源：当前 daily-plan 仍未覆盖本日；按历史候选回退到 `PipelineGraphFunction parallel_consume 批消费框架`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/processor/base/pipeline_function.h`，聚焦批消费、`bthread_async` 并行、剩余任务本地兜底与 `QueueContext` 收敛。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">输入层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/processor/base/pipeline_function.h:50-67`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">consumer 每次吐出一个 range，函数按 batch_size 把输入拆成若干并行片段。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">并行层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`bthread_async([this, &queue_context])`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">前段任务进入 Future 池；每个 worker 处理独立 `QueueContext`。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">收敛层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`future.get()` + 本地 `QueueContext` 回填</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先等并行段完成，再把余量补齐到 `context.queue_contexts`，避免结果分散。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">consumer</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">QueueContext</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">bthread_async</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">future / local fallback</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title PipelineGraphFunction parallel consume boundary
participant consumer as CON
participant "PipelineGraphFunction" as PF
participant "QueueContext[]" as QC
participant "bthread_async" as ASYNC
participant "Future<int32_t>" as FUT
participant "local QueueContext" as LOCAL
participant "context.queue_contexts" as CXT
CON -> PF : consume(batch_size)
PF -> QC : resize(concurrents)
loop while range not empty and i < concurrents
  PF -> QC : input_data_construct(queue_context, range)
  PF -> ASYNC : spawn process(queue_context)
  ASYNC --> FUT : future handle
  PF -> CON : consume(next batch)
end
loop remaining range
  PF -> LOCAL : construct local QueueContext
  PF -> PF : process(queue_context)
  PF -> LOCAL : emplace_back(move(queue_context))
end
loop futures
  PF -> FUT : get()
end
PF -> CXT : append local_queue_contexts
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title PipelineGraphFunction 的 6 个关键点
  desc 这条骨架把批消费、并行处理、结果回收和兜底路径放在同一个 contract 里
  items
    - label 批消费入口
      desc `src/processor/base/pipeline_function.h:50-57` 先清空上下文，再按 `consumer.consume()` 拉取第一批
      icon mdi-download-box-outline
    - label 并行队列
      desc `src/processor/base/pipeline_function.h:57-66` 预分配 `context.queue_contexts`，为并行 worker 留出槽位
      icon mdi-vector-arrange-below
    - label worker 分发
      desc `src/processor/base/pipeline_function.h:63-66` 每个 future 绑定一个 `QueueContext`
      icon mdi-call-split
    - label 本地兜底
      desc `src/processor/base/pipeline_function.h:69-77` 剩余 range 不再等并行队列，直接本地处理
      icon mdi-lan-pending
    - label future 收敛
      desc `src/processor/base/pipeline_function.h:79-84` 所有并行段完成后再汇总错误码
      icon mdi-vector-link
    - label 上下文回填
      desc `src/processor/base/pipeline_function.h:85-87` 把本地保存的队列上下文补回 `context.queue_contexts`
      icon mdi-database-sync
```

## 3. 代码链路拆解
### 3.1 并行骨架先预分配，再发射任务
- `src/processor/base/pipeline_function.h:51-58`：先清空 `context.queue_contexts`，再按 `concurrents` 预设槽位。这说明并行数量是显式契约，不是运行时临时扩容。
- `src/processor/base/pipeline_function.h:59-67`：`consumer.consume()` 每次拉一个 range，前 `concurrents` 个分片交给 `bthread_async`，其余 range 在同一个入口函数里继续推进。
- `src/processor/base/pipeline_function.h:63-65`：`queue_context` 以引用方式进入 lambda，说明其生命周期必须被外层 `context.queue_contexts` 保住。

### 3.2 剩余输入走本地处理，不把所有压力都压给 future
- `src/processor/base/pipeline_function.h:69-77`：并行槽位用满后，剩余 range 直接本地 `process(queue_context)`。这一步是重要的吞吐保护，不让小批次和尾部流量都被线程调度放大。
- `src/processor/base/pipeline_function.h:76-77`：本地处理的 `queue_context` 被移动进 `local_queue_contexts`，后面统一回填，避免丢失在局部变量生命周期内。

### 3.3 future 只负责收敛，不负责重排业务逻辑
- `src/processor/base/pipeline_function.h:79-84`：`future.get()` 只做等待和错误码归并，没有在这里再拆分业务分支。这个边界很清楚：并行阶段完成后，业务排序和结果解释应该在 `process()` 内部结束。
- `src/processor/base/pipeline_function.h:85-87`：本地缓存的 `QueueContext` 再次并入 `context.queue_contexts`，说明最终上下文集合必须完整保留，后续 post_process 才能继续消费。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#3d5a80;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">这类骨架最容易出问题的点，不在并行本身，而在引用生命周期和回填顺序</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`bthread_async([this, &queue_context])` 依赖外层 `context.queue_contexts` 的稳定存活。若 future 还没 `get()` 就提前清掉外层容器，worker 会拿到悬挂引用。</div><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">尾部 range 必须走本地处理并回填 `local_queue_contexts`，否则只并行前半段、丢掉余量，结果集会悄悄缺项。</div></div><div style="margin-top:10px;font-weight:900;color:#3d5a80;">∎ 排查顺序：`consume()` → `resize()` → `input_data_construct()` → `bthread_async()` → `future.get()` → `local_queue_contexts` 回填</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title PipelineGraphFunction 排查清单
  desc 适用于并行任务丢失、future 悬挂、context 被提前清空和尾部输入未处理的场景
  items
    - label 检查 consume
      desc 确认 `consumer.consume()` 的 batch_size 与预期一致
      done true
    - label 检查上下文槽位
      desc `context.queue_contexts.resize(concurrents)` 必须在发射任务前完成
      done true
    - label 检查引用生命周期
      desc lambda 中引用的 `queue_context` 不能早于 future 完成被销毁
      done true
    - label 检查尾部兜底
      desc 剩余 range 必须继续本地 `process()`，不能直接丢弃
      done true
    - label 检查错误收敛
      desc 所有 future 都要执行 `get()`，并归并非 0 返回码
      done true
    - label 检查最终回填
      desc `local_queue_contexts` 要补回 `context.queue_contexts`
      done true
```

## 6. 证据来源
- `src/processor/base/pipeline_function.h:50-67`
- `src/processor/base/pipeline_function.h:69-89`
- `src/processor/base/pipeline_function.h:90-102`

## 7. 说明
当前运行环境没有 2026-09-08 的 daily-plan 文件；本笔记基于历史候选回退生成，KU 正文未读取，业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-09T19:06:43.328318
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析：PipelineGraphFunction 并行消费骨架与队列上下文收敛

## 1. 分析摘要

当前两个业务代码库已经大量使用 `std::vector`、`std::string`、`std::unordered_map` 等标准容器，但对 `PipelineGraphFunction` 所代表的“批量消费 → 并行处理 → future 收敛 → 本地兜底 → `QueueContext` 回填”执行骨架，整体仍处于少量使用或局部探索阶段。`feeda-mv-grg` 中发现 1 个目标技术使用文件，`feeda-mv-grc` 中发现 10 个目标技术使用文件，说明该模式尚未形成跨模块的统一范式。

从迁移潜力看，最适合引入该骨架的场景是：输入能够拆分成多个相互独立的 range、单个 worker 可以使用独立上下文完成计算、结果最终需要统一汇总的批处理逻辑，例如候选打分、特征生成、过滤规则执行和多因子计算。需要注意的是，`std::vector` 等价物的使用规模很大，并不意味着应将标准容器直接替换为 `QueueContext`；两者分别解决“数据存储”和“并行任务上下文收敛”问题，建议以实际处理链路和性能瓶颈为依据进行适配。

---

## 2. 代码库详情

### 2.1 `feeda-mv-grg`：序列生成服务

#### 扫描发现

- 已发现目标技术使用于：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 当前仅发现 1 个文件使用相关技术，说明该代码库尚未广泛采用 `PipelineGraphFunction` 式并行消费模型。
- 标准容器使用规模较大：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

#### 典型现有代码

- `model/model.h:9`

  ```cpp
  class Model {
  public:
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
  };
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

#### 适配判断

`feeda-mv-grg` 当前的模型接口以 `std::vector<RidTmpInfoPtr>&` 传递候选集，具备批量处理的输入形态，但现有接口本身并不等价于 `QueueContext`。如果模型预测、候选过滤或多样性规则能够按候选 range 独立执行，可以在规则层或批处理调度层引入并行骨架；不建议直接修改 `model/model.h` 的公共接口，将 `std::vector` 替换为 `QueueContext`。

`strategy/diversity/rule/low_clarity_diversity_rule.cpp` 是当前最重要的业务侧参考实现，可用于确认以下内容：

- 现有目标技术如何配置并行度和批大小；
- `QueueContext` 中承载哪些业务结果；
- `process()` 是否包含共享状态修改；
- 本地兜底结果如何回填；
- 并行处理后是否需要保持候选顺序。

---

### 2.2 `feeda-mv-grc`：召回汇聚服务

#### 扫描发现

- 已发现目标技术使用于 10 个文件。
- 当前已列出的代表性文件包括：
  - `processor/new_adjust/precise_score_init.cpp`
  - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
- 标准容器使用规模明显高于 `feeda-mv-grg`：
  - `std::vector`：8520 次，分布在 1290 个文件
  - `std::string`：7267 次，分布在 1247 个文件
  - `std::unordered_map`：2860 次，分布在 646 个文件

#### 典型现有代码

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
  static std::vector<std::string> colors{
      "#FFB6C1", "#DC143C", "#DB7093", "#FF1493",
      "#FF00FF", "#800080", "#4B0082", "#7B68EE"
  };
  ```

- `service/grc_http_service.cpp:152`

  ```cpp
  std::string resp_str;

  std::vector<std::string> sub_access_off_vec;
  std::vector<std::string> sub_access_on_vec;
  const std::string *sub_access_off_vec_str =
      cntl->http_request().uri().GetQuery("off");
  ```

#### 适配判断

`feeda-mv-grc` 的处理器和因子生成器数量较多，且大量围绕候选集合、特征、分数和过滤结果进行计算，因此比 `feeda-mv-grg` 更适合评估批量并行消费模式。

其中，以下模块可能具备较明确的适配机会：

- `processor/new_adjust/precise_score_init.cpp`
  - 适合评估批量分数初始化是否可以按候选 range 拆分。
- `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
  - 适合评估 session 内候选的因子计算是否可以使用独立 worker 上下文。
- `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - 适合评估场景级因子生成能否将只读输入切片处理。
- `processor/multi_factor/subcate_future_factor_gen.cpp`
  - 适合评估子类目维度上的特征生成是否存在独立分片。
- `processor/filter/low_agile_goodrate_filter_operator.cc`
  - 适合评估过滤判定是否可以并行执行，并最终统一合并保留结果。

`service/grc_http_service.cpp` 中的 `std::vector`、`std::unordered_map` 和 `std::string` 主要用于 HTTP 参数、图依赖信息和响应内容组织。此类代码可以作为标准容器使用和内存优化的参考，但不应直接作为 `PipelineGraphFunction` 并行改造目标。HTTP 请求解析、图结构整理和响应拼装通常更关注线程隔离、请求生命周期与确定性，不一定适合引入异步 worker。

---

## 3. 💡 适用性评估与建议

- **优先以 `feeda-mv-grc` 的处理器链路作为首批适配对象，而不是直接改造公共模型接口**

  建议优先评估：

  - `processor/new_adjust/precise_score_init.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`

  如果这些模块的处理逻辑满足“每个候选或每个候选 range 独立计算，最终只需要合并分数、特征或过滤结果”，可以按照以下模式改造：

  1. 由 consumer 按 `batch_size` 产生输入 range；
  2. 前 `concurrents` 个 range 通过 `bthread_async` 发射；
  3. 每个 worker 使用独立的 `QueueContext`；
  4. 超出并行槽位的尾部 range 在当前线程本地处理；
  5. 等待全部 future 完成后，再统一回填上下文和结果。

  这类改造比直接把 `std::vector` 替换成其他容器更有可能带来实际吞吐收益。

- **将 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 作为 `feeda-mv-grg` 的现有参考实现**

  `feeda-mv-grg` 目前只有该文件被扫描到使用目标技术，因此它可以作为该代码库内部的迁移模板。建议重点对比新旧实现的：

  - `batch_size` 与候选数量的关系；
  - `concurrents` 的配置来源和默认值；
  - `QueueContext` 的字段内容；
  - `process()` 是否修改共享候选集；
  - `future.get()` 后的错误码归并方式；
  - 本地尾部任务是否被正确追加到最终结果。

  对于 `model/model.h` 和 `model/paddle_model.h`，建议保留现有的 `std::vector<RidTmpInfoPtr>&` 接口，在模型接口外增加并行调度层，而不是让模型基类感知 `QueueContext` 和 future。

- **对 `session_ltr_dibar_factor_gen.cpp` 和 `subcate_future_factor_gen.cpp` 进行“只读输入 + 独立输出”的并行化评估**

  建议重点检查：

  - session、子类目和候选集合是否只读；
  - worker 是否会写入共享 feature map、候选对象或全局缓存；
  - 因子计算结果是否可以先写入每个 worker 的本地 context；
  - 最终结果是否可以按候选下标或原始序号合并。

  如果存在共享写入，可以将每个 worker 的结果先保存到独立 `QueueContext`，在 `future.get()` 后由主线程统一合并，避免在 worker 内直接竞争共享容器。对于必须保持原始候选顺序的逻辑，应为每个输入 range 保留原始 offset，不能简单按照 future 完成顺序追加结果。

- **不要将 `service/grc_http_service.cpp` 中的标准容器直接替换为并行队列上下文**

  `service/grc_http_service.cpp` 中的：

  - `std::unordered_map<std::string, std::vector<int>> depend_map`
  - `std::set<std::pair<int, int>>`
  - `std::vector<std::string>`
  - `std::string resp_str`

  主要是请求处理、图依赖组织和响应构造数据结构，不是批量业务计算上下文。建议只针对该文件进行常规容器优化，例如：

  - 对已知规模的 `vector` 使用 `reserve()`；
  - 对 `unordered_map` 预估容量并设置 `reserve()`；
  - 避免响应字符串反复扩容；
  - 检查 `all_vertex.size()` 的类型和遍历方式。

  如果确实存在图节点级别的独立计算，应在 graph processor 或具体处理器边界引入并行骨架，而不是在 HTTP service 层启动异步任务。

- **将标准容器规模作为优化候选池，而不是作为强制迁移依据**

  两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 的使用量都很大，尤其是 `feeda-mv-grc`：

  - `std::vector` 达到 8520 次；
  - `std::string` 达到 7267 次；
  - `std::unordered_map` 达到 2860 次。

  这些数据说明容器分配、扩容、拷贝和哈希访问可能存在整体优化空间，但不能直接证明 `PipelineGraphFunction` 能够解决问题。建议先通过 profiling 定位以下热点：

  - 候选集合批处理耗时；
  - 单个 factor/filter 的 CPU 占比；
  - `std::vector` 扩容和对象拷贝；
  - `std::string` 拼接和序列化；
  - worker 调度耗时与实际计算耗时的比例。

  只有当业务计算成本明显高于 `bthread_async` 调度和上下文构造成本时，才建议扩大并行化范围。

---

## 4. ⚠️ 引入风险与限制

- **`QueueContext` 的引用生命周期存在悬挂风险**

  技术笔记中的异步任务使用了类似：

  ```cpp
  bthread_async([this, &queue_context] {
      return process(queue_context);
  });
  ```

  此时 `queue_context` 通常引用 `context.queue_contexts` 中的元素。必须确保：

  - 所有 worker 启动前已经完成 `context.queue_contexts.resize(concurrents)`；
  - future 尚未全部 `get()` 前，不能清空或重新扩容 `context.queue_contexts`；
  - 不能在异步任务运行期间触发底层 `std::vector` 重分配；
  - lambda 引用的元素不能被局部变量生命周期提前释放。

  对 `feeda-mv-grc` 中可能存在的共享候选容器尤其需要谨慎，不能一边启动 worker，一边继续修改会导致引用失效的容器。

- **并行结果可能破坏候选顺序和业务确定性**

  future 的完成顺序不等于输入顺序。如果 `precise_score_init.cpp`、`low_agile_goodrate_filter_operator.cc` 或多个 factor generator 依赖候选原始顺序，必须显式保存：

  - 输入 range 的起始下标；
  - 每个结果对应的候选 ID 或位置；
  - 本地兜底 range 的原始位置；
  - 最终合并时的排序规则。

  不能简单按照 `future.get()` 顺序或 worker 完成顺序追加结果，否则可能导致召回排序、过滤结果或特征对齐发生变化。

- **现有处理逻辑可能包含不可并行的共享状态**

  需要重点检查以下类型的状态：

  - session 级缓存；
  - 全局或静态模型对象；
  - 可写的 feature map；
  - 候选对象内部的懒加载字段；
  - 统计计数器和调试信息；
  - 非线程安全的第三方模型推理接口。

  `model/paddle_model.h` 中的模型预测接口虽然接收批量候选，但不能仅凭接口形式判断其线程安全性。若模型实例、tensor buffer 或 predictor 在多个 worker 间共享，应先确认其并发语义，必要时为每个 worker 配置独立实例或串行化关键阶段。

- **小批次场景可能出现负收益**

  `bthread_async`、future、`QueueContext` 构造和最终合并都会引入固定开销。对于候选数量很小、单个 filter 或 factor 计算很轻的请求，并行化可能比本地串行处理更慢。

  建议：

  - 为 `batch_size` 和 `concurrents` 建立可配置策略；
  - 小于阈值时直接走本地 `process()`；
  - 对尾部 range 保留本地兜底路径；
  - 分别统计 CPU 利用率、P99 延迟、吞吐和调度开销；
  - 不要只依据平均耗时判断收益。

- **错误码和异常处理必须完整收敛**

  所有 future 都应执行 `get()`，并统一归并非 0 返回码或异常。尤其在多个因子生成器并行执行时，不能因为某一个 future 失败就直接丢弃其他 worker 的上下文，也不能因为提前返回而让后台任务继续访问已释放的业务对象。

  建议为以下情况补充测试：

  - 首个 worker 失败；
  - 中间 worker 失败；
  - 尾部本地任务失败；
  - 多个 worker 同时失败；
  - 空输入 range；
  - 输入数量小于 `concurrents`；
  - 输入数量远大于并行槽位。

---

总体来看，`feeda-mv-grc` 的处理器和因子生成模块是最值得优先验证的迁移区域，`feeda-mv-grg` 则适合以 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 为参考进行局部扩展。建议先选择一个无共享写入、结果可按候选位置合并的过滤或打分模块做基准实验，再决定是否将该并行消费骨架推广到更多业务处理器。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
