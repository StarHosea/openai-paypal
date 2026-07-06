# MTR `sealedResult` 在真实浏览器中的生成链路分析

分析对象：

- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-102145`
- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-103548`

## 1. 结论

真实浏览器里的 MTR 不是主流程 Python/http 协议代码直接拼出来的。它由 PayPal 页面加载的浏览器脚本：

```text
https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js
```

在 Chromium runtime 中读取页面的 `dfpconfig` 后自动执行：

1. 读取页面配置：`dfpChannel`、`clientMetaDataId`、`fppAPIKey`、`isQA`。
2. 构造 `/mtr/1a7c.../x0` GET bootstrap 请求，拿到 96 bytes base64 token。
3. 在真实浏览器内采集大量 device/browser/bot/session 信号。
4. 把采集结果 JSON 序列化为 bytes。
5. 大于 1024 bytes 且支持 `CompressionStream` 时，使用 `deflate-raw` 压缩。
6. 再用 `dfp.js` 自定义 envelope 做随机 padding + 9 bytes XOR key 封装。
7. 用 XHR `POST text/plain` 提交到 `/mtr/1a7c...`。
8. PayPal 服务端返回 JSON：`requestId`、`sealedResult`、`products.identification.data.result`。
9. `dfp.js` 触发 `dfp-completed-check`，并写入 `sessionStorage` 去重标记。

因此：

- `sealedResult` 是服务端返回，不是本地 JS 直接生成。
- 客户端真正要“生成并提交”的是 MTR POST body。
- POST body 强依赖真实 Chromium runtime，不能用当前 `paypal/mtr.py` 的模板/XOR 协议等价复刻。

## 2. 抓包证据

### 2.1 MTR 请求序列

`roxy-paypal-20260705-103548`：

| ID | Method | URL/用途 | 响应 |
|---:|---|---|---|
| 6 | GET | `www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js` | 200 script |
| 54 | GET | `/mtr/1a7c.../AvQ9/Gr6-8k/ViQEi/xLu1/x0?q=QBzalmMuDFJIiZNebIWt` | 200 text/plain, 96 bytes |
| 66 | POST | `/mtr/1a7c...?chnl=iwc-mxo&cmid=BA-37R61061EU582084R&cr=undefined&btz=Pacific/Honolulu&emf=true&ci=js/3.12.1&q=QBzalmMuDFJIiZNebIWt` | 200 JSON, 含 `sealedResult` |

`roxy-paypal-20260705-102145` 中出现三组同类链路：

| Page | dfp.js | x0 GET | MTR POST | cmid |
|---:|---:|---:|---:|---|
| 3 | 10 | 62 | 74 | `BA-65R58767795383013` |
| 4 | 213 | 265 | 281 | `BA-65R58767795383013` |
| 5 | 410 | 452 | 469 | `BA-4PT6003892106623L` |

### 2.2 页面入口配置

页面 HTML 内存在：

```html
<script id="dfpconfig" type="application/json">
{"dfpChannel":"iwc-mxo","clientMetaDataId":"BA-...","isQA":false,"fppAPIKey":"QBzalmMuDFJIiZNebIWt"}
</script>
```

同时页面 preload/加载：

```text
https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js
```

两个抓包中的该脚本 SHA256 一致：

```text
e5d12d0aac6c1ccd279fc733f5e31842938c890faf46b4c2451019b87e17510c
```

### 2.3 x0 bootstrap 响应

所有 x0 GET 响应一致，96 bytes：

```text
s+7ltUQKtn0tpAcipcu/L0wizaHXIBNAsIzdUjf7hMNqL3eepihr+wsv6u8xutiVmOHmxDXkCa/KksEjO+rjSHv59CyUSA==
```

解码后的 MTR POST JSON 中，该值进入：

```json
"s56": {
  "s": 0,
  "v": "s+7ltUQKtn0tpAcipcu/L0wizaHXIBNAsIzdUjf7hMNqL3eepihr+wsv6u8xutiVmOHmxDXkCa/KksEjO+rjSHv59CyUSA=="
}
```

### 2.4 POST 响应中的 `sealedResult`

| Capture | POST ID | requestId | sealedResult 长度 | identification meta |
|---|---:|---|---:|---|
| `102145` | 74 | `1783218109947.a50E5h` | 2884 | `v1.1.4939+85b5cf445` |
| `102145` | 281 | `1783218155308.OiQcta` | 2904 | `v1.1.4939+85b5cf445` |
| `102145` | 469 | `1783218226752.ap4E8N` | 2952 | `v1.1.4939+85b5cf445` |
| `103548` | 66 | `1783218996246.qa8rTj` | 2892 | `v1.1.4939+85b5cf445` |

响应还设置风险相关 cookie，例如 `_iidt`、`ts`、`ts_c`。

## 3. `dfp.js` 入口逻辑

脚本入口等价逻辑如下：

```js
let r = window.PAYPAL?.dfpData;
if (!r) {
  const n = document.getElementById("dfpconfig");
  r = JSON.parse(n?.textContent ?? "{}");
}

const apiKey = r?.fppAPIKey;
const channel = r?.dfpChannel;
const cmid = r?.clientMetaDataId;
const csrfNonce = r?.csrfNonce;
const isQA = r?.isQA;
const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
```

生产环境构造：

```text
POST /mtr/1a7c3460cd8c343771081839499ed7a0
  ?chnl=<dfpChannel>
  &cmid=<clientMetaDataId>
  &cr=<csrfNonce or undefined>
  &btz=<browser timezone>
  &emf=true
  &ci=js/3.12.1
  &q=<fppAPIKey>

GET /mtr/1a7c3460cd8c343771081839499ed7a0/AvQ9/Gr6-8k/ViQEi/xLu1/x0
  ?q=<fppAPIKey>
```

本次真实环境中：

```text
chnl=iwc-mxo
cr=undefined
btz=Pacific/Honolulu
emf=true
ci=js/3.12.1
q=QBzalmMuDFJIiZNebIWt
```

## 4. POST body 解码后证明它是浏览器采集结果

对四个真实 POST body 按 `dfp.js` envelope 反解：

| POST | body bytes | marker | compressed | raw JSON bytes | top-level keys | `s*` signals |
|---:|---:|---|---|---:|---:|---:|
| `103548#66` | 3236 | `[3,14]` | yes | 9735 | 135 | 128 |
| `102145#74` | 4024 | `[3,14]` | yes | 11664 | 135 | 128 |
| `102145#281` | 4021 | `[3,14]` | yes | 11662 | 135 | 128 |
| `102145#469` | 4085 | `[3,14]` | yes | 11768 | 135 | 128 |

`[3,14]` 对应 `dfp.js` 里的 compressed marker；`[3,13]` 是未压缩 marker。

`103548#66` 解出来的顶层关键字段示例：

```json
{
  "c": "QBzalmMuDFJIiZNebIWt",
  "m": "s",
  "mo": ["id", "bd", "si"],
  "gt": 1,
  "ab": {"noop": "b"},
  "url": "https://www.paypal.com/pay?ssrt=1783218992321&token=BA-37R61061EU582084R&ul=1",
  "s56": {"s": 0, "v": "s+7ltUQKtn0tpAcipcu/L0wizaHXIBNAsIzdUjf7hMNqL3eepihr+wsv6u8xutiVmOHmxDXkCa/KksEjO+rjSHv59CyUSA=="},
  "s55": {"s": -1, "v": null},
  "s48": {"s": 0, "v": [635367469, 383400288, -1438994301, 1390681093, -878569164, 652581458]}
}
```

这说明 POST body 是多模块浏览器指纹报告，不是简单把 `dfpconfig` 编码一下。

## 5. body envelope 算法形态

`dfp.js` 中关键函数关系：

```text
To(...)  -> 构建浏览器信号对象
De(...)  -> 自定义 JSON serializer，得到 UTF-8 bytes
Ho(...)  -> body > 1024 && CompressionStream 可用
Wo(...)  -> CompressionStream("deflate-raw") / fallback deflate
Zo(...)  -> 自定义 binary envelope
eo(...)  -> marker + random padding + 9-byte XOR key + payload
Zr(...)  -> XHR POST, Content-Type: text/plain, withCredentials=true
br(...)  -> 解析响应中的 requestId/sealedResult/products
```

封装核心形态：

```text
byte0: random f
byte1..2: f + marker bytes，compressed 为 [3,14]，uncompressed 为 [3,13]
byte3: f + padding_length，padding_length 为 0..3
next: random padding
next 9 bytes: random XOR key
rest: payload_byte XOR key[i % 9]
```

真实抓包全部为 compressed `[3,14]`，说明 Chromium 支持 `CompressionStream` 且 JSON payload 超过 1024 bytes。

## 6. 采集内容类别

入口实际传入模块：

```js
modules: [ id, bd, si, _o() ]
```

其中：

- `id`：基础识别与环境特征。
- `bd`：bot/headless/automation 检测。
- `si`：session/integration 信号。
- `_o()`：本地事件/生命周期记录。

采集覆盖：

- `navigator` / UA / language / plugins / mimeTypes / connection / hardwareConcurrency / deviceMemory。
- `screen` / viewport / media query。
- Canvas 绘制、文本、emoji、blend/composite、`toDataURL()`。
- WebGL/WebGL2、ANGLE、GPU vendor/renderer、extension、precision。
- AudioContext / OfflineAudioContext 渲染差异。
- Fonts / DOM layout 测量。
- Permissions / Notification。
- Storage / cookie / sessionStorage / localStorage。
- WebDriver、Selenium、ChromeDriver `$cdc_*`、Phantom/Nightmare/CEF 等自动化痕迹。
- 原型链、函数 `toString()`、Error stack 格式一致性。
- adblock selector 探测。
- Performance/timing/visibility/resource lifecycle。

这些字段必须在真实浏览器里跑出来；纯 HTTP 客户端没有这些 runtime 数据。

## 7. 完成状态

MTR 成功后，`dfp.js` 会触发：

```js
document.dispatchEvent(new CustomEvent("dfp-completed-check", {
  detail: { cmid: "BA-...", dfpCompleted: true }
}));
```

并写入：

```text
sessionStorage["4g3-fd7gc5k5"] = true
sessionStorage["4g3-fd7gc5k5-CMID"] = btoa(cmid)
```

如果同一 tab/session 下 CMID 已完成，脚本只派发完成事件，不重复提交 MTR；如果 CMID 改变，清理旧标记并重新跑。

## 8. 当前主流程差距

当前 `/home/nonewhite/paypal-pay/paypal/mtr.py` 做了：

- 提取 `dfpconfig`。
- 拼 GET/POST URL。
- 可选 Python/template 方式构造一个形似 payload。
- `serialize_mtr_body()` 使用自定义 `MTR2` marker + seed XOR。

但这与真实 `dfp.js` 不一致：

| 项 | 真实浏览器 | 当前 Python 逻辑 |
|---|---|---|
| 运行位置 | PayPal 页面里的 Chromium JS runtime | requests/httpx 主流程 |
| JS 来源 | `dfp.js` 真实脚本 | Python 模板/伪结构 |
| body marker | `[3,14]` / `[3,13]` | `MTR2` |
| 压缩 | `CompressionStream("deflate-raw")` | 无真实等价 |
| 信号来源 | Canvas/WebGL/Audio/DOM/automation/timing 等真实 API | profile/screen/viewport 模板 |
| 完成事件 | `dfp-completed-check` | 无浏览器事件 |
| sessionStorage 去重 | `4g3-fd7gc5k5*` | 无真实页面生命周期 |
| `sealedResult` | 服务端对真实 body 返回 | 缺失或不可依赖 |

所以严格模式下正确处理方式不是继续完善 Python 模板，而是增加“真实浏览器 MTR 阶段”。

## 9. 建议接入方式

主流程需要在协议流前/中插入真实 Chromium/Playwright 阶段：

1. 用同一代理、同一 UA/client hints、同一 cookie jar 打开 PayPal `/pay?...token=BA...` 页面。
2. 等待 `dfp.js` 加载执行。
3. 监听网络请求：
   - `/mtr/1a7c.../x0`
   - `/mtr/1a7c...?chnl=...`
4. 捕获 POST 响应 JSON，提取：
   - `requestId`
   - `sealedResult`
   - response `Set-Cookie`，尤其 `_iidt`、`ts`、`ts_c`
5. 等待 `dfp-completed-check` 或 `sessionStorage["4g3-fd7gc5k5"] === "true"`。
6. 把浏览器 cookie jar、`mtr_request_id`、`mtr_sealed_result` 同步回 flow state。
7. 严格模式下如果没有 `sealedResult`，阻断后续支付协议提交。

关键点：不要单独复制旧抓包中的 `sealedResult`。它和 cmid、cookie、浏览器 runtime、server session、时间窗绑定；复制没有复用价值。
