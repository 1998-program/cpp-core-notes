# Context-Mode - AI Coding Agent 上下文优化 MCP 服务器

**作者**: mksglu  
**仓库**: https://github.com/mksglu/context-mode  
**协议**: ELv2  
**推荐日期**: 2026-06-29  
**GitHub Stars**: 15,000+  
**社区**: 24.3 万开发者接入，被微软/谷歌/Meta/字节/Cursor 等团队采用

---

## 一句话总结

**Context-Mode 是 AI Coding Agent 的上下文优化 MCP 中间件——通过虚拟沙盒、存档点和"用代码思考"范式，将 AI 编程的 Token 成本降低 98%，同时把大模型的记忆续航从 30 分钟延长至 3 小时。**

---

## 核心痛点：另一半上下文问题

开发者在使用 Claude Code / Cursor 等 AI 编程助手时，遇到的核心问题不是"模型不够聪明"，而是**上下文被浪费**：

| 浪费场景 | 具体表现 | 后果 |
|---------|---------|------|
| 工具输出膨胀 | Playwright snapshot = 56 KB，20 个 GitHub issues = 59 KB | 窗口迅速被占满 |
| 信息过载 | 让模型"读取 50 个文件来数函数数量" | 上下文 700 KB → 3.6 KB 本该如此 |
| 会话失忆 | 30 分钟后压缩对话，模型忘记正在编辑的文件 | 任务必须从头再来 |
| 输出膨胀 | 模型输出填充词、客套话、冗余解释 | 上下文两头燃烧 |

> 创始人 Mert Köseoğlu 的洞察：**"停止将大模型视为'数据处理器'，它本质上是'代码生成器'。"**

---

## 四大核心机制

### 1. 上下文节省（Context Saving）—— 虚拟沙盒

传统模式：MCP 工具调用 → 原始数据 → 直接倾倒进上下文窗口 → 模型逐行阅读。

Context-Mode 的沙盒机制：
- 工具调用被路由到 sandbox 工具（`ctx_execute`、`ctx_batch_execute`）
- 原始数据在隔离进程中处理，**不进入上下文窗口**
- 模型只收到压缩后的结果、摘要、计数或命中文件列表
- 实测：79.3 KB 文件读取 → Token 消耗降低 **87.7%**

```javascript
// 传统方式：47 × Read() = 700 KB 上下文
// Context-Mode：1 × ctx_execute() = 3.6 KB 结果
ctx_execute("javascript", `
  const files = fs.readdirSync('src').filter(f => f.endsWith('.ts'));
  files.forEach(f => console.log(f + ': ' + 
    fs.readFileSync('src/'+f,'utf8').split('\\n').length + ' lines'));
`);
```

### 2. 会话连续性（Session Continuity）—— 存档点机制

AI 在压缩对话后会遗忘关键信息。Context-Mode 的解法：
- 每次文件编辑、git 操作、任务、错误、用户决策都被追踪到本地 SQLite
- 压缩前，不把所有历史塞回上下文，而是用 **FTS5 + BM25 检索**只取相关事件
- 注入一个通常小于 2KB 的"快照"，模型从断点无缝恢复
- 实测：连续编程时间从 **30 分钟 → 3 小时**

> 孙逸诚（核心开发者）的比喻："传统 AI 编程像看马拉松，盯着每个选手每一步；Context-Mode 把过程扔进沙盒，只看最终排名。"

### 3. 用代码思考（Think in Code）—— 范式革命

核心主张：**让 LLM 编写脚本来处理数据，而非在上下文中做"人肉数据处理"**。

```
任务：统计 src/ 目录下所有 .ts 文件的行数

错误做法：Read 50 个文件 → 全部塞进上下文 → 让模型数
正确做法：模型写一个脚本 → 脚本本地运行 → 只返回结果列表
```

一个脚本替代 10+ 次工具调用，节省 **100 倍上下文**。

### 4. 零输出干预（No Prose Enforcement）

Context-Mode 只做数据路由，**从不干预模型如何写最终答案**。
- 不强制模型缩写输出（已证明会降损编程推理能力）
- 简短、完整、格式——由模型自主决定
- 只控制"数据流向哪里"，不控制"模型怎么说话"

---

## 平台适配：17 个主流开发环境

| 平台 | 安装方式 | 路由机制 |
|------|---------|---------|
| Claude Code | `/plugin install` | 自动（SessionStart + PreToolUse Hook） |
| Gemini CLI | `npm i -g` + `~/.gemini/settings.json` | 自动（4 个 Hook） |
| VS Code Copilot | `.vscode/mcp.json` + `.github/hooks/` | 自动（SessionStart Hook） |
| JetBrains Copilot | Settings UI + `hooks.json` | 自动 |
| GitHub Copilot CLI | `copilot plugin install` | 自动（6 个 Hook） |
| Cursor | 工作进行中 | 自动（含 stop 支持） |
| OpenClaw | 原生集成 | 网关级路由 |

## 11 个 MCP 工具

**6 个沙盒工具：**
- `ctx_execute` - 在隔离进程中运行代码，返回结果
- `ctx_batch_execute` - 批量执行多个脚本
- `ctx_execute_file` - 执行本地脚本文件
- `ctx_index` - 索引文件/目录到 FTS5 知识库
- `ctx_search` - 从 FTS5 检索相关内容
- `ctx_fetch_and_index` - 抓取并索引远程内容

**5 个元工具：**
- `ctx_stats` - 实时查看上下文节省统计
- `ctx_doctor` - 诊断运行状态、Hook、FTS5
- `ctx_upgrade` - 一键更新、迁移缓存
- `ctx_purge` - 清空索引知识库
- `ctx_insight` - 打开企业级分析面板（定向内测）

---

## 企业级扩展：Context-Mode Insights

针对企业研发场景推出的"上下文即服务"：
- 收集团队 AI 使用数据（工具调用、报错、Token 消耗）
- 按岗位定制报告：安全总监 → 安全报告 / 财务团队 → Token 消耗明细
- 衡量 AI 辅助编程的 ROI

---

## 团队与社区

- **Mert Köseoğlu**（创始人）：曾为 OpenAI 等企业提供技术服务，10 年全栈工程与系统架构经验
- **孙逸诚**（中国核心开发者）：大二在读，时序数据 RAG 引擎独立开发者，知乎 A2A 黑客松银奖
- **分布**：土耳其、法国等 4 个国家，GitHub 异步协作
- **认可**：登顶 GitHub Hacker News #1（570+ points），GitHub 15,000+ Stars

---

## 使用场景

### ✅ 强烈推荐
- 使用 Claude Code / Cursor 等 AI 编程助手的日常开发
- 长任务持续开发（超过 30 分钟）
- 频繁搜索大仓库、跑测试、看日志
- 多平台团队（需统一上下文优化策略）
- 关注 Token 成本的开发者和企业

### ⚠️ 需谨慎
- 偶尔读一个小文件——可能没必要
- 平台没有 Hook 能力时，只能靠指令让模型主动用 MCP 工具
- 需要模型逐字检查原始输出的场景

---

## 同类项目对比

| 项目 | 侧重点 | 与 Context-Mode 关系 |
|------|--------|---------------------|
| **context-mode** | 实时上下文过滤 + 会话连续性 | — |
| **Cognee** | 长期记忆层（graph/vector memory） | 互补：一个管过程，一个管知识 |
| **headroom** | Token 压缩 + 上下文裁剪 | 类似目标，Context-Mode 覆盖更广 |
| **agentmemory** | Agent 持久记忆 | 互补：Context-Mode 管会话，agentmemory 管跨会话 |

理想组合：**长期资料 → Cognee** + **coding agent 执行 → Context-Mode** + **任务恢复 → ctx_index/ctx_search**

---

## 创始人核心观点

> **"无限上下文是一个伪命题，克制才是 AI 工具最难建立的壁垒。"**
>
> **"行业里都在卷 100K 甚至 1M 的长文本能力，但这其实是个陷阱。把几十 KB 报错日志一股脑倒给 AI，只会加速它的'失忆'和幻觉。"**
>
> **"下一代 AI 编程的瓶颈不在于模型够不够聪明，而在于上下文管理框架够不够清晰。"**
>
> **"大厂在卷'全家桶'，而我们在做跨平台的'万能插座'。"**

---

## 安装快速开始

**Claude Code（推荐）：**
```bash
/plugin marketplace add mksglu/context-mode
/plugin install context-mode@context-mode
# 重启后验证
/context-mode:ctx-doctor
```

**MCP-only（轻量试用）：**
```bash
claude mcp add context-mode -- npx -y context-mode
```

---

## 技术架构要点

```
Agent 发起工具调用
    ↓
Hook 拦截（PreToolUse）
    ↓
路由到 sandbox 工具（ctx_execute / ctx_batch_execute）
    ↓
在隔离 Node.js 进程中运行脚本
    ↓
仅返回压缩结果 → 模型
    ↓
会话事件写入 SQLite + FTS5 索引
    ↓
压缩前：BM25 检索相关事件 → 注入 2KB 快照
```

---

*参考来源：GitHub 仓库 mksglu/context-mode，36氪《登顶 GitHub Hacker News》，SegmentFault 深度评测*
