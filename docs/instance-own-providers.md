# 实例范围 API 自主配置

此功能只用于显式独立实例。owner 的原 API 页面和配置源不变。

## 授权（仅本机服务端管理员）

使用开发/部署实例自己的 Python 和 **该实例的绝对 data-root**：

```sh
python instance_admin.py --data-root /absolute/instance-A --instance-id A grant-own-providers --username A
```

撤销用 `revoke-own-providers`。授权/撤销会注销该账号现有会话，需要重新登录。
权限名为 `manage_own_providers`，保存在本实例 `.auth/access.sqlite3`，浏览器不能授予。
成功标志：重新登录后出现“API 设置”；未授权账号访问该页或 API 仍为 403。
这不是 admin 权限，不开放原 `PUT /api/providers`、更新、重启、回滚、CLI 或用户管理路由。

## 配置和 Key

复用 API 设置 HTML/样式；显式实例加载专用交互脚本及现有会话/CSRF 层。
接口为 `/api/instance/providers`（GET、PUT 单个、DELETE 单个）以及 POST `/api/instance/providers/discover`。
所有写入要求同源 Origin、登录和 CSRF。

单一配置源仍为 `.auth/model-access.json`，旧 `providers` 数组属于管理员，只读保留。
可选 `personal_providers` 数组记录个人配置；Key 写入 `.auth/credentials/personal-<随机值>.key`，0600。
配置原子替换；不使用 .env、宿主 HOME、环境凭证、localStorage 或第二份 Provider 配置。
返回值只有 `has_key`，无 Key/凭证路径。PUT 不传 `api_key` 保留、传非空值替换、`clear_key:true` 明确删除。
地址（包括路径）或协议变化时，必须提供新 Key 或清除旧 Key。保存不发网络请求。

## 模型和资源边界

- OpenAI-compatible：任意已保存 llm 模型，GET `<base>/models`、POST `<base>/chat/completions`（通常 base 含 `/v1`）。
- Gemini：原生模型发现及文本/参考图理解，统一 `/v1beta/models` 和 `:generateContent`。
- Kie：复用 `gpt-image-2`、`nano-banana-pro` 图片调用链；获取模型显示已有适配目录，不声称远程连接验证。
- 未适配模型/用途可保存，显示不可运行，并从画布可运行目录排除；未配置 Key 或停用同样不能运行。
- CLI、视频生成/处理、任意认证头及单次请求覆盖上游地址仍禁用。
- `max_concurrent` 不可由个人设置修改。个人模型使用服务端保守资源上限，并受现有管理员同类模型更低限制约束。

## 网络

默认仅公网 HTTPS 443；每次模型发现、生成、上传、轮询、媒体下载都通过 GuardedClient。
每次 DNS 结果必须全数获准；连接时审计目标 IP/端口必须属于本次已检查集合。
不跟随任何重定向、不继承代理环境、不转发 Cookie；认证头只发往配置认证主机或固定 Kie 上传主机。
个人 Provider 的媒体 CDN 不要求逐个改代码，但仍强制公网 DNS 和连接校验。

管理员可在同一个 model-access.json 添加可选 `private_targets`：

```json
[{"origin":"https://lan-gateway.example:8443","ips":["10.20.30.40"]}]
```

仅该精确 HTTPS origin 和解析 IP 获准，不接受 CIDR；不接受 loopback、元数据 link-local 或 owner 3000/本实例端口。
私网授权不提供浏览器编辑接口。受控开发用的 HTTP loopback mock 仍只允许启动时的精确 `INSTANCE_MOCK_UPSTREAMS`。

## 运行中变更

有活动 LLM/发现/图片任务，或上游提交状态未确认时，配置写入返回 409，完全不变更配置。
每个新图片任务私下记录配置+凭证摘要；刷新旧任务前比对摘要，配置改变则拒绝查询，绝不重提。
旧版缺少摘要的任务不能拿新凭证查询。服务重启仍不自动重放 createTask。

## 验证

`tests/test_instance_own_providers.py` 使用两套真实临时 ASGI 进程、独立根目录、假 Key 和本地 mock。
覆盖权限/CSRF、CRUD、保留/替换/清除、重启、相同 ID 跨实例、三类协议、越权、重定向、DNS/连接边界和运行中变更。
原未授权拒绝和隔离测试保留。运行 `./scripts/check.sh`；浏览器验收须另外报告，不以 HTTP 测试替代。

### 2026-09-08 页面验收记录

两个临时实例、临时账号和假 Key；唯一页面模型调用发往本地 mock，无付费请求。
已在页面验证：授权入口、新增 Provider、模型发现、手动模型、保存/刷新/重启恢复、
Key 不回填及保留/替换/明确清除、修改名称、停用后从 Smart Canvas 选择器移除。
Smart Canvas 手动模型调用得到 `MOCK garment text`，退出重开后结果和模型选择仍在。
B 无设置入口、无 A 的个人配置；HTTP 复核设置页/配置和更新/回滚/CLI 越权均被拒绝。
修复设置页翻译资源权限后，标题及“新增平台”正常显示；全量检查 453/453 通过。

补充删除验收：旧临时标签已不存在，未确认任何旧弹窗。在新建的隔离临时实例中创建
DELETE ONLY - QA TARGET 和 KEEP - QA CONTROL。触发删除前注册一次性 dialog handler，
校验临时页面 URL、confirm 类型、文案及已选 Provider ID，再用正式 dismiss/accept 接口处理；无需人工点击。

- 取消：无 DELETE 请求；配置及凭证摘要完全不变，刷新后仍存在，qa-delete-model 可在页面选择。
- 确认：仅一条目标 DELETE 请求，HTTP 200 后页面显示“已删除”；刷新、退出重登后仍不存在，模型不再可选。
- 目标个人 Key 文件移除；对照 Provider 配置/凭证、B 配置、既有画布、历史及 mock 结果文件摘要不变。
- 文件基线在画布列表首次自动补齐 board_x/board_y 布局元数据后采集；删除未改动这些文件。
- 本轮页面 error 日志为空；只补充浏览器验收和此文档，未改应用/测试代码，沿用上轮 453/453，不声称重新跑过全量检查。

B 的拒绝导航期间曾捕获无页面 URL、Electron 沙箱调用栈的 MutationObserver 异常。
现有证据更指向浏览器自动化控制环境，尚无法完全排除其他来源；未复现为正常页面应用错误，
也不据此宣称所有环境零错误。未修改浏览器安全设置或删除确认机制。
