# Strict browser-risk remediation validation

依据 `automation_vs_real_browser_risk_diff.md`，本次把默认行为改为“严格模式下不再伪造真实浏览器 runtime 证据”，并添加可验证的运行时报告。

## 已处理项

1. **MTR sealedResult**
   - 已从入口 HTML 提取 `dfpconfig` 和 `dfp.js` URL。
   - 已保留 MTR GET/POST URL 生成用于和 roxy 抓包对照。
   - 严格模式下不再用 Node/happy-dom 或模板生成/提交 MTR 二进制 payload；缺失 `sealedResult` 会成为阻断项。

2. **DataDome**
   - 保留 cookie/clientid/header 注入状态跟踪。
   - `risk_runtime.datadome` 会明确区分 `datadome` cookie 是否真实存在，以及是否只是注入了 `x-datadome-clientid`。

3. **captcha**
   - 严格模式默认忽略 `frontend_disable` synthetic close。
   - 严格模式默认忽略外部 solver，除非显式允许；hcaptchapassive 的 frontend skip 和本地 happy-dom helper 在严格模式下不再使用。
   - `risk_runtime.captcha` 会标记 synthetic 是否使用。

4. **Client Hints**
   - 高熵 CH 默认不强制，只有观察到 `Accept-CH` 后才启用。
   - `risk_runtime.client_hints` 会报告是否通过环境变量强制高熵 CH。

5. **FraudNet/FPTI/Tealeaf/Datadog**
   - 当前仍是 Python/template 生成，严格模式在发送前 fail-fast，不再默认把这些包当成真实浏览器 runtime 输出。
   - `risk_runtime.synthetic_risk_families` 会明确标记这些包族的来源问题。

## 验证命令

```bash
python3 -m py_compile paypal/flow.py paypal/models.py paypal/session.py paypal/mtr.py tests/test_browser_flow_order.py
pytest -q
```

验证结果：`16 passed`。

## roxy 基线验证

已用真实抓包 HTML 验证可提取：

- `dfpChannel=iwc-mxo`
- `clientMetaDataId=BA-37R61061EU582084R`
- `fppAPIKey` 存在
- `dfp.js` URL 可定位
- MTR GET/POST URL 与 roxy 路径一致
