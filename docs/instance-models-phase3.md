# 助理实例受控模型调用（第三阶段）

基于 `78e0e94f999903cca1421bb1e0ef46faeefd2e38`；开发分支
`codex/controlled-assistant-models`，独立 worktree。不发布、不升 VERSION。
本轮只有假凭证、本地模拟上游；真实模型和费用未经验收，必须另获确认。

## 开放范围和限制

认证后的 Smart Canvas Prompt LLM：Gemini 原生 `generateContent`，文本及本实例参考图；
Smart Canvas 图片任务：现有 Kie 适配器的图片生成、参考图上传、查询、下载和保存。
Provider ID 完全由管理员指定（测试用 `atelier-text` / `atelier-images`），根据协议而非
显示名称识别。Gemini 模型 ID 由管理员批准；Kie 只能从现有适配器支持的
`gpt-image-2`、`nano-banana-pro` 中进一步收窄，不接受内部路由名或任意模型。

普通会话页仅保留数据管理，配置受控模型时通用 `/api/chat`、`/api/chat/stream`
调用尚未开放；本阶段模型验收入口是 Smart Canvas。视频、CLI、ffmpeg/ffprobe、Shell、
原生 Workflow 执行、共享目录管理、Provider 网页管理、更新/回滚/重启继续禁止。
素材管理仍可用，但自动素材描述/分类的旁路模型入口也保持“尚未开放”。
未显式启用实例模式时不改变原来的单实例调用链。

## 管理员配置（仅选定实例）

风险：配置决定可消耗的外部调用权限。以下命令只可指向已初始化的助理实例，不能指向
owner；不读取/复制 owner 的 `API/.env`、Provider、Codex 认证或父进程凭证。
没有模型策略时不开放 Gemini/Kie 真实出网；保留前两阶段管理员明确配置的本地 mock
兼容链路，没有任何 owner 回退。策略存在但缺字段、凭证或权限不合格时拒绝启动。

1. 按第二阶段文档初始化空实例、创建账号。使用独立开发环境，不安装到生产 `.venv`。
2. 创建或轮换**助理专用**凭证，值通过终端隐藏输入，不能写入参数、文档或日志：

```sh
.venv/bin/python instance_admin.py --data-root "$assistant_data" --instance-id assistant01 set-model-credential --credential-name gemini.key
.venv/bin/python instance_admin.py --data-root "$assistant_data" --instance-id assistant01 set-model-credential --credential-name kie.key
```

成功标志：仅输出操作完成；`.auth/credentials/` 为私有目录，凭证文件 0600。
自动化可使用 `--credential-stdin` 及私有进程管道（禁止 shell 明文串或回显）。
轮换是原子替换；后续调用重新读取，在途请求可能仍使用旧凭证。删除凭证后新调用不可用；
要撤销供应商权限，仍须管理员在供应商侧撤销，不能把本地删文件当作云端撤销确认。

3. 在实例 `.auth/model-access.json` 建立策略，先设 `umask 077`，用本机编辑器填写。
   下面**仅是本地 mock 格式示例**，端口必须对应管理员自己的模拟服务，不可指向 owner。
   这份实例配置不放入源码仓库或发行清单。

```json
{
  "schema_version": 1,
  "mode": "mock",
  "max_concurrent": 2,
  "providers": [
    {
      "id": "assistant-text",
      "protocol": "gemini",
      "base_url": "http://127.0.0.1:43103/v1beta",
      "credential_file": "credentials/gemini.key",
      "models": {
        "gemini-mock-vision": {
          "max_references": 2, "max_reference_bytes": 1048576,
          "timeout_seconds": 120, "max_output_tokens": 1024, "max_text_chars": 10000
        }
      }
    },
    {
      "id": "assistant-images",
      "protocol": "kie",
      "base_url": "http://127.0.0.1:43103",
      "upload_base_url": "http://127.0.0.1:43103",
      "media_origins": ["http://127.0.0.1:43103"],
      "credential_file": "credentials/kie.key",
      "models": {
        "gpt-image-2": {
          "max_references": 2, "max_reference_bytes": 1048576,
          "timeout_seconds": 900, "max_images": 1,
          "sizes": ["1024x1024"], "resolutions": ["1K"],
          "aspect_ratios": ["1:1"], "output_formats": [""]
        }
      }
    }
  ]
}
```

设置该文件为 0600；配置及凭证均禁止符号链接和越界路径。
Gemini 最终路径由既有原生适配器规范化成 `/v1beta/models/<获批模型>:generateContent`。
Kie 的上传源与结果媒体 origin 都必须明确配置；不由浏览器填写。
`output_formats` 对 gpt-image-2 是空字符串枚举，对 nano-banana-pro 使用 png/jpg 子集。
尺寸、分辨率、比例、数量、参考图字节上限都由后端再次校验，不能靠改前端绕过。

4. 只启动这个临时实例，保留第二阶段认证配置，并设置
   `INSTANCE_MOCK_UPSTREAMS=127.0.0.1:43103`。仍只监听 `127.0.0.1`。
   成功标志：登录后模型列表只有批准 ID；未批准调用 403，未配置凭证 503。
   策略修改须重新启动**该临时实例**，不要操作 owner；凭证轮换无需重启。

将来真实验收需管理员显式改成 `mode: live`、批准的精确 HTTPS 443 地址、模型和专用凭证。
不得在本轮执行。出网只在受控客户端请求期间允许经 DNS 检查的公共 IP；内网、loopback、
链路本地、文件 URL、跳转、系统代理均拒绝。DNS 解析超时有界，连接 IP 必须属于已审查集合。
上传认证 Header 不带到下载域；下载限 32 MiB，并校验 PNG/JPEG/WEBP 和最多 3200 万像素。
没有放开全局出网。HTTPS/SNI/TLS 实际供应商兼容性仍需后续真实验证。

## 凭证和数据边界

| 内容 | 当前实例路径 / 处理方式 |
| --- | --- |
| 策略、凭证 | `.auth/model-access.json`、`.auth/credentials/*`；0700 父目录 / 0600 文件 |
| 任务账本及上游 ID | `.auth/model-tasks.sqlite3`；本地 task ID 对应服务端实例账号 subject |
| Kie 上传映射 | `.auth/reference-cache/`；按 Provider 和凭证摘要分命名空间，轮换不复用旧上传 |
| 原图、生成结果 | 当前实例 `assets` / 当前实例 storage resolver；媒体继续鉴权 |
| History / Preview / Canvas | 当前实例 `history.json`、`data/media_previews`、`data/canvases` |
| 日志 / 临时文件 | 当前实例 `.runtime`；不输出上游原始错误、地址、认证 Header |

只有实例 Python 服务和管理员本机命令需要读取该实例的凭证。相同 OS 用户、root 或能控制
源码的进程仍可能读取；0700/0600 和“前端看不到”不是 OS 沙箱。独立 OS 身份、容器、
硬链接/本机竞态对抗以及管理员恶意配置不属于本阶段完成的边界。

浏览器只收到最小模型 ID、协议、能力/限额；没有 Base URL、密钥、末四位或凭证文件名。
上游原始错误不向浏览器返回；LLM 文本对实际使用的凭证及私有地址做替换。
Canvas / Workflow 只保存用户输入和本地结果 URL，不注入调用配置或上游任务 ID。
`.auth` 不能经静态、下载、预览或导出文件解析器读取，也不进入 Release Manifest。
本轮没有新增依赖或修改生产虚拟环境。

参考图只能为当前实例的相对媒体 URL。后端检查路径、穿越、符号链接、格式、尺寸和字节数；
Gemini 后端读取并转 inlineData，Kie 使用既有后端官方上传适配器。Cookie、Session、API Key
不会进入参考图 URL；不开放 assets，不让供应商访问 owner 或本机登录媒体 URL。

## 任务、费用和停止语义

请求必须通过原有认证、精确 Origin、会话 CSRF；新 refresh 路由已逐项加入默认拒绝分类。
查询/取消/刷新只能凭本实例账本中的本地任务 ID，且 owner 必须匹配服务端 subject。
外部 task_id、role、user_id、instance_id 和客户端 UUID 都不能取得他人任务。
媒体/下载仍需登录；退出后不能获取私人响应或 WS 数据。在途已授权任务不因退出自动退款。

- Gemini 限额：参考图数量/字节、总文本长度、输出 token、固定单候选；请求超时取
  `CANVAS_LLM_TIMEOUT` 与管理员限额中的较小值（管理员最多 120 秒）。
- Kie：单批最多 1–4 张，分辨率、比例、size 和输出格式白名单；按既有适配器逐张提交。
  轮询上限由模型配置决定，最多 1800 秒，独立于 LLM；单次网络等待 120 秒。
- 实例并发配置 1–8，LLM 和图片批次共用占位；每个图片批次内部顺序执行。
  已本地停止但云端未确认终止的图片任务继续占位，避免绕过并发反复提交。
- 提交前持久化账本；相同 request_id 内容不同返回 409；同内容在途或结束后 60 秒内重复
  点击复用原任务。UI 一批只发一次 n 张请求。不会因网络错误自动重发生成。
- 已拿到上游 ID 可 POST `/api/canvas-image-tasks/<本地ID>/refresh`，仅查询原任务，
  不补交缺少的批次。断线丢失 ID 或批次后续提交状态不明标为 manual-reconcile，
  即使已知部分成功也保留未知占位，需要管理员在供应商侧核对。没有网页强制解锁入口。
- 重启不自动恢复付费提交；持久化账本保留归属、去重和未知状态。账号仍须重新登录。
  已知任务可手动查询；尚未提交的重启中断任务也不自动执行。
- LLM 停止关闭本地连接；保留 request token、finally running 清理、立即重跑及多节点隔离。
  Kie 停止是**本地停止等待**，不表示供应商停止计算或退款。现有适配器没有供应商取消确认。

这不是计费系统：没有日/月费用上限、供应商余额同步或自动对账。请在供应商侧使用
助理专用且可撤销/限额的凭证。管理员批准参数并不等于费用已经核实。

## 第二阶段生产摘要补充

上一阶段生产 `data/` 聚合摘要确实变化，不宣称整轮始终无变化。已有审计证据中可指出：

- `data/canvases/d0f7097e58394b80bc2081d245337bb3.json`：mtime 2026-09-08 11:40:22.540126 +0800。
- `data/kie_reference_cache.json`：mtime 2026-09-08 11:40:08.250599 +0800。

两者晚于第二阶段分支创建时刻 11:01:07；当时没有保留整轮开始的逐文件摘要，故不能证明
只有这两份文件内容变化，也不能从 mtime 判断写入进程。**来源未确定**，不归因为已证实
owner 自动保存，不回滚。此前加 OS 写入禁止后执行的完整 417 项检查前后逐文件摘要一致。

## 验证与后续真实请求计划

自动测试使用两个真正独立进程、两套随机临时账号/假凭证和一个本地 mock，上游验证凭证
摘要归属但不打印密钥。覆盖登录后原图上传 → Gemini inlineData → Kie 上传 → createTask
→ recordInfo → 下载校验 → 本实例保存、history、preview、Canvas；跨实例和敏感接口拒绝。
另覆盖限额、参数篡改、任务归属、CSRF 原有回归、凭证轮换、部分批次未知提交、重启去重、
断网恢复只查询原 ID、SSRF/跳转、错误脱敏及前端单批提交和旧结果保护。

运行 `./scripts/check.sh`。本轮完整检查对测试进程树额外施加 macOS 生产目录禁止写入，
不改变 owner 服务权限。测试数量、浏览器实际完成程度及生产摘要结果见本轮最终报告。

本轮浏览器已完成：临时账号登录 → 新建智能画布 → 粘贴有效临时 PNG → 连入 Prompt
LLM（界面显示图1）→ 获批 Gemini mock 返回文本 → 把同一参考图及该文本交给 Kie mock
→ 2 秒后显示本地 48×64 结果 → 画布列表确认 2 节点已保存 → LLM 超时恢复、主动停止和
立即重跑 → 退出。mock 计数证实上传及 createTask 各 1 次，未发生真实请求。
Chrome 选择文件受扩展权限限制，使用现有粘贴上传，没有扩大权限；第一次粘贴测试图
CRC 无效，暴露的 SyntaxError 已修成明确的参考图拒绝并补测试，不计为成功参考链路。
退出后直接打开图片被 Chrome 拦截，不能用此断言服务器状态；独立无 Cookie HTTP 检查
确认同一结果 URL 为 401。两进程自动测试另覆盖会话撤销后的媒体拒绝。

最终完整检查：**436/436**（基线 417 + 新增 19），0 warning，`All checks passed.`。
包括慢速分块响应不能延长 LLM 总截止时间、上游 Set-Cookie 不转发、损坏 PNG 明确拒绝。
没有删除、跳过或放宽原有测试断言。运行模块已纳入 Release Manifest，实例配置、凭证、
账本和缓存均未纳入。

### 第三阶段生产检查（2026-09-08，+0800）

本轮开始记录 3392 份文件内容 SHA-256（仅摘要，不复制内容或密钥）。中途比较曾一致；
最终比较为 3398 份，6 份新增、3 份修改、0 份删除，不能称为整轮无变化：

| 文件 | 变化 | mtime |
| --- | --- | --- |
| `assets/output/online_5d23356b62.png` | 新增 | 12:35:34.396589 |
| `data/canvases/d0f7097e58394b80bc2081d245337bb3.json` | 修改 | 12:35:35.926384 |
| `data/kie_reference_cache.json` | 修改 | 12:33:52.313171 |
| `history.json` | 修改 | 12:35:34.503998 |
| `data/media_previews/31d9e7395fdc367e5f65b25062444b63349b1ac3.webp` | 新增 | 12:32:26.166471 |
| `data/media_previews/9e66a6ff3cb73f71251e9c2da8c6320c554f3ab2.webp` | 新增 | 12:32:26.271653 |
| `data/media_previews/dbbe36ba357b60fedd2480e23f4b97de0e328b50.webp` | 新增 | 12:32:43.860822 |
| `data/media_previews/ee3772d40a916003e97e829fe83415e3842c0317.webp` | 新增 | 12:32:43.995636 |
| `data/media_previews/fa29f9101e28414a4e83e338b014e4bf40836694.webp` | 新增 | 12:35:35.580856 |

上述内容变化来自摘要比较，时间来自 stat；写入进程**未确定**，不能仅凭时间/文件名
归因 owner 自动保存。没有覆盖或回滚这些文件。测试的模型只访问随机端口本地 mock；
浏览器仅访问临时实例，测试进程树用 sandbox-exec 禁止写生产目录。
生产 `API/`、`data/api_providers.json`、Workflow、`.runtime`、`.tools`、`.launchd`
在本次摘要覆盖范围内均无内容变化。owner PID 2184、启动时间 00:13:05、源码
`590a041464fa1551fd248dfabc00c2c2f3946646` 和 clean 工作区未变。
未修改 LaunchAgent、生产 Provider 或 `.env`，未重启或停止 owner。

后续最小真实验收（**尚未授权或执行**）：管理员确认精确 Provider ID、Gemini 模型 ID 和
Kie 适配器模型 ID 后，用一张明确允许外发的临时服装参考图：Gemini 图像理解 1 次；
Kie 官方参考图上传最多 1 次、图片提交 1 次（1 张、1K、1:1），随后查询同一任务并下载。
不额外做“reply OK”测试，不自动重试。费用目前未确定，先核实专用凭证余额/单价/上限。
成功标准是参考图内容理解正确、实际图生图成功、结果只保存到助理实例、退出后访问被拒绝，
并核对供应商计费记录。此 mock 通过不代表真实服装图像质量、供应商兼容性或付费额度已验证。

本阶段完成受控模型调用的代码基础与 mock 验证，不代表远程部署、真实 Provider 验收、
操作系统级沙箱隔离或完整费用风控已经完成。
