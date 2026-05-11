# 🦌 DeerFlow 2.0 — 字节跳动开源 SuperAgent 框架深度解析

> 项目地址：[bytedance/deer-flow](https://github.com/bytedance/deer-flow) · 开源协议：MIT · 2026-02-28 登顶 GitHub Trending #1

---

## 项目概述

DeerFlow（**D**eep **E**xploration and **E**fficient **R**esearch **Flow**）是字节跳动开源的 **Super Agent Harness**——一个可编排 Sub-Agent、长期记忆和沙箱执行的全能 Agent 运行时。

不同于市面上多数 Agent 框架只是 LLM 的工具封装，DeerFlow 的定位更接近"Agent 的操作系统"：它为 Agent 提供了**文件系统、内存、技能市场、沙箱环境和 Sub-Agent 调度**，开箱即用。

**2026年2月的 2.0 版本是一次完整重写**，与 v1（Deep Research 框架）没有代码重叠，底层从 LangGraph + LangChain 重新构建。

---

## 核心架构

```
┌─────────────────────────────────────────────┐
│                  DeerFlow Gateway           │
│  (HTTP / LangGraph-compatible API / nginx)  │
└────────────┬────────────────────────────────┘
             │
    ┌────────▼────────┐
    │   Lead Agent    │  ← 主 Agent，负责规划和调度
    │  (LangGraph)    │
    └────────┬────────┘
    ┌────────▼────────────────────────────────┐
    │            Sub-Agent Pool               │
    │  ┌──────────┐ ┌──────────┐ ┌──────────┐│
    │  │Research  │ │ Coder    │ │ Creator  ││
    │  │  Agent   │ │  Agent   │ │  Agent   ││
    │  └──────────┘ └──────────┘ └──────────┘│
    └─────────────────────────────────────────┘
             │
    ┌────────▼────────────────────────────────┐
    │           Execution Layer               │
    │  Skills │ Sandbox │ Memory │ MCP Tools  │
    └─────────────────────────────────────────┘
```

---

## 五大核心能力

### 1. Skills 技能市场

Skills 是 DeerFlow 的扩展单元——**本质是 Markdown 文件**，定义工作流、最佳实践和工具引用。

```
/mnt/skills/
├── public/
│   ├── research/SKILL.md
│   ├── report-generation/SKILL.md
│   ├── slide-creation/SKILL.md
│   ├── web-page/SKILL.md
│   └── claude-to-deerflow/SKILL.md   ← Claude Code 集成
└── custom/
    └── your-custom-skill/SKILL.md    ← 自定义技能
```

**渐进式加载**：Skills 按需加载，不占用上下文窗口，对 token 敏感模型友好。

### 2. Sub-Agent 并发调度

Lead Agent 可以动态 spawn Sub-Agent，每个 Sub-Agent 拥有：
- **隔离的上下文**（不可见主 Agent 的历史）
- **独立的工具集和终止条件**
- 并行执行，结果汇聚

典型场景：一个研究任务可以拆分为 10+ 个 Sub-Agent 并行探索不同角度，最终由 Lead Agent 合并成报告/网站/幻灯片。

### 3. 沙箱执行环境

三种沙箱模式：

| 模式 | 描述 | 安全性 |
|------|------|--------|
| Local Execution | 直接在宿主机执行 | ⚠️ 仅可信环境 |
| Docker Execution | 隔离容器执行 | ✅ 推荐 |
| K8s Provisioner | 通过 provisioner 在 Pod 执行 | ✅ 生产级 |

文件系统映射：
```
/mnt/user-data/
├── uploads/    ← 用户上传文件
├── workspace/  ← Agent 工作目录
└── outputs/    ← 最终产出物
```

### 4. 长期记忆

跨 Session 的持久化记忆，存储用户画像、偏好和知识积累：
- 本地存储，用户完全掌控
- **去重机制**：重复事实不会无限累积
- 记忆在 Settings > Memory 可视化管理

### 5. IM 频道集成

支持 6 大即时通讯平台，无需公网 IP：

| 平台 | 接入方式 |
|------|----------|
| Telegram | Bot API 长轮询 |
| Slack | Socket Mode |
| 飞书/Lark | WebSocket |
| 微信 | iLink 长轮询 |
| 企业微信 | WebSocket |
| 钉钉 | Stream 推送 |

---

## 与 Claude Code 集成

```bash
# 一键安装 Claude Code Skill
npx skills add https://github.com/bytedance/deer-flow --skill claude-to-deerflow
```

安装后在 Claude Code 中使用 `/claude-to-deerflow` 命令可以：
- 向 DeerFlow 发送研究任务并获取流式响应
- 选择执行模式：flash / standard / pro（规划）/ ultra（Sub-Agent）
- 管理会话历史和文件上传

---

## 模型支持策略

DeerFlow 模型无关（OpenAI 兼容接口），但推荐具备：

- **长上下文**（100k+ tokens）：深度研究必须
- **推理能力**：规划和分解复杂任务
- **多模态**：图像/视频理解
- **强工具调用**：可靠的 function calling

官方推荐模型：Doubao-Seed-2.0-Code、DeepSeek v3.2、Kimi 2.5

自定义模型配置示例：
```yaml
models:
  - name: claude-sonnet-4.6
    use: deerflow.models.claude_provider:ClaudeChatModel
    model: claude-sonnet-4-6
    max_tokens: 4096
    supports_thinking: true
```

---

## 快速上手

```bash
# 克隆 & 交互式配置向导（~2分钟）
git clone https://github.com/bytedance/deer-flow.git
cd deer-flow
make setup

# Docker 启动（推荐）
make docker-init  # 仅首次执行
make docker-start

# 访问 Web UI
open http://localhost:2026
```

---

## 推荐在线架构场景

对于**推荐系统工程师**，DeerFlow 的实际应用价值：

1. **自动化实验报告生成**：接入离线指标系统，定期 spawn 多个 Sub-Agent 并行分析不同实验组，汇总生成对比报告。

2. **架构文档自动维护**：Agent 定期扫描代码变更，更新技术文档，比人工维护成本低 10x。

3. **线上问题快速定位**：配置告警接入 IM 频道，DeerFlow 自动拉取日志、检索历史 incident，生成初步分析报告推送到值班群。

---

## 技术亮点总结

| 特性 | 实现细节 |
|------|----------|
| Context Engineering | Sub-Agent 隔离上下文 + 主动摘要 + 中间结果落盘 |
| Tool-Call Recovery | 强制停止时剥离悬挂 tool_call，注入占位符结果 |
| 内存去重 | apply 时检测重复事实，防止无限累积 |
| 网关安全 | 生成 HTML/SVG artifact 强制 attachment 下载，防 XSS |
| 模型切换 | 无缝支持 Codex CLI / Claude Code OAuth / vLLM / OpenRouter |

---

*生成时间：2026-05-11 · 系列：AI 工具深度研究 · 项目：bytedance/deer-flow*
