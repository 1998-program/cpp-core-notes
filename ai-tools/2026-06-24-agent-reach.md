# Agent-Reach — 给 AI Agent 装上互联网的"眼睛"

- 仓库：[Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach)
- Stars：38.6K（2026-06-24）
- 语言：Python
- 一句话定位：一个**能力层（capability layer）**——把 Twitter/Reddit/YouTube/B站/小红书/LinkedIn/RSS/网页等各路读取通道的"选型 + 安装 + 体检 + 路由"统一收口，让任意 AI Agent（Claude Code、OpenClaw、Cursor、Windsurf…）一句话拥有完整的互联网阅读能力，且**当某条路被风控/停更时自动换下一条**。
- 安装：把以下一行话粘给 Agent 即可：
  > 帮我安装 Agent Reach：https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/install.md

---

## 1. 它真正解决什么问题

Agent 已经能写代码、改文档、管项目，但只要让它"上网找东西"立刻翻车：

| 想做的事 | 不装 Agent-Reach 的现状 |
| --- | --- |
| 看 YouTube 教程讲了什么 | 拿不到字幕 |
| 搜 Twitter 上的产品口碑 | API 收费 |
| 翻 Reddit 是否有同款 bug | 服务器 IP 被 403 |
| 看小红书种草 | 必须登录才能看 |
| 拉 B 站技术视频字幕 | 通用工具被 B 站风控封 |
| 全网搜最新 LLM 框架对比 | 没有好用的免费引擎 |
| 读个网页 | 抓回一堆 HTML 标签 |
| 读 GitHub Issue/PR | 能用但认证配置烦 |

每个平台都有自己的门槛——付费 API、IP 风控、登录态、Cookie、数据清洗、CLI 突然停更。**逐个踩坑**才是过去 Agent 上网的真实成本。

Agent-Reach 把"踩坑成本"一次性收敛掉。**它不是又一个工具，而是工具之上的"选 + 装 + 修 + 路由"层。**

---

## 2. 最核心的使用场景：让 Agent 一句话拿到全网原文

这是项目最具杀伤力的核心用法：**任何需要 Agent 读外网内容的对话**，不再需要你预先配 API Key、调代理、写脚本。

### 2.1 零配置即用（首次安装后立刻可用）

| 平台 | Agent 怎么调 | 背后真实工具链 |
| --- | --- | --- |
| 任意网页 | "帮我看看这个链接" | `curl https://r.jina.ai/<URL>`（Jina Reader，免 Key） |
| YouTube | "这个视频讲了什么" | `yt-dlp` 提取字幕 + 搜索 |
| RSS/Atom | "订阅这个源" | `feedparser` |
| 全网语义搜 | "全网搜一下 LLM 框架对比" | Exa via `mcporter`（免 Key MCP） |
| GitHub | "这个仓库是干嘛的" | `gh repo view owner/repo` |
| V2EX | "看下 V2EX 热门" | 内置 client |
| B 站 | "B 站搜一下 AI 教程" | `bili-cli`（无登录可搜、可读） |

### 2.2 一句话解锁登录类平台

需要 Cookie/登录态的渠道，对 Agent 说一句"帮我配 Twitter / 小红书 / Reddit / LinkedIn / 雪球 / 小宇宙"，它会自己引导你用 [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm) 导出 Cookie 再粘回来。Cookie 落本地 `~/.agent-reach/config.yaml`，文件权限 `600`，不上传。

> 推荐用**专用小号**，主账号有被风控/封号的风险。

### 2.3 一条命令体检：`agent-reach doctor`

针对每个渠道**真实探测**当前后端是否可用（不是看命令存不存在），输出形如：

```
✅ web        → Jina Reader        OK
✅ youtube    → yt-dlp             OK
⚠️ bilibili   → bili-cli           OK（yt-dlp 已退役，由风控封死）
❌ reddit     → OpenCLI            未登录，运行 reach config reddit
✅ exa-search → mcporter+Exa       OK（免 Key）
```

这是 Agent-Reach **最值钱的工程化能力**：你随时知道"现在走的是哪条路、坏在哪里、怎么修"。

---

## 3. 为什么必须有这一层："换代"是常态

这是项目存在的根本理由，也是它跟"再写一个 X 平台 CLI"完全不同的地方。

- **2026-03**：一批单平台 CLI（小红书、Reddit 等）集体停更。
- **2026-06**：`yt-dlp` 在 B 站被 412 风控封死。

如果你把这些工具直接绑死到自己的 Agent 工程里，每次平台更新都要重写一遍。Agent-Reach 把每个渠道写成"**首选 + 备选**"的多后端列表：

```
channels/
├── web.py        → Jina Reader
├── twitter.py    → twitter-cli ▸ OpenCLI ▸ bird
├── youtube.py    → yt-dlp
├── github.py     → gh CLI
├── bilibili.py   → bili-cli ▸ OpenCLI ▸ 搜索 API   # yt-dlp 已退役
├── reddit.py     → OpenCLI ▸ rdt-cli               # 匿名接口被封
├── xiaohongshu.py→ OpenCLI ▸ xiaohongshu-mcp ▸ xhs-cli
├── linkedin.py   → linkedin-mcp ▸ Jina Reader
├── rss.py        → feedparser
└── exa_search.py → Exa via mcporter
```

平台被封 → 调整列表顺序 → 用户**零操作**。

> 真实发生过的换代案例：B 站的 yt-dlp 路线被风控封死后，Agent-Reach 直接把 `bilibili.py` 的首选切到 `bili-cli`，老用户的 Agent 第二天对话照常工作。

这就是它取名叫 **Reach（够得着）** 的原因——它保证你的 Agent **始终够得着**互联网，而不是绑定到某个具体工具的命运上。

---

## 4. 架构亮点：能力层 vs 工具层

```
┌─────────────────────────────────────────────────────┐
│  Agent（Claude Code / OpenClaw / Cursor / 任何 CLI）│
└────────────┬────────────────────────────────────────┘
             │ 自然语言意图
┌────────────▼────────────────────────────────────────┐
│  Agent-Reach（能力层）                              │
│   • 选型：哪条路当前最稳                            │
│   • 安装：pip / npm / mcporter 一键装好             │
│   • 体检：doctor 实时探测                           │
│   • 路由：首选失效 → 备选；备选失效 → 给修复处方    │
│   • SKILL.md：注入 Agent 的技能目录，让 Agent       │
│     遇到"全网调研""读视频""搜推特"自己知道走哪个上游│
└────────────┬────────────────────────────────────────┘
             │ 实际执行
┌────────────▼────────────────────────────────────────┐
│  上游工具（Jina / yt-dlp / gh / bili-cli / Exa …） │
└─────────────────────────────────────────────────────┘
```

**关键设计：Agent-Reach 不包装上游 API**。  
它选好工具、装好工具、写好 SKILL.md，然后**让 Agent 直接调上游工具**——没有一层"reach.read_twitter()"那样的封装。这意味着：

1. 上游工具的所有能力都直接暴露给 Agent，无能力损失。
2. 替换某个 channel = 换一个文件，不影响其他渠道。
3. 升级、扩展由社区接力完成，没有"主仓库不更新就全死"的风险。

---

## 5. 安装与使用要点（OpenClaw 用户特别提示）

OpenClaw 用户在跑 install.md 之前，必须先开 exec 权限，否则 Agent 没法 `pip install` / `mcporter`：

```bash
openclaw config set tools.profile "coding"
openclaw gateway restart
```

或者编辑 `~/.openclaw/openclaw.json`：

```json
{ "tools": { "profile": "coding" } }
```

随后开新对话粘贴：

> 帮我安装 Agent Reach：https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/install.md

安装完成后：

```bash
agent-reach doctor          # 体检
agent-reach install --safe  # 服务器/多人环境，不动系统包
agent-reach install --dry-run  # 预览所有操作，零改动
```

---

## 6. 安全模型

| 措施 | 说明 |
| --- | --- |
| 凭据本地存储 | `~/.agent-reach/config.yaml`，权限 `600`，**不上传** |
| 安全模式 `--safe` | 不会自动改系统，只告诉你要装什么 |
| 完全开源 | 代码 + 依赖全部开源可审 |
| Dry Run | `--dry-run` 预览所有操作 |
| 可插拔 | 不信任某个 channel 直接换文件 |

⚠️ **封号提醒**：Cookie 调用 Twitter/小红书有平台检测风险，**务必用小号**。

---

## 7. 适合谁

- 任何需要"让 Agent 上网调研"的开发者（产品研究、舆情、技术对比、招聘）
- 想给团队 AI Workflow 快速接入"全网原始素材"，但不想自己维护一堆 CLI 的人
- 服务器部署 Agent，需要稳定多平台抓取且能自动换代的运维场景

不适合：需要发帖、表单提交、登录态自动化的**写**类操作（Agent-Reach 是"读"层）。这类需求作者推荐配合 [BrowserAct](https://www.browseract.ai/Agent) 浏览器自动化工具。

---

## 8. 一句话总结

> **Agent-Reach 是 AI Agent 的"互联网驱动层"**——它不写读取逻辑，只保证你的 Agent 永远拥有一组**当下最稳、自动续命**的读取通道，把"平台又改了"这件原本属于你的麻烦，变成它自己的事。
