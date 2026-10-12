# 2026-07-16 基础库代码理解：MultiStreamEnginePlugin 的 GraphDependency 绑定边界

> 日期：2026-07-16｜类型：基础库笔记  
> 今日主题来源：本机未发现可用 `daily-plan` 文件；按周四 fallback 选择 ExecEngine/MultiStreamEngine 运行时依赖绑定主题。  
> 内网 KU：本次任务未提供 KU URL/doc-id；如流知识库检索需人工补充。本文以本地代码库证据为准。  
> 范围：`ExecEnginePlugin` 抽象、`MultiStreamEnginePlugin::bind_graph_dependency()`、GRG `DiversityMerge` 初始化闭包，以及运行期 `merge_pos()` 输出边界。

## 架构全景

<style>.arch716{font-family:Inter,Arial,sans-serif;border:1px solid #d8dee8;border-radius:8px;padding:18px;background:#f8fafc;color:#1f2937}.arch716 h3{margin:0 0 12px;font-size:20px}.arch716 .grid{display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:14px}.arch716 .layer{background:#fff;border:1px solid #dfe6ef;border-radius:8px;padding:12px}.arch716 .layer b{display:block;color:#29415f;margin-bottom:8px}.arch716 .box{border:1px solid #c9d6e5;border-radius:6px;padding:9px;margin:7px 0;background:#fdfefe;font-size:13px;line-height:1.45}.arch716 .hot{border-color:#2d6a4f;background:#eef7f2}.arch716 .arrow{text-align:center;color:#64748b;font-weight:700;padding:4px}.arch716 .note{font-size:12px;color:#526070;margin-top:10px}</style><div class="arch716"><h3>MultiStreamEngine 依赖绑定全景</h3><div class="grid"><div class="layer"><b>图配置与 Vertex</b><div class="box">业务图创建 DiversityMerge / NewDiversityMerge vertex</div><div class="box">vertex 持有 GraphDependency / GraphData 命名依赖</div><div class="box">配置决定 stream、rule、effect 的运行形态</div></div><div class="layer"><b>插件绑定层</b><div class="box hot">ExecEnginePlugin 多态入口</div><div class="box hot">MultiStreamEnginePlugin::bind_graph_dependency(vertex, engine)</div><div class="box">绑定失败直接 FATAL，避免无依赖运行</div></div><div class="layer"><b>运行时闭包</b><div class="box">_diversity_engine 保存已绑定依赖</div><div class="box">select / merge 阶段读取上下文与队列结果</div><div class="box hot">输出 item.ext().merge_pos() 作为响应边界字段</div></div></div><div class="note">核心判断：MultiStreamEngine 不是单独的算法函数，而是通过插件把 graph vertex 的命名依赖闭包化后交给 DiversityMerge 执行。</div></div>

## 调用链

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "Graph 配置" as Conf
participant "DiversityMerge::init" as Init
participant "ExecEnginePlugin" as Plugin
participant "MultiStreamEnginePlugin" as MSE
participant "_diversity_engine" as Engine
participant "merge output" as Output
Conf -> Init: 创建 vertex 与命名依赖
Init -> Plugin: dynamic_cast<MultiStreamEnginePlugin*>()
Init -> MSE: bind_graph_dependency(vertex, _diversity_engine)
alt bind failed
  MSE --> Init: false
  Init -> Init: LOG(FATAL)
else bind success
  MSE --> Engine: 写入 GraphDependency 运行闭包
  Engine -> Output: select / merge / effect
  Output --> Conf: item.ext().merge_pos()
end
@enduml
```

## 结构信息图

```infographic
infographic hierarchy-structure
data
  title MultiStreamEnginePlugin 绑定结构
  desc 从插件抽象到业务 Merge 输出的三层边界
  items
    - label ExecEnginePlugin
      desc 基类插件入口，暴露统一 init/run/bind 能力
      icon mdi/puzzle
      children
        - label MultiStreamEnginePlugin
          desc 负责把 vertex 命名依赖绑定进引擎实例
          icon mdi/source-branch
          children
            - label DiversityMerge
              desc 初始化阶段调用 bind_graph_dependency
              icon mdi/merge
            - label NewDiversityMerge
              desc 新版本路径复用同一绑定契约
              icon mdi/merge
    - label GraphDependency
      desc ChangedCommonInfo、stream 容器、策略配置等运行依赖
      icon mdi/graph
    - label merge_pos
      desc merge 后落在 ContentItem ext 上的响应边界字段
      icon mdi/map-marker-path
theme
  palette #3D5A80 #2D6A4F #E07A5F
```

## 关键代码理解

`DiversityMerge` 在初始化时先从通用插件指针转换为 `MultiStreamEnginePlugin`，随后调用 `bind_graph_dependency(this->vertex(), _diversity_engine)`；该调用失败会直接 `LOG(FATAL)`，说明绑定是引擎运行的硬前置条件，而不是可降级的优化路径。`new_diversity_merge.cpp` 也复用同样模式，因此这是框架契约，不是旧逻辑偶然实现。

`exec_engine.h` 中的 `MultiStreamEnginePlugin` 位于 common-processor 插件层，职责是把 graph 框架的 `vertex` 依赖转成 ExecEngine 可消费的上下文。业务侧 `DiversityMerge` 不直接手工解析所有 GraphData，而是把绑定完成的 `_diversity_engine` 作为运行闭包，后续选择、排序、merge、effect 都围绕这个闭包展开。

`merge_pos()` 是一个值得关注的输出边界：它在 merge 后从 `item->_content_item.ext()` 读取，用于表达最终合并位置。排查“召回有结果但响应位置异常”时，不应只看召回队列，还要确认 MultiStreamEngine 的绑定、merge 选择和 `merge_pos` 写入链路是否连续。

## Pitfalls 卡片

<style>.card716{font-family:Inter,Arial,sans-serif;background:#f4f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:18px;margin-top:14px;color:#243142}.card716 .meta{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#5b6b7f;font-weight:700}.card716 .title{font-size:24px;font-weight:800;margin:6px 0 12px;color:#1f344d}.card716 .cols{display:grid;grid-template-columns:1.3fr 1fr;gap:14px}.card716 .panel{background:#fff;border-top:4px solid #2d6a4f;padding:12px;border-radius:6px;line-height:1.55}.card716 b{color:#29415f}</style><div class="card716"><div class="meta">Debug Pitfalls</div><div class="title">不要把 MultiStreamEngine 当成无状态工具函数</div><div class="cols"><div class="panel"><b>错误切入点</b><br>只看 diversity merge 的算法分支，忽略 init 阶段的 vertex 依赖绑定，会导致“代码看起来能跑、线上却依赖缺失”的误判。</div><div class="panel"><b>正确切入点</b><br>先确认插件类型转换、bind_graph_dependency 返回值、vertex 命名依赖，再看 stream/rule/function/effect 的执行结果。</div></div></div>

## 调试 Checklist

```infographic
infographic list-column-done-list
data
  title MultiStreamEngine 绑定排查顺序
  desc 适用于 DiversityMerge 初始化成功但结果异常的场景
  items
    - label 确认插件类型
      desc dynamic_cast 到 MultiStreamEnginePlugin 后不得为空
      done true
      icon mdi/check-circle
    - label 确认绑定结果
      desc bind_graph_dependency 返回 false 会触发 FATAL
      done true
      icon mdi/check-circle
    - label 核对 vertex 依赖名
      desc 命名依赖缺失会让运行闭包不完整
      done true
      icon mdi/check-circle
    - label 检查 stream 队列
      desc loads/rule/function/effect 的输入输出数量要连续
      done false
      icon mdi/format-list-checks
    - label 回看 merge_pos
      desc 响应位置异常时检查 ContentItem ext 输出
      done false
      icon mdi/map-marker-check
theme
  palette #3D5A80 #2D6A4F #C05621
```

## 证据来源

- `src/process/diversity_merge.cpp:48,88-90`：引入 `MultiStreamEnginePlugin`，初始化时绑定 vertex 依赖。
- `src/process/new_diversity_merge.cpp:30,72-73`：新版本 DiversityMerge 复用同一绑定契约。
- `src/plugin/exec_engine.h:60`：`MultiStreamEnginePlugin` 插件类型定义。
- `src/process/diversity_merge.cpp:1295`：从 `ContentItem.ext()` 读取 `merge_pos()`，说明 merge 输出与响应字段存在直接边界。
