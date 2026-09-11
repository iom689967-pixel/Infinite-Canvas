# 显式实例登录与助理权限（第二阶段）

本阶段在第一阶段 `2a863c7dfd6ba3ee02f73a7be8da6faf46b14d7d` 上实现。
仍是一人一实例、一个进程一个数据根；没有 SaaS 用户路由或网页管理员。
只供 loopback 临时验收，不能开放局域网、Tailscale 或公网。未配置任何
`INSTANCE_*` 时，旧单实例行为不变。不要使用 owner 数据或旧启停脚本测试。

## 本机账号命令

风险：这些命令写入选定实例的账号数据库。重置密码/禁用账号会立即撤销该账号
全部会话和实时连接，不需要停止服务。请先确认目标绝不是 owner 的目录。
账号绑定目录标记与实例 ID；每个实例只能创建一个 `assistant` 账号，没有默认密码。

在独立 worktree 中，使用隔离开发环境（不要安装或升级生产 `.venv`）：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

成功标志：依赖安装到当前开发 worktree 的 `.venv`。
认证使用 Python/OpenSSL 自带 scrypt；新增开发依赖 `websockets==17.0.1`
用于真实 WebSocket 验收及显式实例的 Sans-I/O 服务实现。

初始化临时空目录并创建账号（请在交互式终端输入两遍密码，不要把密码填在命令里）：

```sh
assistant_data=$(mktemp -d /tmp/canvas-assistant01.XXXXXX)
.venv/bin/python instance_admin.py --data-root "$assistant_data" --instance-id assistant01 init
.venv/bin/python instance_admin.py --data-root "$assistant_data" --instance-id assistant01 create --username assistant01
```

密码须为 6–1024 字符，输入隐藏。不能提供安全终端时默认拒绝，绝不降级回显。
自动化可显式使用 `--password-stdin` 配合私有进程管道；不要使用带明文密码的
shell 参数、脚本常量、日志或文档。没有注册、找回密码或网页账号管理入口。
成功标志：仅输出操作完成，实例 `.auth/access.sqlite3` 存在，未输出密码/哈希。

启动（当前阶段只允许本机 HTTP 开发例外，端口由管理员确认空闲）：

```sh
INSTANCE_ID=assistant01 INSTANCE_DATA_ROOT="$assistant_data" \
INSTANCE_HOST=127.0.0.1 INSTANCE_PORT=43101 \
INSTANCE_AUTH_ALLOW_HTTP_LOOPBACK=1 INSTANCE_SESSION_TTL_SECONDS=28800 \
.venv/bin/python main.py
```

成功标志：只监听 `127.0.0.1:43101`；访问 `/` 跳转 `/login`，用刚创建的账号登录。
没有账号、目录/身份不符、非法启动配置、第二个进程占用同一数据根均安全失败。
不设置 mock 上游时不能生成；即便登录也不会读取 owner 配置。
`INSTANCE_MOCK_UPSTREAMS` 仅由本机管理员指定独立本地 mock，不可包含 owner/其他实例。
该参数不是助理可修改的配置。测试 fixture 使用假 Provider，不提供共享真实 Provider。

在线管理已绑定账号：

```sh
.venv/bin/python instance_admin.py --data-root "$assistant_data" --instance-id assistant01 reset-password --username assistant01
.venv/bin/python instance_admin.py --data-root "$assistant_data" --instance-id assistant01 disable --username assistant01
```

成功标志：旧 Cookie 的 HTTP 请求返回 401；已有 WebSocket 关闭；禁用后不能登录。
禁用是明确的本机操作，不会因错误密码而永久锁号；本阶段不提供重新启用命令。

## 会话、来源与数据保护

- 密码：成熟 `hashlib.scrypt`，N=2^17、r=8、p=1，32 字节随机盐、64 字节哈希。
  参考 [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)。
  最多两个并发密码计算，禁止裸 SHA-256 密码存储。
- 服务端 SQLite 会话，浏览器只持有 256-bit 随机会话标识；数据库仅存标识摘要。
  `.auth` 目录 0700、数据库 0600，禁止符号链接和 HTTP 用户文件访问。
  账号角色来自服务端，实例/账号/revision/启动标识均参与验证。
- 默认绝对有效期 8 小时，启动配置可选 1–86400 秒；不滑动续期。
  每次登录更换标识；退出撤销当前会话，重置/禁用撤销全部；重启清空所有会话。
- Cookie：独立实例命名空间、Host-only、Path=/、HttpOnly、SameSite=Strict、Max-Age。
  默认 `Secure` 和 `__Host-` 前缀。只有明确的 loopback HTTP 例外才不用 Secure；
  其他主机仍启动失败。不同 Cookie 名不能提供 OS 或同主机不同端口隔离。
- 每个有副作用的 HTTP 请求（包括上传、退出、取消）必须同时携带精确 Origin 和
  当前会话的 `X-CSRF-Token`；token 只保存在页面内存。登录使用精确 Origin + JSON。
  HTTP/HTTPS、主机、端口必须完全一致，其他端口不自动信任。显式模式不加载通配 CORS。
  一律拒绝 Forwarded/X-Forwarded-*，直接启动也关闭 uvicorn proxy headers。
- 登录失败按来源地址汇总，5 分钟最多 10 次尝试；统一错误，429 + Retry-After，
  不泄露账号存在性、不永久锁号。计数存实例数据库，重启不能绕过。
- 认证及私人响应 no-store/private；不记录认证请求体、密码、Cookie/会话标识。
  私人媒体使用 nosniff + sandbox CSP，上传 HTML/SVG 不能作为同源应用脚本运行。
- WebSocket 在 accept 和每次发送前验证会话/Origin；另有 250ms 撤销检查，
  退出、到期、重置、禁用后关闭现有连接。client_id 强制来自服务端 subject。
- LLM request_id 按服务端账号命名空间隔离；图片任务写入 owner 并在读/取消时验证。
  浏览器 role/user_id/instance_id/X-User-ID/UUID 均不能授予权限。
- 第一阶段目录独占锁、路径/符号链接限制、清理继承凭证和出站 mock 白名单继续生效。
  `.auth`、`API`、`.runtime`、实例标记及配置文件额外排除出用户文件解析器；发行清单
  纳入四个认证源码模块，但 `.auth/`、实例标记、用户数据永远不进入 replacement set。

## 路由权限分类

真实路由快照保存在 `instance_access.py`，测试与 `main.py` 路由逐一核对。
新增/未分类路由默认拒绝，不会因为位于 `/api`、`/static` 或 GET 就自动授权。

| 类别 | 范围 |
| --- | --- |
| 最小匿名 | GET `/healthz`（仅 ok）、GET `/login`、两个登录静态文件、POST `/api/auth/login` |
| 已认证身份 | GET `/api/auth/me`、POST `/api/auth/logout` |
| 工作台（97 条已有路由） | Canvas/Smart Canvas、Projects、Conversations、素材/Prompt/历史、实例内上传/预览/下载/导出、受限 Workflow 读写、mock LLM/图片任务及取消、WS stats |
| 管理拒绝（17 条） | Token、Provider 配置/探测、存储设置、共享目录注册/访问、ComfyUI 实例管理等 |
| 阶段禁用（53 条） | 更新/回滚、CLI、RunningHub/ComfyUI/外部生成、视频处理、执行原生 Workflow 等 |
| 静态媒体 | 已认证 `/assets`、`/output` 和实例文件端点；工作台静态文件逐个 allowlist；API 设置、OpenAPI、docs 和未知静态文件拒绝 |

`/api/config`、`/api/providers`、`/api/models` 提供专用最小模型目录，只包含允许的
mock Provider ID/显示名/协议、模型与能力信息，不返回 Base URL、密钥、末四位或环境变量名。
助理可导入/保存自定义 Canvas Workflow，但不能执行原生 Workflow/CLI。
显式实例使用专用入口显示身份、退出和“尚未开放”提示，不影响旧单实例页面。

## 验证记录（本机临时环境）

- 原第一阶段 27 项真实进程/目录隔离测试保留，改为本机创建临时账号后真实登录；
  后台初始化假 Provider，不通过助理管理接口写入，也没有跳过认证的后门。
- 新测试覆盖默认拒绝、scrypt、Cookie/轮换、过期/撤销/重启、A Cookie 在 B 无效、
  伪造角色和身份、敏感配置拒绝、CSRF/Origin/上传、WS 实时关闭、限速、日志和发行边界。
- 浏览器：匿名跳转登录 → 隐藏输入临时账号 → 新建智能画布 → mock 返回 `MOCK OK`
  → 延迟 mock 请求点击停止并恢复运行按钮 → 通过已有粘贴上传功能上传临时图片
  → 64×48 预览 → 返回列表显示已保存 2 个节点 → 退出 → 再访问跳转登录。
  Canvas API 和该图片 URL 退出后均实际返回 401。
- Chrome 文件选择器受扩展文件权限限制；没有扩大权限，使用同一图片的现有粘贴上传路径。
  浏览器无新 JS 错误；现有打包 Tailwind CDN 开发版警告仍存在，未改第三方代码。
- `tests/manual_instance_browser.py` 仅提供随机临时账号、两个独立进程和本地 mock；
  `copy-password` 临时复制到剪贴板且不输出，`finish` 恢复原剪贴板并清理自己的进程/数据。
  测试完成不保留临时账号、会话或图片，不接触生产账号。
- 本轮完整检查 **417/417**（原有 395 项 + 新增 22 项），没有测试 warning。
  另用 macOS `sandbox-exec` 对整个检查进程树禁止写入生产源码/数据目录后重跑，
  同样 417/417、0 warning；该轮前后生产 `data/` 每个文件 SHA-256 均一致。
- 整轮开始/结束的生产 `data/` 聚合摘要有变化，画布与 Kie 缓存出现新时间戳；
  不将其宣称为整轮字节不变，也没有覆盖/回滚这些文件。生产 API 密钥目录、媒体、
  History、Workflow、`.runtime`、`.tools`、`.launchd` 聚合摘要均一致。
  owner 保持 PID 2184、启动时间 2026-09-08 00:13:05，源码 HEAD 保持 `590a041`，
  没有停止、重启、切换分支或修改 LaunchAgent。没有调用 owner HTTP 接口。

运行检查：

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_instance_*.py' -v
./scripts/check.sh
```

成功标志：全部通过，`All checks passed.`，没有新增测试 warning。
新运行文件必须先进入 Git index，才能参加已有 Release Manifest tracked-file 检查。

## 未完成边界

这是登录与应用权限基础，不代表真实 Provider、共享管理员凭证、远程部署或 OS 沙箱。
仍不允许真实付费上游、CLI、ffmpeg/ffprobe、非 loopback、代理/TLS 部署。
不防同一 OS 用户篡改源码/数据库、原生扩展漏洞、硬链接/本机竞态；需后续独立 OS 身份/容器。
不提供注册、邮件找回、跨实例后台、细分多人 RBAC、容量配额或恶意资源消耗隔离。
会话失效会阻断私人输出和 WS；已经授权并开始的后台任务不会自动回滚已保存的数据。
Windows/远程环境未作真实验收。VERSION 不提升，不 push，不合入 main/stable。
