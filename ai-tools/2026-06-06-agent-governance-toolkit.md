# Agent Governance Toolkit

**链接**: https://github.com/microsoft/agent-governance-toolkit  
**分类**: AI Agent 安全 / 运行时治理 / 企业合规  
**一句话描述**: 微软开源的 AI Agent 运行时治理框架，用确定性策略拦截 Agent 的每一次工具调用，将 OWASP Agentic Top 10 风险降至零。

## 核心功能
- **策略引擎（Agent OS）**：无状态、亚毫秒级策略评估，拦截每个工具调用，支持 YAML / OPA Rego / Cedar 策略语言
- **零信任身份（Agent Mesh）**：基于 Ed25519 签名的去中心化身份认证，Inter-Agent Trust Protocol，动态信任评分（0-1000）
- **执行沙箱（Agent Runtime）**：类 CPU 特权环的执行分级、Saga 事务编排、紧急终止开关
- **可靠性工程（Agent SRE）**：将 SRE 实践（SLO / 错误预算 / 熔断器 / 混沌工程）引入 Agent 系统
- **合规自动化（Agent Compliance）**：自动生成合规报告，覆盖 EU AI Act / HIPAA / SOC2 / OWASP Top 10
- **插件市场（Agent Marketplace）**：Ed25519 签名的插件生命周期管理，信任分级能力门控
- **RL 训练治理（Agent Lightning）**：强化学习训练工作流的策略执行和奖励塑形

## 技术亮点
- **确定性 vs 概率性**：提示词级安全（"请遵守规则"）红队测试违规率 26.67%，AGT 的应用层策略拦截违规率 0.00%
- **P99 延迟 < 0.1ms**：策略检查对 Agent 执行路径几乎零影响
- **框架无关**：兼容 LangChain、CrewAI、AutoGen、Google ADK、Azure AI、OpenAI Agents SDK 等 20+ 框架
- **七包全栈**：Python / TypeScript / Rust / Go / .NET 五语言支持
- **9500+ 测试**：覆盖全部 10 个 OWASP Agentic AI 风险类别
- **监管合规就绪**：EU AI Act 高风险义务（2026-08 生效）和 Colorado AI Act（2026-06 生效）合规预备
- **v3.3.0 新特性**：贡献者信誉检查（可复用 GitHub Action）、Sentry 集成、策略组合、多阶段流水线

## 适用场景
- **金融 / 医疗 AI Agent**：满足严格监管要求，实现可审计的决策记录
- **多 Agent 编排系统**：防止 Agent 间消息投毒和信任滥用
- **企业 AI 部署**：在不重写业务代码的前提下，为已有 Agent 系统加装安全层
- **AI 代码执行环境**：限制代码执行 Agent 只能访问特定文件和 API
- **合规审计**：自动生成覆盖 OWASP / EU AI Act 的证据包，应对监管审查

## 快速上手

**1. 安装**
```bash
pip install agent-governance-toolkit[full]
agt doctor      # 健康检查
agt verify      # OWASP 合规验证
```

**2. 最简示例：拦截危险工具调用**
```python
from agent_os.policies import (
    PolicyEvaluator, PolicyDocument, PolicyRule,
    PolicyCondition, PolicyAction, PolicyOperator, PolicyDefaults
)

evaluator = PolicyEvaluator(policies=[PolicyDocument(
    name="my-policy",
    version="1.0",
    defaults=PolicyDefaults(action=PolicyAction.ALLOW),
    rules=[PolicyRule(
        name="block-dangerous-tools",
        condition=PolicyCondition(
            field="tool_name",
            operator=PolicyOperator.IN,
            value=["execute_code", "delete_file"]
        ),
        action=PolicyAction.DENY,
        priority=100,
    )]
)])

result = evaluator.evaluate({"tool_name": "web_search"})   # ✅ 允许
result = evaluator.evaluate({"tool_name": "delete_file"})  # ❌ 拦截 + 审计日志
```

**3. 与 LangChain 集成（Hooks 方式，无需改业务代码）**
```python
from agent_governance_toolkit.integrations.langchain import GovernanceCallbackHandler

# 在 LangChain Agent 中注入治理层
agent = initialize_agent(
    ...,
    callbacks=[GovernanceCallbackHandler(policy_document="./policy.yaml")]
)
```

**4. 生成合规报告**
```bash
agt verify-evidence ./agt-evidence.json          # 验证运行时证据
agt verify-evidence ./agt-evidence.json --strict  # CI 中失败即中断
```

> 推荐理由：2026 年 AI Agent 已从聊天窗口走向自主订票、执行交易、管理基础设施。此时"用提示词约束行为"已被证明不可靠——AGT 用操作系统内核的思路（特权环、进程隔离）重新设计了 Agent 的安全边界，是第一个在生产环境中将 OWASP Agentic Top 10 全量覆盖的开源工具包。
