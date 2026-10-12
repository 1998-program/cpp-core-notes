# 09 · Hermes Agent — 自我进化的 AI Agent 框架

**项目**：[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
**Stars**：169.9k ⭐ · Python · MIT
**定位**：Nous Research 出品的自我进化 AI agent，内置学习闭环——从经验中创建 skill、跨 session 记忆、多平台通信、可运行在 $5 VPS 上

---

## 根本问题

大多数 AI agent 是无状态的：每次对话从零开始，解决过的问题下次还要重新解决，不会从经验中学习。Hermes Agent 的核心差异化在于**自我进化**：每次完成复杂任务后，agent 自动把解决过程提炼成 Skill（markdown 文档），下次遇到类似问题直接调用；同时有 FTS5 全文搜索的跨 session 记忆，agent 知道"上周我帮你处理过类似的问题，当时的方案是..."。

---

## 核心工作原理

### 学习闭环

```
用户任务
    ↓
Agent 执行（工具调用、代码、分析）
    ↓ 任务完成后
自动 Skill 提炼（判断：这个任务值得记住吗？）
    ↓ 是
生成 SKILL.md（任务类型、解法、示例）
    ↓
写入 ~/.hermes/skills/
    ↓ 下次类似任务
Skill 语义检索 → 命中 → 直接复用
```

### Skill 系统（兼容 agentskills.io 标准）

```markdown
# ~/.hermes/skills/git-pr-workflow/SKILL.md
---
name: git-pr-workflow  
description: 从本地分支到 GitHub PR 的完整工作流，包含冲突处理
triggers: ["开 PR", "提交代码", "merge request"]
---

## 步骤
1. git add . && git commit -m "..."
2. git push origin <branch>
3. 用 GitHub MCP: create_pull_request(...)
4. 冲突处理：git merge main → 解决 → git push -f

## 注意
- 禁止直接 push main 分支
- commit message 格式：type(scope): description
```

### 跨 session 记忆

```python
# 每个 session 结束后：
# 1. 提取关键信息（用 LLM 总结）
# 2. 用 FTS5 索引存储
# 3. 后续 session 可以搜索

# 在对话中：
agent: "我在 session #47（3天前）帮你分析过类似的性能问题，
当时用的是 absl::flat_hash_map 替换方案，是否复用那次方案？"
```

### 多平台通信架构

```
Hermes Agent（运行在 VPS/本地）
    ├── Telegram Bot → 手机随时接入
    ├── Discord Bot → 团队频道
    ├── Slack Bot → 工作群
    ├── WhatsApp → 个人
    └── CLI → 本地终端
```

---

## 安装 / 快速上手

```bash
# Linux/macOS/WSL2
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

# 启动
hermes

# 选择模型（支持 200+ 通过 OpenRouter）
hermes model  # 交互式选择

# 配置 Telegram Bot（可选，实现远程接入）
hermes channel telegram
```

---

## 实践案例

**场景**：你在用 Hermes Agent 做日常开发工作。某天花了 2 小时解决了一个 brpc 服务启动时 `EADDRINUSE` 端口冲突问题（因为上次 kill 进程没有等 TIME_WAIT 超时）。下次遇到类似问题时，Hermes 应该自动记住这个解法。

**第一次解决过程**：

```bash
# 对话
> 我的 brpc 服务启动报 EADDRINUSE，端口 8080 被占用

Hermes: 让我检查一下
  → 执行: lsof -i :8080
  → 发现: 有个 TIME_WAIT 状态的连接
  → 方案: brpc::ServerOptions options; options.reuse_port = true; 
         或者等待 60s TIME_WAIT 超时
  → 长期方案: 启动脚本加 SO_REUSEPORT

# 任务结束，Hermes 自动判断："这是一个可复用的调试技巧"
# 创建: ~/.hermes/skills/brpc-port-conflict/SKILL.md
```

**3 个月后（新 session）**：

```bash
> 我的 brpc 服务又报 EADDRINUSE 了

Hermes: 我在 session #128（3个月前）处理过这个问题。
解法：设置 reuse_port = true 或等待 TIME_WAIT 超时（约 60s）。
长期方案是在启动脚本里加 SO_REUSEPORT。要我直接修改你的启动脚本吗？
```

不需要重新分析，不需要重新搜索，直接给出验证过的方案。这就是 Skill + 跨 session 记忆的价值。

**Telegram 远程控制**（当你不在电脑旁时）：

```
手机 Telegram → "帮我检查一下今天的服务监控，有没有异常"
Hermes（运行在 VPS）→ 连接监控系统 → 发现 P99 延迟异常 → 推送分析结果
```

---

## 关键特性速查

- **自动 Skill 创建**：复杂任务结束后 agent 自判断是否值得提炼，自动写入 skill 库
- **FTS5 跨 session 记忆**：全文搜索历史 session，agent 有"长期记忆"
- **多平台统一接入**：Telegram/Discord/Slack/WhatsApp/CLI，同一个 agent，多端对话连续
- **可运行在 $5 VPS**：无 GPU 依赖，用 OpenRouter API，最低成本部署
- **兼容 OpenClaw skill 格式**：agentskills.io 开放标准，skill 可以在不同 agent 框架间复用

---

**GitHub**：https://github.com/NousResearch/hermes-agent
