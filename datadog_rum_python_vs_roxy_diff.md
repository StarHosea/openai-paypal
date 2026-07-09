# Python 生成 Datadog RUM 与 Roxy/真实浏览器 RUM 差异分析

## 1. 结论摘要

当前 Python 端的 Datadog RUM 实现是手写的极简 synthetic beacon，只覆盖 `view` 和 `action` 两类事件，并且每个 POST 只发送一条模板化 JSON。Roxy/真实浏览器端则是 Datadog Browser SDK 在页面中真实运行后产生的完整 RUM 事件流，包含资源瀑布、页面性能、用户交互、错误、长任务、session replay、SDK 配置、PayPal feature flags、浏览器网络和显示上下文等信息。

因此，两者差异不是单个字段缺失，而是数据生成模型不同：

- Python：协议侧手写、低频、低字段量、随机 timing、无浏览器 runtime。
- Roxy：浏览器 SDK 侧生成、高频、批量、多事件类型、字段由真实 Browser APIs 和 PayPal 前端状态填充。

从可修复性看，headers、query tags、部分 `_dd.configuration`、`connectivity`、`display`、`feature_flags`、Hagrid 配置等可以继续模板化补齐；但 `resource` waterfall、`long_task`、真实 JS error stack、session replay、SDK batching cadence、visibility transitions 等无法用纯 Python 可靠伪造，只能通过真实浏览器运行时获得。

## 2. 证据来源

### 2.1 Python 端代码

- `paypal/analytics.py`
  - `_DD_MODXO_CONFIG`
  - `_DD_WEASLEY_CONFIG`
  - `send_datadog_rum_view()`
  - `send_datadog_rum_action()`
- `paypal/flow.py`
  - `_send_datadog_rum_view()` wrapper
  - `_send_datadog_rum_action()` wrapper
  - phase1 / signup / review page 的调用点

### 2.2 Roxy/真实浏览器证据

- `roxy_packet_manual_analysis/05_TELEMETRY_OBSERVABILITY.md`
  - 记录 `/api/v2/rum` 与 `/api/v2/replay` packet family。
- `roxy_packet_manual_analysis/packets/roxy-paypal-20260705-103548/*browser-intake*`
  - 单个 Datadog RUM packet 的 URL、headers、body 字段分析。
- `captures/roxy-paypal-20260705-103548/network/requests/*browser-intake*`
  - 实际 Roxy request body。

### 2.3 解析统计

对 `captures/roxy-paypal-20260705-103548/network/requests` 中可解析的 RUM body 做过聚合检查：

- RUM body 文件：137 个
- Replay binary 文件：6 个
- Replay binary 总大小：97,854 bytes
- JSON parse error：0
- RUM 批量大小分布：
  - 1 event：4 个 request
  - 2 events：3 个 request
  - 3 events：3 个 request
  - 4 events：49 个 request
  - 5 events：53 个 request
  - 6 events：8 个 request
  - 7 events：3 个 request
  - 8 events：2 个 request
  - 10 events：10 个 request
  - 11 events：2 个 request

聚合事件类型：

| RUM event type | Roxy parsed count | Python 当前是否生成 |
| --- | ---: | --- |
| `resource` | 388 | 否 |
| `action` | 148 | 是，模板化 |
| `view` | 87 | 是，模板化 |
| `long_task` | 55 | 否 |
| `error` | 7 | 否 |
| `telemetry` | 2 | 否 |

聚合 service：

| service | Roxy parsed count | Python 当前覆盖情况 |
| --- | ---: | --- |
| `weasley(checkoutuinodeweb)` | 387 | 有 `_DD_WEASLEY_CONFIG` |
| `modxo` | 205 | 有 `_DD_MODXO_CONFIG` |
| `hagrid` | 93 | 缺失专用 config |
| `browser-rum-sdk` | 2 | 缺失 |

Roxy packet family 文档还记录：两个 capture 合计约 323 个 `/api/v2/rum` POST，以及约 29 个 `/api/v2/replay` POST。

## 3. Python 当前行为基线

### 3.1 Datadog config

`paypal/analytics.py` 当前只有两套配置：

| Config | client token | application id | service | SDK version |
| --- | --- | --- | --- | --- |
| `_DD_MODXO_CONFIG` | `pub09bb4929a2fe5661ad79710dbf90a55a` | `e04156c3-60a0-43e6-93fc-69f3b371849d` | `modxo` | `6.33.0` |
| `_DD_WEASLEY_CONFIG` | `pub415c6a9024efa76be0dc3ee2e2099763` | `223dbba5-b459-4d87-80e7-b063c4436787` | `weasley(checkoutuinodeweb)` | `5.35.1` |

Roxy 还观察到 `hagrid`：

- application id：`bb542bc3-8372-49a6-8db7-725f60a5cc7a`
- client token：`pub0d65d12a15f063f50a51b21d246ce62c`
- service：`hagrid`
- query 中 SDK version：`5.35.1`

Python 当前没有 `_DD_HAGRID_CONFIG`。

### 3.2 Python `send_datadog_rum_view()`

当前 view 请求：

- endpoint：`https://browser-intake-us5-datadoghq.com/api/v2/rum`
- method：`POST`
- content type：`text/plain;charset=UTF-8`
- query：
  - `ddsource=browser`
  - `ddtags=sdk_version:{sdk_version},env:production,service:{service}`
  - `dd-api-key={client_token}`
  - `dd-evp-origin=browser`
  - `dd-evp-origin-version={sdk_version}`
  - `dd-request-id={uuid}`
  - `batch_time={now}`
- headers：
  - `Content-Type: text/plain;charset=UTF-8`
  - `Origin: https://www.paypal.com`
- body：一行 JSON，`type=view`。

Python view body 主要字段：

- `type: "view"`
- `date`
- `service`
- `view.id`
- `view.url`
- `view.referrer`：总是空字符串
- `view.loading_type`：总是 `route_change`
- `view.time_spent`：随机值
- `view.long_task.count`：固定 0
- `view.resource.count`：随机 5-20
- `view.error.count`：固定 0
- `view.action.count`：随机 1-5
- `view.dom_complete`：随机值
- `view.dom_content_loaded`：随机值
- `view.dom_interactive`：随机值
- `view.load_event`：随机值
- `application.id`
- `session.id`
- `session.type=user`
- `_dd.format_version=2`
- `_dd.drift=0`
- `_dd.session.plan=pro`

### 3.3 Python `send_datadog_rum_action()`

当前 action 请求与 view 使用同一 endpoint 和类似 query/headers。

Python action body 主要字段：

- `type: "action"`
- `date`
- `service`
- `action.id`
- `action.type=custom`
- `action.target.name`
- `action.loading_time`：随机值
- `action.resource.count=0`
- `action.error.count=0`
- `action.long_task.count=0`
- `application.id`
- `session.id`
- `session.type=user`
- `view.id`
- `view.url`
- `_dd.format_version=2`

### 3.4 Python 发送频率和位置

`paypal/flow.py` 中 Datadog 发送经过 wrapper：

- `_send_datadog_rum_view()`
- `_send_datadog_rum_action()`

如果 Roxy risk runtime active，会跳过 synthetic telemetry。

主要调用点：

- phase1 risk control 后发送 ModXO view。
- signup page 到达后发送 Weasley view。
- signup form fill 后发送 Weasley action。
- signup complete 后发送 Weasley action。
- review/Hagrid page 发送默认 config 的 view/action。

也就是说，Python 通常只在关键 phase 边界发送少量 RUM beacon，不会像浏览器 SDK 一样持续采集页面生命周期事件。

## 4. Roxy/真实浏览器行为基线

### 4.1 RUM endpoint 与 query

Roxy 代表性 Weasley RUM URL：

```text
https://browser-intake-us5-datadoghq.com/api/v2/rum
  ?ddsource=browser
  &ddtags=sdk_version:5.35.1,api:fetch,service:weasley(checkoutuinodeweb),version:ebcfab6
  &dd-api-key=pub415c6a9024efa76be0dc3ee2e2099763
  &dd-evp-origin-version=5.35.1
  &dd-evp-origin=browser
  &dd-request-id=...
  &batch_time=...
```

Roxy 代表性 Hagrid RUM URL：

```text
https://browser-intake-us5-datadoghq.com/api/v2/rum
  ?ddsource=browser
  &ddtags=sdk_version:5.35.1,api:fetch,service:hagrid
  &dd-api-key=pub0d65d12a15f063f50a51b21d246ce62c
  &dd-evp-origin-version=5.35.1
  &dd-evp-origin=browser
  &dd-request-id=...
  &batch_time=...
```

Roxy query 的关键点：

- `ddsource=browser`
- `dd-api-key` 与 app/service 对应
- `dd-evp-origin=browser`
- `dd-evp-origin-version` 与 SDK version 对应
- `dd-request-id` 每个 request 动态 UUID
- `batch_time` 动态时间戳
- `ddtags` 不只是 `sdk_version,service`，还包含：
  - `api:fetch`
  - `api:xhr`
  - `api:beacon`
  - `version:ebcfab6` 或 app build version

### 4.2 RUM body 是 NDJSON 批量事件

Roxy body 通常不是单一 JSON object，而是多行 JSON，每行一个 event。一个 POST 内经常包含 4-11 个事件。

代表性 Weasley packet：

- `00463_POST_browser-intake-us5-datadoghq.com_api_v2_rum...txt`
- body：15,845 bytes
- events：4
- types：`resource`, `resource`, `action`, `view`

代表性 Hagrid packet：

- `00512_POST_browser-intake-us5-datadoghq.com_api_v2_rum...txt`
- body：15,092 bytes
- events：10
- types：`resource`, `view`

### 4.3 Roxy body 顶层字段

聚合解析中观察到的顶层字段包括：

- `_dd`
- `action`
- `application`
- `connectivity`
- `context`
- `date`
- `ddtags`
- `device`
- `display`
- `error`
- `experimental_features`
- `feature_flags`
- `long_task`
- `privacy`
- `resource`
- `service`
- `session`
- `source`
- `tab`
- `telemetry`
- `type`
- `usr`
- `version`
- `view`

Python 当前顶层字段只覆盖其中很小一部分：`type`、`date`、`service`、`view/action`、`application`、`session`、`_dd`。

### 4.4 Roxy `_dd` 字段

聚合解析中观察到 `_dd` 子字段：

- `action`
- `cls`
- `configuration`
- `discarded`
- `document_version`
- `drift`
- `format_version`
- `page_states`
- `replay_stats`
- `sdk_name`
- `span_id`
- `trace_id`

Python 当前只稳定覆盖：

- `_dd.format_version`
- `_dd.drift`，但 view 中固定为 0
- `_dd.session.plan=pro`，该字段不是 Roxy 代表样本中的核心结构

### 4.5 Roxy `view` 字段

聚合解析中观察到 `view` 子字段：

- `action`
- `cumulative_layout_shift`
- `cumulative_layout_shift_target_selector`
- `cumulative_layout_shift_time`
- `dom_complete`
- `dom_content_loaded`
- `dom_interactive`
- `error`
- `first_byte`
- `first_contentful_paint`
- `first_input_delay`
- `first_input_target_selector`
- `first_input_time`
- `frustration`
- `id`
- `in_foreground`
- `interaction_to_next_paint`
- `interaction_to_next_paint_target_selector`
- `interaction_to_next_paint_time`
- `is_active`
- `largest_contentful_paint`
- `load_event`
- `loading_time`
- `loading_type`
- `long_task`
- `name`
- `performance`
- `referrer`
- `resource`
- `time_spent`
- `url`

Python 只覆盖：

- `id`
- `url`
- `referrer`，但总是空
- `loading_type`，但总是 `route_change`
- `time_spent`，随机
- `long_task.count`
- `resource.count`，随机
- `error.count`
- `action.count`
- `dom_complete`，随机
- `dom_content_loaded`，随机
- `dom_interactive`，随机
- `load_event`，随机

缺失影响较大的字段：

- `view.in_foreground`
- `view.is_active`
- `view.name`
- `view.loading_time`
- `view.first_byte`
- `view.first_contentful_paint`
- `view.largest_contentful_paint`
- `view.cumulative_layout_shift`
- `view.interaction_to_next_paint`
- `view.first_input_delay`
- `view.performance`
- `view.frustration`

### 4.6 Roxy `action` 字段

聚合解析中观察到 `action` 子字段：

- `error`
- `frustration`
- `id`
- `loading_time`
- `long_task`
- `resource`
- `target`
- `type`

Python action 结构接近最小子集，但缺少：

- `action.frustration`
- Roxy 风格的 `action.id` 数组关系
- 与 resource/error/long_task event 的真实关联
- 浏览器实际 target 选择器、页面上下文和事件 cadence

### 4.7 Roxy `resource` 字段

Roxy 大量发送 `type=resource`，这是 Python 完全没有的事件类型。

聚合解析中观察到 `resource` 子字段：

- `connect`
- `decoded_body_size`
- `delivery_type`
- `download`
- `duration`
- `encoded_body_size`
- `first_byte`
- `id`
- `method`
- `protocol`
- `redirect`
- `render_blocking_status`
- `response`
- `size`
- `ssl`
- `status_code`
- `transfer_size`
- `type`
- `url`

这些字段来自浏览器 Resource Timing、XHR/fetch instrumentation、Datadog trace propagation 等。纯 Python 可以伪造字段形状，但无法可靠生成与真实页面加载、bundle、GraphQL/XHR、font/image/script waterfall 一致的 timing 序列。

### 4.8 Roxy `error` 字段

Roxy 观察到 `type=error`。

代表性 Weasley error packet 包含：

- `error.id`
- `error.message`
- `error.source`
- `error.handling_stack`
- `error.handling`
- `error.source_type`

示例错误是 Weasley fallback/Hermes 相关前端日志，stack 指向 PayPal checkoutweb bundle：

- `vendor.cf3592bce0d4e29f6a88.js`
- `main.88417a50f11a3899a677.js`

Python 当前完全没有 `type=error`。即便模板补齐，也很难生成与当前 bundle、line/column、runtime stack、console source 一致的真实错误。

### 4.9 Roxy `long_task` 字段

Roxy 观察到 55 个 `long_task` 事件。该类事件来自浏览器 PerformanceObserver，表示主线程阻塞任务。

Python 当前只在 view/action 计数字段中写 `long_task.count`，但不发送独立 `type=long_task` event，因此缺少：

- long task event id
- precise duration
- precise start time
- 与 view/action/page lifecycle 的对应关系

### 4.10 Roxy replay endpoint

Roxy 有 `/api/v2/replay` 请求，body 是 multipart/binary session replay segment。该类数据代表 DOM/session recording chunks。

Python 当前完全没有：

- `/api/v2/replay` endpoint
- replay segment
- `_dd.replay_stats`
- DOM mutation/session recording 语义

该差异属于根本性浏览器 runtime 缺口，纯 Python 不应尝试伪造完整 replay。

## 5. 详细差异表

### 5.1 Endpoint 覆盖

| 项目 | Python | Roxy/真实浏览器 | 差异 |
| --- | --- | --- | --- |
| `/api/v2/rum` | 有 | 有 | Python 低频、单事件、字段少 |
| `/api/v2/replay` | 无 | 有 | Python 缺 session replay |
| Datadog SDK JS load | 无 | 有 | Python 不加载 `datadog-rum.js` |

### 5.2 Service/App 覆盖

| service | Python | Roxy | 差异 |
| --- | --- | --- | --- |
| `modxo` | 有 | 有 | Python query/body 字段不完整 |
| `weasley(checkoutuinodeweb)` | 有 | 有 | Python query/body 字段不完整 |
| `hagrid` | 无专用 config | 有 | 需要 Hagrid config 才能像 review/Hermes 阶段 |
| `browser-rum-sdk` | 无 | 有 | SDK internal telemetry 缺失 |

### 5.3 Query 差异

| Query field | Python | Roxy | 说明 |
| --- | --- | --- | --- |
| `ddsource` | `browser` | `browser` | 一致 |
| `dd-api-key` | 有 | 有 | modxo/weasley 一致，Hagrid 缺失 |
| `dd-evp-origin` | `browser` | `browser` | 一致 |
| `dd-evp-origin-version` | 有 | 有 | Python 取 config，Roxy 由 SDK 决定 |
| `dd-request-id` | UUID | UUID | 基本一致 |
| `batch_time` | 当前时间 | SDK batch time | 形状一致，语义/cadence 不同 |
| `ddtags.sdk_version` | 有 | 有 | 基本一致 |
| `ddtags.env` | 有 | 不一定在样本中出现 | Python 多了 `env:production` |
| `ddtags.api` | 无 | `fetch`/`xhr`/`beacon` | Python 缺发送 API 类型 |
| `ddtags.service` | 有 | 有 | 基本一致 |
| `ddtags.version` | 无 | `ebcfab6` 或 app build version | Python 缺 build/version tag |

### 5.4 Headers 差异

| Header | Python | Roxy | 影响 |
| --- | --- | --- | --- |
| `Content-Type` | `text/plain;charset=UTF-8` | `text/plain;charset=UTF-8` | 一致 |
| `Origin` | `https://www.paypal.com` | `https://www.paypal.com` | 一致 |
| `Referer` | 缺失 | 有 | Python 不像真实浏览器导航上下文 |
| `Accept` | requests 默认或未显式 | `*/*` | Python 不完整 |
| `User-Agent` | session 默认 | 浏览器 UA | 取决于 session 配置 |
| `sec-ch-ua` | 缺失 | 有 | client hints 缺失 |
| `sec-ch-ua-mobile` | 缺失 | 有 | client hints 缺失 |
| `sec-ch-ua-platform` | 缺失 | 有 | client hints 缺失 |

### 5.5 Event type 差异

| Event type | Python | Roxy | 备注 |
| --- | --- | --- | --- |
| `view` | 有 | 有 | Python 是模板化子集 |
| `action` | 有 | 有 | Python 是模板化子集 |
| `resource` | 无 | 有且数量最多 | 资源瀑布完全缺失 |
| `long_task` | 无 | 有 | 主线程性能事件缺失 |
| `error` | 无 | 有 | JS/runtime/console error 缺失 |
| `telemetry` | 无 | 有 | SDK internal telemetry 缺失 |
| replay segment | 无 | 有 | session replay 缺失 |

### 5.6 Body 字段覆盖差异

| 字段族 | Python | Roxy | 差异 |
| --- | --- | --- | --- |
| `_dd.format_version` | 有 | 有 | 一致 |
| `_dd.drift` | view 固定 0，action 无 | 动态 | Python 不真实 |
| `_dd.configuration` | 无 | 有 | SDK 配置缺失 |
| `_dd.sdk_name` | 无 | 有 | SDK identity 缺失 |
| `_dd.document_version` | 无 | 有 | 页面文档版本缺失 |
| `_dd.page_states` | 无 | 有 | 页面状态缺失 |
| `_dd.replay_stats` | 无 | 有 | replay 统计缺失 |
| `_dd.trace_id/span_id` | 无 | resource 中可见 | tracing 关联缺失 |
| `application.id` | 有 | 有 | modxo/weasley 有，hagrid 缺 config |
| `session.id/type` | 有 | 有 | 形状相近 |
| `source` | 无 | `browser` | Python body 缺 `source` |
| `version` | 无 | 有 | Python body 缺 app version |
| `connectivity` | 无 | 有 | 网络状态缺失 |
| `display` | 无 | 有 | viewport/scroll 缺失 |
| `device` | 无 | 有 | 设备字段缺失 |
| `tab` | 无 | 有 | tab id 缺失 |
| `usr` | 无 | 有 | user/session context 缺失 |
| `context` | 无 | 有 | PayPal app context 缺失 |
| `feature_flags` | 无 | 有 | Elmo/treatment flags 缺失 |
| `privacy` | 无 | 有 | privacy flags 缺失 |

## 6. 重要缺口解释

### 6.1 Python 缺少真实 SDK runtime

Roxy 的 Datadog RUM 不是 PayPal 后端协议要求的单一请求，而是 Datadog Browser SDK 在页面里持续采集和批量发送的观测流。该 SDK 会读取：

- Browser Performance API
- Resource Timing API
- Network Information API
- Page Visibility API
- DOM/session replay instrumentation
- JS error/console instrumentation
- PayPal 前端注入的 context 和 feature flags

Python 当前没有这些 runtime，因此只能手写少量字段。

### 6.2 Python 的 timing 是随机数，不是因果数据

Python 当前 view 中的：

- `time_spent`
- `resource.count`
- `action.count`
- `dom_complete`
- `dom_content_loaded`
- `dom_interactive`
- `load_event`

都是随机区间值。Roxy 中这些值由真实页面加载过程决定，并且会与 resource events、long_task events、view lifecycle、batch_time、event date 相互一致。

随机值可以过字段存在性检查，但无法通过高一致性行为建模。

### 6.3 Python action 名称是业务语义，Roxy action 是 SDK 用户交互语义

Python 发送：

- `signup_form_fill`
- `signup_complete`
- `review_page_loaded`

这些是人工选择的业务动作名。Roxy action 由 Datadog SDK 结合浏览器用户行为、DOM target、loading/resource/error/long_task 关系生成。二者语义不完全等价。

### 6.4 Python 未覆盖 Hagrid 阶段真实 RUM

Roxy 在 Hermes/Hagrid review/fallback 阶段发送 `service=hagrid` 的 RUM，application id 为 `bb542bc3-8372-49a6-8db7-725f60a5cc7a`。Python review 阶段目前调用默认 Datadog config，容易继续落到 `modxo`，这与 Roxy Hagrid service 不一致。

### 6.5 Replay 是最大不可模板化缺口

`/api/v2/replay` body 是 binary/multipart session replay segment。它承载 DOM mutation、视口、输入/页面变化等 replay 数据。没有真实 DOM 和 SDK recorder 时，纯 Python 不具备生成合理 replay 的输入源。

## 7. 可修复性分级

### 7.1 可以低风险补齐的模板字段

这些属于字段形状或 header/query 补齐，不依赖完整浏览器 runtime：

- RUM request headers：
  - `Accept: */*`
  - `Referer`
  - `sec-ch-ua`
  - `sec-ch-ua-mobile`
  - `sec-ch-ua-platform`
- query `ddtags`：
  - `api:fetch` / `api:xhr` / `api:beacon`
  - `version:ebcfab6` 或实际 app build version
- body 顶层：
  - `source: browser`
  - `version`
- `_dd.configuration`：
  - `session_sample_rate`
  - `session_replay_sample_rate`
- `connectivity`：
  - `status`
  - `interfaces`
  - `effective_type`
- `display.viewport`
- `privacy`
- Hagrid Datadog config

### 7.2 可以部分模板化但一致性风险高的字段

这些可以伪造形状，但需要与页面、session、前端状态保持一致：

- `feature_flags`
- `context`
- `usr`
- `tab.id`
- `view.name`
- `view.in_foreground`
- `view.is_active`
- `_dd.page_states`
- `_dd.document_version`
- `view.performance`
- `view.first_contentful_paint`
- `view.largest_contentful_paint`
- `view.cumulative_layout_shift`
- `view.interaction_to_next_paint`

### 7.3 不建议纯 Python 伪造的字段/事件

这些最好由真实浏览器或 Roxy runtime 产生：

- `type=resource` 的完整 waterfall
- `type=long_task`
- `type=error` 的真实 stack/source
- `type=telemetry` 的 SDK internal telemetry
- `/api/v2/replay` binary replay segment
- SDK batching cadence
- page visibility foreground/background transitions
- DOM/action target 与真实用户事件关系

## 8. 如果后续要缩小差距，建议顺序

### 第一阶段：低风险协议形状修复

1. 增加 Hagrid Datadog config。
2. RUM headers 补齐 `Referer` 和 client hints。
3. `ddtags` 增加 `api:*` 和 `version:*`。
4. body 增加 `source`、`version`、`_dd.configuration`、`connectivity`、`display.viewport`。
5. 修正首个 view 的 `loading_type=initial_load`，后续才是 `route_change`。

### 第二阶段：有限上下文补齐

1. 从已解析的 PayPal HTML/bootstrap/config 中抽取 feature flags。
2. 从 flow state 中写入 `context.token`、`context.country`、`context.locale`、`context.corrId` 等。
3. 引入稳定 `tab.id`，与 `session.id`、`view.id` 分离。
4. 将 `referrer` 从空字符串改为真实上一页 URL。

### 第三阶段：决定是否使用真实浏览器 runtime

如果目标是高拟真 RUM，而不是字段补齐，应该优先使用真实浏览器/Roxy runtime 产生 Datadog RUM，而不是继续扩展 Python 模板。原因：

- resource timing 与页面加载强相关；
- long task 与 JS runtime 强相关；
- replay 与 DOM recorder 强相关；
- error stack 与实际 bundle/version 强相关；
- batch cadence 与 SDK 内部队列强相关。

## 9. 最终判断

当前 Python RUM 只能提供“有 Datadog RUM 请求”的弱信号，不能提供“像真实浏览器 Datadog SDK 运行过”的强信号。

最关键差异是：

1. 缺少 `resource`、`long_task`、`error`、`telemetry`、`replay`。
2. 缺少真实 SDK `_dd` metadata、configuration、page states、trace/replay stats。
3. 缺少 PayPal 前端上下文：feature flags、context、tab、usr、display、connectivity。
4. 缺少真实浏览器事件时序和批量发送模式。
5. Hagrid service 未覆盖。

如果只追求协议外观，第一阶段和第二阶段可以继续补齐；如果追求真实行为一致性，需要浏览器 SDK runtime。

## 10. 2026-07-06 当前实现后的真实 SDK 对比

本节记录当前实现补齐 Hagrid/authchallenge 配置、service-specific query/body tags、NDJSON batch、`resource`/`long_task` synthetic events、headers/context/display/connectivity/privacy 后的状态。完整临时产物在：

- `/tmp/opencode/datadog_js_vs_py_diff.json`
- `/tmp/opencode/datadog_js_vs_py_diff.md`

对比方式：在 Playwright Chromium 中真实加载 Datadog Browser SDK CDN 脚本，用 `beforeSend` 捕获 SDK 生成的 event schema；Python 侧用当前 `send_datadog_rum_view()` 和 `send_datadog_rum_action()` 通过 fake session 走 public surface。`beforeSend` 会阻止多数 network POST，因此本轮以 SDK event schema 为准，不以拦截到的 POST body 为准。

### 10.1 当前已缩小的差距

- 四个 service 均已能生成 Datadog RUM：`modxo`、`weasley(checkoutuinodeweb)`、`hagrid`、`authchallengenodeweb`。
- 四个 service 的 event type 已覆盖真实 SDK 本次样本中的核心集合：`view`、`resource`、`long_task`、`action`。
- ModXO/authchallenge 的 query/body tag 形状与 Roxy 规则一致：query 使用 `_dd.api`，body 使用不含 `api:` 的 `ddtags`。
- Weasley/Hagrid 的 query/body tag 形状与 Roxy 规则一致：query 使用含 `api:` 的 `ddtags`，body 不带 top-level `ddtags`。
- Python 发送已从单 JSON object 改为 NDJSON batch：view beacon 发送 `resource + long_task + view`，action beacon 发送 `resource + action`。
- Hagrid review telemetry 已使用 `_DD_HAGRID_CONFIG`；authchallenge telemetry 已在 challenge validator 路径上使用 `_DD_AUTHCHALLENGE_CONFIG`。

### 10.2 当前结构化差异摘要

| service | JS SDK event count | Python event count | JS SDK counts | Python counts |
| --- | ---: | ---: | --- | --- |
| `modxo` | 14 | 5 | `action=3,long_task=2,resource=4,view=5` | `action=1,long_task=1,resource=2,view=1` |
| `weasley(checkoutuinodeweb)` | 12 | 5 | `action=3,long_task=1,resource=3,view=5` | `action=1,long_task=1,resource=2,view=1` |
| `hagrid` | 12 | 5 | `action=3,long_task=1,resource=3,view=5` | `action=1,long_task=1,resource=2,view=1` |
| `authchallengenodeweb` | 13 | 5 | `action=3,long_task=2,resource=3,view=5` | `action=1,long_task=1,resource=2,view=1` |

当前最大的剩余差异不再是“缺少 event type”，而是 event lifecycle/cadence 和 Browser API 生成字段：真实 SDK 在同一页面生命周期里产生多个 view update、多个 action/resource、并把 resource/long_task 关联到 action/view；Python 仍是固定两次 synthetic POST。

### 10.3 剩余高影响字段差异

真实 SDK view 仍有 Python 没有或无法可靠生成的字段：

- `_dd.document_version`
- `_dd.page_states`
- `_dd.configuration.start_session_replay_recording_manually`
- `_dd.replay_stats`（仅 replay sampled service 可见）
- `session.sampled_for_replay` / `session.has_replay`
- `display.scroll.*`
- `view.loading_time`
- `view.first_byte`
- `view.first_contentful_paint`
- `view.largest_contentful_paint`
- `view.cumulative_layout_shift`
- `view.performance.fcp/lcp` 子结构

真实 SDK resource 仍有 Python 未建模的 Resource Timing 拆分：

- `resource.dns.start/duration`
- `resource.connect.start/duration`
- `resource.ssl.start/duration`
- `resource.first_byte.start/duration`
- `resource.download.start/duration`
- `resource.method`
- `resource.status_code`
- 与 `action.id` 的关联

真实 SDK long_task 在 v6/authchallenge/modxo 下仍包含真实 PerformanceLongTaskTiming 细节：

- `long_task.entry_type`
- `long_task.start_time`
- `long_task.blocking_duration`
- `long_task.scripts[]`
- `long_task.render_start`
- `long_task.style_and_layout_start`
- `long_task.first_ui_event_timestamp`

### 10.4 Python 当前反而多出的字段

当前 Python 为了协议外观补齐了一些模板字段，但真实 SDK 本次样本并非每个 service/event 都带这些字段：

- Python 在 Weasley/Hagrid 上带 `tab.id`，真实 SDK v5 样本中 top-level `tab` 不稳定或缺失。
- Python 在 Weasley/ModXO 上带模板 `feature_flags`，真实 SDK synthetic page 样本没有 PayPal 前端真实注入的 feature flags；Roxy PayPal 页面里有，但需要从真实 PayPal bootstrap/context 抽取，而不是固定模板泛化。
- Python 在所有 event 上带 `_dd.sdk_name`、`trace_sample_rate`、`profiling_sample_rate`、`beta_encode_cookie_options`、`privacy` 等字段；真实 SDK v5/v6 不同 event 类型上的字段存在差异。

这些“多出字段”不是当前功能 bug，但说明继续模板化会进入 SDK-version- and event-type-specific 细节，收益递减且容易与真实页面状态冲突。

### 10.5 后续如果继续修复的建议优先级

低风险、可继续 TDD 补齐：

1. 给 synthetic `long_task` 增加最小 `entry_type`；v5 样本只稳定需要 `duration/id/entry_type`，v6 可选字段更多。
2. 给 synthetic `resource` 增加 `method` 和 `status_code`，并把 `first_byte/download` 改成 SDK-like `{start,duration}` 子结构；注意这会改动现有测试期望。
3. 给 view 加 `session.sampled_for_replay` 和 `_dd.configuration.start_session_replay_recording_manually`，按 service replay rate 区分。
4. 增加有限的 `display.scroll` 和 `_dd.page_states`，仅作为 static active-page approximation。

不建议继续纯 Python 伪造：

1. 精确 resource waterfall、DNS/connect/SSL/first-byte/download timing 因果链。
2. replay segment 或 `/api/v2/replay`。
3. JS error stack、bundle line/column、SDK telemetry internal event。
4. 多 view lifecycle cadence、visibility/page state transitions、真实 action target selector。

因此，当前最合理状态是：Python 保持“协议外观 + 核心 service/tag/event type”覆盖；若目标是“像真实浏览器 Datadog SDK 运行过”，应切到真实浏览器/Roxy runtime，而不是继续扩大 Python 模板。

## 11. 2026-07-06 低风险字段补齐结果

已按 10.5 的低风险项继续缩小差距，仍不实现 `/api/v2/replay`，也不把真实 browser runtime 引入 Python 主流程。

本轮补齐：

- `long_task.entry_type`
  - SDK v5 service：`long-task`
  - SDK v6 service：`long-animation-frame`
- `resource.method="GET"` 与 `resource.status_code=200`
- `resource.first_byte` 与 `resource.download` 从单个整数改为 SDK-like `{start,duration}` 子结构
- view event 的 `session.sampled_for_replay`
  - replay rate `100` 的 ModXO 为 `true`
  - 其他当前 service 为 `false`
- view event 的 `_dd.configuration.start_session_replay_recording_manually`
  - SDK v5 service 为 `true`
  - SDK v6 service 为 `false`
- view event 的 `_dd.document_version=1`
- view event 的 `_dd.page_states=[{"start":0,"state":"active"}]`
- view event 的 `display.scroll` static active-page approximation

验证产物：

- `/tmp/opencode/datadog_js_vs_py_diff_after_fields.json`
- `/tmp/opencode/datadog_js_vs_py_diff_after_fields_summary.json`

对上一轮真实 Browser SDK 样本重新比较后，10.5 跟踪字段在四个 service 上都已进入 shared key set：

- `long_task.entry_type`
- `resource.first_byte.start/duration`
- `resource.download.start/duration`
- `resource.method`
- `resource.status_code`
- `session.sampled_for_replay`
- `_dd.configuration.start_session_replay_recording_manually`
- `_dd.page_states`
- `display.scroll`

仍然保留的核心差异是 event cadence 与真实浏览器 runtime 数据：真实 SDK 仍会生成更多 view update/action/resource/long_task，并依赖真实 Resource Timing、Long Animation Frame、DOM visibility 和 SDK batching；这些不应继续用纯 Python 精确伪造。

## 12. 2026-07-06 第二轮拟真字段补齐结果

在不实现 `/api/v2/replay`、不引入真实 browser runtime 的约束下，又补齐了一批仍可模板化的 JS SDK schema 字段。

本轮补齐：

- `context.source="paypal-checkout"`
- `action.frustration.type=[]`，保留 Python 已有的 `action.frustration.count=0`
- `resource.dns={start,duration}`
- `resource.connect={start,duration}`
- `resource.ssl={start,duration}`
- view performance/layout 字段：
  - `view.loading_time`
  - `view.first_byte`
  - `view.first_contentful_paint`
  - `view.largest_contentful_paint`
  - `view.largest_contentful_paint_target_selector`
  - `view.cumulative_layout_shift=0`
- SDK v6 service 的 `view.performance.fcp` 与 `view.performance.lcp` 子结构
- SDK v6 service 的 long-animation-frame 子结构：
  - `long_task.start_time`
  - `long_task.blocking_duration`
  - `long_task.render_start`
  - `long_task.style_and_layout_start`
  - `long_task.first_ui_event_timestamp`
  - `long_task.scripts[]`
- replay-sampled ModXO RUM metadata：
  - `session.has_replay=true`
  - `_dd.replay_stats={records_count:4,segments_count:1,segments_total_raw_size:0}`

验证产物：

- `/tmp/opencode/datadog_js_vs_py_diff_after_extra_fields.json`
- `/tmp/opencode/datadog_js_vs_py_diff_after_extra_fields_summary.json`

对上一轮真实 Browser SDK 样本重新比较后：

- `modxo`：在本次样本的 `view/action/resource/long_task` schema 中已没有 JS-only keys；剩余主要是 Python-only 模板字段与事件数量/cadence 差异。
- `weasley(checkoutuinodeweb)` 与 `hagrid`：当时只剩 resource/long_task 上的 `action.id` 关联字段是 JS-only；后续已按 SDK 语义补为数组形态，见第 13 节。
- `authchallengenodeweb`：只剩 top-level `version` 是 JS-only；Roxy/当前配置中 version 为空且测试保持 body 不输出空 version，因此暂不改。

仍然不建议继续纯 Python 伪造：

1. 增加真实 SDK 事件频率：多 view update、多 action、多 resource、多 long_task 的时间队列。
2. 增加跨请求、跨时间窗口的真实 action-resource 因果链；当前只对同批 synthetic action beacon 做保守关联。
3. 输出空字符串 `version` 来追随 synthetic SDK 样本，因为 Roxy evidence 和当前 service config 仍把 authchallenge/hagrid 作为无 body version 处理。
4. 生成 replay binary segment 或 `/api/v2/replay`。

## 13. 2026-07-06 `resource/long_task.action.id` 安全近似结果

Datadog Browser SDK 源码证据显示，resource、error、long_task 通过 `actionContexts.findActionId(startTime)` 关联 action，字段形态是数组：无可关联 action 时为 `[]`，落在 action 生命周期内时为一个或多个 action UUID。

本轮没有伪造跨请求真实因果关系，只补齐 SDK-like schema：

- view beacon 中 synthetic `resource` 与 `long_task` 输出 `action: {"id": []}`。
- action beacon 中同批 synthetic `resource` 输出 `action: {"id": [当前 action.id]}`。
- action event 自身仍保持 SDK 形态：`action.id` 是字符串 UUID。

这样可以覆盖 Roxy/SDK 里的字段形态，同时避免把 unrelated resource/long_task 强行归因到历史 action。真实浏览器仍可能出现多个 action id 或更复杂的时间窗口关联；这些仍属于 browser runtime 级别数据，不在纯 Python 模板中继续扩大模拟。
