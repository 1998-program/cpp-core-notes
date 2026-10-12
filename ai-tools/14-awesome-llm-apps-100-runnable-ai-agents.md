# 14 · Awesome LLM Apps — 100+ 可运行的 AI Agent 实例

**项目**：[Shubhamsaboo/awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps)
**Stars**：111.9k ⭐ · Python · Apache-2.0
**定位**：100+ 个完整可运行的 AI Agent 和 RAG 应用代码库，覆盖单 agent、multi-agent、MCP agent、Voice agent 等所有主流模式，配套详细教程

---

## 根本问题

学 AI agent 开发最大的障碍是：教程都是简化的 demo，真实应用需要自己从零摸索架构选型、工具集成、错误处理。这个项目直接提供 100+ 个生产质量的完整 AI agent 实现，每个都有完整代码 + 教程 + 可以直接 clone 运行的环境。它覆盖的场景极其广泛——从"GitHub issue 自动修复 agent"到"语音 AI 助手"，从"多模态医疗问诊"到"金融分析 agent"。

---

## 核心工作原理

项目按 agent 类型分类组织：

```
awesome-llm-apps/
├── ai_agent_tutorials/        # 单 agent 教程（最基础）
│   ├── github_issue_fixer/   # GitHub issue 自动修复
│   ├── coding_agent/         # 代码生成 agent
│   └── web_scraping_agent/   # 网络爬取 agent
│
├── multi_ai_agent_tutorials/  # Multi-agent 协作
│   ├── investment_agent_team/ # 多 agent 投资分析
│   └── code_review_team/     # 代码审查团队
│
├── mcp_ai_agent_tutorials/    # MCP-based agents
│   ├── github_mcp_agent/     # GitHub MCP 自动化
│   └── filesystem_agent/     # 文件系统操作 agent
│
├── voice_ai_agent_tutorials/  # 语音 agent
│   └── voice_assistant/      # 实时语音交互
│
└── rag_tutorials/            # RAG 应用
    ├── local_rag/            # 本地文档 RAG
    └── multimodal_rag/       # 多模态 RAG
```

每个目录包含：
- `README.md`：详细教程和架构说明
- `agent.py`（或类似）：完整可运行代码
- `requirements.txt`：依赖列表
- 配套 Unwind AI 上的详细文章

---

## 安装 / 快速上手

```bash
# 克隆整个项目
git clone https://github.com/Shubhamsaboo/awesome-llm-apps

# 运行一个具体的 agent（以 GitHub issue fixer 为例）
cd ai_agent_tutorials/github_issue_fixer

pip install -r requirements.txt

# 配置环境变量
export ANTHROPIC_API_KEY=your_key
export GITHUB_TOKEN=ghp_your_token

python agent.py
```

---

## 实践案例

**场景**：你想学习如何构建一个"代码审查 multi-agent 系统"，但不知道从哪里开始，不确定应该用 LangGraph 还是 AutoGen，不确定 agent 之间怎么通信。直接找这个项目里的对应实现，clone 下来跑一遍，读懂代码，然后改成自己需要的版本。

**找到对应实现**：

```bash
# 在项目里搜索
ls multi_ai_agent_tutorials/
# → code_review_team/  investment_agent_team/  ...

cd multi_ai_agent_tutorials/code_review_team
cat README.md  # 查看架构说明
```

**代码审查 Multi-Agent 系统的核心结构**（项目实际代码模式）：

```python
from agno.agent import Agent
from agno.models.anthropic import Claude
from agno.tools.github import GithubTools

# Agent 1：代码审查员
code_reviewer = Agent(
    name="Code Reviewer",
    role="Review code for quality issues",
    model=Claude(id="claude-sonnet-4-5"),
    tools=[GithubTools()],
    instructions=[
        "Analyze the code for potential bugs",
        "Check for performance issues",
        "Identify security vulnerabilities",
    ]
)

# Agent 2：测试建议员
test_advisor = Agent(
    name="Test Advisor", 
    role="Suggest tests for changed code",
    model=Claude(id="claude-sonnet-4-5"),
    tools=[GithubTools()],
    instructions=[
        "Analyze what tests are needed",
        "Generate test cases for edge cases",
    ]
)

# Multi-Agent Team
from agno.team import Team

review_team = Team(
    name="Code Review Team",
    mode="coordinate",  # coordinator 决定谁先做什么
    model=Claude(id="claude-sonnet-4-5"),  # coordinator 用的模型
    members=[code_reviewer, test_advisor],
    instructions=[
        "First have Code Reviewer analyze the PR",
        "Then have Test Advisor suggest tests",
        "Synthesize results into a comprehensive review"
    ]
)

# 使用
result = review_team.run(
    "Review the changes in PR #47 of repo 1998-program/cpp-core-notes"
)
print(result.content)
```

**示例输出**：

```
## Code Review Team Analysis for PR #47

### Code Reviewer's Findings:
✅ absl::flat_hash_map correctly used in hot path
⚠️ Missing error handling in line 89: external API call may throw
❌ Memory leak risk: raw pointer at line 134 (use std::unique_ptr)

### Test Advisor's Recommendations:
- Add test for error case when external API returns 500
- Add test for boundary: empty candidate list
- Existing tests cover 78% of changes (target: 85%)

### Summary:
2 issues require fixing before merge. Estimated fix time: 30 minutes.
```

这个实现大约 60 行代码，比从零写一个能工作的 multi-agent 系统节省几天时间。

---

## 关键特性速查

- **100+ 完整实现**：每个 agent 都有完整可运行代码，不是伪代码或截图
- **覆盖所有主流模式**：单 agent、multi-agent、MCP agent、Voice agent、RAG，全覆盖
- **多框架示例**：包含 Agno、LangGraph、AutoGen、CrewAI 的实现，便于框架选型对比
- **配套教程**：每个实现都有对应的 Unwind AI 文章，讲清楚设计决策
- **支持所有主流模型**：Claude、GPT-4、Gemini、Qwen、Llama 均有对应示例

---

**GitHub**：https://github.com/Shubhamsaboo/awesome-llm-apps
