# GPT Researcher · 首个开源深度研究 Agent

**项目**：[assafelovic/gpt-researcher](https://github.com/assafelovic/gpt-researcher)  
**Stars**：27k+ ⭐ · Python · MIT License  
**作者**：Assaf Elovic（独立开发者）  
**定位**：首个专为"深度研究"设计的自主 Agent，可同时对互联网和本地文档进行多源并行调研，输出带引用的长篇研究报告

---

## 根本问题

人工研究一个复杂主题往往需要数天甚至数周：搜索→阅读→交叉核实→综合归纳，每一步都是重复劳动且容易产生信息偏差。LLM 虽然可以生成文字，但存在三个根本缺陷：

1. **训练数据截止**：对最新动态一无所知；
2. **Token 上限**：单次 context 容纳不下一份完整报告所需的所有原始资料；
3. **单一信源偏差**：依赖少量搜索结果，结论容易被特定视角污染。

GPT Researcher 的核心价值：**用 Planner + Executor 多 Agent 协作，并行抓取 20+ 信源，绕开 token 限制，输出 2000+ 字带引用的综合报告**。

---

## 核心工作原理

### 整体架构（Plan-and-Solve 范式）

```
用户查询
    │
    ▼
┌──────────────┐
│  Planner Agent│  ← SMART_LLM（强模型）
│  生成子问题列表 │     分解成 5-10 个子研究问题
└──────┬───────┘
       │ 并行分发
       ▼
┌──────────────┐  ×N（每个子问题一个执行器）
│ Executor Agent│  ← FAST_LLM（快模型）
│  搜索 + 摘要  │     每个 Agent 独立爬取、摘要、追踪来源
└──────┬───────┘
       │ 合并所有摘要
       ▼
┌──────────────┐
│ Publisher Agent│  ← SMART_LLM
│  聚合 → 报告  │     跨信源去重、综合、生成最终报告
└──────────────┘
```

### 三类研究模式

| 模式 | 适用场景 | 子 Agent 数 | 深度 |
|------|----------|-------------|------|
| `fast` | 快速摘要 | 1 | 浅 |
| `deep` | 深度研究 | 动态扩展 | 递归深挖 |
| `research` | 标准研究 | 5-10 | 中 |

`deep` 模式最具代表性：Planner 递归生成子查询树，每个叶节点问题都触发独立的网络搜索和摘要，最终汇总成 multilayer research report。

### 检索器支持

```python
# 混合信源：同时用网络搜索 + MCP 数据源
os.environ["RETRIEVER"] = "tavily,mcp"  

researcher = GPTResearcher(
    query="分析 2026 年 AI Agent 框架竞争格局",
    mcp_configs=[{
        "name": "github",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."}
    }]
)
```

支持的检索后端：`tavily`、`bing`、`google`、`duckduckgo`、`arxiv`、本地文档（`./my-docs/`）、MCP Server。

---

## 真实用户场景（来自 issue #1004）

用户：使用 Azure OpenAI，想基于本地文档库做研究（配置了 `./my-docs/` 目录），但发现 GPT Researcher 忽略了本地文档，仍然在调用搜索引擎。

问题根因：`.env` 中 `RETRIEVER` 未显式设为 `local`，默认 fallback 到网络搜索。修复方式：

```bash
# .env
RETRIEVER=local  # 只用本地文档
# 或混合：
RETRIEVER=tavily,local  # 本地 + 网络并行
```

```python
from gpt_researcher import GPTResearcher

researcher = GPTResearcher(
    query="我们产品的核心竞争力是什么？",
    report_type="research_report",
    config_path="./my_config.json"  # 指向自定义配置
)
```

这个 issue（14条讨论）的核心价值在于：它揭示了 GPT Researcher 最典型的**私有知识库研究**场景——把内部文档、技术规范、历史数据作为信源，让 Agent 自动综合分析，而不依赖互联网。这对需要基于内部资料做技术选型报告、竞品分析的工程师非常实用。

---

## 上手代码示例

### 最简安装

```bash
pip install gpt-researcher
export OPENAI_API_KEY=sk-...
export TAVILY_API_KEY=tvly-...  # 免费注册 tavily.com
```

### 基础研究任务

```python
import asyncio
from gpt_researcher import GPTResearcher

async def main():
    # 标准深度研究
    researcher = GPTResearcher(
        query="2026 年 LLM 推理加速技术对比：vLLM vs SGLang vs TensorRT-LLM",
        report_type="research_report"  # 或 "deep" 模式
    )
    
    # 并行调研（内部会 spawn 多个子 Agent）
    await researcher.conduct_research()
    
    # 生成报告（自动聚合所有信源）
    report = await researcher.write_report()
    print(report)  # Markdown 格式，带引用链接
    
    # 查看使用的信源
    sources = researcher.get_source_urls()
    print(f"调研了 {len(sources)} 个信源")

asyncio.run(main())
```

### 基于本地文档研究

```python
import os
from gpt_researcher import GPTResearcher

## 实践案例

**场景：基于 GitHub Issues + 技术博客自动生成框架选型报告**

**问题背景**：团队需要在 vLLM、SGLang、TensorRT-LLM 三个推理框架中选型，要综合 benchmark、GitHub Issues 真实反馈、近期更新动态，手工整理至少需要半天。

**安装配置**：

```bash
pip install gpt-researcher
```

```
# .env 配置
OPENAI_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
FAST_LLM=openai:gpt-4o-mini
SMART_LLM=openai:gpt-4o
LANGUAGE=chinese
MAX_ITERATIONS=3
```

**核心代码**：

```python
import asyncio
from gpt_researcher import GPTResearcher

async def run():
    researcher = GPTResearcher(
        query=(
            "对比分析 vLLM、SGLang、TensorRT-LLM 三个 LLM 推理框架：\n"
            "1. 真实 benchmark 中的吞吐量和 P99 延迟数据\n"
            "2. GitHub Issues 反映的主要用户痛点（近6个月）\n"
            "3. H100/A100 的优化成熟度\n"
            "4. 生产部署的稳定性和运维复杂度"
        ),
        report_type="deep",
        mcp_configs=[{
            "name": "github",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"}
        }]
    )
    await researcher.conduct_research()
    report = await researcher.write_report()
    with open("inference_report.md", "w") as f:
        f.write(report)
    print(f"调研了 {len(researcher.get_source_urls())} 个信源，报告已保存")

asyncio.run(run())
```

**运行结果**：

```
Planner: 分解为 7 个子研究问题，并行启动 7 个 Executor Agent
  ├── "vLLM vs SGLang throughput benchmark 2026"
  ├── "TensorRT-LLM H100 FP8 quantization performance"
  ├── "vLLM github issues production deployment problems"
  └── ... (共 7 个)

所有 Executor 完成（耗时 48 秒，共抓取 23 个信源）
报告：3,912 字，含 21 处引用链接
```

**GPT Researcher 最独特的地方**：Plan-and-Solve 多 Agent 架构允许同时从 20+ 信源并行抓取信息，绕开单次 LLM context 的 token 限制——这是单次 LLM 调用永远做不到的。RETRIEVER 支持混合信源（`tavily,mcp,local`），可以同时参考网络搜索、GitHub Issues 原文、本地设计文档，3 类信源交叉验证，显著减少信息偏差。


# 把文档放在 ./my-docs/ 目录（支持 .txt .pdf .docx 等）
os.environ["RETRIEVER"] = "local"
os.environ["DOC_PATH"] = "./my-docs"

researcher = GPTResearcher(
    query="根据现有设计文档，推荐服务的内存瓶颈主要集中在哪里？",
    report_type="research_report"
)
```

### Docker 快速启动（含 Web UI）

```bash
git clone https://github.com/assafelovic/gpt-researcher
cd gpt-researcher
cp .env.example .env  # 填入 API Keys
docker-compose up

# 访问 http://localhost:3000 使用 Web 界面
```

---

## 关键配置说明

```bash
# .env 核心配置
FAST_LLM=openai:gpt-4o-mini          # 执行层（快速、并发多）
SMART_LLM=openai:gpt-4o              # 规划/汇总层（质量优先）
STRATEGIC_LLM=openai:o3-mini         # deep 模式专用（推理强）

RETRIEVER=tavily                      # 搜索后端
MAX_ITERATIONS=4                      # deep 模式递归深度
REPORT_FORMAT=markdown                # 输出格式
LANGUAGE=chinese                      # 报告语言
```

支持接入 DeepSeek、Ollama（本地模型）、Azure OpenAI 等，通过 `OPENAI_BASE_URL` 指向自定义端点。

---

**GitHub**：https://github.com/assafelovic/gpt-researcher ⭐ 27k+  
**文档**：https://docs.gptr.dev  
**在线体验**：https://gptr.dev
