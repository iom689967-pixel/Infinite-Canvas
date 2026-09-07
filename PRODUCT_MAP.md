# Infinite-Canvas / AI Studio 产品地图

> 本文描述仓库中已确认的产品能力与长期维护边界。项目根目录、当前 HEAD、服务 PID、端口占用、Provider 可用性和测试状态应在实际安装环境中实时确认。

## 1. 软件是什么

这是一个本地运行的 AI 创作工作台，而不只是“无限画布”。它把本地 ComfyUI 工作流、ModelScope、OpenAI-compatible API、Kie、RunningHub、Midjourney/APIMart、Gemini、火山方舟及若干视频服务接到一个 FastAPI 后端，再通过独立工具页、GPT 对话页、普通画布和智能画布提供操作界面。

按产品职责划分，目前共有 **12 个主要功能模块**：

1. 本地图片工具（文生图、细节增强、图片编辑、角度控制）
2. 在线生图
3. GPT 对话与 Agent
4. 项目与画布工作台
5. 普通画布编辑器
6. 智能画布编辑器
7. 素材、提示词与工作流资源库
8. Provider 与模型控制面
9. 视频生成与时间线
10. ComfyUI 与 Workflow 系统
11. 主题、语言、存储等本地设置
12. 本地服务、版本检查、更新与备份

侧边栏只直接展示其中一部分；视频、Midjourney、RunningHub、MiniMax、LTX Director 等主要藏在画布节点或设置页中。

## 2. 完整导航地图

| 一级区域 | 页面/入口 | 实际页面 | 用途 |
|---|---|---|---|
| 本地功能 | 文生图 | `static/zimage.html` | Z-Image 本地 ComfyUI 或 ModelScope 云端文生图 |
| 本地功能 | 细节增强 | `static/enhance.html` | Z-Image-Enhance 生成式细节增强，可选二次放大 |
| 本地功能 | 图片编辑 | `static/klein.html` | Flux2-Klein 参考图编辑，支持最多 3 张图与可选 LoRA |
| 本地功能 | 角度控制 | `static/angle.html` | 单图旋转、俯仰、距离控制后生成一个新视角 |
| 在线功能 | 在线生图 | `static/online.html` | Provider/模型驱动的直接图片生成页 |
| 在线功能 | GPT 对话 | `static/gpt-chat.html` | 对话、Agent、图片生成三种模式 |
| 工作区 | 无限画布 | `static/canvas-list.html` | 项目筛选、普通/智能画布入口、回收站 |
| 工作区 | 素材库 | `static/asset-manager.html` | 图片、提示词、工作流、画布资产、本地目录管理 |
| 系统 | API 设置 | `static/api-settings.html` | Provider、模型、协议、Key 和连接校验 |
| 系统 | 更多设置 | 主壳层弹层 | 主题、语言、UI 缩放、工作流设置入口 |
| 系统 | 工作流设置 | `static/comfyui-settings.html` | ComfyUI 实例、工作流 JSON 与参数映射 |
| 系统 | 项目主页 | 外部 GitHub 链接 | 打开项目主页，不是本地 Project 数据页 |
| 系统 | 更新 | 主壳层更新弹层 | 检查远端版本、下载、备份、替换与回滚 |
| 画布编辑器 | 普通画布 | `static/canvas.html?id=...` | 多类型节点、显式图结构与复杂编排 |
| 画布编辑器 | 智能画布 | `static/smart-canvas.html?id=...` | 以素材节点和统一生成 Composer 为中心的快速编排 |

浏览器实测中，主壳层和上述 10 个侧边栏页面均正常完成加载；普通画布无 `id` 会返回画布列表，智能画布无 `id` 会加载空编辑器壳层。

## 3. 各功能实际用途

| 功能 | 输入 | 输出 | 模型 / Provider | 保存位置 | 当前状态 |
|---|---|---|---|---|---|
| 文生图 | Prompt、宽高、Local/Cloud | 图片 | 本地 Z-Image Workflow；云端 ModelScope Z-Image | `assets/output`、`history.json` | 可用；依赖本地 ComfyUI 或 ModelScope Key |
| 细节增强 | 1 张图、强度、可选 2x/4x | 增强图/放大图 | Z-Image-Enhance + 可选 Upscale Workflow | `assets/output`、历史 | 可用；是生成式增强，不是 Mask Inpaint |
| 图片编辑 | Prompt、最多 3 张参考图、可选 LoRA | 编辑图 | Flux2-Klein 本地或 ModelScope | `assets/output`、历史 | 可用；没有画笔式 Mask UI，也不走 GPT Image |
| 角度控制 | 1 张图、旋转、俯仰、距离、Prompt | 单个新视角 | 本地工作流或云端角度生成接口 | `assets/output`、历史 | 可用；一次一个角度，不是自动多视角批量 |
| 在线生图 | Prompt、最多 3 个可见上传位、模型、比例、分辨率、数量 | 1–4 张图 | 动态图片 Provider | `assets/output`、`history.json` | 可用；具体能力取决于 Provider 配置 |
| GPT 对话 | 文字、图片、文件、System Prompt | 流式文本或图片 | 动态 Chat/Image Provider | `data/conversations/<browser-user>/*.json`、生成图片目录 | 可用；Agent 模式为本地编排 |
| 普通画布 | 节点、连线、Prompt、媒体、Provider 参数 | 图片、视频、文本、工作流结果 | 多 Provider + ComfyUI | `data/canvases/*.json` 与资产目录 | 功能丰富；前端体量大、回归风险高 |
| 智能画布 | 素材节点、Prompt、统一 Composer、图关系 | 图片、视频、分组与时间线结果 | 与普通画布共享后端 Provider | 同一画布 JSON 家族 | 可用；交互更集中，但与普通画布是独立实现 |
| 素材库 | 上传、共享目录、Canvas 资产、Prompt、Workflow | 可复用素材与元数据 | 无模型；分类可调用已配置能力 | `assets/*`、`data/*.json` | 可用；不是数据库系统 |
| API 设置 | Provider、模型、协议、Base URL、Key | 动态 Provider 配置 | 内置与自定义 Adapter | `data/api_providers.json`、`API/.env` | 可用；Key 只应掩码展示 |
| 工作流设置 | ComfyUI 地址、Workflow JSON、字段映射 | 可运行 Workflow | ComfyUI / RunningHub | `workflows/*.json`、`.config.json` | 可用；依赖外部实例/服务 |
| 更新系统 | 版本源、更新/回滚按钮 | 替换后的程序文件 | GitHub/ModelScope 原始文件 | `data/update_staging`、`data/update_backups` | **当前定制版高风险，不应直接点击更新** |

## 4. 在线生图与画布生图

两者使用不同前端，但图片 Provider 的核心后端是共享的。

```text
在线生图页
  → POST /api/online-image
  → build_online_image_result
  → generate_ai_image
  → Provider Adapter / 通用 OpenAI-compatible 请求
  → 下载并保存结果
  → 返回页面

普通/智能画布
  → 节点收集 Prompt、参考图和运行参数
  → POST /api/canvas-image-tasks
  → 本地任务表 CANVAS_TASKS
  → build_online_image_result
  → generate_ai_image
  → 浏览器轮询本地任务
  → 结果写回节点、日志与画布 JSON
```

主要差别：

- 在线生图页是一次性表单，直接等待后端返回；画布使用本地任务 ID 轮询，以便节点显示运行状态和取消。
- 在线页有 3 个可见上传槽；通用上传后端本身允许更多文件。画布参考图来自连线、素材选择或 Canvas Reference Picker。
- 两者都把最终媒体保存到本地输出目录，并记录生成历史；画布还会更新节点、连线、运行日志和持久化状态。
- Kie 等异步 Provider 的上游轮询由 Adapter 处理；画布外层只轮询本地任务状态。

## 5. GPT 对话

- 页面提供 `chat`、`agent`、`image` 三种模式。
- Chat Provider/Model 来自 API 设置；当前可选项由服务端 Provider 元数据动态返回。
- System Prompt 保存在浏览器 LocalStorage，随请求发送。
- `/api/ai/upload` 接受图片、视频、音频和常见文档，单文件上限 50 MB；图片可作为多模态或图片编辑参考，普通文件能否被模型真正理解取决于后端内容转换和 Provider。
- 对话列表与消息保存在 `data/conversations/<浏览器用户标识>/*.json`。这里的浏览器用户标识用于目录隔离，不是账号认证。
- Agent 模式可在本地决定“继续聊天 / 生成图片 / 编辑图片”，然后调用同一套 `generate_ai_image`；没有发现独立的云端 Hosted Image Generation 工具。
- Canvas LLM Node 使用 `/api/canvas-llm`，只共享 Provider 解析和 LLM 调用基础设施；它不共享 GPT 对话页的 UI、路由或会话文件。

## 6. 普通画布与智能画布

| 维度 | 普通画布 | 智能画布 |
|---|---|---|
| 路由 | `canvas.html?id=...` | `smart-canvas.html?id=...` |
| 前端 | `static/js/canvas.js` + `static/css/canvas.css` | `static/js/smart-canvas.js` + `static/css/smart-canvas.css` |
| 核心产品思想 | 每种能力对应一种显式节点 | 素材节点 + 统一 Composer + 少量专用节点 |
| 生成配置 | 分散在 Generator、Video、Comfy 等节点内 | 选中节点后由右侧/浮层 Composer 统一配置 |
| 图结构 | 节点类型多、端口语义明确 | 类型少、自动吸收/自动连接/上下文推导更多 |
| 代码关系 | 大型独立实现 | 大型独立实现 |
| 共享层 | FastAPI 路由、Provider、资产/工作流接口、画布 JSON 模型 | 同左 |
| 未共享层 | 节点工厂、渲染、选择、拖拽、连线、撤销、Picker UI、运行编排 | 各自复制实现 |

两者最大区别不是“皮肤”，而是 **两套独立的前端节点引擎和运行编排**。功能同步必须分别接入，这也是“普通画布已有、智能画布入口遗漏”曾经发生的根因。

### 6.1 普通画布节点

`image`、`prompt`、`loop`、`group`、`promptGroup`、`llm`、`generator`、`midjourney`、`msgen`、`video`、`minimax`、`rh`、`comfy`、`ltxDirector`、`output`。此外，`outputImage`、`generatedImage`、`loopImage` 是运行时/输出语义，不是主菜单中的常规工厂节点。

### 6.2 智能画布节点

`smart-image`、`smart-prompt`、`smart-text`、`smart-image-generation`、`smart-video-generation`、`smart-loop`、`smart-minimax`、`smart-group`，并兼容迁移旧的 `smart-container` 数据。

右键主菜单主要暴露上传、分组、Prompt、Loop、MiniMax；Text、图片生成、视频生成还可由 Quick Connect 创建。也就是说，智能画布“工厂支持的类型”比“主菜单直接展示的类型”更多。

## 7. Canvas Reference Picker

当前普通画布和智能画布都已经包含同一产品契约：

- API 图片生成区域显示 `输入图 | 资产库 | 画布参考`。
- 点击“画布参考”后保存目标生成节点，进入选择模式并显示顶部状态栏。
- 点击画布中的图片只写入 `canvasReferences` 软引用；此时不创建 Edge。
- 软引用保存来源节点、素材序号、文件名、URL 快照、顺序和物化 Edge ID。
- 运行前重新从来源节点解析有效图片；`blob:`、`data:`、localhost 等瞬态地址不会被当作稳定引用保存。
- 首次真正运行时才把软引用物化为 Provider 标记的 Edge。
- 对人工连线、已物化连线和软引用做去重；按模型 capability 限制数量；选择和运行前都做循环依赖检查。
- 选择顺序会编号；视觉状态通过选择覆盖层/编号表达，不依赖永久降低原图透明度。
- 进入、选择、返回、物化和删除关系均纳入各自画布的 Undo/Redo 逻辑。

历史交付已通过两套源码契约与 Picker 专项测试；真实 Provider 生成仍应按环境和授权单独验收，不能仅凭代码与测试宣称付费链路可用。

## 8. 素材、提示词与画布资产

### 8.1 托管素材库

- 图片文件位于 `assets/library/<category>/`，元数据位于 `data/asset_library.json`。
- 支持多个素材库、分类、人物/场景等角色分类、标签、搜索、筛选、批量选择、移动、复制、删除、下载、裁剪和编辑元数据。
- 原图 URL/文件是权威资源；缩略图通常通过 `/api/media-preview` 动态生成并缓存到 `data/media_previews`，不是另一套业务数据库记录。
- 删除动作会同时处理元数据与受控文件，但当前“日志/生成媒体清理”回归测试存在实现缺失，见风险章节。

### 8.2 本地素材与共享文件夹

- 本地上传存入可配置的 `assets/uploads` 等目录，可维护文件夹树、Caption 和分类旁车文件。
- 共享文件夹只登记外部路径并建立浏览入口，不会自动复制全部文件；导入到库时才发生复制。
- Prompt Library 位于 `data/prompt_libraries.json`，区分系统只读库和用户可编辑库。

### 8.3 “画布图片”不是“素材库图片”

- “画布图片”由服务端扫描 `data/canvases/*.json` 中节点的 URL 字段形成索引。
- “素材库图片”有独立的 `asset_library.json` 记录与 `assets/library` 文件。
- 两者能通过“复制到画布、导出到素材库、LocalStorage 收件箱”等桥接，但不是同一 Asset 实体系统。
- 在线生图页使用自己的上传槽，没有直接嵌入完整素材库浏览器。

## 9. Provider 与模型

### 9.1 Provider 总表

| Provider / 协议 | 文本 | 图片 | 视频 | Key 来源 | Adapter / 请求方式 |
|---|---:|---:|---:|---|---|
| OpenAI-compatible 自建中转 | 是 | 是 | 条件式 | `API/.env`，UI 仅掩码 | 通用 OpenAI Chat、Images Generations/Edits；Base URL 由本机 Provider 配置决定 |
| Kie | 否 | 是 | 当前未接入 | `API/.env` | `providers/kie/*` 受控异步 Adapter |
| ModelScope | 是 | 是 | 依模型 | 环境变量 | OpenAI-compatible/专用图片分支 |
| Volcengine Ark | 是 | 是 | 是 | 环境变量 | 方舟任务/模型路由 |
| RunningHub | 否 | Workflow/App | Workflow | 环境变量 | WebApp/Workflow 提交与查询接口 |
| ComfyUI | 否 | Workflow | Workflow | 本地实例，无 API Key | `/api/comfyui/*` 与 `/api/workflows/*` |
| Gemini 原生协议 | 是 | 条件式 | 条件式 | `API/.env` | 原生 `generateContent`、版本路径规范化与动态模型目录 |
| Gemini CLI | 是 | 条件式 | 条件式 | CLI 登录态 | 本机 CLI Adapter |
| Codex CLI | 是 | 条件式 | 条件式 | CLI 登录态 | 本机 CLI Adapter |
| Jimeng CLI | 条件式 | 是 | 是 | CLI 登录态 | 本机即梦 CLI Adapter |
| Midjourney / APIMart | 否 | 是 | 条件式 | 环境变量 | Submit/Action/Modal/Task API |
| Tudou Async 等扩展 | 条件式 | 是 | 是 | Provider 配置 | 专用异步分支 |

Provider 是否已配置和可调用属于本机运行状态；正式文档只描述协议能力，不固化某次审计时的 Key 状态。

### 9.2 图片模型能力

Capabilities 来源分两层：通用模型参数由 `main.py` 的 Provider/参数构建逻辑和 `/api/image-params` 返回；Kie 的受控白名单与映射位于 `providers/kie/models.py`。

| UI 模型 | Provider | 文生图 | 图生图 | 最大参考图 | 分辨率/比例 |
|---|---|---:|---:|---:|---|
| GPT Image 2 | Kie | 是 | 是 | 16 | Adapter capability 决定；UI 只展示产品名 |
| Nano Banana Pro | Kie | 是 | 是 | 8 | Adapter capability 决定 |
| `gpt-image-2` | 自建 OpenAI-compatible | 是 | 是 | Provider/通用请求限制 | 动态参数表；画布已修复 `1K` 等参数归一化 |
| Z-Image Turbo | ModelScope/本地 | 是 | 否 | 0 | 宽高/分辨率由页面或模型参数控制 |
| Qwen Image / Edit、FLUX.2 Klein | ModelScope | 依模型 | 依模型 | 依模型 | 动态模型元数据 |

通用 Provider 模型列表是动态配置，不能把“代码支持某协议”误写成“当前环境一定能调用该模型”。

## 10. OpenAI-compatible 自建中转

- Base URL 由本机 Provider 配置决定；Key 来自 `API/.env`，前端只应获得配置状态和掩码。
- Chat 走 OpenAI-compatible Chat 接口；图片根据是否有参考图选择 `/v1/images/generations` JSON 或 `/v1/images/edits` multipart。
- 参考图会拆分普通图片和 Mask 语义；后端兼容 URL、data URL/Base64 以及多种 Provider 返回格式。
- 结果解析兼容 `b64_json`、URL 和 data URL，随后统一保存到本地输出目录并写历史。
- 错误会转换为较友好的 HTTP/Provider 消息；没有修改这条链路，也没有进行付费调用。

Gemini Provider 不走这条 OpenAI-compatible Chat 转换链路：服务端把 OpenAI messages 转换为 Gemini `contents`，向规范化后的 `models/{model}:generateContent` 发送原生请求，并解析 `usageMetadata`。模型发现会合并 Base URL 下的版本目录与 Gateway 根目录 `/v1/models`，最终仅保留 `gemini-*`，名称含 `image` 的模型归图片能力，其余归 LLM。

## 11. Kie 当前真实实现

- UI 只暴露 GPT Image 2 与 Nano Banana Pro。
- GPT Image 2 无参考图路由到 `gpt-image-2-text-to-image`，有参考图路由到 `gpt-image-2-image-to-image`，字段为 `input.input_urls`。
- Nano Banana Pro 路由到 `nano-banana-pro`，字段为 `input.image_input`。
- GPT Image 1.5 图生图内部路由 `gpt-image/1.5-image-to-image` 已存在，但不在当前 UI 白名单。
- 本地、Blob、data URL 等参考图会先解码、EXIF 转正、规范化为 RGB 8-bit PNG/JPEG、校验 MIME/magic bytes/非零大小，再经官方 File Stream Upload 转成匿名 HTTPS URL。
- 创建任务只调用一次 `/api/v1/jobs/createTask`；查询使用 `/api/v1/jobs/recordInfo?taskId=...`，不会回退到旧 `/v1/images/tasks/...`。
- 成功时解析字符串形式 `data.resultJson`，读取 `resultUrls`；失败时保留 task ID、错误码和消息。
- Adapter 可按上游 task ID 重新查询状态，但画布本地 `CANVAS_TASKS` 只存在内存中；服务重启后没有完整的持久化任务队列与 UI 自动恢复。
- Kie 专项测试有完整通过记录；该记录未创建付费任务。

## 12. 视频体系

项目代码包含：文生视频、图生视频、首尾帧、多参考图/视频/音频、视频上传与结果保存，以及 MiniMax、RunningHub、Volcengine、Jimeng、APIMart、Tudou、Kling/Veo/Seedance 等模型分支。

- 普通画布有通用 Video、MiniMax、RunningHub 与 `ltxDirector` 节点。
- 智能画布有 `smart-video-generation` 和 `smart-minimax`，MiniMax 带分段时间线与多媒体引用。
- `LIX Director` 在需求中的名称对应源码里的 **LTX Director**，它是 ComfyUI 工作流/时间线节点。
- 某些上游只接受公网 URL 时，服务端会把本地视频上传到临时公网托管服务。
- 生成结果会下载到本地资产目录并写回画布。

**可用性结论**：视频 UI 与路由已存在，但多数云视频能力依赖部署环境中的 Key、CLI 登录态、付费额度、Workflow ID 或本地 ComfyUI。应列为“条件可用/实验性”，不能仅凭代码分支宣称已可用。

## 13. Workflow、Project、Canvas、Asset 的真实关系

真实数据模型不是 `Project → Workflow → Asset` 的完整层级：

```text
Project（data/projects.json）
  └─ 通过 canvas.project 字段筛选 Canvas

Canvas（data/canvases/*.json）
  ├─ nodes / connections / viewport / settings / logs
  ├─ 可导出选中节点为 Canvas Workflow JSON/ZIP
  └─ 节点通过 URL 引用 Asset

ComfyUI Workflow（workflows/*.json + *.config.json）
  └─ 全局资源，不隶属于某个 Project

Asset / Prompt / Shared Folder
  └─ 全局资源，通过选择、复制或 URL 被 Canvas 使用
```

- Canvas Workflow 是节点与连线的可移植片段，可 JSON/ZIP 导入导出或存入素材库。
- ComfyUI Workflow 是外部图执行图，与 Canvas Workflow 不是同一种 JSON。
- 没有发现“一键运行整个任意画布图”的统一事务引擎；运行主要从节点、级联、Loop、Composer 或专用 MiniMax/LTX 工作台触发。

## 14. 设置与主题

| 设置 | 保存位置 | 生效方式 |
|---|---|---|
| Provider/模型/协议 | `data/api_providers.json` | 服务端保存后立即供页面读取 |
| API Key | `API/.env` | 保存后服务端重新加载全局环境；无需手工重启的正常路径 |
| ComfyUI 实例/Workflow | 服务器配置 + `workflows/` | 保存后立即广播/读取；外部实例必须在线 |
| 存储目录 | `data/storage_settings.json` | `apply_storage_settings` 立即切换，需谨慎 |
| 主题 | LocalStorage `studio_theme`，兼容旧 `canvas_theme` | 主壳层广播到 iframe，立即生效 |
| 语言 | LocalStorage `studio_lang` | 前端 i18n 立即切换/刷新组件 |
| UI 缩放 | LocalStorage `studio_ui_scale_mode` | 前端立即生效 |
| 后端代码 | Git/文件 | 需要重启服务 |
| 静态 JS/CSS | 文件 + cache token | 浏览器刷新；cache token 在响应内存中生成，不改写 tracked HTML |

Light/Dark 都由 `static/css/theme.css` 的 CSS variables 驱动。2026-08-27 的 `2ad2df6` 把 Dark 改为用户定制的 Neutral Dark，并同步普通/智能画布相关页面。

## 15. 隐藏或集成能力

- **PS UXP 集成**：没有独立页面，但后端存在 `/api/image-jpeg`、`/api/ai/upload-base64`、`/api/comfyui/upload-base64` 等兼容严格图片解码和不能稳定发 multipart 的 UXP 客户端接口。
- **浏览器采集**：没有发现独立的“浏览器采集”页面或明确采集扩展代码；素材可通过 URL/本地文件/共享文件夹导入，这不等同于浏览器采集产品。
- **CLI 集成**：Codex、Gemini、Jimeng 有状态/帮助/登录相关路由，真实可用性依赖本机 CLI 状态。
- **数字人/真人认证**：素材库 UI 会显示多个平台，但源码明确只有已接入平台可用，其他项标记待接入。

## 16. 已确认、条件式与不完整能力

### 已确认或有较强历史证据

- 核心页面、静态资源、API 和 `/ws/stats` 均有真实本地服务验证记录。
- Kie Adapter、Canvas Reference Picker 与图片生成参数均有专项回归测试。
- Smart Canvas 已包含拖动关联 Edge 局部更新、Composer 纵向边界和键盘删除选中 Edge 的专项回归。
- Gemini Provider 已包含原生协议、URL 规范化、动态模型目录和 Gemini-only 分类专项回归。
- 这些是历史交付证据；当前 checkout 升级后仍须重新执行相关测试。

### 条件可用/实验性

- ModelScope、RunningHub、Volcengine、Midjourney/APIMart、各类视频生成、Codex/Gemini/Jimeng CLI。
- 本地 ComfyUI 文生图、增强、Klein、LTX：依赖本机实例和对应 Workflow。
- Agent 自动选工具：代码存在，但不同 Provider 对文件、多模态和工具结果的兼容性不同。
- PS UXP：后端兼容端点存在，未发现本仓库内完整面板 UI。

### 当前明确不可用或不完整

- 素材平台认证并非全平台可用；源码对未接入平台明确返回“待接入”。
- 画布任务没有跨服务重启的完整持久化恢复。
- 历史/画布日志媒体清理实现与测试不一致，14 项测试报 `AttributeError`。
- 浏览器采集没有找到独立实现。

## 17. 当前主要风险

1. **自动更新高风险**：会替换 `main.py` 和整个 `static/`，直接覆盖当前大量二改；又不更新 `providers/kie/`，可能产生前后端/Adapter 版本错配。
2. **两套巨型画布前端重复**：普通与智能画布各自维护大型实现，Picker、交互、参数修复必须双写，最容易再次出现功能漂移。
3. **回归套件存在历史基线错误**：Canvas log cleanup 有 14 项既存错误；在独立修复前不应依赖相关删除/清理路径。
4. **任务恢复不足**：付费上游任务与本地内存任务状态可能在服务重启后脱节。
5. **条件安全风险**：LaunchAgent 当前只监听 `127.0.0.1`；但直接运行 `main.py` 会监听 `0.0.0.0`，同时应用没有认证且 CORS 为 `*`。若暴露到局域网，这是严重风险。
6. **运行时迁移要保持边界**：cache token 已改为响应内存处理；后续启动与保存逻辑仍不得把用户数据或运行时状态写回 tracked source。
7. **浏览器告警**：主壳层组合加载可复现一个 MutationObserver 参数错误和两个 Tailwind CDN 生产警告；智能画布独立页面控制台无错误。

## 18. 产品结论

Infinite Canvas 是 AI Studio 的核心工作区之一，但不是整个产品。已有较强证据的区域包括本地页面可达性、Kie Adapter、Gemini 原生协议、Canvas Reference Picker、Smart Canvas 关键交互与图片参数回归；最需要谨慎的是自动更新、两套画布重复实现、日志媒体清理、异步任务恢复和条件网络暴露。未来新增跨画布功能，应优先建设共享的 Provider 请求模型、capability、引用解析/去重/循环检查、任务持久化和节点 schema 层，再分别做薄 UI 适配。
