# Python 生成 MTR 与 Roxy 浏览器 `dfp.js` MTR 详细差异分析

分析对象：

- Roxy 浏览器抓包：`captures/roxy-paypal-20260705-103548`
- Python 程序抓包目录：`captures/program-paypal-*`
- 当前 Python 生成器代码：`paypal/mtr.py`
- Roxy 浏览器 MTR 执行代码：`paypal/roxy_fingerprint.py`

## 1. 结论摘要

需要分两层看差异：

1. **现有 `program-paypal-*` 程序抓包层面**：Python 程序侧没有实际发出 `/mtr/1a7c...` GET/POST。和 Roxy 浏览器相比，这不是字段差异，而是整条 MTR 链路缺失。
2. **当前 `paypal/mtr.py` 的 `python_generated` 生成器能力层面**：如果单独调用当前 Python 生成器生成一份 MTR body，它已经能复刻真实 `dfp.js` body 的 envelope 形态和字段集合，但仍有约 30 个真实浏览器运行时信号值不同。

因此当前状态可以概括为：

| 层面 | 当前差异大小 | 判断 |
|---|---:|---|
| 实际程序抓包 vs Roxy 抓包 | `/mtr` GET/POST 缺失 | 关键缺口，程序流程未产生 MTR |
| Python 生成器 body 外壳 | 基本一致 | marker、压缩、XOR envelope 已接近 `dfp.js` |
| Python 生成器字段集合 | 完全对齐 | 双方均为 135 个 top-level keys、127 个 `s*` signals |
| Python 生成器字段值 | 最佳情况下 31 个字段不同 | 主要剩余差距在 canvas/WebGL/font/viewport/time/runtime probes |

严格风险链路下，不建议继续把 `python_generated` 当作主方案。项目里已经有真实浏览器方案，优先应使用 `PAYPAL_MTR_RUNTIME=roxy` 让 Roxy Chrome 真实执行 PayPal 页面里的 `dfp.js`，再回灌 `requestId`、`sealedResult` 和 cookies。

## 2. 证据路径

### 2.1 Roxy 浏览器 MTR 链路

Roxy 抓包中 MTR 链路明确存在：

| 请求 ID | 方法 | 证据路径 | 说明 |
|---:|---|---|---|
| 6 | GET | `captures/roxy-paypal-20260705-103548/js/00006_resp_200_script_www.paypalobjects.com_v15170r-1d3n71ph1c4710n_dfp.js_e5d12d0aac.js` | PayPal `dfp.js`，SHA256 `e5d12d0aac6c1ccd279fc733f5e31842938c890faf46b4c2451019b87e17510c` |
| 54 | GET | `captures/roxy-paypal-20260705-103548/network/bodies/00054_resp_200_xhr_www.paypal.com_mtr_1a7c3460cd8c343771081839499ed7a0_AvQ9_Gr6-8k_ViQEi_xLu1_x0_q_QBzalmMuDFJIiZNebIWt_bec1521e97.txt` | `/mtr/.../x0` bootstrap，96 bytes base64 |
| 66 | POST | `captures/roxy-paypal-20260705-103548/network/requests/00066_POST_www.paypal.com_mtr_1a7c3460cd8c343771081839499ed7a0_chnl_iwc-mxo_cmid_BA-37R61061EU582084R_cr_undefined_btz_Pacific_Hono.txt` | 真实浏览器 MTR POST body，3236 bytes binary envelope |
| 66 | response | `captures/roxy-paypal-20260705-103548/network/bodies/00066_resp_200_xhr_www.paypal.com_mtr_1a7c3460cd8c343771081839499ed7a0_chnl_iwc-mxo_cmid_BA-37R61061EU582084R_cr_undefined_btz_Pacific_Hono_c2ac3c3d30.json` | 返回 `requestId`、`sealedResult`、`products.identification` |
| 70 | POST | `captures/roxy-paypal-20260705-103548/network/requests/00070_POST_www.paypal.com_pay_api_trpc_observability.handleClientEmit_token_BA-37R61061EU582084R.txt` | PayPal 页面上报 `dfp_completed_success_occurred` |
| 96 | POST | `captures/roxy-paypal-20260705-103548/network/requests/00096_POST_www.paypal.com_identity_di_log.txt` | DFPJS lifecycle log：`LIB_LOADED`、`VENDOR_INVOKED`、`VENDOR_RESPONSE_RECEIVED` 等 |

Roxy MTR POST URL：

```text
https://www.paypal.com/mtr/1a7c3460cd8c343771081839499ed7a0?chnl=iwc-mxo&cmid=BA-37R61061EU582084R&cr=undefined&btz=Pacific/Honolulu&emf=true&ci=js/3.12.1&q=QBzalmMuDFJIiZNebIWt
```

Roxy MTR response 关键结果：

| 字段 | 值/形态 |
|---|---|
| `requestId` | `1783218996246.qa8rTj` |
| `sealedResult` | 长度 2892 |
| `products.identification.data.result.meta.version` | `v1.1.4939+85b5cf445` |
| response `Set-Cookie` | `_iidt`、`ts`、`ts_c` |

### 2.2 Python 程序抓包层面的缺失

用下面口径检查所有 `program-paypal-*` 的 `network/requests.tsv`，没有发现 `/mtr/1a7c...`：

```bash
rg -l "/mtr/1a7c" captures/program-*/network/requests.tsv
```

实际检查结果为空。

同时已有程序对比报告把 Roxy 的 MTR 明确列为 “Missing in program”：

```text
captures/program-paypal-0cb91250ce90/traffic_diff_report.md
captures/program-paypal-337694e3c0d9/traffic_diff_report.md
captures/program-paypal-a3cebdb6e785/traffic_diff_report.md
captures/program-paypal-de9dfb17692b/traffic_diff_report.md
```

这些报告里均出现：

```text
- x1 GET www.paypal.com/mtr/1a7c3460cd8c343771081839499ed7a0/AvQ9/Gr6-8k/ViQEi/xLu1/x0
- x1 POST www.paypal.com/mtr/1a7c3460cd8c343771081839499ed7a0
```

说明历史 `program-paypal-*` 抓包不是“Python MTR body 不像”，而是程序实际流程没有发 MTR GET/POST。

## 3. 当前 Python 生成器已经做到的部分

当前 `paypal/mtr.py` 已不是旧文档里描述的 `MTR2` marker 方案。现有生成器已经做了以下对齐：

| 项 | Roxy `dfp.js` | 当前 Python `paypal/mtr.py` | 状态 |
|---|---|---|---|
| MTR base URL | `/mtr/1a7c3460cd8c343771081839499ed7a0` | 相同 | 已对齐 |
| x0 bootstrap | `/AvQ9/Gr6-8k/ViQEi/xLu1/x0?q=<apiKey>` | `mtr_get_url()` 相同 | 已对齐 |
| POST query | `chnl/cmid/cr/btz/emf/ci/q` | `mtr_post_url()` 相同 | 已对齐 |
| body marker | compressed `[3,14]` | `MTR_COMPRESSED_MARKER=(3,14)` | 已对齐 |
| 压缩方式 | `deflate-raw` | `zlib.compressobj(wbits=-15)` | 已对齐 |
| envelope | seed + marker + padding + 9-byte XOR key + XOR payload | `_mtr_envelope()` | 已对齐 |
| top-level key 数 | 135 | 135 | 已对齐 |
| `s*` signal 数 | 127 | 127 | 已对齐 |
| x0 token 字段 | `s56` | `s56` | 已对齐 |

核心代码位置：

- `paypal/mtr.py:94`：MTR base/path 常量。
- `paypal/mtr.py:102`：`MTR_COMPRESSED_MARKER=(3,14)`、`MTR_UNCOMPRESSED_MARKER=(3,13)`。
- `paypal/mtr.py:611`：`_deflate_raw()`。
- `paypal/mtr.py:616`：`_mtr_envelope()`。
- `paypal/mtr.py:866`：`_build_mtr_js_like_signals()`。
- `paypal/mtr.py:1355`：`build_mtr_request_object()`。
- `paypal/mtr.py:1382`：`serialize_mtr_body()`。

## 4. 解码对比方法

对比不是直接比 raw body bytes，因为 envelope 内有随机 seed、padding 和 9-byte XOR key；即使明文 JSON 完全相同，raw body 每次也会不同。

正确对比口径：

1. 解 Roxy body：按 `dfp.js` envelope 反解 marker、padding、XOR key。
2. 对 payload 做 `zlib.decompress(wbits=-15)`。
3. JSON parse 得到真实浏览器 signal object。
4. 调用当前 Python `build_mtr_request_object()` + `serialize_mtr_body()` 生成 body。
5. 用同一反解逻辑解 Python body。
6. 比较 decoded JSON 的字段集合和值。

本次使用两个 Python state 做对比：

| 对比口径 | 含义 |
|---|---|
| `empty/default state` | 只给同 page URL、同 apiKey、同 x0 token，其余使用 Python 默认值 |
| `roxy-fed best-case state` | 给 Python 注入 Roxy 样本里的 UA、Windows、timezone、GPU、screen、viewport、部分 canvas/WebGL/audio/x0/page URL，代表“尽量喂同样环境值”的最佳情况 |

## 5. Envelope 与尺寸对比

| 项 | Roxy `dfp.js` body | Python 默认 state | Python best-case state |
|---|---:|---:|---:|
| body bytes | 3236 | 3035 | 3156 |
| marker | `[3,14]` | `[3,14]` | `[3,14]` |
| compressed | true | true | true |
| XOR key length | 9 | 9 | 9 |
| encoded payload bytes | 3222 | 3021 | 3140 |
| decoded JSON bytes | 9735 | 9208 | 9461 |

解释：

- body 外壳已经接近；marker、压缩、key 长度都一致。
- Python body 比 Roxy 小，说明虽然字段集合相同，但若干字段值长度/结构仍不足，例如 Client Hints brand list、font metrics、DOM attributes、WebGL hash/parameters、window dimensions 等。
- body raw SHA 每次会因为随机 seed/padding/XOR key 和时间字段变化而变，不应用 raw SHA 判断是否一致。

## 6. 字段集合与值相似度

### 6.1 默认 Python state

| 指标 | Roxy | Python | 交集 | 值相同 | 值不同 |
|---|---:|---:|---:|---:|---:|
| top-level keys | 135 | 135 | 135 | 97 | 38 |
| `s*` signals | 127 | 127 | 127 | 90 | 37 |

默认 Python state 不同字段：

```text
ab, s2, s4, s5, s9, s15, s17, s20, s21, s29, s44, s45, s48, s49, s51,
s58, s70, s72, s74, s75, s76, s82, s83, s84, s92, s93, s94, s101, s103,
s104, s131, s145, s150, s154, s158, s163, s200, s202
```

默认 state 里 UA、language、timezone、platform、WebGL vendor 等也不同，因此差异较大。

### 6.2 Roxy-fed best-case state

| 指标 | Roxy | Python | 交集 | 值相同 | 值不同 |
|---|---:|---:|---:|---:|---:|
| top-level keys | 135 | 135 | 135 | 104 | 31 |
| `s*` signals | 127 | 127 | 127 | 97 | 30 |

best-case 仍不同字段：

```text
ab, s4, s5, s17, s20, s21, s29, s44, s45, s46, s48, s49, s50, s51,
s58, s70, s72, s75, s76, s84, s92, s93, s94, s104, s131, s145,
s150, s154, s158, s163, s200
```

这说明：即便把 UA、Windows、timezone、GPU、部分 canvas/WebGL/audio 值喂给 Python，仍然有一批 `dfp.js` 在真实 Chromium runtime 中现场采集的信号无法靠静态 profile 完整覆盖。

## 7. Best-case 剩余差异逐项分析

下面表格只列 best-case 仍不同的字段。字段语义依据 `dfp.js` 静态分析、当前 `paypal/mtr.py` 构造逻辑和实际值形态归类；部分 `sXX` 是 PayPal/DFP 内部编号，只能按值类型和采集来源判断类别。

| 字段 | Roxy 值形态 | Python 值形态 | 差异类别 | 影响判断 |
|---|---|---|---|---|
| `ab` | `{"noop":"b"}` | `{"noop":"a"}` | AB/实验分桶 | 低到中；字段小但服务端可见 |
| `s4` | `8` | `32` | 浏览器数值探针 | 中；当前硬编码与真实 runtime 不一致 |
| `s5` | `[864,1536]` | `[700,552]` | 屏幕/窗口尺寸 | 高；Python 使用了 viewport，Roxy 是屏幕高度/宽度形态 |
| `s17` | canvas geometry/text hash | Python 模板 hash | Canvas | 高；真实绘制结果依赖 Chromium/字体/GPU |
| `s20` | `Calibri, Franklin Gothic, MS UI Gothic, Marlett` | `Calibri, MT Extra, Marlett, Segoe UI Light, SimHei` | 字体可用性 | 高；字体列表与 Windows/Roxy 实机不一致 |
| `s21` | `124.68753614230081` | `124.04347527516074` | 字体/DOM 测量 | 中；浮点差异来自真实 layout engine |
| `s29` | `10737418240` | `7516192768` | memory/device numeric | 中；Python 公式值与真实采集值不同 |
| `s44` | `true` | `false` | browser feature/capability bool | 中；能力探测结果不一致 |
| `s45` | `[1783218995880,1783182995880]` | 当前生成时刻 pair | 时间戳 | 预期动态不同；需同页面时序而不是当前执行时刻 |
| `s46` | `cf845af5c17f8505dbe10c1afc548dcd` | 注入的 p2 `cv_sig` 长 hash | Canvas/hash mapping | 高；Python 把 FraudNet p2 canvas hash 映射到 MTR `s46` 并不等价 |
| `s48` | `[635367469,383400288,-1438994301,1390681093,-878569164,652581458]` | `[-2147483,...]` placeholder | 运行时数组探针 | 高；当前是明显占位值 |
| `s49` | `[0.09999999962747097,0.10000000149011612]` | 第二个 float 略不同 | 微时序 | 低到中；小差异但可被模型使用 |
| `s50` | `4395630592` | `4294705152` | JS heap limit | 中；真实 Chrome heap limit 与注入值不同 |
| `s51` | Windows/Roxy 字体 rect metrics | 固定模板 rect metrics | DOM/font layout | 高；真实字体栈和渲染引擎结果不同 |
| `s58` | brands 含 `Not;A=Brand`、`Chromium`、`Google Chrome` | brands 少 `Google Chrome` | Client Hints | 高；header 中有 Google Chrome，body 中缺会形成不一致 |
| `s70` | status `-1` | status `-4` | unavailable/error status | 中；不仅值不同，状态码语义也不同 |
| `s72` | `false` | `true` | feature/capability bool | 中；真实能力探测不一致 |
| `s75` | WebGL parameter/extension hashes | Python 默认 hash | WebGL | 高；依赖 GPU、ANGLE、extension 参数 |
| `s76` | `62ddb773bf1a1e091ec52608b66d0a5d` | p2 `webgl_ext_hash` 长 hash | WebGL hash mapping | 高；MTR hash 与 FraudNet p2 hash 不是同一字段 |
| `s84` | `{"w":1536,"h":864}` | `{"w":552,"h":700}` | screen vs viewport | 高；Python 当前明显取错语义，应用 screen 而非 viewport |
| `s92` | 真实 DOMRect：top=8、height≈23.2、width≈291.16 | 模板 DOMRect：top=10、height=17、width=265.51 | font/layout rect | 高；真实字体和 layout 结果 |
| `s93` | 真实 DOMRect：width≈1597.08、height≈23.2 | 模板 DOMRect：width≈1597.08、height=17 | font/layout rect | 中到高；部分宽度接近但 rect 高度/top 不一致 |
| `s94` | UUID `cda8af11-47ac-c14b-a3fe-49b58ec10ec3` | all-zero UUID | session/local storage id | 高；all-zero 是明显模板痕迹 |
| `s104` | `350` | `100` | numeric runtime probe | 中；当前硬编码不同 |
| `s131` | `data-ppui-mode, dir, lang` | `[]` | document/html attributes | 中；页面 DOM 状态缺失 |
| `s145` | navigator probe names 含 `sendBeacon` | 第 3 项为空字符串 | navigator API enumeration | 中；属性枚举与真实 Chromium 不一致 |
| `s150` | `outerWidth=1242, outerHeight=788, innerWidth=567, innerHeight=700` | `outerWidth=552, outerHeight=700, innerWidth=552, innerHeight=700` | window dimensions | 高；浏览器窗口外框/内框关系明显不同 |
| `s154` | `wv=true` 等 flags | `wv=false` | browser/webview/privacy flags | 中；环境分类不同 |
| `s158` | `false` | `true` | feature/capability bool | 中 |
| `s163` | `false` | `true` | feature/capability bool | 中 |
| `s200` | `1783218990723.1` | 当前生成时刻 `...` | performance/time origin | 高；应来自页面 runtime，而不是 Python 当前时间 |

## 8. 差异类别归纳

### 8.1 已经基本解决的差异

这些部分不再是主要问题：

- endpoint path/query 构造。
- x0 bootstrap URL 和 `s56` 写入。
- compressed marker `[3,14]`。
- `deflate-raw` 压缩。
- 9-byte XOR envelope。
- top-level key 集合。
- `s*` signal key 集合。
- UA 字段在 best-case 下可对齐。
- WebGL basic renderer/vendor 在 best-case 下可对齐到 Roxy 样本。

### 8.2 仍然明显不足的差异

剩余差异主要分四类：

1. **真实渲染/图形类**：`s17`、`s46`、`s75`、`s76`。
   - 需要真实 Canvas/WebGL/ANGLE/extension 参数。
   - 不能把 FraudNet p2 的 hash 直接当作 MTR 的 hash；两者采集函数和 hash 口径不同。

2. **字体/DOM layout 类**：`s20`、`s21`、`s51`、`s92`、`s93`、`s131`。
   - 依赖 Windows 字体栈、真实 DOMRect、页面 HTML attributes。
   - Python 模板字段会留下明显固定形态。

3. **窗口/屏幕/Client Hints 一致性类**：`s5`、`s58`、`s84`、`s150`。
   - Python 当前存在 screen/viewport 语义错配。
   - Client Hints body 内 brand list 和真实 header 不完全一致。

4. **页面时序/本地状态类**：`s45`、`s94`、`s200`。
   - 需要页面加载时的 `performance`、session/local storage、runtime UUID。
   - Python 当前时间和 all-zero UUID 都不像真实页面生命周期。

### 8.3 “可修”与“不值得纯 Python 修”的边界

可在 Python 模板中较容易修正的：

- `ab`: 改成 roxy 样本中的 `{"noop":"b"}`，但可能随实验变化。
- `s58`: 补齐 `Google Chrome` brand，确保和 header 的 `sec-ch-ua` / `sec-ch-ua-full-version-list` 一致。
- `s84`: 用 screen size，不要用 viewport。
- `s150`: 从 Roxy runtime profile 读取真实 `outerWidth/outerHeight/innerWidth/innerHeight`。
- `s94`: 生成非零 UUID，并与 sessionStorage 行为一致。
- `s145`: 按真实 Chromium navigator 属性列表补齐 `sendBeacon` 等。

不建议继续用纯 Python 模板硬修的：

- Canvas/WebGL hash：`s17/s46/s75/s76`。
- DOM/font rect：`s20/s21/s51/s92/s93`。
- performance/time origin：`s45/s200`。
- 页面 attributes/local lifecycle：`s131/s94` 的真实关联。

这些字段的可信来源是 Chromium runtime，不是 HTTP 客户端。

## 9. Roxy 运行时方案和 Python fallback 的差异

项目里已经实现 Roxy MTR 运行时：

- `paypal/mtr.py:1495`：`_send_mtr_with_roxy_browser()`。
- `paypal/roxy_fingerprint.py:1330`：`run_mtr_with_roxy_browser()`。

该路径会：

1. 连接 Roxy Chrome CDP。
2. 注入/复用当前 PayPal cookies。
3. 打开 PayPal page URL。
4. 从页面 DOM/RSC 读取真实 `dfpconfig`。
5. 等待页面自然执行 `dfp.js`。
6. 如果页面没有快速触发，则在 PayPal origin 注入 `dfpconfig` + `dfp.js`。
7. 监听 `/mtr/.../x0` 和 `/mtr?...` responses。
8. 提取 `requestId`、`sealedResult`、`visitorToken`。
9. 回灌浏览器 cookies，尤其 `_iidt`、`ts`、`ts_c` 等。

这条路径比继续补 Python 模板更接近 Roxy 抓包，因为它让同一份 PayPal `dfp.js` 在真实 Chromium 中运行。

## 10. 对主流程的影响

如果主流程实际使用 `python_generated` 或根本没有发 MTR：

- PayPal 后端看不到 `dfp_completed_success_occurred` 对应的真实 `/mtr` server result。
- 后续 GraphQL/sign-up 风控上下文缺少 MTR `sealedResult` 关联。
- 浏览器侧由 MTR response 设置的 `_iidt`、`ts`、`ts_c` 等 cookie 不会同步出现。
- 即便 Python fallback 能构造一个结构相似的 body，也仍可能因为 30 个 runtime signal 值不一致而无法达到真实浏览器可信度。

如果主流程使用 `roxy`：

- MTR request/response 由真实 PayPal JS 产生。
- `sealedResult` 是服务端基于真实 browser body 返回。
- cookie jar 可以和后续 Python 协议请求合并。
- 风险链路中的 UA、Client Hints、GPU、screen、timezone 更容易保持一致。

## 11. 建议优先级

### P0：确认实际程序流程产生 MTR

当前历史 `program-paypal-*` 抓包没有 `/mtr`，所以第一优先级是让实际流程产生 MTR，而不是继续优化模板。

建议运行时配置：

```bash
PAYPAL_FINGERPRINT_SOURCE=roxy
PAYPAL_MTR_RUNTIME=roxy
PAYPAL_DATADOME_MODE=roxy
PAYPAL_RISK_SIGNALS_MODE=roxy
```

执行后在新的 `captures/program-paypal-*` 里确认：

```text
GET  www.paypal.com/mtr/1a7c.../x0
POST www.paypal.com/mtr/1a7c...
```

并确认 response JSON 存在：

```text
requestId
sealedResult
products.identification.data.result.meta.version
```

### P1：用 Roxy runtime 作为主路径，Python generator 只作为 fallback

`python_generated` 当前最适合作为：

- 单元测试桩。
- 无浏览器环境下的降级方案。
- 用来验证 envelope/字段骨架。

它不适合承担严格风控场景下的主 MTR 生成。

### P2：如果仍要提升 Python fallback

优先修这些低成本、高可见差异：

1. `s84` 改用 screen size。
2. `s150` 从 runtime profile 传入 outer/inner dimensions。
3. `s58` 与真实 `sec-ch-*` headers 完全一致，补 `Google Chrome` brand。
4. `s94` 不要 all-zero UUID。
5. `s145` 用实际 Chromium navigator probe list。
6. `s20/s51/s92/s93` 至少从 Roxy runtime profile 回填，而不是固定模板。

但这仍不能替代真实 `dfp.js`，因为 Canvas/WebGL/font/performance 的采集口径需要和当前 PayPal 部署的 `dfp.js` 保持同步。

## 12. 最终判断

当前差距不是“Python body 格式不懂”这个阶段的问题了。格式层面已经接近，字段集合也已经对齐。

真正剩下的是两个问题：

1. **程序实际抓包还没发 MTR**：这是当前最大缺口。
2. **Python fallback 的 runtime 信号仍不像真实 Chromium**：best-case 仍有 31 个字段不同，集中在浏览器渲染、WebGL、字体、screen/window、performance、sessionStorage 类信号。

因此下一步最有效的是跑通并验证 `PAYPAL_MTR_RUNTIME=roxy` 的实际程序抓包，而不是继续扩大 Python 模板。
