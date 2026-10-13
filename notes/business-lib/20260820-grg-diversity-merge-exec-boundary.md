# 2026-08-20 周四代码理解：GRG 散度合并执行边界与结果回收
<!-- topic-nav:start -->
> 主题：`business/diversity-merge` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260619-grg-new-diversity-merge-multistream-engine.md)
<!-- topic-nav:end -->


> 日期：2026-08-20  
> 主题来源：2026-06-01 daily-plan 文件没有可直接执行的当日计划，按历史未覆盖主题 fallback 到 GRG 散度合并执行边界；KU/业务背景需人工补充。  
> 范围：只分析 `src/process/diversity_merge.cpp`，聚焦 `process()` 的结果容器准备、执行上下文建立、散度结果输出、以及融合结束时的回收边界。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d0d7de;border-radius:8px;padding:14px;background:#f8fafc;line-height:1.45;">
  <div style="display:grid;grid-template-columns:1.1fr 1.2fr 1fr;gap:12px;align-items:stretch;">
    <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
      <div style="font-size:12px;color:#475569;font-weight:700;text-transform:uppercase;">输入层</div>
      <div style="margin-top:8px;font-size:16px;font-weight:700;color:#111827;">ExecEngine 上下文</div>
      <div style="margin-top:8px;color:#334155;">从执行引擎拿到 `custom_context`，并把散度相关状态挂到当前图函数实例上。</div>
    </div>
    <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
      <div style="font-size:12px;color:#475569;font-weight:700;text-transform:uppercase;">处理层</div>
      <div style="margin-top:8px;font-size:16px;font-weight:700;color:#111827;">process() / emit() / clear()</div>
      <div style="margin-top:8px;color:#334155;">先把结果容器和统计容器取出来，再清空输出容器，确保本次合并是干净输入、干净输出。</div>
    </div>
    <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
      <div style="font-size:12px;color:#475569;font-weight:700;text-transform:uppercase;">输出层</div>
      <div style="margin-top:8px;font-size:16px;font-weight:700;color:#111827;">_diversity_merge_result</div>
      <div style="margin-top:8px;color:#334155;">结果列表在执行完后回到引擎可见的输出槽位，后续调用链继续消费这些结果。</div>
    </div>
  </div>
  <div style="margin-top:12px;border-top:1px solid #dbe3eb;padding-top:12px;color:#334155;">
    <strong>关键关系：</strong> `process() -> emit() -> clear() -> context setup -> downstream merge output`
  </div>
</div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
start
:enter process();
:emit result / stats containers;
:clear diversity_merge_result;
:prepare exec_context;
:run merge logic;
:write output to emitted containers;
stop
@enduml
```

## 2. 结构信息图
```infographic
infographic sequence-snake-steps-simple
data
  title GRG 合并执行的 4 个关键动作
  items
    - label 取出输出槽
      desc `emit()` 先拿到结果容器，再做本次写入准备
      icon mdi/export
    - label 清空旧结果
      desc `div_result->clear()` 避免上一轮残留影响当前轮
      icon mdi/broom
    - label 准备执行上下文
      desc `get_context()` 之后挂接执行所需状态
      icon mdi/clipboard-text-outline
    - label 写回合并结果
      desc 输出容器在结束时承接新的散度列表
      icon mdi/arrow-right-bold
```

## 3. 代码证据
- `src/process/diversity_merge.cpp:80-97`：`process()` 入口，先 `emit()` 结果容器并 `clear()`，再准备 `exec_context`
- `src/process/diversity_merge.cpp:83-90`：`_diversity_merge_result`、`_sid_rollback_cnt`、`_div_succ_size`、`_redpoint_topn` 四个输出/统计槽位一次性取出
- `src/process/diversity_merge.cpp:344-385`：执行过程中使用 `merge_pos()` 等字段做槽位级写入/判断
- `src/process/diversity_merge.cpp:521-548`：调试日志里输出 `diversity_merge_result`，说明结果向外部可见
- `src/process/diversity_merge.cpp:693`：`GRAPH_FUNCTION_EMIT_DATA(...)` 将最终结果暴露给执行引擎

## 4. Pitfalls
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
  <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
    <div style="font-weight:700;color:#111827;">结果容器要先清空</div>
    <div style="margin-top:6px;color:#334155;">如果只 `emit()` 不 `clear()`，旧的结果会和新结果叠在一起，导致散度输出错位。</div>
  </div>
  <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
    <div style="font-weight:700;color:#111827;">输出槽位依赖固定顺序</div>
    <div style="margin-top:6px;color:#334155;">`sid_rollback_cnt`、`div_succ_size`、`redpoint_topn` 这些槽位都在入口一次性准备，顺序错了会影响下游字段绑定。</div>
  </div>
</div>

## 5. 调试 Checklist
```infographic
infographic list-column-done-list
data
  title GRG 合并链路排查清单
  items
    - label 检查结果容器是否清空
      done true
      desc 入口是否执行了 `div_result->clear()`
      icon mdi/eraser
    - label 检查上下文是否建立
      done true
      desc `get_context()` 后是否完成必要引用挂接
      icon mdi/account-cog-outline
    - label 检查槽位写入是否稳定
      done true
      desc `merge_pos()` 等字段是否按预期落点
      icon mdi/table-arrow-right
    - label 检查最终 emit
      done true
      desc 最终结果是否通过 `GRAPH_FUNCTION_EMIT_DATA` 暴露
      icon mdi/share-variant-outline
```

## 6. 证据来源
- `src/process/diversity_merge.cpp:80-97`
- `src/process/diversity_merge.cpp:344-385`
- `src/process/diversity_merge.cpp:521-548`
- `src/process/diversity_merge.cpp:693`

> 备注：KU 业务背景未逐篇读取正文，需人工补充。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260820-grg-diversity-merge-exec-boundary.md)。
