# 05 · LiteLLM — 100+ LLM 的统一调用网关

**项目**：[BerriAI/litellm](https://github.com/BerriAI/litellm)
**Stars**：48.5k ⭐ · Python · NOASSERTION(Apache-2.0)
**定位**：AI Gateway，统一 100+ LLM 提供商的调用接口为 OpenAI 格式，支持负载均衡、成本控制、可观测性

---

## 根本问题

每个 LLM 提供商（OpenAI、Anthropic、Gemini、Azure、百度文心等）都有不同的 SDK 和 API 格式，在代码里直接调用就意味着强绑定——换个模型要改代码，A/B 测试不同模型要写两套逻辑，成本没法统一监控。LiteLLM 做了一件事：把所有 LLM 的调用统一成 OpenAI 格式，调用层代码完全不感知底层用的是哪家模型，换模型只改配置，不改代码。

---

## 核心工作原理

LiteLLM 有两种使用形态：

**形态 1：Python SDK（轻量，进程内调用）**
```python
from litellm import completion

# 所有模型用同一套接口
response = completion(
    model="claude-sonnet-4-5",    # 或 "gpt-4o", "gemini/gemini-2.0-flash"
    messages=[{"role": "user", "content": "你好"}]
)
```

**形态 2：Proxy Server（AI Gateway，团队共享）**
```
应用 A (OpenAI SDK) ──┐
应用 B (OpenAI SDK) ──┤──→ LiteLLM Proxy ──→ Anthropic/OpenAI/Gemini/...
应用 C (OpenAI SDK) ──┘
```

Proxy 的核心能力：

```yaml
# litellm_config.yaml
model_list:
  - model_name: "gpt-4o"          # 对外暴露的名字
    litellm_params:
      model: "claude-sonnet-4-5"  # 实际调用的模型
      api_key: os.environ/ANTHROPIC_KEY
      
  - model_name: "fast-model"      # 路由到最快的模型
    litellm_params:
      model: "gemini/gemini-2.0-flash"
      api_key: os.environ/GEMINI_KEY

router_settings:
  routing_strategy: "least-busy"  # 负载均衡策略
  
general_settings:
  master_key: "sk-team-key"       # 团队统一 key，控制访问权限
```

---

## 安装 / 快速上手

```bash
# SDK 模式
pip install litellm

# Proxy 模式
pip install 'litellm[proxy]'
litellm --model claude-sonnet-4-5  # 启动代理，监听 4000 端口

# Docker 模式（生产推荐）
docker run -e ANTHROPIC_API_KEY=sk-xxx \
  ghcr.io/berriai/litellm:main \
  --model anthropic/claude-sonnet-4-5 --port 4000
```

---

## 实践案例

**场景**：团队有 5 个 AI 应用（代码生成、文档摘要、数据分析、客服 bot、推荐解释），每个应用原本各自维护一个 API key，各自调不同的模型，成本没法统一管控，某个应用疯狂调用把月度预算超掉了也不知道。现在用 LiteLLM Proxy 统一管控。

**配置**：

```yaml
# litellm_config.yaml
model_list:
  # 复杂任务用高智能模型
  - model_name: "smart"
    litellm_params:
      model: "claude-sonnet-4-5"
      api_key: os.environ/ANTHROPIC_KEY
      
  # 简单任务用快速便宜的模型
  - model_name: "fast"
    litellm_params:
      model: "gemini/gemini-2.0-flash"
      api_key: os.environ/GEMINI_KEY

litellm_settings:
  success_callback: ["langfuse"]  # 所有调用记录到 Langfuse

general_settings:
  master_key: "sk-master-only-admin-knows"
```

**各应用申请虚拟 key，附带预算限制**：

```bash
# 给代码生成应用分配 key，月度限额 $50
curl -X POST http://litellm-proxy:4000/key/generate \
  -H "Authorization: Bearer sk-master-only-admin-knows" \
  -d '{"models": ["smart"], "budget": 50, "metadata": {"team": "code-gen"}}'
# 返回：{"key": "sk-app-codegen-xxxx", "budget": 50}

# 给客服 bot 分配 key，只能用 fast 模型，限额 $10
curl -X POST http://litellm-proxy:4000/key/generate \
  -d '{"models": ["fast"], "budget": 10, "metadata": {"team": "customer-service"}}'
```

**应用代码完全不变**，只需把 `OPENAI_BASE_URL` 指向代理：

```python
import openai
# 原来：client = openai.OpenAI(api_key="sk-openai-key")
# 现在：
client = openai.OpenAI(
    base_url="http://litellm-proxy:4000",
    api_key="sk-app-codegen-xxxx"  # 应用自己的虚拟 key
)
response = client.chat.completions.create(
    model="smart",  # 路由到 Claude Sonnet，但代码不知道
    messages=[{"role": "user", "content": "..."}]
)
```

**成本超限自动报错**：

```
openai.BadRequestError: Budget exceeded for key sk-app-codegen-xxxx. 
Spent: $50.23, Budget: $50.00
```

---

## 关键特性速查

- **100+ 提供商**：OpenAI、Anthropic、Gemini、Azure、Bedrock、文心、通义等，统一格式调用
- **虚拟 Key 管理**：每个 key 可绑定模型白名单 + 预算上限，团队成本一目了然
- **负载均衡**：多个同类模型轮询/最小延迟路由，单个 key 超额自动 fallback
- **可观测性**：原生集成 Langfuse、Prometheus、Datadog，所有调用有 token 用量、延迟、成本记录
- **兼容 OpenAI SDK**：只改 `base_url` 和 `api_key`，现有代码零改动

---

**GitHub**：https://github.com/BerriAI/litellm
