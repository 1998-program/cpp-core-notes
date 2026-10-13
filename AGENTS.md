# 笔记维护约定

- 写作前读取 `governance/catalog.json`，用 `python3 scripts/content_governance.py candidate --topic <关键词>` 查已有主题；有草稿时增加 `--file <草稿路径>`。
- 同一主题默认更新主文。只有独立场景、版本变更、反例或新的源码证据才新增补充文章，并在目录的 `scope` 写清增量。日期变化和换一种表达不算增量。
- 不自动删除相似文章。先逐段比较，保留独有例子、图解、代码路径与未确认问题；移出正文的历史材料记录在治理台账，并使用固定 commit 的原文链接。
- 引用源码结论须说明仓库、版本、路径和复核状态；静态关键词/容器命中统计不能推出“已使用”“未使用”或性能收益。
- 没有当日计划时重新选题或停止当次产出，不得回退 `daily-plan-20260529.json`。该文件及 `pushed_history.json` 均为历史记录，不是当前主题清单。
- 更新目录登记后执行 `python3 scripts/content_governance.py render`，提交前运行 `python3 scripts/content_governance.py check` 和 `python3 -m unittest discover -s test -p 'test_*.py'`。
- `render` 只生成目录和导航，不合并或删除正文。若确需调整历史生成记录，明确说明原因，不伪造推送时间。
