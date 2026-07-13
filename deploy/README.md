# 部署说明

目标域名：`https://pix.shuangdeng.space`  
应用目录：`/opt/openai-paypal`  
服务名：`openai-paypal-web.service`

## 架构

```text
Internet / Cloudflare
        |
   Nginx (443, Let's Encrypt)
        |
   web.py (127.0.0.1:8080)
        |
   Playwright headless Chromium
```

## 一次性服务器初始化

在服务器上执行（需要 sudo）：

```bash
git clone https://github.com/StarHosea/openai-paypal.git /opt/openai-paypal
cd /opt/openai-paypal
bash deploy/scripts/setup-server.sh
```

然后编辑生产配置：

```bash
nano /opt/openai-paypal/.env
```

至少确认以下项：

```env
PAYPAL_WEB_PRODUCTION=1
PAYPAL_WEB_COOKIE_SECURE=1
PAYPAL_PROXY_ENABLED=1
PAYPAL_PROXY_URL=http://user:pass@host:port
PAYPAL_FINGERPRINT_SOURCE=headless
PAYPAL_DATADOME_MODE=headless
PAYPAL_MTR_RUNTIME=headless
PAYPAL_RISK_SIGNALS_MODE=headless
```

重启服务：

```bash
sudo systemctl restart openai-paypal-web
```

## SSL / Cloudflare

域名当前走 Cloudflare。源站证书由 Certbot 自动签发后，请在 Cloudflare 控制台将 SSL/TLS 模式设为 **Full (strict)**。

如果暂时只想用 HTTP 验证，可先把 Cloudflare 代理改为 **DNS only（灰云）**，跑完 `setup-server.sh` 后再打开代理。

## GitHub Actions 自动部署

在仓库 `Settings -> Secrets and variables -> Actions` 添加：

| Secret | 示例 |
| --- | --- |
| `DEPLOY_HOST` | `140.238.56.209` |
| `DEPLOY_USER` | `muhaoxing` |
| `DEPLOY_SSH_KEY` | 部署专用私钥全文 |
| `DEPLOY_SSH_PORT` | `22`（可选） |
| `DEPLOY_APP_DIR` | `/opt/openai-paypal`（可选） |

### 生成部署密钥

在本地：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/openai-paypal-deploy -N ""
cat ~/.ssh/openai-paypal-deploy.pub
```

把公钥追加到服务器：

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
echo 'ssh-ed25519 AAAA...' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

把私钥内容写入 GitHub Secret `DEPLOY_SSH_KEY`。

## 触发方式

- 推送到 `main` 分支自动部署
- 或在 GitHub Actions 页面手动 `workflow_dispatch`

## 常用运维命令

```bash
sudo systemctl status openai-paypal-web
sudo journalctl -u openai-paypal-web -f
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS https://pix.shuangdeng.space/api/health
sudo certbot renew --dry-run
```
