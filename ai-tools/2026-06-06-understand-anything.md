# Understand-Anything

**链接**: https://github.com/Lum1104/Understand-Anything  
**分类**: AI 编程辅助 / 代码理解 / 知识图谱  
**一句话描述**: 将任意代码库、知识库或文档转化为可探索、可搜索、可对话的交互式知识图谱，让你看懂 20 万行陌生代码不再是噩梦。

## 核心功能
- **代码结构图可视化**：将整个代码库渲染为交互式知识图谱，每个文件、函数、类都是可点击节点，附带通俗英文摘要
- **业务逻辑域视图**：切换到"Domain View"，查看代码如何映射到实际业务流程（域→流→步骤的水平图）
- **知识库分析**：支持 Karpathy 模式的 LLM Wiki，提取 wikilink 和类别，结合 LLM 发现隐式关系，构建社区聚类力导向图
- **语义搜索**：支持"哪些模块处理鉴权？"式的自然语言问询，跨图查询
- **变更影响分析**：提交前可视化本次更改对整个系统的连锁影响
- **角色自适应 UI**：根据初级开发/PM/高级用户自动调整展示粒度
- **引导式学习路径**：按依赖顺序自动生成架构学习路线

## 技术亮点
- **多智能体流水线**：底层使用 multi-agent pipeline 同时分析文件、函数、类、依赖关系，一次扫描构建完整图谱
- **跨平台插件**：支持 Claude Code、Codex、Cursor、Copilot、Gemini CLI 等主流 AI 编程工具
- **MIT 开源**：TypeScript 实现，约 8.3k Star（截至 2026-06）
- **社区聚类算法**：对大型代码库自动按架构层级（API/Service/Data/UI/Utility）分组并色彩标注
- **12 种编程模式识别**：泛型、闭包、装饰器等语言特性在上下文中自动解释
- **Token 节省效果**：相比 AI 工具反复重读源码，使用知识图谱可降低高达 90% 的 token 消耗

## 适用场景
- **新成员 Onboarding**：快速摸清大型陌生代码库，数周 Onboarding 缩短至数小时
- **Code Review**：提交前检查变更影响范围，避免意外破坏隐藏依赖
- **AI 编程增强**：为 Claude Code / Cursor 等工具提供预构建的项目全局上下文，减少 token 浪费
- **架构审查**：PM 和设计师无需读代码即可理解系统实际架构
- **技术债梳理**：可视化模块间依赖，识别耦合热点和孤立模块

## 快速上手

**1. 安装插件**
```bash
# 在 Claude Code 中
plugin marketplace add Lum1104/Understand-Anything
plugin install understand-anything
```

**2. 分析代码库**
```bash
# 在项目根目录执行
understand
# 多智能体流水线自动扫描，结果存储在 .understand-anything/knowledge-graph.json
```

**3. 打开交互看板**
- 运行后自动打开本地 Web Dashboard
- 可拖拽、缩放、搜索节点
- 点击任意节点查看代码、关系和简明解释

**4. 常用命令**
```bash
/understand-diff          # 提交前查看变更影响范围
/understand-explain       # 深入解释某个模块
/understand-chat          # 与架构"对话"——用自然语言提问
/understand-knowledge     # 分析 Karpathy 模式知识库
```

**5. 典型问法**
- "哪些模块依赖 AuthService？"
- "UserController 中的数据流是什么？"
- "修改 db.connect() 会影响哪些调用链？"

> 推荐理由：在 AI 编程工具普及的 2026 年，代码理解效率成为工程师的新竞争力。Understand-Anything 的交互知识图谱不是炫技展示，而是真正帮助开发者"看懂"复杂系统——对推荐系统、广告引擎这类百万行级别的大型项目尤为实用。
