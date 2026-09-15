# 邮箱验证码注册

分支 `codex/email-verification-codes`，基于 Resend 版本 `ec4c67c3`。本阶段不部署、不发送真实邮件、不修改 VPS/env 或重启生产服务。

## 流程和配置

用户名、邮箱、12–1024 字符密码、确认密码通过校验并成功发信后，注册页原地显示遮罩邮箱与验证码输入框。
正确验证创建正常 Gateway Session，进入 `/` 完整工作台默认 Canvas，不再输入密码或经过链接页。
刷新通过 HttpOnly 待验证 cookie 恢复状态；返回修改邮箱会取消所属未验证申请、保留发信限速、重新填写注册信息。

| 配置 | 默认 |
| --- | --- |
| MIO_EMAIL_CODE_TTL_SECONDS | 600 秒 |
| MIO_EMAIL_CODE_MAX_ATTEMPTS | 5 次错误后失效 |
| MIO_EMAIL_CODE_RESEND_SECONDS | 60 秒 |
| MIO_EMAIL_CODE_HOURLY_LIMIT | 同邮箱、同申请每小时各最多 5 次发送尝试 |
| MIO_EMAIL_CODE_IP_HOURLY_LIMIT | 同来源 IP 每小时最多 20 次发送尝试 |

`secrets.randbelow(1_000_000)` 格式化为六位字符串，允许前导 0。输入框支持 numeric、one-time-code、maxlength=6、粘贴并过滤常规空格/连字符；后端只接受 ASCII `[0-9]{6}`。

## 存储与验证

复用 Gateway 私有 32 字节 `handoff.key`，以 `mio-email-code-v1` 派生独立 HMAC key，再按 purpose 分离。
HMAC-SHA256 绑定 pending digest、normalized email、generation、code；使用 `hmac.compare_digest`。
不用密码 hash，也不存普通 SHA256(code)。master key 不传给 Instance，原 handoff 为 Instance 分别派生 key 的规则保持。

SQLite `email_code_pending` 与 users 关联，仅存 keyed pending/owner digest、code_hmac、时间、generation、attempts、delivery 状态。
明文 code 只短暂在邮件传输内存与输入框。浏览器不写 code、密码、HMAC 到 storage、URL、cookie；cookie 仅高熵随机申请/ownership 凭证，生产继续 Secure/HttpOnly/SameSite=Strict。
公开 opaque pending_id 不足以验证：必须同时匹配 HttpOnly ownership cookie。验证依据为服务端申请，不接受客户端邮箱。

`BEGIN IMMEDIATE` 原子确认申请、TTL、当前 generation/HMAC、attempts、唯一性与容量，再激活账号、消费 code、创建 Session、写固定安全事件。
同 code 并发最多成功一次。错误次数提交后返回错误；5 次后 attempts gate 禁用当前 code，重发成功重置。
验证先占用账号名额，进入工作区复用该名额执行原 Supervisor provisioning/start/handoff。
pending 不创建 Instance/data root、不占 running slot。首次本地初始化失败保留已验证账号、名额、Session，可重试原 provisioning。
重复首次访问只创建一个 Instance；Supervisor 继续是运行容量与启动的唯一 authority。

## 发送与安全边界

邮件在 SQLite 锁外发送。发送前先失效旧 code、预留 generation/cooldown/短 sending lease；仅同一代成功投递后才 ready。
发送失败、过时 completion、禁用/删除申请不能激活，不自动重试，仅固定安全提示。
申请内所有已发行 code 的 HMAC/generation 都用于有界碰撞检查，禁止任何旧数字重发；历史最多 1024 项（覆盖默认限速下最长 7 天 pending），成功验证即清空。旧邮件乱序不会改变当前 generation。
email/pending/IP 发信 bucket 均 keyed，过期清理、硬上限 4096。沿用 trusted proxy/client IP，不信任任意 X-Forwarded-For。

- POST `/api/beta/register` 返回 verification_required、opaque pending_id、遮罩邮箱和剩余时间，只设置待验证 cookie。
- GET `/api/beta/email-code-pending` 用 ownership cookie 恢复遮罩状态。
- POST `/api/beta/verify-email-code` 只接受 pending_id/code；成功设置 Gateway Session、删除待验证 cookie。
- POST `/api/beta/resend-email-code` 和 `/api/beta/cancel-email-code` 只接受 pending_id。

POST 保持 Origin、请求体上限、rate limit，已有 Gateway Session 时要求其 CSRF。私人响应 no-store/private。
Session、A/B、SSO、ownership、logout 401、Provider Key 不回显等边界保持。
Resend/SMTP/mock 共用 content builder：主题 `你的 Mio Canvas 验证码`，HTML/text 含突出 code、10 分钟提示和非本人操作忽略提示，无 token URL。
Resend 固定 HTTPS endpoint、正常 TLS、有界 timeout、无自动重试，SMTP/disabled 保留。
日志只固定安全事件和内部 user id，不记录 code、HMAC、正文、Key、Authorization、旧 token/URL。

## 旧链接迁移

schema v1 → v2 一次性 additive transaction。迁移时冻结未消费/废弃旧 token 最晚原 expires_at 为 legacy_until（原 TTL 最大一小时）。
单 token 仍检查原 TTL；Gateway/Supervisor 再启动不延长截止时间。新申请/重发不发行链接。
旧邮件原 TTL 内继续走旧验证/密码登录，也可用相同用户名、邮箱、注册密码确认并转换成 code；转换立即失效旧 token。
兼容 `/resend-verification` 仅引导回注册页，旧按邮箱匿名重发 API 不发信。截止后旧链接无法再激活，记录按原清理机制淘汰。
旧用户名登录和管理员显式 set-email/verify-email 保留；send-verification 仅可重发已有 code 申请，不能再发行链接。

## 本地验收

自动测试只使用临时 SQLite、synthetic credentials、MockTransport。`tests/manual_email_registration.py --resend-http-mock` 启动 loopback Gateway 与真实临时 Supervisor/Instance。
内存 code hook、模拟时间、`--slow-mail` 只属于该 harness，生产 app factory 无 debug code 路由。
所有临时账号、实例、进程均在 finish 后清理；结果见 [验证码验收](email-code-acceptance.md)。
