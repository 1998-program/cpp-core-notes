# 11 · AgentScope — 面向 Agent 时代的生产级框架

**项目**：[agentscope-ai/agentscope](https://github.com/agentscope-ai/agentscope)
**Stars**：25.8k ⭐ · Python · Apache-2.0
**定位**：生产就绪的 AI agent 框架，设计哲学是"给越来越聪明的 LLM 提供合适的工具和抽象，而不是用严格的提示词和流程约束它"

---

## 根本问题

大多数 AI agent 框架（LangChain、AutoGen 等）设计于 GPT-3.5 时代，核心思路是"通过精心设计的 prompt chain 和状态机强行编排 LLM 的行为"——但 GPT-4、Claude 3、Qwen3 这代模型已经聪明到不需要被如此精细地控制，反而被繁琐的框架约束降低了效能。AgentScope 2.0 的设计思路反过来：用最小化的抽象，充分发挥模型的推理和工具调用能力，同时提供企业级的部署、可观测性和 fine-tuning 支持。

---

## 核心工作原理

### 最小化抽象设计

```python
from agentscope.agents import ReActAgent
from agentscope.tools import tools_from_mcp

# 5 分钟内构建一个可用的 Agent
# AgentScope 不强迫你定义复杂的状态机或 prompt template
agent = ReActAgent(
    model="claude-sonnet-4-5",
    system_prompt="你是一个代码审查助手",
    tools=tools_from_mcp(["github-mcp-server", "filesystem"]),
    memory_config={"type": "rolling", "max_tokens": 4096}
)

response = await agent.run("审查 PR #47 并给出改进建议")
```

### 灵活的 Multi-Agent 编排（Message Hub）

```python
# 不是硬编码的 DAG，而是基于消息的松耦合
from agentscope.message_hub import MessageHub

hub = MessageHub()

# 定义 Agent 订阅关系
code_agent.subscribe(hub, topics=["review_request"])
test_agent.subscribe(hub, topics=["code_changed"])
doc_agent.subscribe(hub, topics=["code_changed", "review_done"])

# 发布消息，Agent 自主决定是否响应
hub.publish("review_request", {"pr_id": 47})
# code_agent 收到 → 分析 PR → 发布 "code_changed"
# test_agent 收到 → 生成测试建议
# doc_agent 收到 → 更新文档建议
```

### 内置 Fine-tuning 支持

```python
# AgentScope 的独特功能：直接从 agent 交互中收集训练数据
from agentscope.training import TrajectoryCollector

collector = TrajectoryCollector(agent)
# agent 正常工作，同时记录所有工具调用轨迹

# 导出训练数据
trajectories = collector.export()
# 用于 fine-tuning 下一代模型，形成闭环
```

---

## 安装 / 快速上手

```bash
# 基础安装
pip install agentscope

# 含所有扩展
pip install 'agentscope[full]'

# 快速启动 ReAct Agent
python -c "
from agentscope.agents import ReActAgent
agent = ReActAgent(model='gpt-4o', system_prompt='You are a helpful assistant')
print(agent.run('Hello'))
"
```

---

## 实践案例

**场景**：构建一个代码质量保障 multi-agent 系统，自动处理 GitHub PR：一个 agent 负责代码审查，一个负责检查测试覆盖，一个负责更新文档，三者并行，最后一个 agent 汇总结果并发布 PR 评论。

**代码**：

```python
import agentscope
from agentscope.agents import ReActAgent, ConversableAgent
from agentscope.message_hub import MessageHub
from agentscope.tools import tools_from_mcp

agentscope.init(model_configs=[{
    "model_type": "openai_chat",
    "model_name": "claude-sonnet-4-5",
    "api_key": "YOUR_API_KEY"
}])

hub = MessageHub()
github_tools = tools_from_mcp(["github-mcp-server"])

# Agent 1：代码审查
code_reviewer = ReActAgent(
    name="code_reviewer",
    model="claude-sonnet-4-5",
    system_prompt="""审查代码质量：
    - 检查 absl:: 容器使用（热路径）
    - 检查错误处理模式
    - 检查内存分配""",
    tools=github_tools
)

# Agent 2：测试覆盖检查
test_checker = ReActAgent(
    name="test_checker", 
    model="claude-sonnet-4-5",
    system_prompt="检查新代码是否有对应的单元测试",
    tools=github_tools
)

# Agent 3：文档检查
doc_checker = ReActAgent(
    name="doc_checker",
    model="claude-sonnet-4-5", 
    system_prompt="检查代码变更是否需要更新文档",
    tools=github_tools
)

# Agent 4：汇总并发布结果
summarizer = ConversableAgent(
    name="summarizer",
    model="claude-sonnet-4-5",
    system_prompt="汇总三个 agent 的结果，生成结构化的 PR review 评论"
)

# 编排：并行执行三个检查，再汇总
async def review_pr(pr_id: int):
    import asyncio
    
    # 并行执行
    results = await asyncio.gather(
        code_reviewer.run(f"审查 PR #{pr_id} 的代码质量"),
        test_checker.run(f"检查 PR #{pr_id} 的测试覆盖"),
        doc_checker.run(f"检查 PR #{pr_id} 的文档更新需求")
    )
    
    # 汇总
    summary = await summarizer.run(
        f"汇总以下三个检查结果并生成 PR 评论：\n"
        f"代码审查：{results[0]}\n"
        f"测试覆盖：{results[1]}\n"
        f"文档检查：{results[2]}"
    )
    
    # 发布到 GitHub
    await github_tools.add_pr_comment(pr_id, summary)
    return summary

import asyncio
asyncio.run(review_pr(47))
```

**示例输出**（发布到 PR 的评论）：

```
## 🤖 AI Code Review Summary

### Code Quality ✅
- Hot path in feature.cpp correctly uses absl::flat_hash_map
- Error handling follows Status return pattern
- ⚠️ Line 89: std::unordered_map found in process_request() (moderate heat)

### Test Coverage ⚠️  
- New function process_v2() has no corresponding test
- Existing tests cover 87% of changed lines

### Documentation 📝
- README.md may need updating for new API parameters
- Proto file changes are documented

**Overall: Approve with minor fixes**
```

---

## 关键特性速查

- **极简 API**：5 分钟构建可用 agent，不需要学习复杂的框架概念
- **内置 MCP 支持**：`tools_from_mcp()` 一行代码接入任意 MCP Server
- **Message Hub**：松耦合的 multi-agent 编排，不需要硬编码 DAG
- **Fine-tuning 管道**：内置轨迹收集，从 agent 工作中收集训练数据
- **生产就绪**：内置 OTel 可观测性、K8s/serverless 部署支持、A2A 协议

---

**GitHub**：https://github.com/agentscope-ai/agentscope
