# 公网个人 API 完整接入：开发、mock 验收与部署准备

本次仅在 `codex/public-beta-full-personal-api` 开发。开发起点及本轮只读核实的生产运行代码均为 `5449a692da33200f8d621a8974414da5e4c9de7c`；没有将另一工作区的 Git HEAD 当作生产代码。未修改 VPS、生产 env、真实用户配置、owner、main、stable 或 VERSION，未部署。所有执行验收使用临时账号、假凭证和本机 mock；真实模型调用、真实邮件发送均为 0。

## 原问题与本次结果

只读取得的 Mio `custom-api`：enabled=true，protocol=openai，base_url 为已配置的 HTTPS `/v1` API 地址，图片模型 3 个：`gpt-image-2`、`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`。本报告不记录真实 API Key 或用户的完整 API 地址。当前生产用户的 `/api/config` 与 iframe 内存计数未成功取得，不能把离线复现写成在线观测。

在实际生产 SHA 上复现的编译策略把个人 OpenAI/Gemini 图片和个人视频过滤掉。根因位于 Instance 的能力/权限编译层，设置本身已有模型；旧 Canvas 接收到被过滤的目录后显示空列表。本次统一修复所有公网用户，不使用特定账号、Provider ID、user_id 或域名放行。

升级后离线既有用户验收：原存储文件内容、模型顺序和凭证引用不改写，3 个原图片模型进入统一能力结果。浏览器第一阶段的设置、config、Canvas 三层图片计数为 3/3/3；随后临时用户手动添加新模型和多用途模型，最终三层为 5/5/5，文本 2 个、视频 2 个。Canvas `apiProviders` 为 1 个 Provider，其图片列表为 5 个；默认托管选择解析到该用户的个人 Provider。

## 统一链与权限边界

`provider_schema` 保留协议、全局 OpenAI 图片模式、编辑路由、同源自定义端点、逐模型协议、逐用途适配器、RunningHub 定义、平台参数和四类私有凭证。`instance_provider_settings` 保存个人配置；`instance_providers` 读原存储并编译；`provider_capabilities` 解析执行能力；`instance_model_policy` 检查 ownership、启用状态、用途、凭证与资源边界；`instance_model_tasks / NetworkTasks` 管任务；`Execution + GuardedClient` 为已有网络构造器注入当前用户凭证与受控 HTTP client。

模型身份为 Instance 内的 Provider + 精确模型 ID + 用途；任务另绑定 owner、Provider/凭证版本、原 Canvas/节点和生成 nonce。逐用途协议/适配器优先，兼容旧逐模型覆盖；同 ID 的 llm/image/video 不再相互去重。全局 OpenAI 请求模式不再污染原生逐模型协议。

公网 Instance 在已认证 session 中统一授予 `manage_own_providers`；既有权限表无需人工修改，新注册用户无需管理员批准。新用户初始个人 Provider 为空。管理员共享 Provider 仍保持原明确授权、成本与参数策略，不能通过个人接口修改。

设置、目录、`/api/config` 与节点使用同一能力解析结果。已配置但停用、缺凭证、缺契约的模型保留在列表中并显示原因。区分 configured、protocol_executable、call_verified；mock_verified 不会变成真实调用已验证。`/models` 目录只用于添加与推荐，不等于实际账号有调用权限，也不是手动添加的前置条件。

## 协议 × 用途 × 请求模式与验收表

“通过”指完整 mock 请求、响应解析、归属及结果保存通过，不代表真实上游已验证。原生支持范围以当前构造器和接口契约为准。

| 有效协议 | 用途 | 已贯通请求模式/执行器 | 返回与恢复 | mock 验收 |
|---|---|---|---|---|
| openai | 文本/多模态 | Chat Completions；用户 token、temperature、图片/视频输入 | JSON、SSE；私有会话；本地取消 | 通过 |
| openai | 图片生成/编辑 | 标准 generations JSON / edits multipart；同源自定义生成/编辑端点 | URL/base64，受控下载与原格式保存 | 通过 |
| openai | 图片生成/编辑 | openai-json | JSON 响应、图片参考字段 | 通过 |
| openai | 图片生成/编辑 | openai-responses（background） | 原 response ID 查询，图片解析 | 通过 |
| openai | 图片生成/编辑 | openai-responses-stream | SSE、提前保存 response.created 收据，原任务恢复 | 通过 |
| openai | 图片生成/编辑 | openai-responses-sync | 同步 Responses 图片解析 | 通过 |
| openai | 图片生成/编辑 | openai-video-proxy | 专用代理构造及异步查询；参考素材须明确发布 | 通过 |
| openai | 图片生成/编辑 | tudou-async | 专用 task/input 结构、原 task ID 查询 | 通过 |
| openai | 图片生成/编辑 | modelscope-async | ModelScope 异步标头、精确 ID、LoRA、原任务查询 | 通过 |
| openai | 图片生成/编辑 | tudou-grok-image（逐模型适配器） | Grok multipart 编辑、原图片结果 | 通过 |
| openai | 视频 | 通用 videos multipart/JSON 契约及下表专属适配器 | 异步 ID、查询、MP4/WebM 保存 | 通过 |
| gemini | 文本/多模态 | generateContent，原生多模态 parts | JSON；公网流式入口转为 SSE 输出 | 通过 |
| gemini | 图片生成/编辑 | generateContent + imageConfig，参考图 inlineData | inline 图片/URL，受控保存 | 通过 |
| gemini | 视频 | 当前没有 Gemini 原生视频执行契约 | 显式原因；可为模型另选已实现的协议 | 不伪装可运行 |
| kie | 图片生成/编辑 | 已有 GPT Image 2 / Nano Banana input 模板；新精确 ID 可明确选择已有模板 | 原 createTask/query、私有上传缓存、原任务恢复 | 通过 |
| kie | 文本/视频 | 当前没有这些原生契约；图片新 input 结构也不能猜测 | 保留配置、明确缺少适配 | 不伪装可运行 |
| apimart | 文本/多模态 | OpenAI 兼容 Chat Completions | JSON/SSE、私有会话 | 通过 |
| apimart | 图片生成/编辑 | 原生图片任务；独立 Midjourney imagine/blend/edit/action/modal | 异步查询，后续动作绑定自己的原任务 | 通过 |
| apimart | 视频 | 原生视频任务；可明确选 apimart-veo31 | 查询、素材角色、下载保存 | 通过 |
| volcengine | 文本/多模态 | 已有兼容文本构造器 | JSON/SSE、私有会话 | 通过 |
| volcengine | 图片生成/编辑 | 原生图片 JSON，参考图片及自选尺寸 | URL/base64、原格式保存 | 通过 |
| volcengine | 视频 | Ark contents 任务；多图/视频/音频；显式帧角色与 owned asset URI | 原任务查询、受控下载保存 | 通过 |
| runninghub | 文本/多模态 | 已有兼容文本构造器 | JSON/SSE、私有会话 | 通过 |
| runninghub | 图片/视频 | 标准 OpenAPI 或已保存上游参数定义 | wallet Key，submit/query，原结果保存 | 通过 |
| runninghub | 图片/视频 | app / workflow，用户保存的节点字段契约 | primary/wallet 明确区分；POST outputs 查询 | 通过 |

全部 8 种 `image_request_mode` 已贯通；Grok 图片是另选适配器。标准编辑、原生多参考图编辑与自定义端点有实际 mock 调用，不只验目录。`image_edit_route=chat` 虽有旧界面选项，但原基线没有可用图片响应契约，本次显示明确缺少契约，并在上传前拒绝编辑；没有把文字响应当图片。

### 已有专属视频适配器（全部 mock 贯通）

| 适配器 | 提交/查询方式 | 参数及素材边界 |
|---|---|---|
| tudou-grok-video | Grok multipart / videos ID | 精确模型、时长、像素/比例、multipart 图片 |
| tudou-sora2 | Tudou JSON / tasks ID | 已有结构没有独立 resolution 字段；显式请求该字段提前报错 |
| tudou-veo31 | Tudou JSON / tasks ID | 已有 Veo 参数结构、明确发布的当前用户素材 |
| tudou-kling | Tudou JSON / tasks ID | 已有结构没有独立 resolution 字段；提前报错 |
| tudou-pixverse | Tudou JSON / tasks ID | 首尾帧不能再混入其它图片，不静默丢素材 |
| tudou-seedance | Tudou JSON / tasks ID | 复用已有 Seedance 结构、明确发布素材 |
| yuli-openai-video | multipart / videos ID | 只有一个 input_reference；多图提前报错 |
| yuli-native-video | 专用 create / query?id | 比例、enhance_prompt、enable_upsample；无时长/分辨率/音视频参考契约 |
| lingjing-video | 专用 multipart / 原任务查询 | 自选比例和时长；原模型 ID |
| agnes-video | 专用字段 / agnesapi 原 ID + 原 model_name | 自选像素、时长转帧数；明确发布素材 |
| apimart-veo31 | APIMart 原生结构 / 原任务查询 | 保留精确 ID、分辨率、时长和素材 |

没有根据域名或模型别名偷偷选适配器，也没有让它们统一走 Kie。通用 OpenAI 视频尚无音频参考字段；相关适配器缺少音视频参考时提前拒绝。RunningHub 节点缺少素材槽位、必填值或参数枚举不匹配时提前拒绝，不先上传再失败。

### 平台专属网络操作

| 操作 | 受控凭证与目标 | 验收 |
|---|---|---|
| Kie 参考素材上传 | 当前 Provider 私有 Key；固定批准上传 origin；按当前用户与凭证版本缓存 | >1 MiB RGBA 原字节、隔离与回归通过 |
| RunningHub 元数据、workflow 保存/读取、素材上传 | 显式选当前用户 Provider；app primary / OpenAPI wallet 区分 | 所有操作及专用 submit/query mock 通过 |
| 明确云端发布 temp.sh / Litterbox | 当前用户素材；固定匿名目标；不携带 Provider Key | 上传 registry 与后续素材归属通过 |
| APIMart Avatar 注册/查询 | 自己的素材与 Provider；持久化原注册收据 | 原注册处理、重复防护和 A/B 隔离通过 |
| 火山 Asset 注册/查询 | 自己的 AK/SK、region/project；固定 Action 签名边界 | HMAC、归属、Active URI 使用及外用户拒绝通过 |
| ModelScope 独立生图、角度控制、普通 MS 节点 | 自己的 Provider/模型/Key；统一异步任务，不读取 owner 全局 token | 路由 mock 与浏览器按钮运行通过 |
| 素材 caption/classify、聊天 Agent | 自己的多模态/文本/图片执行器，无 owner 路径 | mock 通过；浏览器验普通聊天，Agent 自动意图另由后端 mock 验收 |

## 参数：移除与保留

移除个人账号旧简化策略：OpenAI/Gemini 只准 llm、image 只准 Kie、video 为空、固定模型名权限白名单、模型 ID 全局用途去重、默认强制 1K/1:1/1 张、参考文件 1 MiB、固定输出 token、quality 只准空/auto、个人文本沿用短 120 秒等待、部分视频固定低清/短时长/截取 3 个视频、LoRA 自动夹到 0–2、根据名称切换旧模型或接口、刷新时自动替换精确模型选择。个人 Kie 上传保留原图字节，不自动缩图或转格式；个人火山未来模型不再套用 Seedance 的固定音频时长推测。参数不适配时明确报错，不降画质、不截 Prompt、不替换模型。

上游/适配契约限制与服务器限额分开：专用接口有哪些字段、单 reference 槽位、帧角色、必填项、已保存 RunningHub 枚举、Kie 选定模板的既有枚举，来自当前构造器或明确的参数定义。推荐清单不再决定通用协议调用权限。这些接口契约已经 mock 验证，本轮没有实测上游权限或最新能力；不能把某个模板的数字推断为所有未来模型的真实上限。未有契约的参数会在上传前明确拒绝。

保留且向个人设置界面说明的服务器保护：每次最多 8 个图片执行、每类最多 20 个参考素材、单素材 30 MiB、请求/响应 32 MiB（请求预留协议开销）、图片解码 32 MP、单执行等待 1800 秒、视频时长参数 1–3600 秒安全范围。元数据读取仍 120 秒，专用上传/注册操作 600 秒；长生成 Gateway 转发 1830 秒，仅对应长路由，连接仍 3 秒。上述是服务器资源保护，不能当作上游能力承诺。文本请求受 32 MiB 预算，现有产品 Prompt 100,000 字检查保留且不自动截断。

现有生产用户上限、运行 Instance 上限、并发/队列、磁盘阈值、上传与存储配额配置未修改；没有关闭 INSTANCE_MODELS、请求/响应大小、像素、内存或超时保护。个人配置仍有安全 ID 格式、单 Provider 200 个不同 ID / 600 个用途项的容量界限。

## 任务、结果与网络安全

生成 POST 前先持久化未知状态；收到 task/response ID 后保存原查询契约。每个执行只允许一次付费提交，没有接口回退或自动再生成。已知远端成功而下载失败时保留原响应，恢复下载；未完成时只查原 ID。没有收据的未知提交需要人工核对。批量中尚未提交的项不会在恢复时补交；恢复已提交的结果不表示剩余批量项也已生成。取消为提交前取消或停止本地等待；未确认上游取消/退款。

绑定原节点、nonce 和 Provider/凭证版本，History 按任务 ID 去重。显式新 nonce 代表用户新生成意图；重复 nonce 幂等。重启兼容迁移只接受与 5449 旧编译摘要精确匹配的原配置/凭证，黄金摘要及原 Kie/网络任务 query-only 测试通过。修改配置/凭证后不猜测旧任务环境，恢复会明确拒绝；已成功存储的旧媒体和 History 可读。

Session、CSRF、Origin、用户/Instance/Provider/任务/素材归属保留。真实出站仅 HTTPS/批准端口，公网 DNS/IP 验证、固定解析连接与 socket audit、禁代理继承/重定向/自动重试。所有已接入构造器通过注入 client；额外 requests/CLI 出口在 Instance 中被拒绝，回归检查仍在。只有启动配置允许的 mock localhost 可用于测试，用户不能自行开放 localhost。

凭证只发往对应 Provider origin 或适配器批准的固定目标；RunningHub primary/wallet 和火山 HMAC 分开，下载 CDN 不带 Provider Key/Cookie。混合 Provider 只有有效 Kie 图片执行器获得固定 Kie 上传权限，其它执行器不继承该权限。四类秘密不回显、写入私有引用，错误/上游回声/SSE 分段及 JSON 转义表示经过共享脱敏。adapter_parameters 不能覆盖身份、模型、Prompt、Key、端点、任务绑定或绕过素材引用；LoRA 必须为有限数值，不自动修正权重。Gateway 转发视频 Range/Content-Range/Accept-Ranges，浏览器实际解码测试通过，另一用户不能取同一素材。

## 验收映射

完整检查使用 `./scripts/check.sh`（Python 编译、项目 JS 与 22 段 inline JS 的 Node 语法检查、全部 unittest、Git diff 检查），另运行 pip check。旧测试全部保留；旧策略断言按新产品行为更新，并增加变更目标仍须重新提供凭证、缺凭证原因与未知专属契约拒绝等检查。没有删除或跳过失败项。

测试集合共 797 项：原集合 749 项，加 41 项个人网络适配器、3 项公网注册/既有用户/媒体隔离、2 项真实前端选择与刷新函数、2 项多凭证 JSON/URL/日志脱敏测试。最终本地全量 797 项通过，耗时 448.210 秒；编译、JS 语法、Git diff 检查及 pip check 均通过。收尾补充的共享 Provider 能力目录与 RunningHub 原生文本缺契约检查也局部通过；提交后 CI 对最终提交重新执行完整 797 项检查，对应状态与链接在交付记录中记录。

| 要求 | 自动或浏览器证据 |
|---|---|
| 1 既有用户原 Provider | public legacy-upgrade；原存储逐字节/顺序/Key ref；3 图进入能力结果 |
| 2 新注册无需特批 | public mock 邮箱注册 Alice/Bob；初始空 Provider；自配能力 |
| 3 相同 Provider ID 不同 Key | own-provider + network A/B 调用身份、元数据、缓存、目录、任务 |
| 4 文本/图片/视频分类 | multi-purpose catalog；实际普通 Canvas 与 Smart 的三种调用 |
| 5 同模型多用途 | arbitrary-text-v99 三种用途同时从 GUI 目录添加；5/2/2 保留 |
| 6 自定义新模型 | 六协议及全部通用模式的 novel/future 精确 ID；上游 body/路径断言 |
| 7 禁用/未配置/缺凭证 | negative preflight；GUI 禁用仍显示原因，配置恢复 Key ref 保留 |
| 8 各网络适配器完整请求/结果 | 网络 mock 原生协议、8 图片模式、Grok、11 专属视频、平台操作 |
| 9 生图/编辑/视频轮询/文本 | multipart 编辑 + 原生多参考图编辑；原任务异步与 JSON/SSE |
| 10 高于旧默认参数 | 2K/4K、2:3、9:16、high、3 张、8/24 秒、3.25 LoRA、16 秒音频 |
| 11 原节点/History/Preview/重登 | 普通 12 节点、Smart 3 节点；批量 History 一次；视频 readyState=4、32×48、1 秒；重登数据保留 |
| 12 保存实时更新 | 原 iframe + 第二标签页 BroadcastChannel；停用原因；Smart refresh 无异常 |
| 13 无串用户/Key 泄漏 | A/B 任务、媒体、会话、原 Avatar/upload registry；Key 回声/SSE；Gateway 日志检查 |
| 14 无额外网络出口 | socket audit、controlled client、无 owner fallback、原认证隔离回归 |
| 15 无适配明确提示 | Gemini 视频、Kie 缺模板、编辑 chat、缺字段/枚举/素材槽位等提前拒绝 |
| 16 Kie/LLM/认证/注册回归 | 原测试保留；全量脚本与 CI 跑同一套测试 |

浏览器为真实本机 Gateway + Supervisor + Instance，mock HTTP 上游，真实产品按钮运行；用开发接口/工厂准备临时 Provider、节点和连线，没有替换产品生成结果。覆盖普通 Canvas 3 张批量图/LLM/视频、Smart 图/LLM/视频、在线图、普通聊天、MS 节点 LoRA 3.25、独立 MS 生图与角度控制。在线最终按钮运行参数为 2K/2:3/high，输出图片实际解码。两阶段验收期间只重启临时进程；保留配置及画布，遇到 session handoff 后按实际重新登录。临时环境验收结束已停止并清除。

## 尚未实现或不应承诺的能力

Gemini 原生视频、Kie 原生文本/视频、RunningHub app/workflow 文本结果、未知平台 input 结构、`image_edit_route=chat` 图片解析尚无完整契约，均明确可见。RunningHub 文本表格中的支持是已有 Chat Completions 兼容执行器，不代表原生 app/workflow 的节点文本结果已实现。为已有通用协议新增精确 ID 不需改公网白名单；专用平台的新 input 结构仍需适配器，不能凭目录名字猜测。

没有新增远端供应商退款/取消 API，没有在无收据状态自动补交或自动重跑批量剩余项，没有真实上游验权。视频保存支持有界 MP4/WebM，未开放公共任意转码/任意服务器进程。Agent 自动意图由后端完整 mock 验收，浏览器聊天验收使用个人默认普通 chat 模式。Smart 的通用参数和协议选择已贯通，专属高级参数结构通过配置/专用入口或 adapter_parameters 复用，未为每个专属平台另造整套 Smart 高级面板。

任意 Shell、ComfyUI 本地执行、owner CLI 登录、服务器源码更新、服务器任意本地文件和其他用户凭证继续关闭。这些不属于个人网络 API 开放范围。

## 后续生产升级方案（需要另行部署授权）

| 层 | 需要加载的改动 | 加载要求 |
|---|---|---|
| Gateway | 长路由转发时间、视频 Range 响应、前端资源缓存 | 新 release 进程重启/重新加载；保持原 env 与容量配置 |
| Supervisor | 新 Worker 程序路径与 Python/venv | 服务 WorkingDirectory/ExecStart 指到批准新 release；重启并核实旧 Worker 被 adoption |
| 每个 Instance | 能力、权限、私有 Provider 编译、受控执行与任务恢复；缓存的 JS 字节 | 每个既有 Worker 必须按用户滚动 stop/start，PID/start time 会变化 |
| 前端 | 设置、节点选择、通知、独立入口、缓存 hash | Instance 加载后刷新 iframe/页面；必要时重新登录 |

`program_assets` 按进程缓存字节；仅替换磁盘 JS 不会让旧进程加载新资源。Supervisor 退出默认保留 Worker；新 Supervisor 可以核对标记、PID/starttime/UID/health 后接管，所以仅重启 Gateway/Supervisor不能声称既有用户都已更新。

审批后建议依次：

1. 备份 Gateway/用户私有数据及任务 ledger，保存 5449 回滚 release；不导出明文 Key。核对容量 env 原值不变。
2. 暂停新生成入口并等待在途调用排空。逐实例盘点已知收据、远端成功待下载与无收据未知状态；不要用批量杀进程代替任务核对。
3. 准备新 release 与独立 venv，先完成同环境 pip check；切 Supervisor 到新 release，核对 adoption、原数据根和账户数量；加载新 Gateway。
4. 通过既有 Supervisor 控制接口按 user_id 串行停止、启动既有 Instance，不直接改业务 DB、不按猜测端口/PID操作。逐个核对新 PID/starttime、WorkingDirectory/SHA、健康、私有数据根及 config 模型数。容量满时严格一停一启。
5. 刷新前端并执行私有配置三层检查。所有已有 Instance 完成后才能宣布统一生效；新注册 Instance 从新 release 创建。
6. 对仍有收据的任务只恢复原查询/下载，并核对原节点与 History 去重；未知状态人工核对，不重新提交。确认 A/B 媒体与会话隔离，再恢复入口。

重启会断开 WebSocket、文本流和本地等待，正在上传的请求可能中断；上游生成可能继续计费，不能承诺远端取消。Gateway/Instance handoff 与 session 状态可能要求重新登录，本轮临时滚动验收确实重新登录过；保存的 Provider、画布、History、媒体和原任务记录保留。不能同时承诺 PID 不变与新 Python 已加载。回滚需按原 release/venv 滚动重新加载，并核对任务 ledger 兼容性；旧编译策略会再次过滤新用途目录，不能误删用户配置或自动重放生成。

最终测试数量、Git commit SHA 与对应 CI run URL 以本报告所属提交的交付记录为准；CI workflow 仅安装依赖、pip check 与项目检查，没有部署步骤。
