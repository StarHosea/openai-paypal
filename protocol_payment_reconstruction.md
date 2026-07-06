# PayPal → Stripe/OpenAI 协议支付重构方案（基于 roxy 文档）

本文基于：

- `/home/nonewhite/paypal-pay/full_flow_analysis.md`
- `/home/nonewhite/paypal-pay/manual_path_analysis.md`

目标不是把抓包里的请求逐条硬编码复发，而是把流程重构成“浏览器驱动、状态机编排、关键业务包可观测”的支付复现方案。PayPal/Stripe 的风控、captcha、device fingerprint、telemetry 由当前真实浏览器/SDK 现场生成；协议层只消费页面/接口返回的会话状态，不伪造一次性风险证明。

## 1. 总体结论

抓包证明主链路是：

```text
OpenAI/Stripe 创建 Checkout/BA
  → PayPal /agreements/approve?ba_token=BA-*
  → PayPal /pay ModXO 初始化与风控
  → captcha/passive challenge/国家 BR
  → /checkoutweb/signup?ba_token=BA-*&token=EC-*
  → Weasley GraphQL 初始化
  → 地址/分期/OTP/signup
  → signup 绑卡失败后 fallback 到 Hermes/Hagrid review
  → GraphQL authorize
  → pm-redirects.stripe.com/return?...status=success&token=EC-*&ba_token=BA-*
  → pay.openai.com/c/pay/{cs_*}?redirect_status=failed
  → Stripe payment_pages init 返回 paypal_payment_declined / PAYER_CANNOT_PAY
```

最终失败点不是页面流量缺失，而是 PayPal provider decline：

- Stripe `PaymentIntent.status=requires_payment_method`
- `last_payment_error.code=payment_method_provider_decline`
- `decline_code=paypal_payment_declined`
- PayPal issue: `PAYER_CANNOT_PAY`

因此重构重点应分成两类：

1. **流程正确性**：BA/EC/cs/pi/pa_nonce 等 token 是否沿真实浏览器链路传递。
2. **风控一致性**：浏览器环境、cookie、challenge、telemetry 是否由同一会话连续产生。

## 2. 推荐重构方式

### 2.1 不推荐纯 HTTP 协议复刻

纯 HTTP 客户端很难稳定重构本流程，因为以下数据不是普通参数：

- PayPal `/mtr/*` 的 sealed result。
- FraudNet `p1/p2/pa/w/p3` 中的浏览器、性能、GPU、插件、定位、行为序列。
- hCaptcha/reCAPTCHA challenge token、CSRF、session、timing。
- Tealeaf UI replay 事件。
- Stripe `r.stripe.com`、`m.stripe.com/6`、PerimeterX/HumanSecurity 侧的设备会话。
- Next/RSC server action body、build/deployment-scoped headers。

这些值必须由真实页面、真实 SDK、真实 cookie jar、真实 JS runtime 在同一浏览器上下文里产生。抓包里的值只能作为字段结构参考，不能作为可复用模板。

### 2.2 推荐架构：浏览器主导 + 状态机观测

```text
BrowserContext
  ├─ 页面导航：approve/pay/signup/hermes/stripe
  ├─ 自动产生：cookie、client hints、captcha iframe、FraudNet、Tealeaf、Stripe telemetry
  ├─ 用户输入：邮箱/手机/OTP/地址/卡/CPF/密码
  └─ Network Observer
       ├─ 识别 BA/EC/cs/pi/pa_nonce
       ├─ 记录 GraphQL operationName 和关键状态
       ├─ 判断 challenge / fallback / decline
       └─ 输出脱敏审计报告
```

协议层只做三件事：

1. 监听/归档网络事件。
2. 从响应和 URL 中提取当前状态。
3. 根据状态机判断下一步页面动作或人工输入点。

## 3. 状态机重构

| 状态 | 入口信号 | 必须观测到的关键事件 | 下一状态 |
|---|---|---|---|
| `BA_APPROVE` | `/agreements/approve?ba_token=BA-*` | HTML、Set-Cookie、跳转或 `/pay` | `PAY_MODXO` |
| `PAY_MODXO` | `/pay?...token=BA-*` | `/mtr/*`、FraudNet、hCaptcha passive、`/pay` RSC | `COUNTRY_SELECTED` |
| `COUNTRY_SELECTED` | `country.x=BR` | `/pay/api/countries`、captcha solved 后的 `/pay` POST | `SIGNUP_ENTERED` |
| `SIGNUP_ENTERED` | `/checkoutweb/signup?...token=EC-*` | content manifest、Weasley logger、GraphQL warm-up | `SIGNUP_READY` |
| `SIGNUP_READY` | `DeferredFeature` / `CheckoutSessionDataQuery` | merchant=OpenAI、checkout type、funding sources | `OTP_PENDING` |
| `OTP_PENDING` | `InitiateRiskBasedTwoFactorPhoneConfirmationMutation` | `authId`、`challengeId`、`state=PENDING` | `OTP_CONFIRMED` |
| `OTP_CONFIRMED` | `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` | `state=CONFIRMED` | `SIGNUP_SUBMIT` |
| `SIGNUP_SUBMIT` | `SignUpNewMemberMutation` | success 或 `CARD_GENERIC_ERROR` / challenge | `HERMES_REVIEW` 或 `CHALLENGE` |
| `HERMES_REVIEW` | `/webapps/hermes?...fromSignupLite=true` | Hagrid review、buyer id、logger/FPTI | `PAYPAL_AUTHORIZE` |
| `PAYPAL_AUTHORIZE` | GraphQL `authorize` | `billingAgreementToken=BA-*`、Stripe return URL | `STRIPE_RETURN` |
| `STRIPE_RETURN` | `pm-redirects.stripe.com/return` | 302 到 OpenAI/Stripe checkout | `STRIPE_RESULT` |
| `STRIPE_RESULT` | `payment_pages/{cs_*}/init` | PaymentIntent status / last_payment_error | `SUCCESS` 或 `DECLINED` |

## 4. 关键封控点清单

### 4.1 会话一致性

- `BA-*`、`EC-*`、Stripe `cs_*`、`pi_*_secret_*`、`pa_nonce_*` 必须来自同一次浏览器会话的响应/URL。
- 不跨代理、跨浏览器 profile、跨 cookie jar 复用 token。
- `Referer`、`Origin`、`sec-fetch-*`、client hints 应由浏览器自然发出，而不是手工拼接。

### 4.2 浏览器环境一致性

需要在同一 BrowserContext 内保持一致：

- UA / client hints / platform / device memory。
- timezone / locale / `Accept-Language`。
- viewport / screen / colorDepth / DPR。
- WebGL/GPU、plugins、hardwareConcurrency。
- network effective type、RTT、downlink。
- geolocation permission 与坐标。

文档样本里出现过语言、时区、设备参数不一致的问题；这类不一致会放大风险评分。

### 4.3 PayPal 风控链

必须让页面自然产生：

- `/mtr/*`
- `c.paypal.com/v1/r/d/b/p1,p2,pa,w,p3`
- `/identity/di/log`
- `/platform/tealeaftarget`
- `/xoplatform/logger/api/logger`
- `t.paypal.com/ts`
- hCaptcha/reCAPTCHA/authchallenge 相关 iframe/API/PayPal `/auth/*`

这些请求的作用是设备识别、行为回放、页面生命周期、challenge 校验。缺失并不一定立刻导致 HTTP 错误，但会影响后续 signup/authorize/支付工具接受度。

### 4.4 GraphQL 业务链

PayPal signup 阶段的最小业务顺序应以 roxy 主链路为准：

1. `DeferredFeature`
2. `CheckoutSessionDataQuery`
3. `SupportedFundingSourcesQuery`
4. 地址/分期相关查询
5. `InitiateRiskBasedTwoFactorPhoneConfirmationMutation`
6. `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation`
7. `SignUpNewMemberMutation`
8. fallback 后 `authorize`

`SignUpNewMemberMutation` 里的个人资料、卡、CPF、密码、手机号、OTP 都是用户输入/测试数据，不能从抓包静态复用。

### 4.5 Stripe/OpenAI 回跳链

PayPal `authorize` 返回 `status=success` 的 Stripe return URL，只代表 PayPal 页面授权流程完成。真正支付结果要看：

- OpenAI/Stripe URL 的 `redirect_status`
- Stripe `payment_pages/{cs_*}/init`
- `PaymentIntent.status`
- `last_payment_error`

如果出现 `PAYER_CANNOT_PAY`，应判定为支付工具/PayPal provider 拒绝，不应继续通过重复重放同一 BA/EC/卡/账号组合解决。

## 5. 当前代码重构建议

### 5.1 把“协议发包器”降级为“浏览器流量观察器”

现有 `paypal/flow.py` 已经尝试手工发送 FraudNet、Tealeaf、captcha、GraphQL、Hermes 等包。建议重构为：

- `BrowserFlow`：用真实浏览器完成页面动作。
- `NetworkClassifier`：按 `manual_path_analysis.md` 的路径归并表分类请求。
- `StateExtractor`：提取 BA/EC/cs/pi/pa_nonce、GraphQL operation、Stripe result。
- `RiskCompletenessReport`：检查关键风控包族是否自然出现。
- `DecisionEngine`：只根据页面状态决定“等待、输入 OTP、进入下一页、结束”。

### 5.2 保留协议层的安全用途

协议层适合做：

- 离线解析 roxy/program 抓包。
- 脱敏对比字段结构。
- 生成状态机报告。
- 读取 Stripe/PayPal 响应中的最终错误原因。

协议层不适合做：

- 手工合成 captcha/risk/fingerprint proof。
- 复用抓包 token。
- 跳过浏览器页面直接提交支付授权。

## 6. 验收标准

一次重构后的浏览器复现，至少应满足：

- 同一会话内出现 BA → EC → Stripe return 的完整 token 链。
- PayPal 风控包族出现顺序与 roxy 主链路同类，而不是只有 GraphQL 主包。
- `SignUpNewMemberMutation` 前已经完成 OTP confirmed。
- Hermes/Hagrid review 后才出现 `authorize`。
- Stripe `payment_pages/init` 能明确输出最终状态：成功、`requires_payment_method`、`paypal_payment_declined` 或其他 provider decline。
- 输出报告默认脱敏卡号、CVV、密码、CPF、OTP、client secret、cookie。

## 7. 最小输出报告字段

```json
{
  "ba_token_seen": true,
  "ec_token_seen": true,
  "paypal_risk_families": {
    "mtr": true,
    "fraudnet": true,
    "identity_di": true,
    "tealeaf": true,
    "captcha_or_authchallenge": true
  },
  "paypal_business": {
    "checkout_session_data": true,
    "otp_confirmed": true,
    "signup_result": "CARD_GENERIC_ERROR | SUCCESS | CHALLENGE | UNKNOWN",
    "authorize_seen": true
  },
  "stripe_result": {
    "redirect_status": "failed",
    "payment_intent_status": "requires_payment_method",
    "decline_code": "paypal_payment_declined",
    "paypal_issue": "PAYER_CANNOT_PAY"
  }
}
```

