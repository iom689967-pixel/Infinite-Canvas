# Public Beta 邮箱注册与登录

此功能在 `codex/verified-email-auth` 开发。生产 `92e4f3e9` 不在本轮部署范围内。

## 身份与状态

新注册需要邮箱、现有规则的用户名（3–32 位字母/数字/下划线/连字符）、12–1024 字符密码及确认密码。
登录页面使用「邮箱 / 用户名」。新账号只通过邮箱登录；升级前账号保留用户名登录资格，之后绑定邮箱也不会撤销原 Session。
用户名继续用于展示及原有 Instance 身份，未新增昵称重命名或团队功能。

`pending_verification → verified_waiting → provisioning → active`：

- pending 不创建 Instance、不启动进程、不占正式 seat。
- 验证先原子消费 token，保留 verified 状态，然后调用现有 Supervisor `provision`。
- `GatewayStore.reserve()` 在原 `BEGIN IMMEDIATE` 事务内为已验证账号保留原 user_id，检查名额，再创建 Instance 记录。
- 名额满：保留 `verified_waiting`，页面提示名额已满；不自动轮询、不自动启用。
- 初始化失败：只按原流程清理此次创建的实例目录，保留已验证用户和原 user_id，释放 provisioning seat。管理员之后可以明确 `enable`。
- registration=closed 时仍可验证邮箱，但不会新 provisioning。
- Supervisor 的代码、IPC 字段、start/stop、reconcile、端口及 MAX_RUNNING_INSTANCES authority 未修改。

## SQLite migration

`public_beta_migrations.py` 使用 `PRAGMA user_version`：旧库 0 → 1。在写事务中添加：

- `email`、`email_normalized`、`email_verified_at`，旧用户均允许 NULL。
- `email_status`（legacy/pending/verified）。
- `legacy_username_login`：旧记录默认 1，新邮箱注册明确写 0。
- `pending_expires_at`。
- `email_normalized IS NOT NULL` 的唯一索引。
- 通用 `account_tokens` 表（purpose、digest、user_id、created_at、expires_at、used_at、invalidated_at）。

迁移不改 user_id、username、password_hash、sessions、handoff.key、instances 或用户数据根。重复打开已迁移库不会重复 ALTER。
高于当前支持版本的库安全拒绝。并发验证的正式 seat 仍只统计 active/provisioning。

未来生产部署前必须先关闭新增注册，并完成 Gateway SQLite **online backup**；本轮没有执行生产迁移。
备份需要使用 SQLite `Connection.backup()`，备份文件权限 0600，目录 0700；对备份执行 `PRAGMA quick_check` 并记录摘要。
不能复制正在写入的裸 sqlite 文件代替 online backup。

**运行代码一致性**：Gateway 与 Supervisor 都导入 GatewayStore。未来部署必须确保二者加载同一邮箱 schema release，不能让旧 Supervisor 的六列 INSERT 操作新数据库；这不改变 Supervisor 生命周期设计，但需要另行授权部署/重载。不得只升级 Gateway。
回滚不能直接让旧代码打开新增列后的数据库。需预先验证恢复数据库快照的方案，并保留快照之后新增账号/数据；本轮不做生产回滚或删除。

## 邮箱规范化

依赖 `email-validator==2.3.0`，使用库的语法与 IDNA 规范化，展示保存 normalized 地址；唯一性和登录使用 ASCII-domain 地址的 casefold。
先 trim，整地址大小写不敏感。不删除 Gmail 点号或 plus suffix。不使用自写正则替代邮箱验证。
第一版不接收要求 SMTPUTF8 的本地部分；国际化域名可规范化。所有权由验证邮件确认，注册/登录不做 MX 网络探测。
`.test` 仅在显式 loopback mock 环境允许。库说明：https://github.com/JoshData/python-email-validator

## 邮件及安全阀

生产必须显式配置 SMTP；默认 disabled。公网 origin 下 mock 不接受注册。配置不完整时在创建 pending 前返回「邮件服务暂不可用，请稍后重试」。
SMTP 连接失败不会回显原错误；保留 bounded pending 供 resend/到期清理，并使此次 token 失效。
SMTP 不自动重试，不开启协议 debug。凭证只从私有 env 读取，不写 SQLite、Instance、Provider、页面、日志。

| 配置 | 默认值 |
| --- | --- |
| MIO_MAIL_MODE | disabled（生产 smtp，本地 mock） |
| MIO_SMTP_HOST / USERNAME / PASSWORD / FROM | 必须由管理员配置 |
| MIO_SMTP_PORT | 587 |
| MIO_SMTP_TLS | starttls（另支持 ssl，均校验证书） |
| MIO_SMTP_TIMEOUT_SECONDS | 10，最大 30 |
| MAX_PENDING_REGISTRATIONS | 100（pending + verified_waiting 合计受限） |
| MIO_EMAIL_VERIFICATION_TTL_SECONDS | 3600 |
| MIO_PENDING_TTL_SECONDS | 86400 |
| MIO_EMAIL_RESEND_COOLDOWN_SECONDS | 60 |
| MIO_EMAIL_RESEND_LIMIT | 每个限速窗口 3 次 |

继续使用现有 IP 注册限速、用户名冲突限速、磁盘安全余量、MAX_PUBLIC_USERS=20、MAX_RUNNING_INSTANCES=4。
过期 pending 只在没有 Instance、没有验证时间时可删除。新注册事务和显式 CLI cleanup 会清理；登录和验证不会让过期 pending 进入工作区。
已验证待名额账号不会被 pending TTL 清理。

Token 使用 `secrets.token_urlsafe(32)`（256 bit），只存 SHA-256。单次消费、60 分钟有效；resend 在事务内废弃旧 token，并受 cooldown 与单邮箱/IP 限速保护。
验证邮件采用纯文本和 HTML。链接形式 `/verify-email#token=...`，fragment 不发送给服务器/Caddy；页面同步清除 fragment 后以 POST 提交。
验证成功不创建 Gateway Session；需正常邮箱密码登录，原 Secure/HttpOnly/SameSite、CSRF/Origin 和一次性 SSO handoff 不变。

Resend 对未知、已验证、冷却或单邮箱限速返回相同提示；邮箱专属 SMTP 投递失败也不会改变提示。全局邮件配置不可用可返回统一 503，IP 限速可返回 429。
安全事件仅固定 enum + 内部 user_id。无 token、SMTP 异常 message、邮件正文或完整邮箱日志。

## 管理员命令

在未来已配置私有 env 的部署环境，由管理员明确执行；本轮未对真实账号运行：

```sh
python public_beta_admin.py users
python public_beta_admin.py set-email legacy-user user@example.com
python public_beta_admin.py send-verification legacy-user
# 或管理员明确验证；不会默认为新注册跳过邮箱验证：
python public_beta_admin.py verify-email legacy-user
# 有名额后明确为已验证等待账号 provisioning：
python public_beta_admin.py enable waiting-user
python public_beta_admin.py cleanup-pending
```

users 采用 LEFT JOIN，包括无 Instance 的 pending；只展示 masked_email、email_status、账号/实例状态，不展示 token/hash/Session/Key。
set-email 不猜测用户邮箱、不发送邮件、不自动验证。原用户和已登录 Session 保持；验证后可以增加邮箱登录。

## 验收

`./scripts/check.sh` 覆盖原隔离/SSO/生命周期回归及邮箱专项。CI 安装同一 requirements-dev 并执行 pip check 与完整检查。
`tests/manual_email_registration.py` 只在 loopback 使用内存 mock 邮箱与临时真实 Supervisor/Instance；测试 mailbox 路由只存在于这个 harness，正式应用不开放。
启动 harness 后浏览器使用其 stdout 给出的本地 origin，`status` 仅输出安全状态，`finish` 清理临时进程和数据。
浏览器测试密码仅在临时 0600 文件，验证 token 不输出到工具或验收报告。

本轮本地完整检查为 **651/651**（原 610 项 + 41 项新增回归），`pip check` 通过。
浏览器已实际验证 Alice/Bob 注册、未验证登录拒绝、mock 邮件验证、完整工作台登录、各自 Canvas 保存及隔离；直接跨账号请求 Canvas 返回 404。
退出后新的 Gateway/Canvas/Provider 请求返回 401，旧无邮箱账号仍能使用用户名登录。
最新代码另行完成注册→验证→登录→退出复测，Gateway 日志无 traceback，测试凭证及 token 未进入日志。
临时实例与测试数据已清理；真实 SMTP 和模型调用均为 0。

本轮不实现重设密码、OAuth、真实邮件投递或生产部署。
