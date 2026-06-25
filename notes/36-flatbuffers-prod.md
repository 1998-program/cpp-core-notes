# FlatBuffers 生产实战：与 Protobuf/brpc/ng-framework 深度对比

> 笔记编号：36　日期：2026-06-25　主题：FlatBuffers 零拷贝序列化在推荐在线服务的实战落地

前面 #01、#16、#31 三篇覆盖了 FlatBuffers 的设计哲学、binary layout 与 builder 基础。本篇换一个角度，聚焦"在推荐融合在线服务（brpc + ng-framework + protobuf 主导）中，FlatBuffers 到底用在哪、怎么用、踩过什么坑"。

---

## 1. 为什么还要再聊 FlatBuffers？

在线推荐链路的典型痛点：

- **召回 → 排序 → 重排** 之间需要透传大量 item 特征（数万 item × 数百维），单 RPC payload 经常 5–50 MB。
- Protobuf 编解码是 CPU 重灾区：在 ng-framework P99 火焰图里，`protobuf::Message::ParseFromArray` 常年占 **8–15%**。
- 排序服务对 item 特征是 **"读 1 次，丢掉"** 的访问模式——根本不需要把它们 deserialize 成一棵对象树。

这正是 FlatBuffers 的甜区：**mmap / 直接指向 wire buffer，访问即解析**，零拷贝、零反序列化。

---

## 2. 核心实现回顾（仅记住三件事）

1. **逆序 builder**：`FlatBufferBuilder` 从 buffer 末尾向前写，最后写 root offset。所以"完成构建"才能取得最终偏移。
2. **vtable 偏移寻址**：每个 table 通过一个 vtable 记录 "field id → 字段偏移"，缺失字段返回默认值。这让 **schema 演化天然向前/向后兼容**，新增字段老代码自动忽略。
3. **`Verifier`**：访问前必须验签——验证所有 offset 都落在 buffer 内，防止恶意/损坏数据导致越界。**线上服务必须开**，否则 FlatBuffers 就是一个未校验的指针强转。

```cpp
flatbuffers::Verifier v(buf, size);
if (!VerifyFeatureBatchBuffer(v)) {
    LOG(ERROR) << "corrupted flatbuffer payload";
    return -1;
}
const FeatureBatch* fb = GetFeatureBatch(buf);  // 零拷贝，直接用
```

---

## 3. 在 brpc 里怎么传 FlatBuffer？

brpc 默认协议是 baidu_std + Protobuf，但 **request/response 的 message 字段允许是 `bytes`**。生产里通常这样裹一层：

```proto
// frame.proto，外壳走 protobuf 拿 brpc 调度/染色/超时
message RankRequest {
  string trace_id = 1;
  bytes  payload  = 2;   // FlatBuffer 二进制
  uint32 payload_schema_version = 3;
}
```

服务端：

```cpp
void RankServiceImpl::Rank(google::protobuf::RpcController* cntl_base,
                           const RankRequest* req,
                           RankResponse* rsp,
                           google::protobuf::Closure* done) {
    brpc::ClosureGuard guard(done);
    auto* cntl = static_cast<brpc::Controller*>(cntl_base);

    // 1) 验签：必做
    flatbuffers::Verifier v(
        reinterpret_cast<const uint8_t*>(req->payload().data()),
        req->payload().size());
    if (!VerifyFeatureBatchBuffer(v)) {
        cntl->SetFailed(brpc::EREQUEST, "bad flatbuffer");
        return;
    }
    const FeatureBatch* fb = GetFeatureBatch(req->payload().data());

    // 2) 直接遍历，无 copy
    for (auto* it : *fb->items()) {
        score(it->id(), it->dense(), it->sparse());  // it 直接指向网络 buffer
    }
}
```

**关键点**：

- `req->payload()` 是 std::string，但 brpc 底层是 `IOBuf`。当 payload 较大（> 几百 KB）时，**直接拿 `cntl->request_attachment()` 走 attachment**，避免一次大块 `protobuf bytes → std::string` 的连续内存分配。attachment 是 IOBuf，FlatBuffer 又是连续内存——这两者之间需要 `IOBuf::fetch1()` 把多个 block 拼成一段连续区。如果 attachment 跨多个 block，必须先 `copy_to_continuous_buffer()`，否则 FlatBuffer 验签会 segfault。
- **Schema 版本字段**比 protobuf 的 reserved 更脆弱：FlatBuffers 一旦改变字段顺序或类型，二进制就不兼容。生产规范是 **schema 只追加 field、永远不删、不改类型**。在 `.fbs` 里用 `// id: 7` 注释手动记字段 id 是好习惯。

---

## 4. 在 ng-framework 的算子里怎么落？

ng-framework（推荐离在线一体化框架）以"算子 DAG"组织业务流。FlatBuffers 在算子里有两种典型用法：

### 4.1 跨算子上下文透传

不要把 FlatBuffer 反序列化进 `Context`。**把整段 buffer 放进 `Context` 的 shared_ptr，按需要在算子内拿指针访问**：

```cpp
class FeatureUnpackOp : public Op {
  Status Process(Context* ctx) override {
    auto buf = ctx->Get<std::shared_ptr<std::string>>("fb_payload");
    const FeatureBatch* fb = GetFeatureBatch(buf->data());
    ctx->Set("fb_view", fb);   // 只塞指针，下游零拷贝
    return Status::OK();
  }
};
```

下游 `RankOp`、`RerankOp` 都拿 `const FeatureBatch*`，靠 `buf` 的 shared_ptr 续命。**绝不可以**把 `fb->items()->Get(i)->dense()` 这种内部指针存到生命周期长于 buf 的地方。

### 4.2 Cache / 离线特征落盘

`jemalloc` arena + FlatBuffer 是绝配：

- 用一个 **dedicated arena**（`mallctl("arenas.create")`）给特征 buffer 分配；
- FlatBuffer 本身是 POD 连续内存，**没有析构函数链**，可直接 `mmap` 到磁盘/共享内存；
- 读取时 `mmap(MAP_PRIVATE)` 后直接 `GetFeatureBatch(addr)`，**进程启动 0 反序列化**。

我们线上 item embedding 全量加载从 protobuf 改 FlatBuffer 后：
- 加载耗时：38 s → 1.2 s
- 常驻 RSS：因为没有解出的对象树，**降了约 35%**
- 但 CPU cache miss 略升，因为访问是 pointer chasing：通过对 hot field 做 **struct（fixed layout）** 而不是 **table** 解决，详见下一节。

---

## 5. table vs struct：性能差异不止一倍

FlatBuffers 有两种复合类型：

| 类型 | 字段访问 | 大小 | 演化 | 适用 |
|---|---|---|---|---|
| `table`  | 经 vtable 二跳 | 可变 | 可加字段 | 主接口、对外协议 |
| `struct` | 直接偏移 | 固定 | 不可演化 | hot 内层数据：embedding、dense feature |

热点路径（每条样本调用 N 万次）务必用 struct：

```fbs
struct DenseFeat {
  user_emb_offset: uint32;
  item_emb_offset: uint32;
  ctr_prior:       float;
}

table Item {
  id:      uint64;
  dense:   DenseFeat;     // struct，直接内联
  sparse:  [uint32];      // vector
}
```

一次实测，把 `DenseFeat` 从 table 改 struct，排序算子的 `cycles/sample` 下降 18%。原因是 struct 的内联访问让 prefetch 友好，避免 vtable 的额外间接。

---

## 6. 与 Protobuf Arena 的对比与共存

#17 笔记里我们讨论过 `protobuf::Arena`：通过线性分配把 Protobuf 反序列化的小对象 (`new`) 聚到一起，减少 malloc 压力。Arena 解决的是 **"必须 deserialize"** 场景下的内存碎片，而 FlatBuffers 解决的是 **"根本不要 deserialize"**。

实践决策树：

```
RPC payload?
├─ 字段经常被修改 / 反复访问 / 嵌套深   → Protobuf + Arena
├─ 只读 / 单次扫描 / 大 batch          → FlatBuffers
├─ 离线特征文件 / 共享内存             → FlatBuffers (mmap)
└─ 配置 / 控制面                       → Protobuf (可读性 + 工具链)
```

**共存技巧**：外壳 Protobuf（trace / 路由 / 控制元数据），内层重数据 FlatBuffer bytes。我们整条推荐链路 90% 的服务都是这种"P 套 F"结构。

---

## 7. 五个真踩过的坑

1. **`bytes` 字段被 brpc 默认按 string 拷贝**：大 payload 用 attachment，避免双倍内存峰值。
2. **不开 Verifier**：被压测脏数据打挂过一次 Rank 服务，core dump 在 `vector::Get`。从此 Verifier 写进 base op 模板，开关只能在 dev 环境关。
3. **schema 兼容性**：曾经一个新人把 `int32 ctr` 改成 `float`，灰度的两个版本互发请求时直接读出 NaN。规范：**只 append、不 modify、不 reorder**，加字段必须 bump `payload_schema_version`。
4. **对齐**：FlatBuffer 要求 buffer 起始地址按 root type 的最大字段对齐。从 IOBuf 拷出来的 std::string 数据起点不一定 8 字节对齐，访问 double 字段在某些 ARM 机器上直接 SIGBUS。解决：分配时 `std::aligned_alloc(16, n)`，或 `flatbuffers::DetachedBuffer` 自带对齐。
5. **`CreateVector` 误用**：在循环里把每个元素先 `CreateString` 再放入临时 vector，结果偏移在 builder 里"前后穿插"导致最终乱序。规则：**所有子对象必须先全部创建完，再 `CreateVector`**；或者用 `CreateVectorOfStrings` / `CreateVectorOfStructs` 一次到位。

---

## 8. 一段拿来就能用的最小生产模板

```cpp
// 构建端
flatbuffers::FlatBufferBuilder fbb(64 * 1024);  // 预估初始容量，避免多次 grow
std::vector<flatbuffers::Offset<Item>> items;
items.reserve(n);
for (size_t i = 0; i < n; ++i) {
  DenseFeat dense(emb_off[i], item_off[i], prior[i]);  // struct 直接构造
  auto sparse = fbb.CreateVector(sparse_ids[i]);
  items.push_back(CreateItem(fbb, ids[i], &dense, sparse));
}
auto root = CreateFeatureBatch(fbb, fbb.CreateVector(items));
fbb.Finish(root);

// 通过 brpc attachment 发出去，零拷贝
butil::IOBuf attach;
attach.append(fbb.GetBufferPointer(), fbb.GetSize());
cntl->request_attachment().swap(attach);
```

```cpp
// 消费端
const auto& iobuf = cntl->request_attachment();
butil::IOBuf::Block* contiguous = nullptr;
const uint8_t* data = nullptr;
size_t size = iobuf.size();
std::unique_ptr<uint8_t[]> own;
if (iobuf.backing_block_num() == 1) {
  data = static_cast<const uint8_t*>(iobuf.fetch1());
} else {
  own.reset(new (std::align_val_t(16)) uint8_t[size]);
  iobuf.copy_to(own.get(), size);
  data = own.get();
}
flatbuffers::Verifier v(data, size);
CHECK(VerifyFeatureBatchBuffer(v));
const FeatureBatch* fb = GetFeatureBatch(data);
// ... 直接读 fb
```

---

## 9. 一句话总结

> **FlatBuffers 不是 Protobuf 的替代品，是它的补集。**
> Protobuf 管控制面、跨语言、强演化；FlatBuffers 管数据面、低延迟、零拷贝。
> 在 brpc + ng-framework 的推荐链路里，把它放在 attachment / 共享内存 / mmap 三个位置上，能省下整条链路 5–10% 的 CPU 与 20%+ 的内存。

---

参考：
- 笔记 #01 / #16 / #31：FlatBuffers 设计与 builder 内部
- 笔记 #17：protobuf::Arena
- 笔记 #18 / #34 / #35：brpc bthread / butex / IOBuf
- 笔记 #24：jemalloc Arena 与 mmap 协同

---

## 七、业务代码库适配分析
> **分析时间**：2026-06-25T19:02:52.840139
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：FlatBuffers 引入可行性

### 1. 分析摘要

- 本次扫描的两个业务代码库 `feeda-mv-grg`（序列生成服务）和 `feeda-mv-grc`（召回汇聚服务）中，**尚未发现 FlatBuffers 的直接使用**，说明当前业务链路仍以 Protobuf 序列化/反序列化为主，尚没有可直接复用的 FlatBuffers 工程实践代码或基础封装。

- 从现有等价用法看，两个代码库均存在一定规模的 Protobuf `SerializeToString` / `ParseFromString` 调用。其中 `feeda-mv-grg` 中 `ParseFromString` 出现 18 次、`SerializeToString` 出现 6 次，说明该服务既有较多解析逻辑，也有响应侧序列化逻辑；`feeda-mv-grc` 中 `SerializeToString` 出现 16 次，主要集中在缓存、下游请求封装、特征/候选集写入等场景。整体来看，**FlatBuffers 更适合优先评估“大 payload、只读透传、缓存落盘、召回候选集/特征批量传输”场景**，而不是替换所有 Protobuf 使用点。

---

### 2. 代码库详情

#### 2.1 `feeda-mv-grg`：序列生成服务

- 当前未发现 FlatBuffers 使用。
- 扫描到的 Protobuf 等价调用规模：
  - `SerializeToString`：6 次，分布在 3 个文件。
  - `ParseFromString`：18 次，分布在 7 个文件。
- 从示例看，主要序列化场景集中在响应侧扩展字段构造，例如：
  - `process/new_response_function.cpp:118`
    - 将 `extmsg` 序列化为字符串后进行 base64 编码，再写入响应扩展字段。
  - `process/new_response_function.cpp:249`
    - 将 `_ext_msg` 序列化到 `_ext_msg_res`，再 base64 编码写入 `predictor_extmsg`。
  - `process/response_function.cpp:4219`
    - 将 `extmsg` 序列化并 base64 编码后写入 `content_item_ptr->mutable_ext()->set_predictor_extmsg(...)`。

- 该代码库的特点是：
  - 响应侧存在较多“Protobuf 二进制串 + base64 + ext 字段”的封装模式。
  - `ParseFromString` 次数明显多于 `SerializeToString`，说明服务内部可能存在多处从上下文、上游响应、缓存或扩展字段中恢复 Protobuf 对象的逻辑。
  - 如果这些 Protobuf 内容是大批量 item 特征、候选集、排序辅助信息，且下游只读访问，则具备 FlatBuffers 迁移潜力。
  - 如果只是小型扩展信息、控制字段或需要频繁修改的结构，继续使用 Protobuf 更合适。

#### 2.2 `feeda-mv-grc`：召回汇聚服务

- 当前未发现 FlatBuffers 使用。
- 扫描到的 Protobuf 等价调用规模：
  - `SerializeToString`：16 次，分布在 9 个文件。
  - 未在本次结果中看到明确的 `ParseFromString` 统计。
- 典型场景包括：
  - `plugin/cache_queue.cpp:127`
    - 将 `queue_cache` 序列化为 `cache_data_str` 后写入缓存。
  - `plugin/feed_ufs_plugin.cpp:148`
    - 构造 `feed_request` 后序列化到 `fork_request.mutable_req_str()`，用于下游 UFS 请求。
  - `processor/msv_implicit_write_sndb.cpp:73`
    - 将 `candidates` 序列化为 `pb_str` 后写入 SNDB 或类似存储。

- 该代码库的特点是：
  - 更偏向召回汇聚、缓存写入、下游请求打包。
  - `SerializeToString` 使用点较多，可能存在“候选集/队列/缓存对象整体序列化”的批量数据路径。
  - 其中缓存与候选集写入类场景，比普通 RPC 控制请求更适合优先评估 FlatBuffers，因为 FlatBuffers 可支持 mmap / 直接读取 / 零反序列化。

---

### 3. 💡 适用性评估与建议

- **建议一：优先评估 `feeda-mv-grc/plugin/cache_queue.cpp` 的缓存序列化场景**
  - 当前代码：
    - `plugin/cache_queue.cpp:127`
    - `queue_cache.SerializeToString(&cache_data_str);`
  - 该场景很可能属于“结构化数据写缓存，再由后续流程读取”的路径。
  - 如果 `queue_cache` 中包含较多 item、候选队列、特征字段，且读取侧主要是只读扫描，可考虑设计对应 `.fbs` schema，例如：
    - `QueueCache`
    - `Candidate`
    - `ItemFeature`
  - 迁移收益：
    - 避免从缓存读取后 `ParseFromString` 生成 Protobuf 对象树。
    - 可直接从缓存 value 或 mmap 文件中访问 FlatBuffer。
    - 对大候选集、批量特征字段场景，CPU 与内存收益会更明显。
  - 迁移方式建议：
    - 初期不要直接替换现有缓存格式。
    - 增加新 key 后缀或版本字段，例如 `cache_format_version = flatbuffer_v1`。
    - 读路径同时兼容 Protobuf 与 FlatBuffers，灰度验证命中率、解析耗时和错误率。

- **建议二：评估 `feeda-mv-grc/processor/msv_implicit_write_sndb.cpp` 中 `candidates` 写入 SNDB 的格式替换**
  - 当前代码：
    - `processor/msv_implicit_write_sndb.cpp:73`
    - `candidates.SerializeToString(&pb_str);`
  - `candidates` 从命名上看可能是候选集或召回结果集合，通常具备以下特征：
    - item 数量较多。
    - 字段以只读为主。
    - 写入后被下游批量读取。
  - 这是 FlatBuffers 的高潜力场景。
  - 建议将候选集中热点字段拆成：
    - 外层 `table CandidateBatch`：保留 schema 演化能力。
    - 内层热点 `struct CandidateFeature`：用于固定布局字段，例如 `id`、`score`、`reason`、`feature_offset` 等。
  - 这样可以减少 Protobuf parse 开销，同时降低候选集读取时的小对象分配和内存碎片。

- **建议三：谨慎处理 `feeda-mv-grg/process/response_function.cpp` 和 `process/new_response_function.cpp` 中的 extmsg base64 场景**
  - 当前代码：
    - `process/new_response_function.cpp:118`
    - `process/new_response_function.cpp:249`
    - `process/response_function.cpp:4219`
  - 这些位置目前是：
    - Protobuf `SerializeToString`
    - base64 编码
    - 写入 `predictor_extmsg` 或响应扩展字段
  - 如果 `extmsg` 只是小型控制信息、调试信息、预测扩展字段，FlatBuffers 收益有限，因为：
    - base64 本身会引入额外体积和 CPU 开销。
    - 小对象 Protobuf 序列化成本不一定是瓶颈。
    - FlatBuffers 主要优势在大 payload 和只读批量访问。
  - 但如果 `extmsg` 中实际携带了大规模 item 特征、排序解释信息或下游只读数据，则可以考虑：
    - 先保留外层字段 `predictor_extmsg` 不变。
    - 内部二进制格式由 Protobuf 改为 FlatBuffers。
    - 增加格式版本，例如 `predictor_extmsg_format = FB_V1`。
  - 注意：如果仍然必须 base64，FlatBuffers 只能减少反序列化成本，无法消除 base64 编解码成本。

- **建议四：`feeda-mv-grc/plugin/feed_ufs_plugin.cpp` 中下游请求封装不建议第一批迁移**
  - 当前代码：
    - `plugin/feed_ufs_plugin.cpp:148`
    - `feed_request.SerializeToString(fork_request.mutable_req_str())`
  - 该场景更像是下游服务请求协议封装。
  - 如果下游 UFS 服务当前只接受 Protobuf 请求，直接替换为 FlatBuffers 需要上下游协议同时升级，联动成本较高。
  - 建议优先保留 Protobuf，除非满足以下条件：
    - 请求体中存在大规模只读特征或候选集。
    - 上下游都能同时支持 FlatBuffers。
    - 能通过外层 Protobuf `bytes payload` 或 brpc attachment 传输内部 FlatBuffer。
  - 更稳妥的方案是采用“Protobuf 外壳 + FlatBuffers payload”的混合模式：
    - Protobuf 保留 trace、路由、超时、AB 参数等控制面字段。
    - FlatBuffers 只承载大体积特征、候选列表、embedding offset 等数据面字段。

- **建议五：为两个代码库先建设统一的 FlatBuffers 基础封装，而不是分散落地**
  - 由于 `feeda-mv-grg` 和 `feeda-mv-grc` 当前都没有 FlatBuffers 使用经验，建议先抽象公共组件：
    - `fb_verifier_util.h`
    - `fb_payload_view.h`
    - `fb_schema_version.h`
    - `brpc_attachment_flatbuffer_util.h`
  - 基础封装应包含：
    - `Verifier` 强制校验。
    - schema version 检查。
    - buffer 生命周期管理，例如 `shared_ptr<std::string>` / `IOBuf` 连续化后的 holder。
    - 对齐分配工具。
    - 统一错误码和日志。
  - 避免各业务文件直接调用 `GetXXX(buf)`，否则容易出现生命周期、对齐和校验遗漏问题。

---

### 4. ⚠️ 引入风险与限制

- **风险一：当前代码库没有 FlatBuffers 既有样例，首批迁移需要补齐工程链路**
  - 两个代码库均未发现 FlatBuffers 使用，因此没有可直接参考的本地代码。
  - 需要新增：
    - `.fbs` schema 管理。
    - `flatc` 代码生成规则。
    - 编译系统接入。
    - 单测/兼容性测试。
    - 灰度开关和回滚路径。
  - 建议首批只选择一个低风险缓存或候选集场景试点，不建议全链路直接替换。

- **风险二：FlatBuffers 不适合频繁修改字段的对象模型**
  - FlatBuffers 更适合只读、单次扫描、大 batch 数据。
  - 如果业务代码在解析后会频繁修改字段、补充嵌套对象、合并多个消息，Protobuf 仍更合适。
  - 对于 `feed_request` 这类请求构造过程，尤其是 `plugin/feed_ufs_plugin.cpp` 中不断 `mutable_xxx()` 填字段的模式，FlatBuffers builder 反而可能增加开发复杂度。

- **风险三：schema 演化规则必须强约束**
  - FlatBuffers 的兼容性依赖字段 id、类型和顺序规范。
  - 禁止：
    - 修改已有字段类型。
    - 重排字段。
    - 删除字段后复用 id。
  - 建议在 `.fbs` 中显式维护字段编号，并通过 code review 或 CI 检查 schema 变更。
  - 所有 payload 都应带版本，例如 `payload_schema_version` 或缓存 value header。

- **风险四：大 payload 传输时需要关注 brpc / IOBuf 连续内存问题**
  - FlatBuffers 访问要求底层 buffer 是连续内存。
  - 如果后续在 brpc attachment 中传输 FlatBuffer，`IOBuf` 可能由多个 block 组成，不能直接把分散 block 当作连续 FlatBuffer 使用。
  - 消费端需要统一处理：
    - 优先 `fetch1()` 获取连续片段。
    - 如果跨 block，则 copy 到连续 buffer。
    - copy 后保证 buffer 生命周期长于所有 FlatBuffer 指针视图。
  - 同时要注意对齐问题，尤其是 ARM 环境下访问 `double` / `uint64` 字段可能触发 SIGBUS。

---

### 结论

- `feeda-mv-grg` 和 `feeda-mv-grc` 当前都没有 FlatBuffers 使用，短期不建议做全量替换。
- 更合理的落地路线是：
  - 第一阶段：选择 `feeda-mv-grc/plugin/cache_queue.cpp` 或 `feeda-mv-grc/processor/msv_implicit_write_sndb.cpp` 这类缓存/候选集场景试点。
  - 第二阶段：沉淀统一 FlatBuffers verifier、schema version、buffer holder、brpc attachment 工具。
  - 第三阶段：再评估 `feeda-mv-grg/process/response_function.cpp`、`process/new_response_function.cpp` 中较大的 `extmsg` payload 是否值得替换。
- 对小型控制消息、下游固定 Protobuf 协议请求、频繁修改的对象结构，继续保留 Protobuf 更稳妥。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
