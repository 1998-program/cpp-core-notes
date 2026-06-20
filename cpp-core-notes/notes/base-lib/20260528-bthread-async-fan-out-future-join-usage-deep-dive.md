# bthread_async fan-out 与 Future join 深度解析

> 生成时间：2026-05-28  
> 目标代码库：`feeda-mv-grc`  
> 关联周级计划选题：`bthread_async fan-out 与 Future join`（P0/高难度）

---

## 一、API 模型与调度语义

### 1.1 核心 API

代码库使用的 bthread_async 声明于 `src/processor/video_launch/user_intent_predict.cpp` 第 17-22 行：

```cpp
#include <baidu/feed/mlarch/babylon/future.h>
#include <bthread.h>
using ::baidu::feed::mlarch::babylon::bthread_async;
using baidu::feed::mlarch::babylon::Future;
```

**API 签名**：`bthread_async(F &&func) -> Future<Ret, Mutex>`

- 返回类型是 `babylon::Future`，不是 `std::future`
- 模板参数 `Mutex` 默认值是 `::bthread::Mutex`（bthread 兼容锁，而非 pthread mutex）
- `Future::get()` 会阻塞等待 bthread 完成

### 1.2 调度边界

`bthread_async` 启动的是 **bthread**（brpc 内置轻量线程），不是系统线程：
- bthread 绑定到 Worker 线程池（由 brpc Server 初始化时创建）
- bthread 之间共享同一个 Worker 的 CPU 时间片，切换成本极低
- 但 bthread **不能调用阻塞系统调用**（如 pthread_mutex_lock、fsync、connect）；若调用会退化为系统线程

### 1.3 Future join 语义

```cpp
// user_intent_predict.cpp:145-152
for (auto& future : future_list) {
    if (future.valid()) {
        int32_t ret = future.get();  // 阻塞等待当前 future
        if (ret != 0) {
            ++failed_batch_count;
        }
    }
}
```

**关键语义**：
- `future.valid()` 检查 future 是否有效
- `future.get()` 阻塞等待并获取返回值（不是 `std::future` 的 `get()` 共享值语义）
- 循环中串行 `get()`，等同于逐个 join（不是一次性 `wait_all`）

---

## 二、真实服务使用示例

### 2.1 UserIntentPredictFunction 批量并发模型

**文件**：`src/processor/video_launch/user_intent_predict.cpp:91-141`

```cpp
// 第 99-103 行：结果容器预分配
std::vector<std::unordered_map<uint64_t, std::vector<float>>> batch_results;
batch_results.resize(batch_num);  // 预分配避免引用失效
std::vector<Future<int32_t, ::bthread::Mutex>> future_list;
future_list.reserve(batch_num);

// 第 111-141 行：拆分 effect_queue 并行发送
while (current_batch_index < (uint32_t)batch_num && index < _effect_queue->size()) {
    // ... 计算 batch_start, batch_count ...
    auto& batch_result = batch_results[current_batch_index];
    future_list.emplace_back(bthread_async([this, batch_start, batch_count, &batch_result]() {
        return this->process_single_batch(batch_start, batch_count, batch_result);
    }));
    index = start_index + batch_count;
    ++current_batch_index;
}

// 第 145-152 行：join 所有 future
int32_t failed_batch_count = 0;
for (auto& future : future_list) {
    if (future.valid()) {
        int32_t ret = future.get();
        if (ret != 0) {
            ++failed_batch_count;
        }
    }
}
```

**设计要点**：
1. **batch_results 预分配**：大小为 `batch_num`，每个元素是 `unordered_map<uint64_t, vector<float>>`，通过索引访问而非 push_back 避免扩容导致引用失效
2. **future_list 预分配**：`reserve(batch_num)` 避免扩容导致迭代器失效
3. **捕获策略**：`[this, batch_start, batch_count, &batch_result]` —— 值捕获 `this`、原始类型、`&batch_result` 按引用捕获（因为预分配后索引固定）
4. **失败容错**：若所有 batch 均失败，则跳过结果合并

### 2.2 process_single_batch 单 batch 实现

**文件**：`src/processor/video_launch/user_intent_predict.cpp:228-391`

```cpp
int32_t process_single_batch(uint32_t start_index, uint32_t count,
                              std::unordered_map<uint64_t, std::vector<float>>& batch_result) noexcept {
    // 第 232-239 行：提前提取上下文指针（安全，因为 bthread 内无法安全访问 Graph Context）
    const std::string* uid_str = context.get_uid();
    const std::string* cuid_str = context.get_cuid();
    const std::string* baidu_id = context.get_baiduid();
    const uint64_t* logid = context.get_logid();

    // 第 249-268 行：构造 PredictorRequest
    ::baidu::feed::mlarch::PredictorRequest request;
    auto* user_p = request.mutable_user();
    uint64_t uid = 0;
    CastUtil::string_to_numeric(uid, *uid_str);
    user_p->set_uid(uid);
    static std::string token = "intent_longterm";
    request.set_queue(baidu::feed::mlarch::Queue::INTENT_LONGTERM_VALUE);
    request.set_token(token);

    // 第 270-310 行：透传 user/request feature（CopyFrom 是安全的，因为是单线程）
    auto request_feature = sample.mutable_request_feature();
    request_feature->set_ut((*_request_response)->ut());
    // ...

    // 第 316-328 行：填充 context 样本
    for (uint32_t i = start_index; i < _effect_queue->size() && processed < count; ++i) {
        auto context_sample_iter = _doc_smaple->find(ridinfo->rid);
        if (context_sample_iter != _doc_smaple->end() && context_sample_iter->second != nullptr) {
            request.add_nid(ridinfo->rid);
            auto new_sample = sample.add_context();
            new_sample->CopyFrom(*(context_sample_iter->second));
            ++processed;
        }
    }

    // 第 349-363 行：调用 PredictorPlugin
    GET_DTCNTL(dt_cntl);
    int32_t predict_ret = _predictor_plugin->predict(request, response, *logid, dt_cntl);

    // 第 375-378 行：解析结果写入 batch_result
    if (process_response(response, batch_result) != 0) {
        return -1;
    }
    return 0;
}
```

---

## 三、关键 Pitfalls

### 3.1 捕获引用指向的对象可能失效

**陷阱**：若捕获 `&_effect_queue`（指向 Graph 生命周期管理的 vector），但 `process_single_batch` 执行晚于 `Graph::reset()`，则访问违例。

**正确做法**（如本例）：
- 在 `process()` 主流程中按索引拆分 batch，不捕获整个 queue
- 在 lambda 中捕获 `batch_start, batch_count`（原始类型）和 `&batch_result`（预分配后固定索引）

### 3.2 bthread 中调用阻塞系统调用

**陷阱**：若 `process_single_batch` 内部调用了 `pthread_mutex_lock` 或 `connect()`，bthread 会阻塞整个 Worker 线程，影响其他请求。

**检查点**：
- brpc 提供的 bthread 版本 `bthread::Mutex` 是 bthread 兼容的（用户态自旋+让出）
- 但 `std::mutex`（Pthreads）会退化为系统线程

### 3.3 Future join 顺序影响延迟

**陷阱**：若在循环中顺序 `future.get()`（如本例），则 P99 = sum(batches_latency)。若 batches 可并发处理，最终延迟应该接近 max(batches_latency)。

**当前实现分析**：
```cpp
for (auto& future : future_list) {
    int32_t ret = future.get();  // 串行等待
}
```
这实际上是串行 join，不是并发等待。但 `bthread_async` 在 launch 时已经并发执行了，`get()` 的串行调用不影响实际并发度（因为 bthread 已经在运行）。

---

## 四、调试 Checklist

| 步骤 | 命令/检查点 | 预期 |
|------|------------|------|
| 1. 确认 bthread_async 调用成功 | 搜索 `bthread_async` 调用点，验证返回非空 future | future_list.size() == batch_num |
| 2. 确认 batch_results 预分配 | 检查 `batch_results.resize(batch_num)` 或 `reserve` | 无 `push_back` 后索引访问 |
| 3. 确认 lambda 捕获安全 | 检查捕获列表中无指向 Graph 生命周期的指针 | 捕获原始类型 + 预分配容器的引用 |
| 4. 确认 Future::get() 不阻塞主线程 | 验证 `future.get()` 在 bthread 上下文 | 无 pthread mutex lock |
| 5. 确认失败容错 | 检查 `failed_batch_count` 判断逻辑 | 所有失败时跳过结果合并 |
| 6. 确认 batch_size 参数 | 检查 `_batch_size` 是否符合预期 | 通常 50-150 |

---

## 五、证据来源

| 文件 | 行号 | 内容 |
|------|------|------|
| `src/processor/video_launch/user_intent_predict.cpp` | 17-22 | bthread_async/Future 头文件与 using 声明 |
| `src/processor/video_launch/user_intent_predict.cpp` | 91-103 | batch_results/future_list 预分配 |
| `src/processor/video_launch/user_intent_predict.cpp` | 111-141 | bthread_async 启动并发 batch |
| `src/processor/video_launch/user_intent_predict.cpp` | 145-152 | Future join 串行等待 |
| `src/processor/video_launch/user_intent_predict.cpp` | 228-391 | process_single_batch 单 batch 实现 |
| `src/processor/video_launch/user_intent_predict.cpp` | 232-239 | 提前提取 Graph Context 指针 |
| `src/processor/video_launch/user_intent_predict.cpp` | 270-310 | feature CopyFrom 透传（单线程安全） |

---

## 六、未确认问题

1. **batch_size 上限**：第 41 行注释 `// batch_size 固定为150`，但第 437 行默认值是 50。需要验证运行时实际值来源（gflag 或 config）。
2. **future_list 的 Mutex 类型**：代码使用 `Future<int32_t, ::bthread::Mutex>`，需确认 bthread::Mutex 在 bthread_async 中的具体语义（是否会影响并发度）。