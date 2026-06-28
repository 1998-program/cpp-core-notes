# FlashInfer — 大模型推理的「内核工厂」

> 仓库：[flashinfer-ai/flashinfer](https://github.com/flashinfer-ai/flashinfer) · ⭐ 5.8k · 2026-06-28
> 一句话：**为 LLM Serving 而生的 GPU Kernel 库与代码生成器**，统一封装 Attention / GEMM / MoE / Sampling，自动在 FlashAttention‑2/3、cuDNN、CUTLASS、TensorRT‑LLM 之间挑选最快后端。

---

## 1. 它解决的核心问题

LLM 推理引擎（vLLM、SGLang、TensorRT‑LLM、TGI…）真正烧 GPU 时间的就那么几类算子：Paged Attention、Prefill/Decode、GEMM、MoE 路由、采样。每家以前都各写一套，导致：

- **同一张 H100 上同一个模型，跨引擎延迟能差 2–3 倍**——差距全在 kernel 上；
- 新硬件（Hopper FP8、Blackwell FP4、MNNVL）来一次，所有引擎要再重写一次；
- DeepSeek‑MLA、Llama‑4 路由、推测解码……每个新 trick 都要从零写 CUDA。

FlashInfer 的定位就是：**做这一层的 "Intel MKL"**。把 LLM serving 里最痛的算子集中维护，给上层引擎一个稳定 Python API，下面在多个后端里自动选最快的。

事实上 **vLLM、SGLang、MLC‑LLM 都已经把 attention/sampling 切到 FlashInfer**，它是 NVIDIA 官方 TRT‑LLM 之外目前最有影响力的开源 LLM kernel 库。

---

## 2. 最核心的三个使用场景

### 场景 A：给推理引擎做后端（90% 用户的真实用法）

你不是直接 `import flashinfer` 写应用，而是**作为引擎开发者**用它替换自家 kernel：

```python
import flashinfer

# Paged KV-Cache 的 Prefill+Decode 混合调度（生产 serving 最常见）
wrapper = flashinfer.BatchPrefillWithPagedKVCacheWrapper(workspace_buffer, "NHD")
wrapper.plan(
    qo_indptr, paged_kv_indptr, paged_kv_indices, paged_kv_last_page_len,
    num_qo_heads, num_kv_heads, head_dim, page_size,
    causal=True, pos_encoding_mode="ROPE_LLAMA",
)
o = wrapper.run(q, paged_kv_cache)
```

要点：
- `plan / run` 两段式 API——把 indptr 这种**仅依赖 batch 形状**的开销提前在 plan 阶段算掉，run 阶段只跑真正的 CUDA kernel，是 continuous batching 能跑满 GPU 的关键；
- 同一份调用代码，在 SM75（T4）到 SM12（RTX 50/DGX Spark）上都能跑，FlashInfer 内部决定走 FA2 / FA3 / cuDNN / CUTLASS / TRT‑LLM；
- **CUDAGraph + torch.compile 友好**——这是低延迟在线服务能不能上的硬门槛。

### 场景 B：自定义 Attention Variant 的 JIT 工厂

LLM 团队最痛的事：「我想给 attention 多塞一个 bias / 一个 mask / 一个 sink token，要重写 kernel 吗？」FlashInfer 用 **JIT 模板 + Python DSL** 让你只写「分子运算」，剩下的 tiling、warp specialization、async copy 全自动：

```python
# 伪代码示意
@flashinfer.jit.attention_variant
def my_attn(q, k, v, sink_bias):
    s = q @ k.T / sqrt(d) + sink_bias  # 你只关心数学
    return softmax(s) @ v
```

工业意义：**MLA、StreamingLLM、Sink Attention、ALiBi、Sliding Window 这些 paper trick 不再要 C++/CUDA 工程师介入**。这是 vLLM 等社区能在 paper 出来一周内支持新模型的底层原因。

### 场景 C：Sorting‑Free Sampling — 在线服务的隐形大头

很多人以为采样开销可以忽略，实际在 H100 + 短输出 + 高并发场景下，**采样能吃掉 10–20% 的 step 时间**，因为传统实现要 sort + cumsum：

```python
# Top-p / Top-k / Min-p 一步到位，无需 sort
ids = flashinfer.sampling.top_p_sampling_from_probs(probs, uniform_samples, top_p=0.9)
```

FlashInfer 的[排序自由采样](https://flashinfer.ai/2025/03/10/sampling.html)用 rejection sampling + 并行扫描代替 sort，是当前生产引擎默认的方案，**值得任何做在线 LLM 服务的人单独抠一遍**。

---

## 3. 工程亮点（为什么值得"啃源码"）

| 维度 | 设计要点 |
| --- | --- |
| 多后端抽象 | `Backend` 枚举：FA2 / FA3 / cuDNN / CUTLASS / TRT‑LLM；plan 阶段做 cost model 选型 |
| KV‑Cache | 同时支持 **Paged** 和 **Ragged** 两种布局；前者给 vLLM，后者给短上下文 batch |
| Cascade Attention | 层级化 KV‑Cache，**共享前缀**（System Prompt、Few‑Shot）只算一次——长 Prompt 服务关键技 |
| POD‑Attention | 把 Prefill 和 Decode 融到一个 kernel，混合 batch 时减少 launch + tail latency |
| 量化 | FP8（per‑tensor / groupwise）、NVFP4、MXFP4 都已落地到 attention/GEMM/MoE |
| 通信 | 自研 AllReduce + NVSHMEM + **MNNVL 多节点 NVLink**，给大模型 TP/PP 用 |
| 部署友好 | `flashinfer-cubin` 预编译包 + `flashinfer-jit-cache` 按 CUDA 版本分发，免去首启 JIT 抖动 |

---

## 4. 我自己上手的最小路径

```bash
# 1. 装核心 + 预编译 cubin，避免首次 JIT 卡 30 秒
pip install flashinfer-python flashinfer-cubin

# 2. 看硬件兼容、kernel 路径
flashinfer show-config
flashinfer list-modules

# 3. 打开日志，看它替你选了哪条后端
export FLASHINFER_LOGLEVEL=3
python my_bench.py
```

读源码顺序建议：
1. `python/flashinfer/decode.py` / `prefill.py` — 看 plan/run 双段式 API 是怎么搭的；
2. `csrc/attention/` — 看 FA2/FA3 模板 + JIT 入口；
3. `flashinfer/sampling.py` + Blog《Sorting‑Free GPU Kernels》——一晚上能学会一个生产级 trick；
4. `flashinfer/cascade.py` — 共享前缀 KV‑Cache，对长 prompt / 多租户场景立竿见影。

---

## 5. 适合谁

- **正在做 / 维护 LLM 推理引擎**：直接换 backend，几乎一定能拿到性能；
- **平台/Infra 团队**：评估 vLLM/SGLang 时，要知道它们底下的 attention 都是这家；
- **想读真正生产级 CUDA**：比从零看 FlashAttention 源码门槛低，结构更工程化，多后端对比一目了然；
- **量化 / MoE / 推测解码研究者**：FP8/FP4、Grouped GEMM、Chain Speculative Sampling 都已是 first‑class API。

> 简评：FlashInfer 已经从「论文配套库」长成了 LLM Serving 的事实标准 kernel 层。**如果你今天还在自己写 paged attention，那基本是在重复造一台跑得更慢的车。**
