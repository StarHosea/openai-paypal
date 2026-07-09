# PayPal 协议支付网页端

新增了一个本地网页控制台，入口文件：`web.py`。

## 启动

```bash
python web.py --host 127.0.0.1 --port 8080
```

然后打开：<http://127.0.0.1:8080>

## 可选：本地 Headless Playwright

网页端默认使用本地 Headless Playwright 为浏览器指纹、DataDome 和 MTR 读取真实 Chromium runtime。Playwright 仍是可选依赖，未放入基础 `requirements.txt`；如需使用默认 headless 模式，先安装：

```bash
pip install -r requirements.txt
pip install -r requirements-headless.txt
python -m playwright install chromium
```

不想安装 Playwright 时，可在网页下拉菜单把浏览器指纹/DataDome/MTR 切到程序随机生成、协议边缘模拟或程序模板生成；命令行/环境变量也可显式回退：

```bash
export PAYPAL_FINGERPRINT_SOURCE=random
export PAYPAL_DATADOME_MODE=protocol
export PAYPAL_MTR_RUNTIME=python_generated
```

## 功能

- 输入 `BA Token`、手机号、最大换卡次数后启动任务。
- 可勾选 `使用 SMSBower 自动接码`：程序会取最低价 PayPal 巴西号码，优先复用未到期且成功接码的号码；复用号码超时无码会放弃并取新号码，同一供应商连续失败后切到下一个价格渠道。
- 可在启动任务时动态勾选是否启用代理；代理来源可选环境变量代理，或手动填写自定义链式代理 URL。
- 可在启动任务时勾选“测试：自动记录程序发包”，并可填写 roxy 抓包目录，任务结束后自动生成差异报告。
- 后台自动生成用户、卡片、巴西地址等资料。
- 实时显示任务阶段、日志、生成资料和最终结果。
- 执行到短信验证时，网页会暂停并显示验证码输入框。
- 验证码错误后可继续输入验证码；也可以输入新手机号重新发送。
- 输入 `q` / `quit` / `exit` 可结束当前验证码流程。

原来的命令行入口 `main.py` 保持不变，也可通过 `--smsbower` 使用自动接码。

## SMSBower 自动接码

在 `.env` 或环境变量中配置 API key：

```bash
SMSBOWER_API_KEY="..."
SMSBOWER_WAIT_SECONDS=30
```

网页端勾选 `使用 SMSBower 自动接码` 后，手机号可留空。命令行入口可用：

```bash
python main.py --ba-token BA-xxx --smsbower
```

可选参数/环境变量：

- `SMSBOWER_POLL_INTERVAL_SECONDS`：轮询间隔，默认 `2`
- `SMSBOWER_MAX_CHANNEL_FAILURES`：同一供应商失败阈值，默认 `3`
- `SMSBOWER_ACTIVATION_TTL_SECONDS`：成功号码复用窗口，默认 `1200`
- `SMSBOWER_MAX_ATTEMPTS`：完整接码尝试次数，默认 `12`

## PayPal 安全挑战处理

如果 PayPal 返回 `authchallenge`、reCAPTCHA 或 hCaptcha，程序会停止当前协议流程并返回
`AUTHCHALLENGE_MANUAL_VERIFICATION_REQUIRED`。外部 CAPTCHA 打码功能已移除；
`PAYPAL_CAPTCHA_BYPASS_MODE=capsolver`、`CAPSOLVER_API_KEY` 等配置不会触发第三方
solver 调用。

如果 checkoutweb/signup 页面无法刷新 `contentIdentifier`，可显式指定或复用最近一次成功抓到的缓存：

```bash
export PAYPAL_SIGNUP_CONTENT_IDENTIFIER="BR:pt:<contentHash>:compliance.signupTerms"
# 或
export PAYPAL_SIGNUP_CONTENT_HASH="<contentHash>"
# 可选：自定义持久缓存文件；默认同时读写 /tmp/paypal_signup_content_manifest_last.json
# 和 ./cache/paypal_signup_content_manifest_last.json
export PAYPAL_SIGNUP_CONTENT_CACHE="/path/to/paypal_signup_content_manifest_last.json"
```

抓包目录可用下面命令反查 `contentIdentifier` 来源：

```bash
python3 tools/analyze_paypal_content_identifier.py captures/roxy-paypal-20260705-103548
```

## 程序侧发包记录与抓包对比

测试模式会把程序侧所有通过 `PayPalSession` 发出的请求/响应保存到 roxy 类似目录，方便和浏览器抓包对比：

网页端直接在“启动新任务”表单中打开：

- 勾选 `测试：自动记录程序发包`
- `程序发包记录目录` 可留空，默认写入 `captures/program-paypal-<job_id>`；目录会在任务创建时立即返回前端
- `完成后自动对比 roxy 抓包目录` 可填写已有 roxy 抓包目录，任务结束后自动生成报告

记录不是等流程结束才落盘：每个 request/response 会实时追加到 `network/events.jsonl`、`network/requests.tsv`，请求体实时写入 `network/requests/`，响应体实时写入 `network/bodies/`，`summary.json` 也会随每次请求/响应刷新。

```bash
python3 main.py --ba-token BA-xxx --phone +55... --record-traffic --traffic-dir captures/program-paypal-test
```

如果已知 roxy 抓包目录，也可以跑完后自动生成对比报告：

```bash
python3 main.py --ba-token BA-xxx --phone +55... \
  --record-traffic \
  --traffic-dir captures/program-paypal-test \
  --compare-roxy-capture captures/roxy-paypal-20260705-103548
```

也可以用环境变量打开：

```bash
export PAYPAL_TRAFFIC_RECORD=1
export PAYPAL_TRAFFIC_RECORD_DIR=captures/program-paypal-test
```

完成一次程序流程后，对比浏览器抓包：

```bash
python3 tools/compare_paypal_traffic.py \
  --program captures/program-paypal-test \
  --roxy captures/roxy-paypal-20260705-103548
```

报告会写入：

```text
captures/program-paypal-test/traffic_diff_report.json
captures/program-paypal-test/traffic_diff_report.md
```

记录目录会包含原始请求/响应体，里面可能有 token、手机号、卡信息等运行数据；不要提交到仓库。

## 生产部署注意事项

建议生产环境使用反向代理提供 HTTPS，并设置：

```bash
export PAYPAL_WEB_PRODUCTION=1
export PAYPAL_WEB_COOKIE_SECURE=1
python web.py --host 127.0.0.1 --port 8080
```

当前网页端已做以下生产收敛：

- 多用户隔离：每个任务绑定到创建它的浏览器设备，其他设备看不到任务，也不能提交验证码。
- 并发保护：默认全局最多 4 个执行槽，每个设备最多 2 个未完成任务；可用 `PAYPAL_WEB_MAX_ACTIVE_JOBS`、`PAYPAL_WEB_MAX_ACTIVE_JOBS_PER_DEVICE` 调整。
- 日志脱敏/降噪：网页端默认不显示 DEBUG；token、邮箱、手机号、CPF、卡号、CVV、密码、URL 参数会脱敏， traceback 默认不返回前端。
- 安全头：已添加 CSP、X-Frame-Options、nosniff、Referrer-Policy；POST 会校验同源 Origin/Referer。

## 代理开关与自定义环境代理

默认代理关闭。可通过网页端“启用代理”勾选框按任务开启/关闭：

- 代理来源选 `环境变量代理`：每个任务会使用 `PAYPAL_PROXY_URL` 或 `PAYPAL_PROXY_POOL`。
- 代理来源选 `自定义链式代理 URL`：每次任务可以手动填写 `http://user:pass@host:port`、`https://user:pass@host:port` 或同等格式的代理 URL；页面和日志只显示脱敏后的代理地址。

不要把代理账号密码提交到代码中，请用网页输入框、环境变量或部署平台 secret manager 注入。

命令行入口也支持：

```bash
python main.py --ba-token BA-xxx --phone +5591980133818 --proxy
python main.py --ba-token BA-xxx --phone +5591980133818 --no-proxy
```

如需指定代理池里的固定序号（从 `0` 开始）：

```bash
python main.py --ba-token BA-xxx --phone +5591980133818 --proxy --proxy-index 0
```

命令行也可临时指定自定义链式代理：

```bash
python main.py --ba-token BA-xxx --phone +5591980133818 --proxy-url "http://user:pass@host:port"
```

用环境变量配置代理：

- `PAYPAL_PROXY_ENABLED=1`：命令行未传 `--proxy/--no-proxy` 时默认开启。
- `PAYPAL_PROXY_URL="http://user:pass@host:port"`：使用单个代理 URL。
- `PAYPAL_PROXY_POOL="host:port:user:pass,host:port:user:pass"`：配置自定义代理池。

## 浏览器指纹来源

命令行/config 默认仍使用程序内随机指纹；网页端默认使用本地 Headless Playwright。也可以切换到 RoxyBrowser Local API：程序会创建一个随机指纹 Profile，默认按 `headless=true` 打开无头窗口，通过 CDP 读取真实 Chromium runtime 中的 UA、screen、viewport、canvas、WebGL、audio、JS heap、connection 等字段，再关闭并删除临时 Profile。当前示例为直接打开，不会先 close 再重新打开。Web 前端/CLI 启用代理时，同一个 `ProxyConfig.url` 会同步写入 Roxy `proxyInfo`，确保协议请求和 Roxy 浏览器使用同一代理 IP；前端未启用代理时会显式传空代理，Roxy `proxyInfo` 为 `noproxy`，不会使用 `PAYPAL_ROXY_PROXY_URL` 兜底代理。

`.env` 示例：

```bash
PAYPAL_FINGERPRINT_SOURCE=headless # random | headless | roxy | auto
PAYPAL_ROXY_API_KEY="..."
PAYPAL_ROXY_API_HOST=127.0.0.1
PAYPAL_ROXY_API_PORT=50000
PAYPAL_ROXY_HEADLESS=1
PAYPAL_ROXY_FORCE_OPEN=0
PAYPAL_ROXY_CLOSE_BEFORE_OPEN=0
PAYPAL_ROXY_FINGERPRINT_FALLBACK=random
# Roxy Local API 没启动/端口拒绝连接时，当前任务自动降级到 protocol/python，避免直接失败
PAYPAL_ROXY_RUNTIME_FALLBACK=1

# DataDome 两种方法保留，通过开关选择：
# protocol = 原来的 clientid/header 边缘模拟
# headless = 本机 Playwright headless Chromium 真实加载 PayPal/DataDome 并回灌 cookie
# roxy     = 同一个 Roxy 指纹浏览器真实加载 PayPal/DataDome 并回灌 cookie
# auto     = 优先 roxy，失败回退 protocol
PAYPAL_DATADOME_MODE=headless
PAYPAL_DATADOME_ROXY_WAIT_SECONDS=12

# MTR sealedResult 两种方法保留，通过开关选择：
# python_generated = 原来的协议模板提交
# headless         = 本机 Playwright headless Chromium 运行 dfp.js，监听真实 /mtr 响应并回灌 sealedResult
# roxy             = 同一个 Roxy 指纹浏览器运行 dfp.js，监听真实 /mtr 响应并回灌 sealedResult
# auto             = 优先 roxy，失败回退 python_generated
PAYPAL_MTR_RUNTIME=headless
PAYPAL_MTR_ROXY_WAIT_SECONDS=20

# signup-context browser risk 运行模式；主流程已不再执行独立 Phase 1 风控信号步骤。
# SignUpNewMemberMutation 前仍会按该模式执行 signup-context browser risk；
# roxy 可用时优先复用 roxy，否则使用本机 headless。
# 旧的 PAYPAL_ENABLE_SIGNUP_CONTEXT_RISK 不再是生产关闭开关。
PAYPAL_RISK_SIGNALS_MODE=roxy      # roxy | headless | auto | off
PAYPAL_RISK_ROXY_WAIT_SECONDS=18
PAYPAL_ROXY_INTERACTION_PROFILE=normal  # subtle | normal | active | off
# 可选细调；默认只做 move/wheel/pause，不点击、不输入：
# PAYPAL_ROXY_INTERACTION_MOVES_MIN=7
# PAYPAL_ROXY_INTERACTION_MOVES_MAX=14
# PAYPAL_ROXY_INTERACTION_WHEELS_MIN=2
# PAYPAL_ROXY_INTERACTION_WHEELS_MAX=4
# PAYPAL_ROXY_INTERACTION_THINK_MIN_MS=350
# PAYPAL_ROXY_INTERACTION_THINK_MAX_MS=1400
# PAYPAL_ROXY_INTERACTION_PAUSE_MIN_MS=80
# PAYPAL_ROXY_INTERACTION_PAUSE_MAX_MS=620
```

命令行也可临时覆盖：

```bash
python main.py --ba-token BA-xxx --phone +5591980133818 --fingerprint-source roxy
python main.py --ba-token BA-xxx --phone +5591980133818 --fingerprint-source random
python main.py --ba-token BA-xxx --phone +5591980133818 --fingerprint-source roxy --datadome-mode roxy
python main.py --ba-token BA-xxx --phone +5591980133818 --fingerprint-source roxy --mtr-runtime roxy
```
