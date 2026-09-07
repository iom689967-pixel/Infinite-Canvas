# Infinite-Canvas 技术架构审计

> 本文描述仓库中已确认的长期架构与历史交付节点，不代表任意远端或本机的实时状态。当前分支、HEAD、PID、端口、Provider 可用性和测试结果应在实际环境中检查。本文不包含 API Key、用户画布内容、Prompt、素材名称或对话内容。

## 1. 运行拓扑

```text
Browser / PS UXP / CLI helper
  │ HTTP + WebSocket
  ▼
FastAPI main.py（大型单体后端）
  ├─ 静态页面与 assets/output 挂载
  ├─ Provider/Model Router
  ├─ Canvas / Project / Conversation / Asset JSON persistence
  ├─ ComfyUI / RunningHub / CLI / 视频适配
  ├─ 更新、备份、存储迁移
  └─ /ws/stats
       │
       ├─ providers/kie/*
       ├─ OpenAI-compatible / ModelScope / Ark / Gemini / APIMart 等
       ├─ ComfyUI instances / workflows
       └─ 本地文件：data/、assets/、workflows/
```

推荐由本机服务管理器或正式启动 helper 运行，并遵守以下边界：

- 项目根目录按实际安装位置解析，正式 macOS helper 优先使用项目 `.venv/bin/python`，不存在时才走既有 fallback。
- 服务应只绑定 loopback；端口、PID、LaunchAgent label、plist 和日志路径属于本机运行状态，不写入正式架构文档。
- 主页面、核心静态资源、API 与 `/ws/stats` 应在每次部署后重新做可达性检查。
- 直接运行 `python main.py` 的监听地址可能与受控启动方式不同；应用没有认证且 CORS 宽松时，不得暴露到局域网或公网。

## 2. 目录与模块

| 路径 | 角色 | 关键说明 |
|---|---|---|
| `main.py` | FastAPI 单体后端 | 路由、Provider 分发、生成、持久化、更新、资产、Workflow 几乎全部集中于此 |
| `static/index.html` | AI Studio 壳层 | 侧边栏、iframe 导航、主题/语言/更新入口 |
| `static/js/canvas.js` | 普通画布引擎 | 大型独立实现，包含节点、Edge、运行、Picker、持久化与交互 |
| `static/js/smart-canvas.js` | 智能画布引擎 | 大型独立实现，包含节点引擎、Composer、MiniMax、Picker 与运行编排 |
| `static/css/canvas.css` | 普通画布样式 | 节点、Edge、选择、Picker |
| `static/css/smart-canvas.css` | 智能画布样式 | Composer、节点、时间线、Picker |
| `static/js/api-settings.js` | Provider 设置 UI | 协议识别、模型、Kie 受控展示 |
| `static/js/asset-manager.js` | 素材管理 UI | 素材、Prompt、Workflow、本地目录、平台认证 |
| `providers/kie/` | Kie Adapter | `client.py`、`tasks.py`、`models.py`、`uploads.py` |
| `workflows/` | ComfyUI Workflow | JSON 图与 `.config.json` 字段映射 |
| `tests/` | 回归测试 | 图片参数、Picker、Kie、日志清理 |
| `data/` | JSON 状态与缓存 | Provider、Canvas、Project、Prompt、对话、更新备份等 |
| `assets/` | 用户媒体 | input、output、library、uploads |

历史 smart-canvas broken/stable/mojibake 备份已从源码跟踪中清理；本机备份与运行时数据必须继续留在忽略边界内。

## 3. 应用初始化与静态资源

1. FastAPI 初始化 CORS、静态挂载和路由。
2. `VersionedStaticFiles` 在返回 HTML 时于内存中重写 JS/CSS cache-busting query，不修改磁盘上的 tracked HTML。
3. 启动过程还会迁移素材库目录、双扩展名和错误图片扩展名。
4. `static/index.html` 以 iframe 切换功能页，并把主题、语言等状态广播给子页面。
5. Canvas List 加载项目与画布元数据；打开某个画布时进入独立编辑页。

运行时迁移可能更新用户资产或缓存，但不应再修改被 Git 跟踪的源码与 HTML；正常启动后的 working tree 应保持 clean。

版本来源不完全一致：根 `VERSION` 为 `2026.08.04`，`main.py` 中还有 `APP_VERSION = "2026.06.03"`；`/api/app-info` 以 `VERSION` 文件为准。

## 4. 后端路由分区

| 路由族 | 代表端点 | 职责 |
|---|---|---|
| App/Update | `/api/app-info`、`/api/check-update`、`/api/update-from-github`、`/api/update-rollback` | 版本、下载、备份、替换、回滚 |
| Storage/Preview | `/api/storage-settings`、`/api/storage-files/*`、`/api/media-preview`、`/api/image-jpeg` | 存储位置、文件、缩略图、UXP JPEG 兼容 |
| Upload | `/api/upload`、`/api/ai/upload`、`/api/ai/upload-base64`、`/api/comfyui/upload-base64` | 图片/文件上传与 PS UXP 兼容 |
| Provider | `/api/providers`、`/api/providers/test-connection`、`/api/providers/fetch-models`、`/api/image-params` | Provider CRUD、探测、模型与 capability |
| Online image | `/api/online-image` | 一次性在线图片生成 |
| Canvas tasks | `/api/canvas-image-tasks`、`/api/canvas-comfy-tasks` | 浏览器本地任务创建、查询、取消 |
| Video | `/api/canvas-video`、`/api/cloud-video/upload` | 通用视频请求与公网素材准备 |
| LLM/Chat | `/api/canvas-llm`、`/api/chat`、`/api/chat/agent`、`/api/chat/stream` | 节点 LLM 与独立对话系统 |
| Canvas/Project | `/api/canvases*`、`/api/projects*`、`/api/canvas-assets*` | 画布、项目、回收站、画布媒体索引 |
| Asset/Prompt | `/api/asset-library*`、`/api/prompt-libraries*`、`/api/local-assets*`、`/api/shared-folders*` | 托管素材、本地目录、共享目录、Prompt |
| Workflow | `/api/workflows*`、`/api/canvas-workflows*` | ComfyUI Workflow 与 Canvas 片段导入导出 |
| RunningHub | `/api/runninghub/*` | WebApp/Workflow 元数据、提交、查询、素材上传 |
| CLI | `/api/codex/*`、`/api/gemini-cli/*`、`/api/jimeng/*` | 本机 CLI 状态、帮助、登录与媒体查询 |
| Local tools | `/generate`、`/api/generate`、`/api/ms/generate`、`/api/angle/*` | 本地/ModelScope 图片工具与角度控制 |
| Realtime | `/ws/stats` | 系统/队列状态推送 |

## 5. 数据模型与持久化

### 5.1 Canvas

典型持久化结构：

```json
{
  "id": "...",
  "title": "...",
  "icon": "...",
  "kind": "normal|smart",
  "owner": "...",
  "color": "...",
  "pinned": false,
  "project": "...",
  "created_at": 0,
  "updated_at": 0,
  "nodes": [],
  "connections": [],
  "viewport": {"x": 0, "y": 0, "scale": 1},
  "logs": [],
  "settings": {}
}
```

- 文件：`data/canvases/<id>.json`。
- 自动保存：前端 debounce 约 500 ms。
- 乐观并发：请求携带 `base_updated_at`；Smart 在 409 时尝试按节点 ID 和图片并集合并后重存。
- 软删除：进入回收站，默认保留 30 天；purge 才是硬删除。
- Undo/Redo：浏览器内存快照，不跨刷新持久化。
- 普通画布 viewport 有本地缓存倾向；智能画布明确把 viewport 写回服务器 payload。

### 5.2 其他 JSON/文件数据

| 数据 | 位置 | 说明 |
|---|---|---|
| Project | `data/projects.json` | 只通过 `canvas.project` 关联画布 |
| Provider 元数据 | `data/api_providers.json` | 不应保存明文 Key；Key 在环境文件 |
| API Key | `API/.env` | 服务端读写；UI 仅返回配置状态/掩码 |
| Conversation | `data/conversations/<user>/*.json` | GPT 对话记录 |
| Asset metadata | `data/asset_library.json` | 托管素材记录 |
| Prompt library | `data/prompt_libraries.json` | 系统/用户提示词库 |
| Shared folders | `data/shared_folders.json` | 外部目录注册表 |
| Storage settings | `data/storage_settings.json` | 可立即切换资源目录 |
| Generation history | `history.json` | 独立页面和生成结果历史 |
| Comfy Workflow | `workflows/*.json`、`*.config.json` | 工作流图与公开参数映射 |
| Media | `assets/input|output|library|uploads` | 本地用户媒体 |
| Preview cache | `data/media_previews` | 按原文件状态生成的 WebP/PNG/JPEG 缓存 |

没有数据库引擎、迁移表或事务层；“数据库”在产品语境中实际是 JSON + 文件系统。

## 6. Provider 架构

### 6.1 注册与协议

内置 Provider 包括 ModelScope、RunningHub、Volcengine、Kie；用户还可配置 OpenAI-compatible、APIMart、Gemini、Jimeng、Codex/Gemini CLI 等。Gemini 协议使用原生 `generateContent`，并对 Base URL 的 `/v1`、`/v1beta` 后缀做规范化。协议枚举包括：

`openai`、`apimart`、`gemini`、`gemini-cli`、`volcengine`、`runninghub`、`jimeng`、`codex`、`kie`。

通用图片请求模式还区分：

`openai`、`openai-json`、`openai-video-proxy`、`openai-responses`、`tudou-async`。

### 6.2 图片分发

核心入口为 `generate_ai_image(...)`，大致按以下顺序分发：

```text
Kie Adapter
→ Tudou/特殊异步分支
→ ModelScope
→ Codex CLI / Gemini CLI / Jimeng
→ RunningHub / Gemini / Volcengine
→ 通用 OpenAI-compatible
```

通用 OpenAI-compatible：

- 无参考图：`/v1/images/generations`，JSON。
- 有参考图：`/v1/images/edits`，multipart；Mask 与普通输入分开。
- 响应兼容 Base64、URL、data URL 等形态。
- 统一下载到本地、记录历史并生成浏览器可访问 URL。

Gemini 原生链路：

- 把 OpenAI messages 转换为 Gemini `contents`，请求规范化后的 `models/{model}:generateContent`。
- 解析 Gemini candidates 与 `usageMetadata`，不把请求伪装成 OpenAI Chat。
- 模型发现合并 Provider Base URL 的版本目录与 Gateway 根目录 `/v1/models`。
- 补充目录只接受 `gemini-*`；名称含 `image` 的模型归图片能力，其余归 LLM，防止其他模型误入 Gemini Provider。

`build_online_image_result(...)` 被 `/api/online-image` 与 Canvas 图片任务复用，因此 Request Builder/Provider Router/结果保存是共享的；Canvas 只增加本地异步任务包装。

### 6.3 Capability

- `/api/image-params` 与服务端参数构建函数根据 Provider/模型返回比例、分辨率、数量等 UI 字段。
- Kie 的静态白名单、UI 名称、内部 routing ID、参考图上限与字段映射在 `providers/kie/models.py`。
- 前端还保留局部默认值与 Provider 特判，因此 capability 尚未完全单一来源。

## 7. Kie Adapter Flow

```text
generate_ai_image
  → 解析 Kie UI model
  → normalize_and_upload_references
      ├─ 识别 blob/data/local path/localhost/public HTTPS
      ├─ 下载或读本地字节
      ├─ EXIF transpose
      ├─ RGB、8-bit、PNG/JPEG、MIME、magic bytes、size 校验
      ├─ File Stream Upload
      └─ 对匿名 HTTPS URL 再做 HTTP/Content-Type/magic 校验
  → create_task（仅一次）
      POST /api/v1/jobs/createTask
  → query_task(taskId)
      GET /api/v1/jobs/recordInfo?taskId=...
  → waiting/queuing/generating 持续轮询
  → success: json.loads(data.resultJson).resultUrls
  → 下载并进入通用结果保存
```

| UI 模型 | 无参考图 | 有参考图 | 字段 | 上限 |
|---|---|---|---|---:|
| GPT Image 2 | `gpt-image-2-text-to-image` | `gpt-image-2-image-to-image` | `input_urls` | 16 |
| Nano Banana Pro | `nano-banana-pro` | 同模型 | `image_input` | 8 |
| GPT Image 1.5（内部） | 非 UI 能力 | `gpt-image/1.5-image-to-image` | `input_urls` | Adapter 定义 |

每张参考图上限 30 MB。Adapter 失败会返回图片序号、文件名、上传/读取阶段、HTTP 状态和 Content-Type 等上下文，并保留上游 task ID。

限制：`CANVAS_TASKS` 是进程内字典。Kie Adapter 能用 task ID 再查上游，但当前没有把整个画布本地任务状态持久化并在重启后自动挂回节点的机制。

## 8. 普通画布架构

### 8.1 初始化与渲染

- HTML：`static/canvas.html`
- JS：`static/js/canvas.js`
- CSS：`static/css/canvas.css`
- 节点工厂：`createNodeByType`
- 菜单创建：`menuAdd`
- 主渲染：`renderNode`，LLM 子面板 `renderLLMNodePane`
- 连接校验：`canConnect`
- 主要鼠标/键盘交互：Canvas pointer/keyboard handlers

### 8.2 Node Registry

普通画布没有一个声明式、单一对象形式的 Registry；`createNodeByType`、`menuAdd`、`linkCreateOptions`、`renderNode` 和各运行分支共同构成事实 Registry。

| type | 中文/用途 | 输入 | 输出 | 工厂默认数据/尺寸 | 渲染 |
|---|---|---|---|---|---|
| `image` | 图片/上传媒体 | 文件、URL | IMAGE/MEDIA | 空 URL；尺寸由媒体/CSS 推导 | `renderNode` image 分支 |
| `prompt` | Prompt | 文本 | TEXT | `text:''`；CSS 自适应 | `renderNode` prompt 分支 |
| `loop` | 循环 | Prompt/图/视频 | 迭代上下文 | count=3、serial、start=1 | loop 分支 |
| `group` | 分组 | 子节点/图片 | 聚合媒体 | 300×220 | group 分支 |
| `promptGroup` | 派生 Prompt 组 | 多 Prompt | 聚合 TEXT | 运行时/迁移结构 | group/prompt 分支 |
| `llm` | LLM 对话/改写 | TEXT/图片上下文 | TEXT | system prompt、输入高 110、输出高 150 | `renderLLMNodePane` |
| `generator` | API 图片生成 | TEXT、IMAGE | IMAGE | provider/model、square、模型默认分辨率；面板约 460 宽 | generator 分支 |
| `midjourney` | Midjourney | Prompt、图片 | IMAGE | v6.1、1:1、relax | Midjourney 分支 |
| `msgen` | ModelScope 生图 | Prompt、图片（依模型） | IMAGE | 1024×1024、1k、count=1 | msgen 分支 |
| `video` | 通用视频生成 | TEXT、IMAGE、VIDEO | VIDEO | 5s、16:9、Provider 首模型 | video 分支 |
| `minimax` | MiniMax H3 时间线 | 图/视频/音频/Prompt | VIDEO | 980×720、8s、16:9 | MiniMax workbench |
| `rh` | RunningHub | 动态 Workflow 参数 | IMAGE/VIDEO | 430 宽、App 模式 | RunningHub 分支 |
| `comfy` | ComfyUI | TEXT、IMAGE | IMAGE/VIDEO | 420×460、1024×1024、count=1 | Comfy 分支 |
| `ltxDirector` | LTX Director | Prompt、图片、音频、分段 | VIDEO | 1000×800、5s/120 帧/24fps | LTX timeline |
| `output` | 输出汇总 | 上游媒体 | 可下载/再转输入 | `images:[]` | output 分支 |

`generatedImage`、`outputImage`、`loopImage` 是生成结果/连接语义，不从主工厂直接创建。

### 8.3 创建入口

- 顶部/左侧按钮和右键菜单调用 `menuAdd`。
- `menuAdd` 没有 Group 项；Group 主要由框选分组、上传多图或其他组合动作创建。
- Handle 拉线松开到空白处会打开“连接到新节点”，经 `createLinkedNode → createNodeByType` 创建后自动连接。
- `linkCreateOptions` 会按源节点类型筛选 Generator、Midjourney、ModelScope、Video、MiniMax、RunningHub、ComfyUI、LTX、LLM、Output 等目标。
- 拖放、粘贴和文件选择会创建 Image/Group 节点。

多个入口并未共享单一菜单 Registry，因此存在“工厂支持、某菜单未展示”的漂移风险。

## 9. 智能画布架构

### 9.1 初始化与渲染

- HTML：`static/smart-canvas.html`
- JS：`static/js/smart-canvas.js`
- CSS：`static/css/smart-canvas.css`
- 基础工厂：`createNode`、`createPromptNode`、`createTextNode`、`createGenerationNode`、`createLoopNode`、`createMinimaxNode`、`createSmartGroupNode`
- 节点主体：`smartNodeBodyHtml`
- 主 DOM 渲染：Smart node render pipeline
- Quick Connect：`openQuickConnectMenu`
- 连接校验：`canConnectSmartInputNodes`
- 主菜单分发：`createNodeFromMenu`
- 主要鼠标/键盘交互：Smart Canvas pointer/keyboard handlers

### 9.2 Node Registry

| type | 中文/用途 | 输入 | 输出 | 默认尺寸/数据 | 渲染 |
|---|---|---|---|---|---|
| `smart-image` | 上传/素材/历史图；兼容旧 API 运行形态 | MEDIA/上下游 | IMAGE/VIDEO/ANY | 图片尺寸自适应；多图 scale=0.8 | 通用 image node body |
| `smart-prompt` | Prompt + 可选 LLM 辅助 | TEXT | TEXT | 316×240 | `promptNodeBodyHtml` |
| `smart-text` | 普通文本 | TEXT | TEXT | 440×250 | `textNodeBodyHtml` |
| `smart-image-generation` | 专用图片生成 | TEXT、IMAGE | IMAGE | 440×250，runSettings 锁定 image | Composer + generation node |
| `smart-video-generation` | 专用视频生成 | TEXT、IMAGE、VIDEO | VIDEO | 440×250，runSettings 锁定 video | Composer + generation node |
| `smart-loop` | 循环/变量 Prompt | TEXT/IMAGE | 批次上下文 | 340×168、count=1 | `smartLoopBodyHtml` |
| `smart-minimax` | MiniMax H3 时间线 | 图/视频/音频/Prompt | VIDEO | 1040×640、8s、16:9 | `smartMinimaxBodyHtml` |
| `smart-group` | 智能分组/自动吸收 | 子节点/媒体 | 聚合上下文 | 340×286 | `smartGroupBodyHtml` |
| `smart-container` | 历史兼容类型 | 旧数据 | 迁移后节点 | 非新建类型 | 加载时规范化 |

主右键菜单默认偏向素材、Group、Prompt、Loop、MiniMax；Text、Image Generation、Video Generation 在 Quick Connect Registry 中可创建。专用生成节点与旧 `smart-image + runSettings` 都被运行层兼容。

智能画布 Edge selection 使用稳定的 connection selection key；键盘删除会解析选中 connection index，并复用正式连接删除核心流程，统一更新数据、可视 Edge、hit path、cut UI、selection、history 与保存状态。`input`、`textarea`、`contenteditable`、Prompt/节点文本编辑等输入状态由共享 editable guard 拦截，Delete / Backspace 只做文字编辑。

## 10. Edge 与 Interaction

### 10.1 普通画布

| 操作 | 当前行为 | 主要代码位置 |
|---|---|---|
| 左键点空白 | 清除/重置选择 | Canvas background handlers |
| 左键拖空白 | 框选；Shift 追加 | 同上 |
| 点击 Node | 单选；Ctrl/Cmd 切换多选 | Node pointer handlers |
| 拖 Node | 移动；Alt 拖复制；Alt+Shift 可带入连接复制 | 同上 |
| Shift+左拖 | 在空白处追加框选；在线条区域可作为 knife 切 Edge | Edge/canvas handlers |
| 中键拖 | Pan | canvas background handlers |
| 右键点 | 节点/Edge/空白上下文菜单 | contextmenu handlers |
| 右键拖 | Pan，短按仍保留菜单语义 | 右键 pan handlers |
| Space+拖 | Pan | keydown/pointer handlers |
| 滚轮 | 围绕指针 Zoom | wheel handler |
| Text/Prompt | 单击选中、拖动；双击打开扩大编辑器 | node dblclick/editor functions |
| LLM | 输入区域阻断节点拖动；标题区拖动；Enter 发送、Shift+Enter 换行 | `NODE_DRAG_BLOCK_SELECTOR`、LLM pane |
| Handle | 拖线、磁性浮动 Handle、空白 Quick Connect | ports + `createLinkedNode` |
| Edge | 贝塞尔曲线精确命中、框选、knife、删除 | connection hit/cut functions |
| Resize | 节点 resize handle；类型有最小尺寸 | node resize handlers |
| 快捷键 | Cmd/Ctrl C/V、Z/Shift+Z/Y、G、Delete；Z 图片 Zoom Preview；Esc 取消 | keyboard handlers |

### 10.2 智能画布

| 操作 | 当前行为 | 主要代码位置 |
|---|---|---|
| 左键空白拖 | Marquee；多选规则与普通画布相似 | Smart Canvas background handlers |
| Node 拖动 | 有激活阈值；Group 可自动吸收/整理成员 | Smart node pointer handlers |
| 中键/右键/Space | Pan；Composer、控件、时间线等由 `SMART_CANVAS_OVERLAY_SELECTOR` 阻断 | Smart Canvas pan handlers |
| 滚轮 | Zoom | wheel handlers |
| Prompt/Text | 单击选择、拖动；双击扩大编辑 | Prompt/Text handlers |
| Handle/Edge | 磁性端口、Quick Connect、类型校验、精确命中/切断 | Connection handlers |
| Resize | Text/Generation/Prompt/Loop/Group 分别限制最小尺寸 | Resize handlers |
| 快捷键 | A 素材库、Z 预览、复制粘贴、撤销重做、G 分组、Shift+G 解组、Delete / Backspace 删除选中 Edge 或既有节点目标 | keyboard handlers |
| MiniMax | Space 播放/暂停有专用 keyup 防冲突 | MiniMax handlers |

## 11. Canvas Reference Picker 技术契约

普通画布入口在 Generator 输入区域，相关函数包括：

- `canvasReferenceStableKey`
- `canvasReferenceItemFromNode`
- `canvasReferenceSources`
- `canvasReferenceLimitForNode`
- Picker candidate/record helpers
- Picker 鼠标选择 handlers
- 运行前物化与请求收集 helpers

智能画布入口位于 API-like 图片生成 Composer 的参考图 Tabs，由 `showCanvasReference` 控制展示，并通过选择/状态函数及运行前物化 helpers 完成同一契约。

软引用结构：

```json
{
  "id": "stable-reference-id",
  "sourceNodeId": "source-node",
  "assetIndex": 0,
  "fileName": "display-only",
  "url": "stable-url-snapshot",
  "order": 1,
  "materializedEdgeId": ""
}
```

事件流：

```text
点击“画布参考”
  → 保存 targetGenerationNodeId
  → 进入 picker mode
  → 点击来源图片
  → 检查 eligibility / capability limit / cycle
  → 更新临时选择与编号 overlay
  → 返回节点并写入 canvasReferences
  → scheduleSave + Undo snapshot
  → 首次 Run
  → 重新解析来源 URL
  → 与人工 Edge/软引用去重
  → 创建 origin=canvas-reference 的正式 Edge
  → 构建 Provider 请求
```

关键不变量：选择阶段不建 Edge；运行阶段才物化；不持久化 transient URL；模型上限来自 capability；删除自动 Edge 同步移除对应软引用；循环依赖在选择和执行两处防御。

## 12. Request Flow

### 12.1 Online Image

```text
online.html form
→ FormData / JSON
→ POST /api/online-image
→ build_online_image_result
→ generate_ai_image
→ provider adapter
→ normalize result
→ assets/output + history.json
→ response URLs
```

### 12.2 Canvas Image

```text
Node graph
→ collect prompt + manual inputs + materialized canvas references
→ POST /api/canvas-image-tasks
→ CANVAS_TASKS[local_task_id]
→ background coroutine
→ shared build_online_image_result
→ browser GET /api/canvas-image-tasks/{id}
→ output node/images/logs
→ PUT /api/canvases/{canvas_id}
```

### 12.3 Chat/Agent

```text
gpt-chat.html
→ /api/ai/upload（附件）
→ /api/chat/stream 或 /api/chat
→ provider LLM
→ conversation JSON

agent mode
→ /api/chat/agent
→ LLM 决策 action
→ chat / generate_image / edit_image
→ shared image provider flow
```

### 12.4 Video

```text
Canvas video node / Smart composer
→ collect text, image, first/last frames, media refs
→ 如上游需要公网 URL：/api/cloud-video/upload
→ POST /api/canvas-video
→ provider-specific submit/poll
→ download result
→ local asset + node/log persistence
```

## 13. Workflow 架构

### 13.1 ComfyUI Workflow

- `workflows/*.json` 保存 ComfyUI graph。
- 同名 `.config.json` 保存暴露到 UI 的节点字段、默认值和映射。
- `/api/workflows` 提供列表、读取、保存、配置、删除、运行。
- `comfyui-settings.html` 管理实例与 Workflow，可执行测试运行；本审计没有点击运行。

当前仓库包含 Z-Image、Enhance、Flux2-Klein、Upscale、MiniMax H3、LTX Director 等 Workflow 文件。

### 13.2 Canvas Workflow

- 导出选中节点与内部 connections 为 JSON，或打包资源为 ZIP。
- 导入时重映射 ID 并追加到当前画布。
- 可导出到素材库，作为可复用 Workflow Asset。
- 它是“画布片段”，不等同于 ComfyUI graph。

## 14. Asset Flow

```text
上传/共享目录/本地文件
  ├─ assets/uploads + sidecar caption/classification
  └─ import/copy
       ▼
assets/library/<category> + data/asset_library.json
       │
       ├─ asset-manager 搜索/标签/分类/裁剪/批量动作
       ├─ LocalStorage inbox 复制到 Canvas
       └─ URL 被 Canvas node 引用

data/canvases/*.json
  → /api/canvas-assets 扫描 URL 字段
  → “画布图片”视图
```

Preview 由 `/api/media-preview` 根据源文件生成缓存。`/api/image-jpeg` 为不能显示 WebP 等格式的 PS UXP 客户端转换 JPEG。

## 15. 设置与即时生效边界

- Provider 元数据：`data/api_providers.json`；Key：`API/.env`。正常保存流程会调用环境重载函数，立即供后续请求使用。
- 主题：LocalStorage `studio_theme`，兼容 `canvas_theme`；主壳层用消息广播同步 iframe。
- 语言：LocalStorage `studio_lang`。
- UI scale：LocalStorage `studio_ui_scale_mode`。
- Storage：`data/storage_settings.json`，服务端 `apply_storage_settings` 立即切换。
- Workflow：保存文件后即时可读，页面间使用 BroadcastChannel/刷新同步。
- 静态代码：需要浏览器重新加载；cache token 用于破缓存。
- Python 代码：需要 Uvicorn 重启。
- CLI 登录：取决于外部进程/本机状态，不由 JSON 配置完全控制。

## 16. 更新器与升级危险

更新器不是 `git pull`、不是 Git merge，也不是安装 GitHub Release ZIP。它会：

1. 从 GitHub 与 ModelScope 的 `VERSION` 检查可达性和版本。
2. 只允许下载根 `main.py`、`VERSION` 和整个 `static/` 树。
3. 下载到 `data/update_staging` 并做基础校验。
4. 把当前允许范围完整备份到 `data/update_backups`，最多保留 10 份。
5. 删除当前整个 `static/`，再复制远端 `static/`；原子替换根文件。
6. 可重启服务；回滚也会替换整个 `static/`。

高风险点：

- 当前定制提交大量修改 `main.py`、`static/js/canvas.js`、`static/js/smart-canvas.js`、CSS、HTML 和设置 UI，会被直接覆盖。
- `providers/kie/` 与测试不在更新 allowlist；远端新 `main.py` 可能与本地旧 Adapter 不匹配，或本地 Adapter 留下但前端入口消失。
- 更新器不了解 Git dirty state，不做三方合并，也不会保护未提交的静态改动。

结论：**当前高度定制版不能安全点击自动更新。** 正确升级方式应先完整备份用户数据和工作区，再在新分支用 Git 对远端提交做审计式 rebase/cherry-pick/merge，并运行完整回归和浏览器验收。

## 17. Git 与运行数据边界

- 当前运行分支、HEAD、远端 main 和 ahead/behind 必须从实际 checkout 实时读取，不能固化为架构事实。
- 定制历史以 `CUSTOM_CHANGELOG.md` 中标为“历史交付 commit”的节点和 Git log 为准。
- Git 长期跟踪源码、测试、正式项目文档及无凭证 example/template；真实 Provider 配置、Key、用户画布/项目/素材/Prompt/历史/对话、Preview、缓存与本机环境不进入仓库。
- `static/runninghub/api_providers.json` 是只读默认值；Provider/RunningHub 保存写入 `data/`。
- `.runtime/`、`.tools/`、`.launchd/` 实例和本机 LaunchAgent 控制脚本属于本机状态；正式通用 `.command` / `.sh` helper 仍可跟踪并保留 executable mode。
- 不用 `git clean`、`reset --hard` 或全量 `git add .` 清理混合工作区；升级前先备份并校验用户数据。

## 18. 历史测试与浏览器证据

以下是功能交付时的历史证据，不是任意未来 HEAD 的实时状态；版本升级后应重新运行对应检查。

### 18.1 自动测试

- Kie、Canvas Reference Picker 与图片生成参数有专项回归记录。
- Smart Canvas targeted Edge update、Composer 纵向布局与 Edge 键盘删除有独立专项测试及浏览器验收。
- Gemini 原生协议、URL 规范化、动态模型目录和 Gemini-only 分类有独立专项测试。
- Canvas log cleanup 的 14 项错误是既存基线，需要单独修复，不能与上述功能提交混合处理。
- `node --check`、Python 编译/AST 与 `git diff --check` 应作为相关提交的基础门禁。

### 18.2 浏览器

- 历史审计真实打开过主壳层、工具页、素材库、API Settings、Canvas List 与 Smart Canvas，核心 HTML/JS/CSS、API 和 `/ws/stats` 均有成功记录。
- 浏览器验收不得创建付费生成；涉及 Canvas 写入交互时使用临时审计画布，并在确认用户数据哈希不变后清理。
- Provider 设置页默认只读检查；除非能证明是 no-op，否则不保存真实 Provider 配置。
- 正常启动、读取与停止之后，源码 working tree 必须仍为 clean。

## 19. Bug 与技术债优先级

### P0

- **当前已验证 P0：无。**
- 条件 P0：若某个部署把服务暴露到局域网或公网，应用无认证、CORS 为 `*`，且存在配置、删除、更新等写接口，会形成严重风险；部署时必须实时确认仅监听 loopback 或补充认证。

### P1

1. 自动更新会覆盖定制 `main.py/static`，且不更新 Kie 包/测试，存在直接丢二改与版本错配风险。
2. 69 项测试中 14 项因生产清理函数缺失而错误；历史提交曾有这些实现，当前 lineage 中测试保留而实现消失。
3. 普通/智能画布是两套大型独立前端，跨画布功能容易漏接或行为漂移。
4. Canvas 图片任务只保存在内存；服务重启可能让已付费的上游任务与本地节点失去自动关联。
5. `0.0.0.0 + 无认证 + CORS *` 的备用启动方式风险高，必须保持仅本机监听或增加认证。

### P2

1. 主壳层组合加载的 MutationObserver 错误可稳定复现，但当前未定位到具体脚本 URL。
2. cache token 已改为响应内存处理；仍需持续防止任何启动/保存路径把运行时状态写入 tracked source。
3. JSON 文件存储缺少数据库事务和正式 schema migration；并发与半写入风险由局部锁/原子写兜底，不是系统性保障。
4. Capability、节点菜单、节点工厂和运行分支不是单一来源；已发生过参数/入口漂移。
5. 根 `VERSION` 与 `APP_VERSION` 不一致。
6. Provider capability、UI 模型名和内部 routing ID 仍分布在多处，容易造成 UI/运维混淆。

### P3

1. Tailwind CDN 脚本在生产页面输出“不建议生产使用”警告。
2. 本机历史 source backup 必须留在忽略边界，不能重新进入正式仓库。
3. Group、Text、专用生成节点在不同创建入口的可见性不一致。
4. 多个外部平台认证项只展示“待接入”，容易被误认为已实现。
5. `LIX Director` 与代码 `LTX Director` 命名易混淆。

## 20. 后续共享层优先级

这不是本次的改造计划，只是架构审计建议：

1. **共享请求 Schema 与 Capability Registry**：Prompt、refs、尺寸、比例、数量、模型限制都从同一来源生成前端字段和后端校验。
2. **共享 Reference Resolver**：统一手工 Edge、素材库、Canvas soft reference、URL 稳定性、去重、数量限制和循环检查。
3. **持久化任务仓库**：本地 task ID、上游 task ID、Provider、Canvas/Node、状态和输出持久化，支持服务重启恢复。
4. **声明式 Node Registry**：type、中文名、输入输出类型、默认尺寸、工厂、菜单可见性、renderer、runner 集中注册。
5. **共享 Graph Core**：选择、viewport、Edge 几何、Undo/Redo、复制粘贴、Group 与 schema migration 抽成无 UI 核心；Normal/Smart 只保留视觉与编排差异。
6. **安全配置层**：统一 localhost 绑定、认证/CORS 策略、秘密掩码、写操作审计。
7. **Git-aware 更新器**：在定制版中默认禁用文件覆盖式自动更新，改为检测 dirty/custom commits 后阻断并生成升级报告。

## 21. 文档与验收边界

- 文档不得记录或展示真实 API Key、Token、Cookie、账号凭证、用户画布/Prompt/素材/对话内容。
- 不把某次审计的本机路径、PID、端口占用、Provider Key 状态、working tree 或当前 HEAD 固化为架构事实。
- 无付费授权时不调用 Kie/OpenAI/视频/ComfyUI 真实生成。
- 运行验收只读用户数据；需要写入交互时使用临时审计数据，并以哈希/清单证明用户数据未变化。
- 远端内容差异必须在独立升级审计中 fetch 后比较，不能依赖文档中的旧 remote-tracking ref。
