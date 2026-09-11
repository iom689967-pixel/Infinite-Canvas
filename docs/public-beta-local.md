# Mio Canvas Public Beta v1：本地功能基线

本阶段仅监听 `127.0.0.1`，不包含 VPS、域名、HTTPS、邮件或计费部署。
共享一份只由管理员维护的 PROGRAM_ROOT，每个用户拥有独立进程和 UUID 数据根。
Gateway 负责身份、注册目录、实例映射和转发；Canvas、Prompt、图片、Provider 和 Key
始终由用户实例读写，Gateway SQLite 不保存这些内容。

## 基线与结构

基线 `27982b4162de140ab6e8187b46361abcccfe1722` 已包含 b088ffc、c15ef12、
3a20750、249ffd3、78e0e94 和 2a863c7。保留完整 `static/index.html`、正式 API 设置、
实例权限、SSRF、CSRF、Origin、任务 ownership 和 Kie 原任务恢复能力。

- `public_beta.py`：注册/登录首页、服务端 SSO、HTTP/WS 单 origin 代理。
- `public_beta_store.py`：SQLite、密码哈希、摘要会话、限速与安全事件。
- `public_beta_supervisor.py` / `public_beta_worker.py`：注册事务、固定 argv/env、实例生命周期。
- `public_beta_handoff.py`：45 秒、绑定用户与实例、一次性 HMAC ticket。
- `instance_storage_quota.py`：内容用量缓存、增量计数、写入预留与有界上传。
- `public_beta_admin.py`：本机管理，不输出 hash、会话或 Key。

默认 Gateway 数据库：`~/.infinite-canvas/public-beta/gateway.sqlite3`，权限 0600。
旁边的 `handoff.key` 是 Gateway 专用随机签名密钥，不是 Provider Key。

| 表 | 字段与用途 |
|---|---|
| users | UUID id、规范化 username、scrypt password_hash、status、created_at、last_login_at |
| instances | UUID instance_id、user_id、随机 slug、data_root、assigned_port、status、创建/启动时间、PID/OS start time |
| sessions | 随机会话的 SHA-256 摘要、user_id、expires、csrf |
| attempts | 限速 bucket 摘要、窗口开始时间、次数；不保存原始 IP |
| security_events | allowlist event、内部 user_id、时间；不存请求内容或原始异常 |

注册字段仅用户名、密码、确认密码；不收集邮箱。用户名 3–32 位 ASCII 字母、数字、
下划线或连字符，首字符必须字母或数字，统一小写。密码 6–1024 字符，沿用 scrypt。
用户名不作为路径。数据根为 `~/.infinite-canvas/instances/<random_uuid>/`，初始化
`data/`、`assets/`、`output/`、`workflows/custom/`、`runtime/`、`.auth/`，不复制 owner 内容。

注册先保留记录，再创建根、初始化认证与私有策略，最后事务标记 active。失败只回滚
本次记录和本次创建的根。启动时清理可证明属于中断注册的半成品；未知非空目录保留供
管理员核对，绝不通过 username 或用户传入路径删除既有内容。

## 配置与资源安全阀

| 服务端环境变量 | 默认 |
|---|---|
| PUBLIC_BETA_ROOT | `~/.infinite-canvas/public-beta` |
| PUBLIC_BETA_INSTANCES_ROOT | `~/.infinite-canvas/instances` |
| PUBLIC_BETA_BACKUP_ROOT | `~/.infinite-canvas/backups` |
| GATEWAY_HOST | `127.0.0.1`（不能改为公网监听） |
| GATEWAY_PORT | 32100 |
| INSTANCE_PORT_START / INSTANCE_PORT_END | 32000 / 32999 |
| MAX_PUBLIC_USERS | 20（包括禁用账号，已有用户不受注册满额影响） |
| INSTANCE_STORAGE_QUOTA | 5368709120 bytes，即 5 GiB |
| MAX_UPLOAD_BYTES | 52428800 bytes，即 50 MiB |
| MAX_CONCURRENT_GENERATIONS | 2（只限制生成任务，不限制浏览编辑） |
| MAX_RUNNING_INSTANCES | 4（服务器级同时运行工作区保护） |
| MIN_FREE_DISK_BYTES | 2147483648 bytes（VPS 配置可提高） |
| PUBLIC_BETA_REGISTRATION_MODE | `open`（生产首次部署应设为 `closed` 或 `invite`） |

新实例在注册时固化资源预算到私有 `.auth/public-beta.json`。修改 Gateway 默认预算影响
随后创建的实例；已有实例的调整属于管理员操作，需修改该实例预算并安全重启该实例。
MAX_PUBLIC_USERS 直接由 Gateway 服务配置控制，不需要改源码。

注册 IP 默认 5 次/300 秒，同用户名 3 次/300 秒，登录失败 IP 默认 10 次/300 秒。
无永久封禁；成功登录清除该 IP 的失败 bucket。开发模式只使用实际 socket peer。
生产模式要求 HTTPS Origin 和本机 trusted proxy；只有 socket peer 在精确允许列表内时，
才接受单一、合法的 X-Forwarded-For。任意代理链、错误 Host/scheme 均拒绝。

配额是应用层保护，不是 OS 磁盘硬 quota。内容计数保存在实例 `.auth/storage.sqlite3`，
认证、运行临时文件及凭证目录为运营开销，不计入内容额度。首次初始化/管理员重算才
遍历内容目录；平时按写入、删除、目录移动维护缓存。媒体写入预留额度，失败删除本次
部分新文件；Canvas/History JSON 原子替换，避免满额时截断既有文件。

单文件限制同时检查实际流式 bytes，取现有业务更严格值；Gateway 总请求上限为单文件
预算加 1 MiB 协议开销，因此一次批量请求的总量也有上限。ZIP 导入检查展开大小、文件数
（最多 1000）与单文件限制。满额仍可登录、读取、删除和有限导出；拒绝新的内容写入并
显示“当前工作区存储空间已满。” 上游已成功而结果因配额不能保存时保留原任务恢复状态，
释放空间后只恢复原结果，不重新生成。

## SSO、进程与网络边界

Gateway 登录发 HttpOnly/SameSite=Strict cookie；HTTPS 生产模式同时强制 Secure。进入工作区时
服务端将 45 秒 ticket POST 给已验证健康的目标实例，ticket 不经过浏览器 URL、日志或
localStorage。实例通过固有用户/实例绑定验证签名、期限和唯一 nonce，事务消费后建立
自身正常 Session。实例直接密码登录在 Beta 模式下关闭，用户只需 Gateway 一次登录。

Gateway 只转发当前中央 Session 映射的实例，不接受浏览器传端口、实例或目录。
Origin 必须精确匹配 Gateway origin，再转换为固定内部 origin；实例 CSRF 继续校验。
WS 同样验证会话/Origin，单用户最多 8 连接，撤销后关闭。保留实例对远端模型和媒体的
DNS/SSRF/redirect 校验。`/__mio/*` 只用于服务端，浏览器无法代理调用。

Supervisor 使用跨进程文件锁和固定 argv/env。端口在配置范围内扫描，跳过登记端口、
Gateway 端口、3000 和真实占用端口；映射持久化，启动遇占用不接管、不偷偷换目标。
健康证明绑定实例私有派生密钥。停进程核对 PID、OS start time 及实例进程记录，
不根据端口杀进程。禁用先撤销中央及实例 Session，再停止实例，数据保留。

VPS 的发行、systemd、Caddy、备份和生产安全设计见 `docs/public-beta-vps.md`。

## 本地运行步骤

风险边界：下列命令创建独立 SQLite/用户目录及 loopback 子进程；不会配置公网。
务必在本开发 checkout 使用其兼容 Python 依赖，根目录不要指向任何已有 owner/assistant 数据。

1. 选定独立目录与端口；例如在当前 shell 设置：

   ```sh
   export PUBLIC_BETA_ROOT="$HOME/.infinite-canvas/public-beta"
   export PUBLIC_BETA_INSTANCES_ROOT="$HOME/.infinite-canvas/instances"
   export GATEWAY_PORT=32100
   export MAX_PUBLIC_USERS=20
   python public_beta.py
   ```

   成功标志：`http://127.0.0.1:32100/` 显示 Mio Canvas 注册/登录页。
2. 访问 `/register`，输入用户名及至少 12 字符的密码。
   成功标志：显示“正在启动你的工作区…”后自动进入完整正式 UI，无第二次密码登录。
3. 在 API 设置填写自己的 Provider。Key 只保存到个人实例，不回显、不进入 Canvas 或 localStorage。
4. 同一配置环境运行管理员 CLI：

   ```sh
   python public_beta_admin.py users
   python public_beta_admin.py instance alice
   python public_beta_admin.py recount-storage alice
   python public_beta_admin.py disable alice
   python public_beta_admin.py enable alice
   python public_beta_admin.py stop alice
   ```

   `instance` 仅向本机管理员显示映射；disable 不删除数据；enable 后用户重新登录启动。
   成功标志：禁用账号无法登录，既有 Session 失效，原数据根仍在。

## 离线回归与浏览器验收

`./scripts/check.sh` 运行原 518 项加 Public Beta 专项。测试使用临时目录、独立真实子进程
及 loopback mock，不需要 Key，不访问付费服务。`tests/manual_public_beta.py` 提供一次性
浏览器 fixture，输出本地 Gateway 入口；合成密码/mock Key 留在私有临时文件，不记录正文。
stdin `status` 返回安全摘要，`disable-alice` 执行禁用，`finish` 停本次子进程并清理测试根。

浏览器验收直接复用正式 UI：Alice 注册/自动 SSO、建 Smart Canvas、上传合成图、配置 mock
Provider、刷新后可选 gpt-image-2；同一 tab 退出切 Bob，Canvas/素材/Provider 为空，无法直接
读取 Alice ID/媒体。跨用户 localStorage 继续按 instance+user namespace 隔离。
专项另覆盖配额、无 Content-Length 上传、并发、SSO 一次性与过期、注册回滚、禁用、恢复
原生成任务、CSRF/Origin、owner 单机兼容。没有真实模型任务，没有本轮费用。

### 本次验收记录（2026-09-10）

- 最终 `./scripts/check.sh`：546/546（原 518 + 新增 28），编译/JS 语法/diff 检查通过。
- 同一个真实浏览器 tab 注册 Alice/Bob，自动进入正式 UI；Alice Smart Canvas、合成图
  上传、个人 mock Provider 保存/刷新和模型选择通过。Bob 首次 Canvas/素材/Provider 为空；
  Bob 自己的 Smart Canvas 不继承 Alice 平台/模型。切回 Alice，原 Canvas 仍在。
- Bob 直接请求 Alice Canvas 和媒体均 404；匿名 Canvas、Provider、媒体均 401。
- Gateway WebSocket ping/pong 通过，logout 后连接关闭，匿名 WebSocket 拒绝。
- CLI 禁用 Alice 后旧页面会话失效，正确密码也无法登录；实例停止，Canvas 和媒体仍保留。
- 浏览器 fixture mock createTask=0、真实模型调用=0，Gateway 日志无合成密码/mock Key。
  自动测试的生成均为 loopback mock；没有本轮付费请求。
- 浏览器临时 Gateway、用户进程和数据根已清理。owner PID 2184 未改变，主页 HTTP 200，
  owner Git 工作区干净。没有创建默认目录下的正式 Beta 用户，没有替换旧运行实例。
