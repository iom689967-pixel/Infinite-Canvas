# 统一完整工作台验收记录

日期：2026-09-09。基线：`3a20750661d68224cc0536cc08989fae05864a05`。
分支：`codex/unified-instance-workspace`。

## 实现结果

显式实例登录后，`/` 直接使用正式 `static/index.html`。普通单机模式也使用这个文件，保留原启动行为。没有新增 `instance-index.html`，也没有复制 Canvas 实现。

`instance-workspace.html` 已降级为指向 `/` 的兼容跳转页；旧书签仍可使用。后续不再需要旧入口时可以删除。

认证在服务端渲染 HTML 时接入：`instance_frontend.py` 从已校验 principal 构造身份、权限和存储 namespace，统一注入公共模块。HTML 不包含 Key、CSRF token 或数据根路径。身份响应头防止旧页面接收切换账号后的另一身份响应。

公共模块：

- `instance-storage.js`：所有 localStorage/sessionStorage 调用和 BroadcastChannel 名称按实例、数据根及用户名的哈希隔离；在正式脚本之前同步安装。
- `instance-session.js`：初始化 `/api/auth/me`，等待 session 就绪、添加同源写请求 CSRF、处理 401/身份变化、同步 logout、检查恢复的旧页面。
- `instance-ui.js` 与 `instance-ui.css`：在原导航加入账号和退出控件，按权限隐藏系统入口或禁用执行按钮。

## 页面与权限

| 页面 | 接入方式与边界 |
| --- | --- |
| 正式首页、项目/Canvas 列表 | 原 HTML/CSS/JS，实例默认进入无限画布 |
| Smart Canvas、普通 Canvas | 原编辑器，统一 session/fetch/存储层；数据仍走实例 API |
| GPT 对话、素材库、Prompt 库 | 原页面及数据接口；没有读取 owner 文件的新增路径 |
| API 设置 | 原 HTML/CSS，继续使用个人 `instance-api-settings.js` 和 `/api/instance/providers`；保留 manage_own_providers |
| 文生图、增强、编辑、角度、在线生图 | 原页面可见；当前后端未开放的执行能力禁用并说明 |
| Workflow 设置 | 原工作流界面；隐藏机器后端地址管理，禁用本地执行测试 |

更新/rollback、全局重启、全局 Provider、CLI 和服务器文件管理的服务端权限规则没有放宽。原 Session、CSRF、Origin、SSRF、task/media/provider ownership 和实例路径边界继续生效。前端显示完整功能结构不表示新增后端执行授权。

个人 Provider 的增删改、启停、手动模型与模型发现流程保留。Key 仍由后端保存；输入保存后清空，不回显，不写 localStorage 或 Canvas。Canvas/GPT 模型目录继续由当前实例的过滤接口提供。

## 存储与资源审计

所有共享页面内容状态统一使用 `mio:<namespace>:<原键名>`。Prompt presets/overrides、viewport、model order、上次项目/Canvas、Smart Canvas 参数以及 sessionStorage 草稿都在范围内。theme/language 本轮也选择隔离，不继承旧无前缀内容。

logout 使当前页面存储接口失效，并同步同一身份的其他标签页返回登录页；保留用户自己的前缀数据用于下次登录恢复。新身份使用另一前缀。Node VM 测试在同一个底层 Storage 中验证 A/B，而不依赖两个端口天然隔离。

登录页公开资源保持 2 个。登录后固定静态清单共 71 个，A/B HTTP 检查全部 200。补齐工作流翻译资源，并将纯 API 设置翻译包归入已登录共享资源，避免无 Provider 管理权限的用户加载通用 i18n 时 403；API 设置页面及管理脚本仍需要原权限。

浏览器逐页访问正式导航、API 设置、素材库和工作流设置。修正翻译资源后，最后一批捕获的 123 条网络事件无失败；事件缓冲曾截断，因此完整资源覆盖以固定清单 HTTP 回归及 i18n loader 清单测试为准，不将浏览器日志称为无遗漏的网络抓包。

## 真实浏览器 A/B 验收

两个临时 ASGI 实例使用不同数据根、账号和假凭证，仅配置精确授权的本地 mock 地址。

| 验收项 | 结果 |
| --- | --- |
| A/B 登录到正式完整主页、相同导航 | 通过 |
| A 创建项目与 Smart Canvas | 通过 |
| A 使用原上传节点/文件选择器上传纯色测试 PNG | 通过 |
| A 保存 Prompt 节点及图片节点中的 Prompt | 通过 |
| A 在正式 API 设置新增个人 mock、手动模型 | 通过；Key 保存后不回显 |
| Smart Canvas 选择个人 Provider 与模型 | 通过，无运行请求 |
| A logout/重新登录恢复项目、图片、Prompt、模型选择 | 通过 |
| A 普通 Canvas 创建、保存 Prompt、重载 | 通过 |
| B 不见 A 项目、Canvas、画布资产和个人 Provider | 通过 |
| B 猜测 A Canvas/media 路径 | 404 |
| 无认证的 Canvas API、个人设置 API、上传媒体和正式 HTML | 401；浏览器导航到登录页 |
| A logout 后其他 A 标签页与单独 Canvas 页面 | 同步回登录页 |

最终安全断言见 `full-workspace-acceptance.json`。A 有 2 个 Canvas，B 为 0。浏览器验收的 mock 请求和 mock create 均为 0；真实 upload/createTask/图片生成均为 0，无模型费用。

临时 A/B 服务和测试数据已清理。原 assistant 运行实例没有替换或重启；本轮交付是开发分支提交，不是运行实例部署。

## 视觉与 owner 兼容

只读查看了 owner 正式首页，对比导航、项目区、卡片、字体、颜色、图标和间距；A 检查了宽屏、窄窗口、导航收展和暗色主题。界面直接来自同一套实现，没有另做用户版布局。

抽查的 15 个页面、样式及共享脚本与当前 owner 文件逐字相同。首页差异仅为实例默认页和更新权限判断。Smart Canvas JS 保留基线已有的受控模型任务/个人 Kie protocol 适配差异，本轮没有改动该编辑器，也没有拿 owner 旧逻辑覆盖已有安全修复。

owner 与 A 均观察到既有 Tailwind 开发提示和 MutationObserver observe 控制台错误；未因此扩展修改正式前端依赖。上述页面与保存验收动作正常，不宣称控制台零错误。

owner 首页 HTTP 200；生产 PID 2184，HEAD `590a041464fa1551fd248dfabc00c2c2f3946646`，工作区干净。本轮未修改、重启 owner，没有访问 owner Key/Provider 配置文件。

## 验证和文件

`./scripts/check.sh`：475/475，全部通过（91.923 秒），包含 Python 编译、JS 语法与 git diff 检查。新增覆盖存储/广播隔离、CSRF/身份切换/logout、静态资源、翻译模块、权限拒绝及重复身份响应头回归。

修改/新增：

- `instance_frontend.py`、`instance_http.py`、`instance_access.py`、`main.py`
- `static/index.html`、`static/instance-workspace.html`、`static/comfyui-settings.html`
- `static/js/instance-session.js`、`static/js/instance-storage.js`、`static/js/instance-ui.js`、`static/css/instance-ui.css`
- `static/js/asset-manager.js`、`static/js/comfyui-settings.js`
- `tests/test_instance_full_workspace.py`、`tests/test_instance_storage.js`、`tests/test_instance_session.js`、`tests/test_instance_own_providers.py`、`tests/manual_full_workspace_browser.py`
- 本记录及 `docs/full-workspace-acceptance.json`

原 Kie 修复 worktree 的 6 字符密码改动和 4 个旧诊断文件均保留原状；没有复制或提交到本分支。未 push、发布、合并 main/stable 或修改 VERSION。
