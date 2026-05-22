# TuriX-CUA：桌面自动化 Computer-Use Agent 深度解析

> GitHub: https://github.com/TurixAI/TuriX-CUA
> Stars: ~12,000 | Language: Python | License: Apache-2.0
> OSWorld 基准 64.2%（第3名），Mac 自有基准 80%+

---

## 核心定位

TuriX 是一个**开箱即用的桌面级 CUA（Computer-Use Agent）**，让大模型（Claude/GPT/Qwen3-VL）直接操作 macOS/Windows/Linux 桌面：点击、滚动、截图感知、输入文字、调起应用——凡是人能点的，TuriX 都能点。

和大多数"演示项目"不同，它在 OSWorld 公开基准上排第3，Mac 端 80%+ 成功率是有实测支撑的数字，不是营销文案。

---

## 架构拆解

### 三层分工

```
Planner（计划层）         ← 用户任务描述 → 拆解步骤
    ↓ 选 Skill Guide
Brain/Policy（执行层）    ← VLM：看截图 → 决定下一步动作
    ↓ 输出 action
Executor（执行层）        ← 操控 OS：点击/输入/拖拽
```

**Planner** 负责把自然语言任务拆成子步骤，并从 Skills 目录里检索相关 markdown 说明文档；  
**Brain/Policy** 是热插拔的 VLM（通过 `config.json` 换模型，不改代码），负责"看截图→决定动作"这个感知-决策循环；  
**Executor** 封装 macOS Accessibility API / AppleScript / xdotool（Linux）/ PyAutoGUI（Windows）。

### Skills 机制（类 OpenClaw）

TuriX 的 Skills 就是 markdown 文件，结构上和 OpenClaw 的 SKILL.md 完全一致：

```
skills/
  browser.md       # "打开 Safari 的步骤是..."
  office.md        # "创建 Pages 文档的步骤是..."
  custom.md        # 用户自定义
```

Planner 先做 embedding 检索选出相关 skill，再把 skill 全文送给 Brain 作为 few-shot 上下文。这和 RAG 原理相同，但检索目标是"操作剧本"而不是知识。

### MCP 集成

TuriX 暴露一个 MCP Server，Claude for Desktop / OpenClaw 可以通过工具调用触发桌面操作：

```
Claude（对话层）
  └── 调用 turix_mcp.execute_task(task="订一张机票")
         └── TuriX Agent 在桌面上执行
```

这意味着你可以在聊天里说"帮我截图当前屏幕"或"打开 Excel 填数据"，Claude 会调用 TuriX 完成，而不需要人去操作桌面。

---

## 核心使用场景集合

### 1. 跨应用自动化（最核心）

不需要目标应用提供 API，只要有 GUI 就行：

- **线上数据采集**：打开内部系统页面、截图、OCR 解析关键指标——适合没有 API 的老系统
- **报告填写**：从 Excel 读数据 → 打开 PPT → 自动插图表（Demo 里就演示了这个场景）
- **重复性 Office 任务**：整理邮件附件、批量命名文件、格式化表格

### 2. 自动化测试 QA

把测试用例写成自然语言："打开登录页，输入错误密码，截图确认报错文案是否为 'Invalid credentials'"  
TuriX 执行后返回截图+成功/失败判定，可以接入 CI pipeline：

```bash
python turix.py --task "登录页错误密码场景截图验证" --output ./qa_result.png
```

### 3. 线上验证辅助（推荐系统场景）

在没有内部 API 的情况下，用 TuriX 打开线上页面 → 截图 → 让 VLM 判断推荐结果是否符合预期，替代人工点击验证：

```
任务：打开首页推荐流，截图前10个坑位，判断是否存在低质内容
```

成本：一次验证约 $0.02（截图 + VLM 判断），比人工点击快 10 倍。

### 4. 个人工作流自动化

TuriX SuperPower 3.0（2026-04-08 发布）把 TuriX CUA + TuriX CLI 合并成一个 app：

- `TuriX-work`：Office 任务编排（类 n8n，但靠视觉感知而不是 API）
- `TuriX-code`：编码+调试，用 GUI 动作"闭环"（比如自动在浏览器里打开 localhost:8080 验证效果）

---

## 性能数据

| 基准 | TuriX 得分 | 排名 |
|------|-----------|------|
| OSWorld（Linux） | 64.2% | #3 全球 |
| Mac 自有基准 | 80%+ | #1 |
| 对比 UI-TARS | 高约 5-8pp | — |

关键设计：训练数据**零 Linux 数据**，但 OSWorld 是 Linux 环境，仍然排第3——说明 VLM 策略的泛化能力不依赖 OS 特定数据。

---

## 接入方式

### 方式 A：OpenClaw Skill（最简单）

```bash
# 在 OpenClaw 里安装 clawhub skill
# https://clawhub.ai/Tongyu-Yan/turix-cua
```

安装后直接在聊天里说"帮我截图桌面"，OpenClaw 会调用 TuriX。

### 方式 B：MCP Server

```json
// claude_desktop_config.json
{
  "mcpServers": {
    "turix": {
      "command": "python3",
      "args": ["/path/to/TuriX-CUA/mcp_server.py"]
    }
  }
}
```

### 方式 C：CLI 直接调用

```bash
python turix.py --task "打开Safari，搜索今日天气，截图" --model claude-3-7-sonnet
```

---

## 与同类工具的对比

| 工具 | 方式 | 需要 API | 跨应用 | 开源 |
|------|------|---------|--------|------|
| TuriX-CUA | 视觉感知 | ❌ | ✅ | ✅ |
| Browser-use | DOM 解析 | 部分 | 仅浏览器 | ✅ |
| Playwright | DOM API | ✅ | 仅浏览器 | ✅ |
| Selenium | DOM API | ✅ | 仅浏览器 | ✅ |
| Zapier/n8n | 应用 API | ✅ | ✅ | 部分 |

TuriX 的独特价值：**对没有 API 的应用（内部系统、老旧工具）也能自动化**。

---

## 限制与注意事项

1. **执行速度**：每步需要截图 + VLM 推理，单步约 1-3 秒，复杂任务可能需要 30-60 秒
2. **权限要求**：macOS 需要开启 Accessibility 权限 + Safari 自动化，有一定安全风险
3. **动态页面脆弱性**：页面布局变化会导致坐标偏移失败，需要 retry 逻辑
4. **成本**：每次截图调用 VLM 产生 API 费用，高频任务需要算成本
5. **不适合高精度数据输入**：打字识别率接近 100%，但表格数据批量输入仍推荐用 openpyxl 等 API

---

## 总结

TuriX-CUA 的核心价值是**把"视觉+点击"这个人类操作计算机的方式 API 化**。它不需要目标应用配合，只要能在屏幕上看见就能操作。在推荐系统场景里，最直接的用法是**自动化线上验证**——打开页面截图、让 VLM 判断结果是否异常，替代人工走查，接入 CI 触发。

Skills 机制（markdown 剧本 + 语义检索）和 OpenClaw 的 SKILL.md 体系完全同构，有 OpenClaw 经验的开发者上手成本极低。
