# Public Beta 名额与 Gateway 重启审计

2026-09-13；基线 `00f9bc594e3eb6e9e4eb98d4da9cb6d27cc27ac2`。

## 名额语义与事务

源码实际 `users.status` 仅有 active、provisioning、disabled。instances 表另有
provisioning-owned / starting / running / stopped，它们不是用户状态。

| 用户状态 | 占名额 | 原因 |
|---|---|---|
| active | 是 | 可登录用户 |
| provisioning | 是 | 已预留用户、实例根和端口，尚未初始化完成 |
| disabled | 否 | 禁用，记录与用户数据保留 |
| rollback 后 | 否 | 仅删除 provisioning 的失败预留，不存在失败用户记录 |
| 未知值 | 不允许分配 | fail closed，要求管理员检查，不能当作空闲名额 |

注册的 seat count 与 users/instances INSERT 同处 BEGIN IMMEDIATE SQLite 事务。
disabled → active 的检查与 UPDATE 也在同类写事务中，因此与注册竞争同一个最后名额。
已 active 的重复 enable 不新增名额；provisioning 不能通过 enable 绕过初始化。
完整账户启停与注册/start/stop 共用 supervisor 文件锁，避免并发启停使中央状态与实例权限反向。
disable 先撤销中央会话，再撤销实例会话、更新权限并停止实例；不删除用户工作内容。

管理 CLI `users` 输出改为对象：total_users、active_users、provisioning_users、active_seats、
disabled_users、max_public_users、remaining_registration_slots 和安全的 users 列表。
其中 active_seats 包含 provisioning 预留；其余状态仍分别列出。
当前真实数据按新算法为 total=3、active seats=1、disabled=2、remaining=19，上限仍为 20。
在发布到线上前，旧进程仍沿用旧算法，不能把开发计算值冒充线上已生效值。

## 当前 Gateway 启动结构

- 命令为 venv Python 执行 public_beta.py；内部单进程 uvicorn.run(create_app(config))。
- VPS Uvicorn 0.52.4，监听 127.0.0.1:38000。Caddy 仍反代该单一 upstream。
- service 的 KillMode 为 control-group；ASGI lifespan 退出时还会调用 Supervisor.close()。
- close() 停止本 Gateway 的 children 中仍运行的用户进程；systemd 还会清理同 cgroup 子进程。
- Gateway Session、分配端口、实例 PID/启动时间及 SSO master key 持久化。
- children/Popen、WebSocket 计数与连接为进程内状态；用户实例的 session 与 boot 绑定。
- 用户实例 start_server() 会清除旧 session。一次性 SSO nonce 已写入实例 SQLite，不可重放。
- /healthz 会在 create_app 初始化 registry、lifespan 完成恢复后被 Uvicorn 接受请求；
  但 Gateway 健康不代表先前被停掉的用户实例仍可路由。

## 在隔离 systemd 环境中的真实测量

生产服务、正在使用的真实用户和 Caddy 都没有为此实验重启。实验使用独立临时目录、
随机 loopback Gateway 端口、35000 段实例端口和一个合成测试用户。
对 `/`、`/login`、已认证 `/api/auth/me`、实例 HTML 连续发请求，并正常 systemctl restart。
无生成任务、无 Provider 请求；不是 kill -9 / OOM 模拟。

| 指标 | 原结构 | 仅增加 systemd socket 继承 |
|---|---:|---:|
| 请求总数 | 140 | 55 |
| HTTP 200 | 30 | 28 |
| HTTP 500 / 502 | 0 / 0 | 0 / 0 |
| HTTP 503 | 30 | 27 |
| httpx.ConnectError | 80 | 0 |
| Connection reset | 0 | 0 |
| 最长单请求 | 683.0 ms | 1388.1 ms |
| restart 命令耗时 | 346.8 ms | 348.8 ms |
| 旧 Gateway 会话重启后 | 200 | 200 |
| 旧实例路由重启后 | 503 | 503 |

原结构健康探测的不可用区间包络约 1256.9 ms（20 ms 探测间隔，并非精确事件边界）。
socket 实验中长等待期间 1 秒健康探测 timeout 不表示 listener 关闭。
这些是直接 loopback HTTP 实验，不冒充生产 HTTPS / Caddy 零错误压测。

## Socket activation 评估结论

[Uvicorn 官方选项](https://www.uvicorn.org/)明确有 --fd。还读取了 VPS 实际安装版本的
uvicorn.Server.startup/Config.bind_socket 源码，确认支持显式 fd，但不会自动解析 LISTEN_FDS。
实验使用独立 `.socket` 持有 loopback listener，fd=3 交给真实 Uvicorn，确认传输层排队可用。
[systemd 文档](https://www.freedesktop.org/software/systemd/man/252/systemd-socket-activate.html)
也说明普通模式从 fd 3 开始传递监听描述符。

结论：fd 兼容，但只加 socket unit 不满足本产品的重启要求。连接拒绝消失后，实例停止造成
的 503 和实例 session 生命周期问题仍然存在。不能以此宣称完成无中断发布。
不使用 KillMode=process/none 让未设计接管/回收语义的子进程残留，也不并行运行两个 Gateway。
要继续需先设计用户实例独立生命周期、受控接管/清理、正在执行请求的排空、跨版本更新和首次迁移。

用户允许在当前结构不安全时停止扩大改造。因此本次交付名额修复，Gateway 重启方案保留为
需要后续设计的明确结论，不修改 production service/socket/Caddy 来掩盖错误。
线上当前有真实运行实例；旧 Gateway 的任何重启都会触及其会话。本次不会未经安排直接切换。

## 发布准备

先完成本地检查和开发分支 CI，全部代码/依赖/配置检查须在当前 Gateway 服务期间完成。
现阶段仅名额修复可进入受控维护发布：备份 Gateway SQLite、service、Caddy 配置，确认窗口后
fast-forward beta，切源码并重启 Gateway，等待 health 和测试用户实例路由检查。
现有服务没有承诺无中断能力，不能在维护窗口批准之前对在线真实用户实验。
不改 main / stable / VERSION，不读取用户 Provider Secret，不删除 disabled 用户数据。
