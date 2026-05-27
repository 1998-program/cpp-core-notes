# 06 · vLLM — 高吞吐量 LLM 推理引擎

**项目**：[vllm-project/vllm](https://github.com/vllm-project/vllm)
**Stars**：81.2k ⭐ · Python · Apache-2.0
**定位**：基于 PagedAttention 的 LLM 推理框架，通过显存管理优化和连续批处理，将 LLM 服务吞吐量提升 20x+

---

## 根本问题

LLM 推理的显存瓶颈在 KV Cache：生成每个 token 时需要保存之前所有 token 的注意力键值对（KV Cache），这块显存是动态的、不连续的，传统框架提前预分配导致严重浪费（通常浪费 60-80% 显存），而且无法让多个请求共享前缀缓存。vLLM 引入了 PagedAttention——借鉴操作系统虚拟内存的分页机制管理 KV Cache，让显存利用率接近 100%，同等显存下可并发处理更多请求。

---

## 核心工作原理

### PagedAttention：分页管理 KV Cache

```
传统方式：
Request 1: [KV Cache 块 1][KV Cache 块 2][预留空白......]  ← 大量浪费
Request 2: [KV Cache 块 1][预留空白........................]  ← 更多浪费

PagedAttention：
物理显存被分割为固定大小的"块"（默认 16 token/块）
Request 1: 块A → 块C → 块F（不连续，但逻辑连续）
Request 2: 块B → 块A（共享前缀块A！）
Request 3: 块D → 块E
按需分配，用完即释放，无预留浪费
```

### 连续批处理（Continuous Batching）

```python
# 传统静态批处理：等待一批请求都结束后才开始处理下一批
# 问题：长请求拖慢整个 batch

# 连续批处理：一个请求结束就立刻加入新请求
时间轴：
t=0: [Req1  Req2  Req3  Req4]  # 4个请求同时跑
t=3: Req2结束 → [Req1  Req5  Req3  Req4]  # 立刻填入 Req5
t=5: Req4结束 → [Req1  Req5  Req3  Req6]  # 立刻填入 Req6
# GPU 利用率接近 100%，不再有空闲等待
```

### Prefix Caching（前缀共享）

```python
# 多个请求有相同的系统提示词时，KV Cache 自动共享
System Prompt (1000 tokens): "You are a helpful assistant..."
  ↑ 这部分 KV Cache 只计算一次，所有请求共享

Request 1: [Shared Prefix] + "Tell me about Python"
Request 2: [Shared Prefix] + "Explain sorting algorithms"  
# 节省 1000 token 的重复计算
```

---

## 安装 / 快速上手

```bash
# 安装（需要 CUDA 11.8+）
pip install vllm

# 启动 OpenAI 兼容 API Server
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --port 8000

# 调用（与 OpenAI SDK 完全兼容）
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="none")
response = client.chat.completions.create(
    model="meta-llama/Llama-3.1-8B-Instruct",
    messages=[{"role": "user", "content": "你好"}]
)
```

---

## 实践案例

**场景**：团队需要自托管一个 7B 参数的中文大模型，用于内部代码注释生成工具，要求：QPS 50，P99 延迟 < 2s，成本控制在 4 张 A100 以内。

**配置**：

```bash
# 启动 vLLM 服务，开启所有优化
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-7B-Instruct \
  --tensor-parallel-size 4 \        # 4 张 GPU 张量并行
  --gpu-memory-utilization 0.90 \   # 显存利用率 90%（PagedAttention 管理）
  --enable-prefix-caching \         # 开启前缀缓存
  --max-num-seqs 256 \              # 最大并发序列数
  --quantization fp8 \              # FP8 量化，显存减半
  --port 8000
```

**性能测试结果**：

```bash
# 用 vLLM 自带的 benchmark 工具
python benchmarks/benchmark_serving.py \
  --backend openai \
  --model Qwen/Qwen2.5-7B-Instruct \
  --num-prompts 1000 \
  --request-rate 50

# 示例输出
Throughput: 52.3 requests/s     # 满足 QPS 50 要求
P50 latency: 0.8s
P99 latency: 1.7s               # 满足 < 2s 要求
Total tokens/s: 8,340
GPU memory utilization: 88.2%   # PagedAttention 高效利用
```

对比 Hugging Face 原生推理在相同硬件上只能达到约 3 requests/s，vLLM 提升了约 17 倍。

**集成到应用层**（兼容 OpenAI SDK）：

```python
from openai import AsyncOpenAI
import asyncio

client = AsyncOpenAI(base_url="http://vllm-server:8000/v1", api_key="none")

async def generate_comment(code: str) -> str:
    response = await client.chat.completions.create(
        model="Qwen/Qwen2.5-7B-Instruct",
        messages=[
            {"role": "system", "content": "你是一个代码注释生成助手"},
            {"role": "user", "content": f"为以下代码生成注释：\n{code}"}
        ],
        max_tokens=200,
        stream=True  # 流式输出，减少首 token 延迟
    )
    result = ""
    async for chunk in response:
        result += chunk.choices[0].delta.content or ""
    return result
```

---

## 关键特性速查

- **PagedAttention**：分页 KV Cache 管理，显存浪费从 60% 降到 <5%，并发能力大幅提升
- **连续批处理**：请求完成即填充新请求，GPU 利用率接近 100%
- **Prefix Caching**：相同系统提示词只计算一次 KV Cache，节省重复 token 计算
- **多种量化支持**：FP8、INT8、GPTQ、AWQ，显存减半不损失太多精度
- **OpenAI 兼容 API**：一行代码切换，现有使用 OpenAI SDK 的代码无需修改

---

**GitHub**：https://github.com/vllm-project/vllm
