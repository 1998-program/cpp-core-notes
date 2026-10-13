# 维护与新增内容

本仓库按主题组织，`governance/catalog.json` 是目录的唯一来源。每个主题只有一篇 `primary`（阅读入口），其他版本为 `supplement`。它们可能讨论不同业务场景；同主题不等于全文重复。

## 写作前查重

```sh
python3 scripts/content_governance.py candidate --topic flatbuffers
python3 scripts/content_governance.py candidate --topic base/graphpool --file /tmp/draft.md
```

先检索主题名和近义词，再检查草稿重合候选。工具采用日期归一化后的文本指纹，不能识别全部语义改写；作者仍须比较已存在的文章。只有标题不同不足以证明内容有增量。

已有主题优先修改主文。新增补充时，在目录 `documents` 中注明 `path`、`title`、`role: supplement` 和具体 `scope`，例如“补充超时分支的 Controller 归还证据”。新主题需登记唯一 `id`、`category`、`canonical` 和一篇 `primary`。不要为同一主文创建多个主题标识。

## 自动生成任务的接入约定

外部生成器应在写作前执行上述查重，并在推送前执行 `check`。使用日计划时，可增加：

```sh
python3 scripts/content_governance.py candidate --topic base/graphpool \
  --plan /path/to/daily-plan.json --date YYYY-MM-DD
```

把 `YYYY-MM-DD` 替换为任务当天日期。计划必须有一致的 `date` 字段；只有 `week_of` 的旧周计划不能当作当日日计划。没有新选题、新源码版本或新证据时，跳过生成，不循环重写旧选题。

仓库未包含 OpenClaw/Hermes 调度器实现，本次没有修改外部任务，也没有配置 GitHub 分支保护。CI 可报告直接推送后的失败；要在合入前强制拦截，维护者需将 `content-governance` 配为必需检查并让生成器走 PR。

## 证据与历史材料

正文中的源码路径、行号和性能数据属于对应记录时点；没有固定源码版本或实际复测，不能宣称它们已经核实。`std::vector` 等出现次数可作为扫描线索，不能替代调用链证据。

本次只将反复使用同一组扫描统计的适配附录、空附录，以及选定文章的重复原理移出当前正文。完整原文保存在治理基线提交中，链接见 `governance/changes.json` 和各文章说明。其他适配分析仍保留；保留并不代表已验证。

`pushed_history.json` 的 22 条记录是旧推送账本，不是当前文章数。本次保留其原始记录；新增内容以主题目录查重，推送账本只由实际发布流程更新。

## 验证与目录生成

```sh
python3 scripts/content_governance.py render
python3 scripts/content_governance.py check
python3 -m unittest discover -s test -p 'test_*.py'
python3 scripts/content_governance.py audit
```

仅依赖 Python 3.9+ 标准库。`check` 检查登记完整性、唯一主文、目录同步、归一化全文重复、正文高重合候选，以及 Markdown/HTML 相对文件链接。不会联网验证外链、检查源码是否存在或校验历史标题锚点。

高重合阈值为短文指纹的 80%；合法重合须在 `governance/similarity-reviewed.json` 登记文章对及具体保留理由。失效链接若确因历史材料暂时保留，须在 `governance/link-baseline.json` 逐条说明理由；不能用整目录豁免隐藏新问题。
