# Mio Canvas Public Beta：VPS 部署设计

## 发行通道

VPS 使用独立 `beta` 分支，并在每次部署记录精确 commit。现有 `stable` 保持 owner
自动更新通道的含义，不承载 public registration / Gateway 的发布节奏。流程为：

1. `codex/*` 开发分支通过本地完整回归；
2. 推送并由 GitHub Actions 在 Linux/Python 3.12/Node LTS 运行 `scripts/check.sh`；
3. 审阅后进入 `main`；
4. 仅以 fast-forward 更新 `beta`，VPS fetch 后 checkout 精确 SHA；
5. 保留上一 SHA 和部署前备份，失败时 checkout 上一 SHA 并 reload Mio 服务。

不得 force push `beta`、`main` 或 `stable`。Public Beta 普通用户没有源码更新、rollback
或服务管理权限；VPS 更新只能由本机管理员执行。

## 已审计服务器（2026-09-10）

- Ubuntu 24.04，Linux 6.8，x86_64；2 vCPU，RAM 1.9 GiB（审计时约 1.3 GiB 可用）。
- 根磁盘 58 GB，已用 6.5 GB，约 51 GB 可用。
- 公网 IPv4 `198.211.113.180`；未发现稳定公网 IPv6，因此不配置 AAAA。
- Caddy 2.11.4 已运行并独占 80/443；Nginx 不存在。
- 现有站点 `api.cannacoastpackaging.com` 反代 `127.0.0.1:18080`。
- Sub2API、PostgreSQL、Redis 三个 Docker 容器健康，restart count 均为 0。
- sing-box 继续使用 46020/46021；SSH 使用 22。现有服务不得修改或重启。
- Python 3.12.3、Git 2.43.0 可用；Node 不存在。Node 仅由 GitHub CI 做 JS 检查，
  生产运行不依赖 Node。系统有 logrotate；Python 自带 sqlite3 backup API。
- UFW/firewalld 当前未启用。本部署不改变防火墙，也不开放 32000–32999。

`mio-canvas.eu.cc` 公网 DNS 当前没有 A 或 AAAA。上线前只新增：

```text
类型: A
名称: mio-canvas
值: 198.211.113.180
```

等待公共解析器返回该 IPv4 后再让 Caddy申请证书。不要添加 AAAA，不重定向裸 IP。

## 目录和权限

```text
/opt/mio-canvas/
├── app/                 # root 拥有的 Git checkout，服务用户只读
├── venv/                # root 构建的 Python venv，服务用户只读
├── config/
│   └── public-beta.env  # root:mio-canvas 0640，不提交
├── public-beta/         # mio-canvas 0700，Gateway SQLite / handoff key
├── instances/           # mio-canvas 0700，每用户随机 UUID 根
├── backups/             # mio-canvas 0700，7 个每日增量快照
├── logs/                # mio-canvas 0700
└── runtime/             # mio-canvas 0700，受控 smoke 临时根
```

应用进程使用无登录 shell 的 `mio-canvas` 系统用户。程序/venv 不由应用用户写入；
Provider Key 只存在各实例私有凭证 store，Gateway 不读取，Mac 的 API/.env、Provider、
素材、Canvas 和 Codex auth 均不复制。

## systemd 和网络

`mio-canvas-gateway.service` 只启动一个 Gateway。用户实例仍由 Gateway Supervisor 按需
启动，不为每个用户创建 unit。Gateway 与所有用户实例属于同一 systemd cgroup；
service 使用只读系统、最小 writable paths、空 capability、NoNewPrivileges、私有设备，
限制 180% CPU、900 MB memory high、1200 MB memory max。代码另限制最多 4 个同时运行
的用户实例，适配当前 1.9 GiB VPS；注册账号上限仍为 20。

- Caddy：公网 80/443。
- Gateway：`127.0.0.1:38000`。
- 用户实例：`127.0.0.1:32000–32999`，持久分配，永不公网监听。

Caddy 独立 site block 使用 `reverse_proxy` 的内置 Host、X-Forwarded-Proto、
X-Forwarded-For 处理；Gateway 只有在 socket peer 是配置的 `127.0.0.1` 时才接受这些头，
并要求单一、合法 client IP。
公网连接不能直接访问 loopback Gateway，也不能通过伪造 X-Forwarded-For 改变限速身份。
WebSocket 由 Caddy 原生 reverse_proxy 转发。

生产 Origin 必须精确是 `https://mio-canvas.eu.cc`。Gateway 拒绝任意 Origin、错误 Host、
错误 forwarded scheme 和非 trusted proxy；CSRF 继续开启。Gateway Session 和实例 Session
cookie 都加 HttpOnly、SameSite=Strict、Secure。SSO ticket 仍只在 Gateway 到 loopback
实例的服务端 POST 中出现，浏览器 URL/localStorage 不包含 ticket。

## 生产环境

以 `deploy/public-beta/public-beta.env.example` 为模板创建服务器私有配置。关键值：

| 变量 | VPS 值 |
|---|---|
| GATEWAY_HOST / GATEWAY_PORT | `127.0.0.1` / `38000` |
| PUBLIC_BETA_ORIGIN | `https://mio-canvas.eu.cc` |
| PUBLIC_BETA_TRUSTED_PROXIES | `127.0.0.1` |
| PUBLIC_BETA_REGISTRATION_MODE | 首次部署 `closed`；受控 smoke 临时使用 `invite` |
| MAX_PUBLIC_USERS | 20 |
| INSTANCE_STORAGE_QUOTA | 5368709120（5 GiB） |
| MAX_UPLOAD_BYTES | 52428800（50 MiB） |
| MAX_CONCURRENT_GENERATIONS | 2 |
| MAX_RUNNING_INSTANCES | 4 |
| MIN_FREE_DISK_BYTES | 10737418240（10 GiB） |
| PUBLIC_BETA_BACKUP_RETENTION | 7 |

`invite` 模式需要 `PUBLIC_BETA_INVITE_CODE_HASH`，只保存邀请码 SHA-256；邀请码正文不进入
Git、聊天或日志。smoke 完成后切回 `closed` 并 reload Mio Gateway，既有测试账号可登录，
新注册被拒绝。外部公开注册必须由管理员以后将模式明确改为 `open`。

单用户配额是应用层限制。除每用户 5 GiB 外，当文件系统可用空间低于 10 GiB 时，
Gateway 暂停新注册，实例拒绝会增长内容的写入；登录、读取、删除和零增长原子替换仍可用。
由于 20 × 5 GiB 大于本机总盘，10 GiB 总盘阀是必须的第二层保护。

## 备份和日志

`mio-canvas-backup.timer` 每日运行，带随机延迟。Gateway 和实例 SQLite 均使用 SQLite
backup API；普通文件使用上一快照的硬链接做增量，改变中的文件跳过并留待下次，避免复制
半写入内容。`.runtime`/`runtime` 不备份。快照目录及其中 Provider 凭证为 0700/0600，
日志仅记录快照名、用户实例数量、文件数量、逻辑字节与跳过数量，不记录路径或内容。
默认保留 7 个快照。恢复操作应先在隔离目录验证 `PRAGMA quick_check`，本轮不自动覆盖生产数据。

Gateway/backup 日志写入 `/opt/mio-canvas/logs/`，logrotate 每日检查、20 MB 提前轮转、
保留 14 份并压缩。应用 access log 关闭；安全日志不记录密码、Key、Session、Cookie、
Authorization、Prompt、原始 payload、完整 signed URL 或完整上游 task id。

## 部署闸门和回滚

实际写入服务器前必须满足：独立 beta 分支已获批准、commit 已推送、GitHub CI 成功、
DNS A 生效。随后才创建系统用户/目录/venv，安装 unit，备份并追加 Caddy site block。
Caddy 必须先 `caddy validate` 成功，再 `systemctl reload caddy`；不重启 VPS、不停止 Docker、
sing-box 或 Sub2API。

首次 HTTPS smoke 使用隔离 smoke 数据根和单独邀请码，只调用 loopback mock Provider。
覆盖注册、provisioning、SSO、完整 UI、Canvas、上传、Provider、A/B 隔离、401/403、WS、
Gateway/instance restart 持久化。通过后清理 smoke 根，切换正式空数据根并保持 registration
closed，最后再次检查旧站点、Docker、sing-box、Caddy restart count 和所有 listener。
