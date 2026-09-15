# Resend HTTPS 邮件后端

> 传输边界保持；当前共享邮件内容为 [6 位邮箱验证码](email-verification-codes.md)，新注册不再发送验证链接。

开发基线 `cb9fb33`，分支 `codex/resend-mail-backend`。本轮不部署生产，不修改生产 env，不重启服务，不发送真实邮件。

## 配置

未来单独授权生产配置时使用：

```dotenv
MIO_MAIL_MODE=resend
MIO_MAIL_FROM="Mio Canvas <noreply@mio-canvas.eu.cc>"
MIO_RESEND_API_KEY=
```

Key 必须由管理员安全录入 VPS 私有 env，不提交填写后的配置。缺失 Key 或 sender 不合法时安全拒绝注册。
`resend` 模式完全忽略 `MIO_SMTP_*`，不从 Provider/用户配置/SQLite 获取或回退凭证。
不新增第三方 SDK，复用现有 httpx。

发件人使用标准邮件 HeaderParser/Address 解析，要求单一合法邮箱，可带 display name。
接受 `noreply@mio-canvas.eu.cc` 及 `Mio Canvas <noreply@mio-canvas.eu.cc>`；拒绝 CR/LF、控制字符、地址列表、群组、畸形 sender。
复用现有邮箱库校验地址，但没有改变账号邮箱的 normalization。

发送域默认取 `PUBLIC_BETA_ORIGIN` 的 hostname，精确匹配邮箱域，不隐式允许子域。
如果实际使用独立的已验证邮件域，可由管理员显式设置 `MIO_MAIL_SENDING_DOMAIN`。
本地校验域名绑定不等于远程验证域名所有权；Resend 仍负责校验其域名验证与 Key 权限，本轮不调用域名管理 API。

## 传输与安全

唯一 endpoint：`POST https://api.resend.com/emails`，不支持 env 覆盖 endpoint。
发送 `from`、单一 `to`、`subject`、`html`、`text`，使用 Bearer 鉴权及 JSON。
接口依据 [Resend 官方 Send Email 文档](https://resend.com/docs/api-reference/emails/send-email)。

- 使用 `httpx.AsyncClient`、验证 TLS 证书、关闭 redirect、关闭环境 proxy、HTTP/1.1。
- httpx 分阶段 timeout 10 秒，并通过 `asyncio.wait_for` 施加整体 10 秒 deadline。
- transport retries=0；不自动重发，不执行其他 API。
- 任意 2xx 视为接受发送；非 2xx（包括 redirect）安全失败。
- 使用 streaming 只取 status，关闭 response，不读取/解析/保存响应正文或远端消息 ID。
- 网络异常仅保留类别；对外异常不带原始异常链/request/response。
- 可用安全异常属性仅 provider、status_code、error_class；不主动输出请求或响应日志。
- 不记录 Key、Authorization、token、邮件正文或 SMTP 密码。
- 用户只收到既有统一提示「邮件服务暂不可用，请稍后重试。」。

兼容入口 `send()` 在现有 Gateway `asyncio.to_thread` 中执行，通过 `asyncio.run(asend())` 使用异步传输；CLI 也可继续同步调用。
直接异步调用者使用 `await asend()`。`send()` 若误用在正在运行的事件循环内会安全拒绝，不阻塞 FastAPI。
慢速发信时并发 Gateway `/healthz` 请求可以完成，已由专项测试覆盖。

## 保留行为

SMTP backend 保留，支持新 `MIO_MAIL_FROM` 与旧 `MIO_SMTP_FROM` 别名，支持裸邮箱及 display name。
新 `MIO_MAIL_FROM` 受上述发送域限制；仅使用旧 SMTP_FROM 的通用 SMTP 配置保留兼容性，或显式设置发送域约束。
SMTP TLS、超时、凭证保存规则保持。`disabled` 和 loopback `mock` 保留。

未修改 schema、migration、注册编排、账号 normalization、token hash/TTL、pending、seat accounting、legacy login、Session、Supervisor 或实例生命周期。
GatewayStore 唯一变化是接受 `mail_mode=resend` 枚举。
发送失败继续由原注册逻辑使此次 token 失效，并保留 bounded pending 供用户重发或到期清理。

## 验收

`tests/test_public_beta_resend.py` 新增 30 项，HTTP 测试全部使用 MockTransport，覆盖各状态码、超时、TLS 参数、固定 endpoint、日志安全、sender、配置、SMTP/mock 及旧邮件异常调用兼容、Gateway 非阻塞行为。
原 651 项测试全部保留，`./scripts/check.sh` 已通过 **681/681**，`python -m pip check` 通过；Linux CI 使用同一检查。

浏览器 harness：

```sh
.venv/bin/python -u tests/manual_email_registration.py --resend-http-mock
```

此模式使用实际 Resend backend + MockTransport，测试控制路由仅存在于 loopback harness，不存在于生产应用。
Alice：注册、未验证登录拒绝、mock 邮件验证、登录完整工作台、退出后新 API 请求 401。
Bob：模拟 HTTP 500，页面仅显示统一错误、pending 不创建 Instance；恢复 mock 202 后通过重发完成验证、登录、退出。
浏览器验收共 3 个 mock 请求（2 次成功、1 次失败），真实邮件/模型请求均为 0；日志没有 traceback 或测试凭证/token。

VPS 只进行无认证 `HEAD https://api.resend.com/` 预检查：DNS、TCP 443、TLS 1.3 和证书验证通过，HTTP 200，约 212 ms。
未请求 `/emails`，未携带 API Key；检查前后 env、Gateway/Supervisor/Caddy 状态不变，生产 SHA 仍为 `92e4f3e9f493de2035436248e4157b6ff1cb7bf9`。
这只证明网络可达，不证明真实 Key、域名权限或邮件送达；真实发送需后续单独验收。
