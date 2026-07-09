# paypal-pay

PayPal Billing Agreement 自动化运行工具，支持命令行和本地 Web UI 两种入口。项目会为每个任务生成运行所需的用户、账单地址、卡信息，并按配置使用本地 headless 浏览器或 RoxyBrowser 运行浏览器侧信号。

## 目录结构

```text
paypal-pay/
├── main.py                    # CLI 入口
├── web.py                     # 本地 Web UI 入口
├── config.py                  # 默认浏览器/地区/运行时配置
├── requirements.txt           # 基础依赖
├── requirements-headless.txt  # Playwright/headless 依赖
├── paypal/                    # 核心流程代码
├── web_static/                # Web UI 静态文件
├── tools/                     # 可选诊断/对比工具
└── tests/                     # 自动化测试
```

运行时产生的 `.env`、`cache/`、`var/`、`debug/`、`captures/` 默认不会提交到 Git。

## 环境要求

- Python 3.11+
- 可访问目标网络的运行环境
- 可选：Chrome/Chromium 或 Playwright Chromium
- 可选：RoxyBrowser Local API
- 可选：SMSBower API Key，用于自动接收短信验证码

## 安装

```bash
cd /path/to/paypal-pay
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-headless.txt
python -m playwright install chromium
```

只运行协议模式时可以不安装 `requirements-headless.txt`；生产推荐安装 headless 依赖。

> **运行模式建议**：纯 Python/协议模拟的风控信号仍未完全完善，`random`、`python_generated`、`protocol` 等模式仅建议用于调试或回退。生产环境建议优先使用本地 `headless`（Playwright/Chromium）或 `roxy`（RoxyBrowser）执行浏览器侧指纹、DataDome、MTR 和 signup-context risk signals。

## 配置 `.env`

可以先复制示例配置：

```bash
cp .env.example .env
```

然后按实际环境填写 `.env`。不要提交真实 `.env`。

### 推荐生产配置：本地 headless

```env
PAYPAL_PROXY_ENABLED=1
PAYPAL_PROXY_URL=http://user:pass@host:port

PAYPAL_FINGERPRINT_SOURCE=headless
PAYPAL_DATADOME_MODE=headless
PAYPAL_MTR_RUNTIME=headless
PAYPAL_RISK_SIGNALS_MODE=headless

PAYPAL_HEADLESS=1
PAYPAL_HEADLESS_MAX_CONCURRENCY=1
PAYPAL_HEADLESS_DEBUG=0
PAYPAL_HEADLESS_OPTIMIZED_DEBUG=0
PAYPAL_HEADLESS_DEBUG_RAW=0
PAYPAL_HEADLESS_OPTIMIZED_DEBUG_RAW=0

PAYPAL_ROXY_FORCE_TEMP_WORKSPACE=0
PAYPAL_ROXY_AUTO_CREATE_WORKSPACE=0
PAYPAL_ROXY_DELETE_AUTO_WORKSPACE=0
```

### 使用 RoxyBrowser

确保 RoxyBrowser 已登录并启用 Local API：

```env
PAYPAL_FINGERPRINT_SOURCE=roxy
PAYPAL_DATADOME_MODE=roxy
PAYPAL_MTR_RUNTIME=roxy
PAYPAL_RISK_SIGNALS_MODE=roxy

PAYPAL_ROXY_API_KEY=your_roxy_api_key
PAYPAL_ROXY_API_HOST=127.0.0.1
PAYPAL_ROXY_API_PORT=50000
PAYPAL_ROXY_HEADLESS=1

# 生产不要自动创建团队/workspace
PAYPAL_ROXY_FORCE_TEMP_WORKSPACE=0
PAYPAL_ROXY_AUTO_CREATE_WORKSPACE=0
PAYPAL_ROXY_DELETE_AUTO_WORKSPACE=0

# 如果 Local API 无法自动返回 workspace，可固定已有 workspace
# PAYPAL_ROXY_WORKSPACE_ID=123456
# PAYPAL_ROXY_PROJECT_ID=123456
```

### SMSBower 自动短信

```env
SMSBOWER_ENABLED=1
SMSBOWER_API_KEY=your_smsbower_api_key
SMSBOWER_WAIT_SECONDS=30
```

也可以不配置 SMSBower，运行时通过 `--phone` 使用手动验证码。

## CLI 使用

手动手机号/验证码：

```bash
python main.py \
  --ba-token BA-xxxxxxxxxxxxxxxx \
  --phone +5591999999999 \
  --fingerprint-source headless \
  --datadome-mode headless \
  --mtr-runtime headless \
  --risk-signals-mode headless \
  --proxy
```

SMSBower 自动取号：

```bash
python main.py \
  --ba-token BA-xxxxxxxxxxxxxxxx \
  --smsbower \
  --fingerprint-source headless \
  --datadome-mode headless \
  --mtr-runtime headless \
  --risk-signals-mode headless \
  --proxy
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--ba-token` | Billing Agreement token，必填 |
| `--phone` | 手动手机号，未启用 SMSBower 时必填 |
| `--smsbower` | 启用 SMSBower 自动取号和收码 |
| `--proxy` / `--no-proxy` | 启用或禁用代理 |
| `--proxy-url` | 本次运行指定代理 URL 或 `host:port:user:pass` |
| `--proxy-index` | 从 `PAYPAL_PROXY_POOL` 选择指定代理 |
| `--fingerprint-source` | `headless`、`roxy`、`random`、`auto` 等 |
| `--datadome-mode` | `headless`、`roxy`、`protocol`、`auto`、`off` |
| `--mtr-runtime` | `headless`、`roxy`、`python_generated`、`auto`、`off` |
| `--risk-signals-mode` | `headless`、`roxy`、`protocol`、`auto`、`off` |
| `--max-card-attempts` | 卡提交失败后的换卡重试次数 |
| `--max-flow-attempts` | 完整流程重试次数，默认 1 |
| `--debug` | CLI 输出 DEBUG 日志 |

## Web UI 使用

启动本地 Web UI：

```bash
python web.py --host 127.0.0.1 --port 8080
```

浏览器打开：

```text
http://127.0.0.1:8080
```

生产反向代理示例环境变量：

```env
PAYPAL_WEB_PRODUCTION=1
PAYPAL_WEB_COOKIE_SECURE=1
PAYPAL_WEB_ALLOW_DEBUG_LOGS=0
PAYPAL_WEB_MAX_ACTIVE_JOBS=4
PAYPAL_WEB_MAX_ACTIVE_JOBS_PER_DEVICE=2
PAYPAL_WEB_JOB_RETENTION_SECONDS=86400
PAYPAL_WEB_OTP_TIMEOUT_SECONDS=1800
```

如果通过 HTTP 本地访问，`PAYPAL_WEB_COOKIE_SECURE` 保持 `0`；通过 HTTPS 反向代理访问时设为 `1`。

## 代理配置

单代理：

```env
PAYPAL_PROXY_ENABLED=1
PAYPAL_PROXY_URL=http://user:pass@host:port
```

代理池：

```env
PAYPAL_PROXY_ENABLED=1
PAYPAL_PROXY_POOL=http://u1:p1@h1:10000,http://u2:p2@h2:10000
```

CLI 可用 `--proxy-index 0` 固定某个代理，或 `--proxy-url ...` 覆盖 `.env`。

## 日志和生产空间控制

默认生产行为：

- 不写 headless 调试目录。
- 不写原始请求/响应 body。
- 不在 INFO 日志输出 `debug_log=...` 路径。
- `captures/`、`debug/`、`cache/`、`var/` 均被 Git 忽略。

需要排障时再临时开启：

```env
PAYPAL_HEADLESS_DEBUG=1
PAYPAL_HEADLESS_DEBUG_DIR=debug/headless
# 只有需要原始 body 时才开启
PAYPAL_HEADLESS_DEBUG_RAW=1
```

程序侧流量记录默认关闭；需要对比时手动开启：

```bash
python main.py --ba-token BA-xxx --phone +5591999999999 --record-traffic
```

记录会写入 `captures/`，不会提交到 Git。

## 测试

```bash
pytest -q
```

当前测试覆盖代理解析、模型生成、SMSBower、headless/Roxy 交互、核心流程顺序和浏览器运行时结果处理。

## 部署建议

1. 拉取代码并安装依赖。
2. 创建 `.env`，填写代理、运行时、SMS/Roxy 配置。
3. 生产默认保持调试落盘关闭。
4. 运行 `pytest -q` 做部署前检查。
5. 用 CLI 跑单任务，确认代理、短信和浏览器运行时正常。
6. 启动 `web.py` 并放到进程管理器或反向代理后面。
