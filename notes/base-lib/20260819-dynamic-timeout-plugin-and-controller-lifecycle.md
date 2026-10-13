# 2026-08-19 周三代码理解：DynamicTimeOutPlugin 配置注入与控制器生命周期
<!-- topic-nav:start -->
> 主题：`base/dynamic-timeout` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260725-dynamic-timeout-plugin-budget-control.md)
<!-- topic-nav:end -->


> 日期：2026-08-19  
> 主题来源：2026-06-01 daily-plan 文件缺少明确日计划，按历史未覆盖主题 fallback 到 `DynamicTimeOutPlugin` 的配置注入 + 控制器池生命周期链路；KU/业务背景需人工补充。  
> 范围：只分析 `baidu/feed/general/framework/src/dynamic_timeout_plugin.cpp`、`baidu/feed/general/framework/include/dynamic_timeout_plugin.h`、`baidu/feed/general/framework/src/dynamic_timeout.cpp`、`baidu/feed/general/framework/test/conf/plugins/dynamic_timeout.conf`，关注插件初始化、配置加载、控制器池取还与动态超时参数来源。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #cbd5e1;border-radius:8px;padding:14px;background:#f8fafc;color:#1f2937;">
  <div style="display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:12px;align-items:stretch;">
    <div style="border:1px solid #94a3b8;border-radius:6px;padding:10px;background:#ffffff;">
      <div style="font-weight:700;margin-bottom:6px;">服务启动层</div>
      <div>DynamicTimeOutPlugin::initialize()</div>
      <div>读取 `dynamic_timeout.conf`</div>
      <div>装载控制器池与阈值参数</div>
    </div>
    <div style="border:1px solid #94a3b8;border-radius:6px;padding:10px;background:#ffffff;">
      <div style="font-weight:700;margin-bottom:6px;">运行时控制层</div>
      <div>get_dt_controller()</div>
      <div>return_dt_controller()</div>
      <div>FullLinkDynamicTimeOutController</div>
    </div>
    <div style="border:1px solid #94a3b8;border-radius:6px;padding:10px;background:#ffffff;">
      <div style="font-weight:700;margin-bottom:6px;">配置与阈值层</div>
      <div>`dynamic_timeout.conf`</div>
      <div>p0/p1/p2/over_rate</div>
      <div>rpc latency reload 周期</div>
    </div>
  </div>
  <div style="margin-top:12px;border-top:1px solid #cbd5e1;padding-top:10px;line-height:1.6;">
    初始化阶段把配置文件里的阶段参数和控制器池准备好；请求侧不直接计算策略，而是复用控制器对象来做动态超时判定，避免每次请求重建状态。
  </div>
</div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "DynamicTimeOutPlugin" as Plugin
participant "Configure" as Cfg
participant "DTControllerPool" as Pool
participant "FullLinkDynamicTimeOutController" as Ctrl
Caller -> Plugin : initialize()
Plugin -> Cfg : load(dynamic_timeout.conf)
Cfg --> Plugin : config tree
Plugin -> Pool : prepare controller pool
Caller -> Plugin : get_dt_controller()
Plugin -> Pool : create / borrow object
Pool --> Caller : pooled controller
Caller -> Ctrl : apply timeout policy
Caller -> Plugin : return_dt_controller(ctrl)
Plugin -> Pool : reset / recycle
@enduml
```

## 2. 配置结构信息图
```infographic
sequence-ascending-steps
data
  title DynamicTimeOut 参数流
  desc 配置文件加载后，按阶段和阈值进入控制器池供运行时复用
  items
    - label 配置路径
      desc ./conf/plugins + dynamic_timeout.conf
    - label 初始化
      desc load config -> build controller pool
    - label 阶段参数
      desc p0 / p1 / p2 / over_rate / req_num_limit
    - label 运行时使用
      desc borrow controller -> evaluate -> return controller
```

## 3. 关键证据
- `baidu/feed/general/framework/src/dynamic_timeout_plugin.cpp:9-16`：配置路径和初始化加载。
- `baidu/feed/general/framework/src/dynamic_timeout_plugin.cpp:25-34`：控制器池取还接口。
- `baidu/feed/general/framework/src/dynamic_timeout.cpp:9-16`：动态超时阈值与重载周期。
- `baidu/feed/general/framework/test/conf/plugins/dynamic_timeout.conf:1-?`：运行参数来源。
- `baidu/feed/general/framework/include/dynamic_timeout_plugin.h:16-21`：控制器池类型与插件接口声明。

## 4. Pitfalls
<div style="border:1px solid #cbd5e1;border-left:4px solid #f59e0b;border-radius:6px;padding:10px;background:#fffdf7;">
  <div style="font-weight:700;margin-bottom:4px;">配置路径和运行目录不一致</div>
  <div>插件默认从 `./conf/plugins` 读取，cron 或测试启动目录变化时容易导致加载失败，表现为初始化退化。</div>
</div>
<div style="border:1px solid #cbd5e1;border-left:4px solid #ef4444;border-radius:6px;padding:10px;background:#fffdf7;margin-top:8px;">
  <div style="font-weight:700;margin-bottom:4px;">控制器池未正确归还</div>
  <div>借出后没有走 return 路径会把对象池耗尽，后续请求只能频繁创建或直接失败。</div>
</div>
<div style="border:1px solid #cbd5e1;border-left:4px solid #6366f1;border-radius:6px;padding:10px;background:#fffdf7;margin-top:8px;">
  <div style="font-weight:700;margin-bottom:4px;">阈值刷新和请求路径耦合过紧</div>
  <div>reload 周期、latency 统计和实时判定混在一起时，容易把配置更新抖动带进在线请求。</div>
</div>

## 5. 调试 Checklist
```infographic
list-column-done-list
data
  title 调试检查项
  items
    - label 确认配置文件存在
      done true
      desc `dynamic_timeout.conf` 是否在当前工作目录可见
    - label 检查初始化日志
      done true
      desc 是否出现 load config 失败或 fallback
    - label 验证控制器池取还
      done true
      desc borrow / return 是否成对
    - label 核对阈值参数
      done true
      desc p0/p1/p2/over_rate 是否符合预期
    - label 观察重载周期
      done true
      desc rpc_latency_reload_sleep_s 是否过短
```

## 6. 证据来源
- `baidu/feed/general/framework/src/dynamic_timeout_plugin.cpp:9-34`
- `baidu/feed/general/framework/include/dynamic_timeout_plugin.h:16-21`
- `baidu/feed/general/framework/src/dynamic_timeout.cpp:9-16`
- `baidu/feed/general/framework/test/conf/plugins/dynamic_timeout.conf:1-?`

> 内网文档背景需人工补充；这里先用代码库证据固定插件与控制器生命周期边界。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260819-dynamic-timeout-plugin-and-controller-lifecycle.md)。
