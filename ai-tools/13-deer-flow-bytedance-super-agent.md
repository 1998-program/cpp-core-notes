# 13 · DeerFlow — 字节跳动开源的超级 Agent 框架

**项目**：[bytedance/deer-flow](https://github.com/bytedance/deer-flow)
**Stars**：69.8k ⭐ · Python · MIT
**定位**：字节跳动开源的 super agent harness，通过编排 sub-agents、记忆和沙箱，结合可扩展 skill 系统，执行研究、编码、数据分析等长链任务

---

## 根本问题

单一 LLM 调用能处理的任务有限，复杂任务（深度研究、多步数据分析、跨工具工作流）需要多轮调用、工具组合和中间状态管理。DeerFlow 的设计目标是让一个 agent 能完成"几个小时的人类工作"——给它一个研究任务，它会自主分解、并行搜索、综合信息、生成报告，整个过程不需要人工干预。2.0 版本是完全重写，引入了 sub-agent 并行、沙箱代码执行、skill 系统等核心能力。

---

## 核心工作原理

### 任务分解与 Sub-Agent 编排

```python
# DeerFlow 2.0 的核心设计：Orchestrator → Sub-agents
任务："分析 2026 年 AI agent 框架的技术趋势，生成研究报告"

Orchestrator 分解：
├── ResearchAgent #1: 搜索 GitHub 活跃度数据（LangChain/AutoGen/AgentScope）
├── ResearchAgent #2: 搜索学术论文（arxiv 近半年）  
├── ResearchAgent #3: 分析 HN/Reddit 社区讨论热点
└── AnalysisAgent: 综合三者结果，生成结构化报告

# 三个 Research Agent 并行执行，Analysis Agent 等待结果后汇总
```

### Skill 系统（可扩展工具包）

```python
# DeerFlow 的 skill 是可复用的工具模块
from deer_flow import skill

@skill(name="web_search", description="Search the web for information")
async def web_search(query: str, max_results: int = 10) -> list[SearchResult]:
    # 底层调用 InfoQuest / Tavily / Brave Search
    ...

@skill(name="code_executor", description="Execute Python code in sandbox")  
async def execute_code(code: str) -> CodeResult:
    # 在隔离沙箱执行，返回 stdout/stderr/plots
    ...

# Agent 自主选择调用哪个 skill
agent = DeerFlowAgent(
    skills=[web_search, execute_code, "mcp://github-mcp-server"],
    memory=VectorMemory(),
    sandbox=DockerSandbox()
)
```

### 记忆系统

```python
# 三层记忆架构
agent.memory = {
    "working": WorkingMemory(max_tokens=4096),    # 当前任务上下文
    "episodic": EpisodicMemory(backend="sqlite"),  # 历史任务记录
    "semantic": VectorMemory(backend="chromadb")   # 知识库检索
}
```

---

## 安装 / 快速上手

```bash
# 克隆
git clone https://github.com/bytedance/deer-flow
cd deer-flow

# 安装（Python 3.12+）
pip install -e .

# 配置（.env 文件）
LLM_API_KEY=your_key
LLM_MODEL=doubao-seed-2.0-code  # 推荐：字节 Doubao / DeepSeek / Kimi

# 启动（Web UI）
make dev

# 访问
# http://localhost:3000
```

---

## 实践案例

**场景**：你需要了解当前 LLM 推理框架的性能对比（vLLM vs TGI vs TensorRT-LLM），包括吞吐量数据、社区活跃度、适用场景，并生成一份可以直接发给团队的分析报告。以前这需要花 3-4 小时手动搜索和整理，现在交给 DeerFlow。

**操作**（Web UI 或 API）：

```python
from deer_flow import DeerFlowAgent, Task

agent = DeerFlowAgent(model="deepseek-v3.1")

result = await agent.run(Task(
    goal="分析 vLLM、TGI（Text Generation Inference）和 TensorRT-LLM 的性能对比",
    requirements=[
        "包含吞吐量基准数据（tokens/s）",
        "分析各框架的适用场景",
        "查看 GitHub stars 趋势和社区活跃度",
        "查阅 2026 年的最新 benchmark 报告",
        "输出一份 Markdown 格式的分析报告"
    ]
))
```

**DeerFlow 内部执行流程**：

```
Orchestrator 分解任务：
├── ResearchAgent #1 (并行)
│   → web_search("vLLM vs TGI vs TensorRT-LLM benchmark 2026")
│   → web_search("vllm throughput benchmark A100")
│   → 搜索 arxiv 近期推理优化论文
│
├── ResearchAgent #2 (并行)
│   → github_mcp.get_repo_stats("vllm-project/vllm")
│   → github_mcp.get_repo_stats("huggingface/text-generation-inference")
│   → github_mcp.get_repo_stats("NVIDIA/TensorRT-LLM")
│   → 分析 star 增长趋势、issue 活跃度
│
├── DataAgent (并行，等待 #1 结果)
│   → execute_code("""
│       import pandas as pd
│       # 整理搜索到的 benchmark 数据
│       data = {...}  # 从 Research #1 获取
│       df = pd.DataFrame(data)
│       # 生成对比图表
│       """)
│
└── ReportAgent (最后汇总)
    → 综合三个 agent 的结果
    → 生成结构化 Markdown 报告
```

**生成的报告摘要**：

```markdown
# LLM 推理框架对比分析 (2026)

## 性能基准（A100 80G，Llama-3-70B）
| 框架 | 吞吐量 (tokens/s) | 延迟 P99 | 显存效率 |
|------|-----------------|----------|---------|
| vLLM | 8,340 | 1.7s | ⭐⭐⭐⭐⭐ |
| TGI | 6,120 | 2.3s | ⭐⭐⭐⭐ |
| TensorRT-LLM | 12,500 | 0.9s | ⭐⭐⭐⭐⭐ |

## 适用场景建议
- **vLLM**：开源模型，快速原型，社区支持好
- **TensorRT-LLM**：NVIDIA GPU，追求极致性能
- **TGI**：Hugging Face 生态，与 transformers 深度集成

## GitHub 活跃度（近 90 天）
vLLM: 2,340 commits | TGI: 1,210 commits | TRT-LLM: 890 commits
```

整个过程约 8-12 分钟，无需人工干预。

---

## 关键特性速查

- **Sub-agent 并行**：复杂任务自动分解并并行执行，时间从小时压缩到分钟
- **沙箱代码执行**：安全执行 Python 代码，生成图表和数据分析
- **InfoQuest 集成**：字节内部开发的搜索工具，支持深度爬取（非单一 Google）
- **Skill 扩展系统**：标准化接口，MCP Server 可直接作为 skill 接入
- **Web UI + API**：既有可视化界面，也有编程接口，适合不同使用场景

---

**GitHub**：https://github.com/bytedance/deer-flow
