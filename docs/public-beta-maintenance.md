# Public Beta 维护门禁与首次旧版本升级

开发基线为 `dee70bfc1754a1877bfc7474cbe2a8767bb997cc`，完整个人 API 的能力解析和适配器保留。本轮仅开发、临时环境演练和 CI，不部署。生产事实必须在下次部署重新读取，本文没有把历史 PID、人数或路径当作现场数据。最终对象是包含本工具的新提交及其 CI，不能复用 dee70bf 的 CI 作为最终验收。

## 工具入口与权限

服务器管理员在批准的 release/venv 内运行 `public_beta_maintenance.py`；没有公网管理接口。实际操作需要 root；`--sandbox` 仅允许系统临时目录，用于离线测试。普通公网账号无权修改维护文件，也不能通过 Provider 设置、更改 env 或传入 HTTP header 绕过门禁。

以下命令是后续授权部署的模板，本轮没有执行。变量必须来自现场核实的 systemd 配置、Supervisor 注册表和私有配置；不要使用猜测的 PID、用户名或端口。命令会使新任务返回预期 503，`sealed` 期间读取/保存也可能暂不可用。先通知用户保存画布、暂停操作；通知不能代替门禁。

```sh
# 示例路径是部署计划，不能直接当作当前生产路径。
MIO_PY=/opt/mio-canvas/venvs/APPROVED_SHA/bin/python
MIO_RELEASE=/opt/mio-canvas/releases/APPROVED_SHA
MIO_CONTROL=/opt/mio-canvas/release-control
MIO_GATEWAY_DB=/现场核实的/gateway.sqlite3
MIO_INSTANCES=/现场核实的/instances

# 仅首次建立，UID/GID 来自现场运行服务；不能重新 init 既有目录。
sudo "$MIO_PY" "$MIO_RELEASE/public_beta_maintenance.py" --root "$MIO_CONTROL" \
  init --worker-uid ACTUAL_SERVICE_UID --worker-gid ACTUAL_SERVICE_GID

sudo "$MIO_PY" "$MIO_RELEASE/public_beta_maintenance.py" --root "$MIO_CONTROL" draining
sudo "$MIO_PY" "$MIO_RELEASE/public_beta_maintenance.py" --root "$MIO_CONTROL" \
  status --gateway-db "$MIO_GATEWAY_DB" --instances-root "$MIO_INSTANCES"
sudo "$MIO_PY" "$MIO_RELEASE/public_beta_maintenance.py" --root "$MIO_CONTROL" \
  drain --gateway-db "$MIO_GATEWAY_DB" --instances-root "$MIO_INSTANCES" --timeout 60
```

`init` 默认 draining。`open` 明确解除，`draining` 开始维护，两者重复执行幂等，不重启业务进程。没有直接设置 sealed 的命令；只有成功排空才封口。超时 60 秒只是停止等待的期限，不是旧请求生命周期上界，更不是到了就可以重启。

Runtime 使用 `MIO_MAINTENANCE_ROOT`；没有显式变量时，Gateway 的默认位置是 `PUBLIC_BETA_ROOT` 父目录下的 `release-control`。Supervisor 将这个固定路径注入新 Worker。门禁缺失、格式/权限错误时拒绝接单，不自动初始化成 open。仅显式进程内测试工厂可以用 `maintenance_root=None`；正常 from_env 启动不使用这个豁免。部署必须核对 Gateway、Supervisor 和全部新 Worker 的相同控制目录。

当前实现复用已有共享服务 OS UID、Python Instance 隔离的部署形态；如果现场改成每用户独立 OS UID，先停止并审查 journal 权限，不能 chmod 777 解决。

| release 外文件 | 归属/权限 | 用途 |
|---|---|---|
| 控制目录 | root:service-group，0750 | 不允许 Worker 替换管理状态 |
| `state.json` | root:service-group，0640 | schema/phase/epoch，重启保持维护 |
| `gate.lock` | root:service-group，0660 | 跨 Gateway/Instance/admin 的同一 flock |
| `activity/` | service UID，0700 | 私有活动 journal |
| `activity/journal.sqlite3` | service UID，0600 | 活动、进程身份、未知请求、人工核对审计 |

状态不在 release 或任何用户数据根中。Instance 仅在服务器内部 ContextVar 能力作用域内读固定 state、锁固定 lock、写固定 journal；不开放该目录到文件 API，不授予任意文件/网络能力。sqlite3.connect 也经过 Instance 文件边界。这个机制维持现有应用隔离，不宣称同 OS UID 下任意服务器代码的 OS 沙箱；公网任意代码、Shell/CLI 仍关闭。

## 行为门禁与活动生命周期

`maintenance_routes.py` 是原生门禁和 Caddy 屏障共享的路由行为表。未知路由在维护期间默认拒绝，不用 GET/POST 粗略判断。

| 行为 | draining | sealed |
|---|---|---|
| 图片/编辑/视频、LLM/流式/Agent、caption/classify、角度、Midjourney 后续动作、探测连接 | 拒绝 | 拒绝 |
| 上游/本地素材上传、外链导入、新 Avatar 注册 | 拒绝 | 拒绝 |
| 账号注册、邮箱验证/重发等注册流程 | 拒绝 | 拒绝 |
| 已接收 HTTP 与后台 runner 的子活动 | 完成原工作 | 完成原工作；存在则无法成功 sealed |
| 原任务 query/refresh/download/recovery、停止本地等待 | 允许并计数 | 暂停新请求 |
| config/画布/历史/媒体读取、画布保存、私有设置保存、登录/交接/退出 | 允许并计数 | 暂停新请求 |
| 健康、只读登录页面及程序静态资源 | 允许；不含模型操作 | 允许；不含模型操作 |
| stats WebSocket | 原生通道没有生成操作，不作排空证据 | 重启时断开；刷新/重登后重新连接 |

sealed 是备份/重启前短暂稳定检查点，draining 保留正常恢复路径。临时旧版入口只转发 HTTP，stats WS 在临时入口期间不可用；不会把它作为模型活动为零的证据。外部 sealed 屏障也不能替代旧业务内部活动证明。

原生拒绝 HTTP 503，JSON 为 `{"detail":{"code":"maintenance","message":"工作区维护中，暂时不能提交新任务，请稍后再试。"}}`，并带 `X-Mio-Maintenance: 1`、Retry-After、no-store。拒绝发生在读取上传 body 和发送上游请求之前。前端把它作为明确维护错误，保留文本输入、参考附件与节点输入；不排队、重试或重放。Session bootstrap 遇到维护不会伪装为失效并跳转隐藏输入。

`instance_maintenance.Maintenance.admit()` 在同一个跨进程 flock 内完成检查状态和插入活动；admin 进入 draining/sealed 也使用该锁。Gateway 已接收的随机 admission ID 只在私有 loopback 请求中交给 Worker；公网传入的同名 header 被丢弃，必须匹配仍存活的 Gateway 活动及用途。后台 runner 在创建 Task 前登记独立 lease，并在整个 runner 作用域继承该 lease，HTTP 完成后仍可写原结果。

`MaintenanceMiddleware` 覆盖慢 body 接收、鉴权前的已接收请求、完整 ASGI 响应体/后台工作/关闭 finally。SSE 不在返回响应头或最后字节时提前释放。后台图片/视频 runner 完全结束后释放；`tracked_to_thread()` 对真实线程完成做登记，取消等待不会让仍在写盘、登录或 DNS 的线程消失。普通 LLM 在实际提交前持久登记未知收据，完整 JSON/SSE 正常结束后才清除。断开、超时、异常、取消只释放结束的本地活动，不证明远端取消或退款。

管理员 `status/drain` 输出 schema/phase/epoch、安全总计/用途阶段、orphaned、未知请求数、账本阻塞及 restart_safe；不输出 Prompt、Key、Cookie、验证码、完整素材 URL。`unknown_llm_responses` 包含原生 LLM 和临时入口未知旧 HTTP 请求，不能把后者理解为“仅 LLM”。进程 PID/starttime 不匹配的活动保留为 orphan blocker，不自动删掉。

排空同时读取全部账户的原 Instance ledger、未完成/提交不确定/结果待下载任务、Avatar 注册状态；关闭或未运行账户的未知状态也不能遗漏。数据库损坏、不可读、归属不符一律阻塞。新 Gateway 与运行/starting Worker 必须有匹配实际 PID/starttime 的原生进程登记；旧进程即使 journal 为空，仍返回 `legacy_or_unobserved_process`。

仅当门禁已关闭、HTTP/runner/stream/upload/thread/write/query/recovery 活动全为零、没有 orphan/未知收据/账本阻塞且进程覆盖已确认时，自动转 sealed 并报告 `restart_safe=true`。等待超时 exit 3，JSON `timed_out=true, action=stop_upgrade`；证据/状态错误 exit 2。工具不停止、杀死或重启进程，不改任务 ledger，不自动回放。

计划启动期间的服务器初始化/迁移单独登记为 local_write，允许在 sealed 下完成必要启动，并阻塞再次报告排空；这不是公网新工作豁免。注册表中仍为 starting 的 Instance 同样阻塞。需要等 startup complete、Supervisor 已核对 running、写入线程真正结束后再作后续检查。

## 5449 首次升级：先在业务外阻止新工作

旧 `5449a692da33200f8d621a8974414da5e4c9de7c` 没有原生门禁；已核实历史部署也曾混合使用更旧 b5c0 Worker。不能先重启旧进程安装门禁再声称重启前排空。部署时仍须核实实际旧目录和 SHA，若与演练范围不符先停止审计。

入口采用现有 Caddy 的 Mio **精确单域名 subroute**，不修改 Sub2API 路由；工具拒绝多域名共享/歧义路由。保存 Caddy 当前完整 JSON 到管理员私有文件，比较 Mio 之外内容不变，生成新文件后先 validate、再 reload，绝不 restart Caddy：

```sh
sudo "$MIO_PY" "$MIO_RELEASE/public_beta_maintenance.py" --root "$MIO_CONTROL" caddy-barrier \
  --input /管理员私有目录/caddy-before.json --output /管理员私有目录/caddy-draining.json --host ACTUAL_MIO_HOST
sudo caddy validate --config /管理员私有目录/caddy-draining.json
sudo caddy reload --address ACTUAL_LOCAL_ADMIN_ADDRESS --config /管理员私有目录/caddy-draining.json
# 排空证据确认后才生成并加载更严格屏障：
sudo "$MIO_PY" "$MIO_RELEASE/public_beta_maintenance.py" --root "$MIO_CONTROL" caddy-barrier \
  --input /管理员私有目录/caddy-before.json --output /管理员私有目录/caddy-sealed.json --host ACTUAL_MIO_HOST --sealed
```

完整配置可能含非 Mio 的私有信息，不能打印、上传日志或放公共目录；文件 0600。部署审批时还要核对 Caddy 的权威启动配置来源，保留 Mio include/私有 JSON 的维护版本，避免随后 reload/reboot 从旧 open 配置解除屏障；不能仅依赖当前进程内配置。本轮未读取/修改生产 Caddy。

实测官方 Caddy 2.11.4：共享 reload 后原有限 HTTP/SSE 流继续结束，但已有 unrelated WebSocket 被关闭，尽管 unrelated 路由内容完全不变；与[官方 reverse_proxy streaming 说明](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#streaming)一致。下一次审批必须明确这个共享连接影响；不能承诺 Sub2API WS 零中断，也不能事后设置 stream_close_delay 假称旧连接受保护。演练的 unrelated 服务是独立 mock，不是真实 Sub2API。

Caddy in-flight metrics 在 reload 后会重建，实测可由非零重置为零，所以不能单独证明旧工作结束。临时 `public_beta_maintenance_ingress.py` 提供跨 reload 的私有 HTTP 生命周期 journal，只绑定 loopback、只转发固定 loopback Gateway，不接受任意目标、重定向或公网管理请求，保持原 Host/Origin/CSRF/Cookie/可信代理边界。它不保留跨用户 cookie jar。

该观测器是首次升级期间的临时进程，所有原生门禁加载并恢复 Caddy 直连 Gateway 后移除，不建立长期服务。批准后以服务 UID、原匹配 venv 启动，核实 loopback 监听并仅替换 Mio reverse_proxy dial；其他路由字节保持不变：

```sh
"$MIO_PY" "$MIO_RELEASE/public_beta_maintenance_ingress.py" --root "$MIO_CONTROL" \
  --target http://127.0.0.1:ACTUAL_GATEWAY_PORT --public-host ACTUAL_MIO_HOST --port APPROVED_LOOPBACK_PORT
```

**首次生产安全限制：观测器不能追溯切入前的请求。** 当前旧代码不存在覆盖所有路径的可靠总时长上界：客户端慢上传/JSON 接收；上传完成前及结果落盘阶段；普通/流式 LLM 与自动描述分类的上游/关闭处理；metadata/fetch-models/discover 的 body/上游等待；旧同步图片/编辑/视频请求及无收据异常。HTTP read timeout 不是总时长上界。旧异步模型 ledger + 实际原任务 API `local_wait_active=false`、原收据下载和完成状态能核对已识别 runner，但不能证明上述不可见工作不存在。

因此，生产默认 `drain` 对旧进程阻塞；不能使用测试底层 inspect、空新 journal、health 200、空 ledger、TCP 暂空、无日志或任意 sleep 绕过。下一次部署先启用 Caddy 屏障，盘点全部原收据并收集可信生命周期证据。凡切入前工作缺乏结束证据，停在预检，列出具体旧进程/请求类型和需单独批准的中断范围；若需无收据供应商人工核对，同样先停。本文没有声称现有未知旧生产活动可自动无损排空，不使用调试注入、强杀或自动重试制造“零”。

## 离线真实旧代码演练与证据

执行 `tests/rehearse_legacy_upgrade.py`：git archive 提取真实 5449 Gateway/Supervisor、b5c0 与 5449 Worker，不修补旧源代码。旧进程使用临时新数据根、假凭证、mock 上游、真实 loopback TLS Caddy、HTTPS public Origin、Secure cookie、CSRF 与可信代理边界；没有系统 CA 安装。控制所有 mock 请求在临时观测器安装后才开始，因而结束证据具有连续性；这是可重复用的旧版演练，不能追溯证明真实生产切入前请求。

```sh
# 完整依赖使用已准备的临时开发 venv，Caddy 只下载到临时工具目录。
python scripts/install_rehearsal_caddy.py /系统临时目录/mio-rehearsal-caddy
python tests/rehearse_legacy_upgrade.py --caddy /系统临时目录/mio-rehearsal-caddy \
  --evidence /系统临时目录/mio-first-upgrade-evidence.json
./scripts/check.sh
python -m pip check
```

| 演练阶段 | 证据/断言 |
|---|---|
| old_started | 真实旧 SHA、两个旧 Worker PID/starttime、混合启动目录 |
| shared_caddy_reload_effect | shared SSE 正常结束，WS 断开，unrelated 路由不改，业务 PID 不改 |
| barrier_enabled_without_old_restart | 原 mock LLM 与慢上传继续计数，业务进程尚未重启；Caddy metric 非排空证据 |
| new_work_rejected | 8 个实际生成/上传/注册入口明确 503，意外 500/502=0，拒绝造成上游请求=0 |
| drain_timeout_stopped_upgrade | 活动仍计数，旧 PID 不变，慢上传未被中断，强停=0 |
| legacy_drained | 原 LLM 完成、原上传完成、异步图片 succeeded/local_wait_active=false、原结果下载、journal/未知/ledger 同时清零 |
| new_supervisor_adopted | 加载新 Gateway/Supervisor，原 Worker PID 仍被接管 |
| serial_roll_complete | Supervisor UDS 一停一启，cap=2 未超限，两个 Worker 新 PID，数据根不变、仍 sealed |
| reopened | 原 Provider 顺序与文件/凭证引用不变，设置/config 原 3 个图片模型保留；原节点、History 与媒体可读；实际重新登录 |
| rollback | sealed Caddy 下加载旧代码，保留当前数据，不回灌旧 DB，原图片提交次数=1、生成重放=0 |

安全 JSON evidence 由 CI 上传 artifact `mio-first-upgrade-evidence`。另有原生 Gateway/Worker、并发提交/门禁、真实受控 LLM JSON/SSE、断开/超时/取消、慢接收、未完线程写盘、未知提交、坏 ledger、进程异常、重复开关、人工核对和实际前端函数测试。已有 797 项保留，旧 Supervisor IPC fixture 仅补 root 维护目录初始化/显式 open，原 SIGTERM/crash/WS/Session 断言不删不跳过。最终数量和新 SHA/CI 在交付记录中给出。

## 正常维护、回滚与后续部署顺序

已获明确批准、且只剩精确指定历史未知记录时，可使用[受限单次维护中断](bounded-maintenance-interruption.md)。它直接兼容f598原生门禁，保留记录与restart_safe=false；不得用它代替原drain或reconcile的证明条件。

1. 只读现场核实 beta/目标关系和目标 CI、systemd WorkingDirectory/ExecStart/venv、Gateway/Supervisor/全部 running Worker 的 PID/starttime/UID/数据根/SHA；注册状态/人数/容量/磁盘/健康/原任务。beta 有新提交/分叉则停止，不覆盖或 force push。
2. 已有原生门禁用 draining；首次旧版先准备并 validate Mio 入口屏障。保存维护状态和 Caddy 权威来源。通知用户保存画布。注册可保持原 open 配置，因为门禁真正阻止注册；不靠关闭注册挡生成，也不更改容量、安全阀、真实 Key。
3. 保留原收据查询/恢复/下载，禁止自动补交。连续证据不足或未知状态未核对时停止，不重启。原生 drain 成功后进入 sealed；旧版按上一节明确证据/额外批准边界处理，不用底层测试接口自签排空。
4. 排空后备份 Gateway SQLite、各 Instance 认证/Provider/ledger/画布/History/媒体、维护 journal/state、生产 env/systemd units/旧 release/venv。仍运行的 SQLite 用 online backup，逐库 integrity_check；备份私有且不输出秘密。复用既有备份流程，不能把变化中的普通文件复制视为一致性证明。
5. 独立新 release + 匹配 venv，pip check；批准后 beta 安全快进到本次最终 SHA。修改获批服务的启动目录/程序/venv，并按一次正常计划加载 Gateway/Supervisor。确认 Supervisor 默认保留/adopt 旧 Worker，不能 stop-all；其他服务、VPS、Caddy均不 restart。
6. 首个已排空 Worker 用既有私有 Supervisor UDS stop/start；验证后再串行其余。可用管理员 Python 中 `SupervisorClient(GatewayStore(BetaConfig.from_env())).call('stop', instance_id=...)` 与 `call('start', instance_id=...)`，在正确服务 env/venv 内运行；UDS 固定 schema、root/服务 UID 权限，无 Shell 路径字段。按注册表 instance_id 调用，不直接杀 PID/改业务数据库。每个一停一启，不超过现场运行实例上限。
7. 核对各 PID/starttime、新程序/版本、原数据根、Provider 顺序/凭证引用、目录、原节点/History/媒体及 ledger 无重放；全部新 Worker 仍维护。未运行账号不启动，但 Supervisor 下次启动路径/venv 和 maintenance root 必须已指向新版本，新注册同样使用它。
8. 首个目录/执行初始化/数据/隔离异常就停止后续滚动，保留证据。保持入口维护，按兼容的旧 release/venv 和原 Supervisor UDS 串行回滚程序，不回灌旧数据库覆盖新数据，不清 ledger/Key。旧代码回滚后没有原生门禁，Caddy 屏障必须继续保留；先核对原任务再申请恢复入口。默认 session/WS 可能失效，需要用户在网页重登；不能索取密码/Cookie/验证码。
9. 新版加载完整且静态验收通过后，先恢复 Mio 直连 Gateway（原生仍 sealed），转 draining 允许配置/原任务读取和登录，验证现场设置→config→Canvas 实际计数。最后管理员 `open`，再核实注册原状态、容量、健康及预期维护 503/意外 500/502 分开记录。临时观测器在计数归零后退出。维护失败可选择停止升级后明确 open 撤回本次维护，但不能把撤回称为排空成功。

人工核对 `reconcile --id ... --evidence-file ... [--uncertainty] --gateway-db ... --instances-root ...` 仅用于供应商原工作确实结束、原 ledger 已解决且相关本地活动已结束后的管理员确认。私有文件 schema=1、id、original_remote_work_ended=true、original_ledgers_reviewed=true；实际供应商凭据/证据另存私有审计位置，布尔文件是管理员声明，不是工具自动验证上游。保留 hash/epoch 审计；禁止清除存活进程活动、把本地断开当远端取消、批量清 journal 或自动回放。

首次 Caddy reload 的共享 WS 影响、切入前旧不可观测活动的具体中断，以及生产 release/venv/systemd/逐 Worker 重启，全部属于后续生产审批。本轮生产写入/重启、真实模型、真实素材上游上传、真实邮件发送均为 0。没有继续扩展或重写模型适配器。
