# Infinite-Canvas 用户定制变更台账

> 用途：以后升级官方版本时，明确哪些本地二改必须保留、哪些文件最容易冲突。
> 本文以已确认的历史交付 commit 记录长期定制；当前分支、HEAD、远端差异和运行状态应以实际环境中的 Git 与服务检查为准。

## 1. 基线与可信边界

| 项目 | 长期维护约定 | 解释 |
|---|---|---|
| 项目根目录 | 按实际安装位置确定 | 文档和正式脚本不得依赖某个用户的绝对路径 |
| 运行分支与 HEAD | 以 `git status`、`git log` 为准 | 文档中的 hash 是历史交付节点，不代表当前 HEAD |
| 远端差异 | fetch 后做 patch-id/三方比较 | 不能用陈旧的 remote-tracking ref 判断升级范围 |
| 用户与运行数据 | 保留在 Git 跟踪边界之外 | `data/` 运行数据、`assets/`、凭证、缓存和本机环境不得随源码提交 |

## 2. 定制提交逐项记录

| 日期 | Commit | 功能 | Normal | Smart | 主要文件 | 当前状态 |
|---|---|---|---:|---:|---|---|
| 2026-08-27 | `def4b87` | Prompt 大编辑器 | — | 是 | `static/js/smart-canvas.js`、`static/css/smart-canvas.css` | 历史交付 commit |
| 2026-08-27 | `553bf1f` | 放大 API 生成面板 | — | 是 | Smart JS/CSS | 已合入 |
| 2026-08-27 | `d7a98d8` | Quick Connect 节点菜单 | — | 是 | Smart JS/CSS | 已合入 |
| 2026-08-27 | `585875c` | Quick Connect 拖线保持可见 | — | 是 | Smart JS、HTML | 已合入 |
| 2026-08-27 | `d80e7f1` | 修复 LLM 编辑区与节点拖拽冲突 | 是 | 是 | 两套 JS/HTML、`touch-mouse.js` | 已合入 |
| 2026-08-27 | `a134890` | 左键 Marquee 与显式 Canvas Pan | 是 | 是 | 两套 JS/CSS/HTML | 已合入 |
| 2026-08-27 | `674b486` | Edge 按贝塞尔曲线与框选区域精确相交选择 | 是 | 是 | 两套 JS/HTML | 已合入 |
| 2026-08-27 | `2ad2df6` | Neutral Dark 主题 | 是 | 是 | `theme.css`、Smart CSS、两套 HTML、Canvas List | 已合入 |
| 2026-08-27 | `16feea4` | Text/Prompt 双击扩大编辑器 | 是 | 是 | 两套 JS/CSS/HTML | 已合入 |
| 2026-08-27 | `549962a` | 右键拖动画布 Pan | 是 | 是 | 两套 JS/HTML | 已合入 |
| 2026-08-27 | `e7005d6` | 磁性浮动连接 Handle | 是 | 是 | 两套 JS/CSS/HTML | 已合入 |
| 2026-08-27 | `f4a5588` | 磁性连接锚点居中 | 是 | 是 | 两套 JS/HTML | 已合入 |
| 2026-08-28 | `eccfcf5` | 稳定浮动 Handle 生命周期 | 是 | 是 | 两套 JS/CSS/HTML | 已合入 |
| 2026-08-28 | `1fd8e66` | Pointer Move 持续追踪磁性 Handle | 是 | 是 | 两套 JS/HTML | 已合入 |
| 2026-08-28 | `ddd7941` | 接入 Kie GPT Image 2 与 Nano Banana Pro | 后端共享 | 是（设置/运行入口） | `main.py`、`providers/kie/*`、API Settings、Smart JS、测试 | 已合入；核心 20 项测试通过 |
| 2026-08-29 | `058bc8c` | 图片缩略图 Hover Preview | 是 | 是 | 两套 HTML、共享预览 JS/CSS | 已合入 |
| 2026-08-29 | `138a17a` | Kie 使用 `recordInfo` 轮询 | 后端共享 | 后端共享 | `main.py`、Kie client/tasks、测试 | 已合入；旧 Ark/OpenAI 查询路径不再用于 Kie |
| 2026-08-29 | `f08deff` | Kie API 设置页协议与模型展示对齐 | 间接 | 间接 | `main.py`、`api-settings.js`、测试 | 已合入本地 main |
| 2026-08-29 | `7f40a1d` | 恢复 Canvas 生成比例/分辨率等参数 | 是 | 是 | 两套 Canvas JS、参数测试 | 已合入；2 项测试通过 |
| 2026-08-30 | `550eed4` | Kie 参考图规范化、官方上传与 URL 校验 | 后端共享 | 后端共享 | `main.py`、`models.py`、`uploads.py`、Kie 测试 | 已合入；无新付费测试 |
| 2026-08-30 | `6e1b678` | 合并 Kie 图片输入审计分支 | 是 | 是 | Merge commit | 历史合并节点 |
| 2026-08-31 | `44e296e` | Canvas Reference Picker 核心：软引用、顺序、物化、去重、上限、循环与 Undo | 是 | 是 | 两套 Canvas JS/CSS、Picker 测试 | 已合入 |
| 2026-08-31 | `0794c66` | 把“画布参考”入口暴露到真实生成面板 | 是 | 是 | 两套 JS/CSS/HTML、Picker 测试 | 已合入 |
| 2026-08-31 | `16963c3` | 修复 Smart Canvas “画布参考”入口 | — | 是 | Smart JS/CSS/HTML、Picker 测试 | 历史交付 commit |
| 2026-09-05 | `a247747` | Smart Canvas 拖动节点时只更新关联 Edge | — | 是 | Smart JS、专项测试 | 历史交付 commit |
| 2026-09-07 | `0773a53` | 修复 Smart Canvas Composer 纵向溢出 | — | 是 | Smart HTML/CSS/JS、专项测试 | 历史交付 commit |
| 2026-09-07 | `86708c4` | 分离运行时数据与源码跟踪边界 | 后端共享 | 后端共享 | `.gitignore`、`main.py`、模板、边界测试 | 历史交付 commit |
| 2026-09-07 | `88cc02e` | 正式记录 macOS helper 可执行权限 | 后端共享 | 后端共享 | 正式 `.command` / `.sh` helper | 历史交付 commit |
| 2026-09-07 | `c478614` | Gemini 原生协议与 Gateway 动态模型目录 | 后端共享 | 后端共享 | `main.py`、API Settings、专项测试 | 历史交付 commit |
| 2026-09-07 | `65c01f2` | macOS 启动脚本优先使用项目 `.venv` | 后端共享 | 后端共享 | 两个正式 macOS 启动 helper | 历史交付 commit |
| 2026-09-07 | `daeb3eb` | Delete / Backspace 删除 Smart Canvas 选中连线 | — | 是 | Smart JS、专项测试 | 历史交付 commit |

说明：需求列表中的“图片/视频节点拆分”在当前 Smart 架构中确实表现为 `smart-image-generation` 与 `smart-video-generation` 两种专用节点，也出现在 Quick Connect；但在上述可审计范围内没有一条标题完全对应的独立提交。不能为了凑清单虚构 commit，应在获取更早官方/本地历史后再追溯它的原始引入点。

## 3. 按功能聚合的升级保留清单

| 定制域 | 必须保留的行为 | 高冲突文件 | 现有验证 |
|---|---|---|---|
| 编辑器效率 | Prompt/Text 扩大编辑、LLM 输入不拖节点 | 两套 Canvas JS/CSS/HTML | JS syntax；需浏览器交互回归 |
| 画布导航 | 左键 Marquee、中键/右键/Space Pan、滚轮 Zoom | 两套 Canvas JS/HTML | 源码核验；需真实操作回归 |
| Edge/Handle | 贝塞尔命中、Quick Connect、磁性 Handle、锚点居中 | 两套 Canvas JS/CSS/HTML | 源码与历史；需交互回归 |
| 主题 | Neutral Dark token | `static/css/theme.css`、画布 CSS/HTML | 页面加载通过 |
| 图片体验 | Hover Preview | `image-hover-preview.js/css`、两套 HTML | 静态资源 HTTP 200 |
| Kie | 受控 Provider、模型映射、创建/轮询、上传规范化 | `main.py`、`providers/kie/*`、`api-settings.js` | Kie 20/20 mock/静态测试 |
| Gemini | 原生 `generateContent`、URL 规范化、Gateway 动态模型目录与 Gemini-only 分类 | `main.py`、`api-settings.js` | 原生协议专项测试 |
| 生成参数 | ratio/resolution/custom size 正确传递 | 两套 Canvas JS | 2/2 参数测试 |
| Canvas Reference | 软引用、Picker、入口、物化 Edge、去重/限制/循环/Undo | 两套 Canvas JS/CSS/HTML | 33/33 专项测试 |
| Smart Canvas 布局与 Edge 交互 | Composer 内部滚动边界、拖动关联 Edge 局部更新、键盘删除选中 Edge | Smart JS/CSS/HTML | 对应专项测试与浏览器验收 |

## 4. 普通与智能画布的变更覆盖

### 同时改过两套画布

- LLM 拖拽冲突
- Marquee / 显式 Pan / 右键 Pan
- Edge 曲线相交选择
- Text/Prompt 扩大编辑
- Magnetic Handle 与 Anchor Center
- Hover Preview
- Generation Params
- Canvas Reference Picker 核心与入口

### 主要只改 Smart Canvas

- Prompt 大编辑器最初提交
- API 生成面板放大
- Quick Connect 菜单与临时连接线
- 最后一轮“画布参考”入口补漏

### 后端共享，两个画布都会受影响

- Kie Adapter 与模型路由
- Kie task polling
- Kie reference upload normalization
- 通用图片 Request Builder 与任务包装

## 5. 源码与运行数据边界

- 正式源码、测试、项目文档和 example/template 配置由 Git 跟踪。
- `data/api_providers.example.json` 与 `API/.env.example` 只提供无凭证模板；真实 Provider 配置与 Key 留在本机忽略文件中。
- 用户画布、项目、素材、Prompt、历史、对话、Preview、缓存、`.runtime/`、`.tools/` 和 `.launchd/` 实例都不进入 Git。
- `static/runninghub/api_providers.json` 只作为只读默认值；运行时保存写入 `data/`。
- HTML cache token 在响应内存中处理，应用启动不应再改写 tracked HTML。
- 本机 LaunchAgent 控制脚本保留在本地并使用根目录精确忽略规则；正式仓库中的其他 `.command` / `.sh` helper 继续跟踪。
- 清理或升级前先备份并校验用户数据；禁止用 `git clean`、`reset --hard` 或全量 `git add .` 处理混合工作区。

## 6. 历史验证记录

以下结果是对应功能交付时的验证记录，不代表任意未来 HEAD 的实时测试状态；升级后应重新运行相关测试。

| 检查 | 结果 | 解释 |
|---|---|---|
| Kie 专项 | 20/20 | 未调用付费服务 |
| Canvas Reference Picker | 33/33 | 普通与智能代码契约测试 |
| Canvas generation params | 2/2 | ratio/resolution 参数回归 |
| Smart drag targeted Edge | 专项测试通过 | 只更新拖动节点关联的 Edge |
| Smart Composer 纵向布局 | 专项测试通过 | 中间区收缩/内部滚动，底部工具栏保留 |
| Gemini 原生协议 | 专项测试通过 | `generateContent`、URL 与动态模型目录 |
| Smart Edge 键盘删除 | 专项测试与浏览器验收通过 | 输入状态受保护，复用正式连接删除逻辑 |
| Canvas log cleanup | 既存 14 项错误 | 与上述功能提交无关，仍需独立修复 |

## 7. 升级冲突地图

| 文件/区域 | 定制密度 | 官方升级冲突概率 | 原因 |
|---|---:|---:|---|
| `static/js/smart-canvas.js` | 极高 | 极高 | 交互、Composer、节点、Picker、MiniMax 集中，定制新增约数千行 |
| `static/js/canvas.js` | 极高 | 极高 | 节点/Edge/运行/Picker/参数均改动 |
| `main.py` | 高 | 极高 | 单体后端且 Kie/任务/Provider 修改集中 |
| 两套 Canvas CSS | 高 | 高 | Theme、Handle、Picker、面板、交互状态 |
| Canvas HTML | 中 | 高 | 脚本版本、资源引入、交互壳层 |
| `static/js/api-settings.js` | 中 | 高 | Kie 受控元数据与通用 Provider UI 相交 |
| `providers/kie/*` | 新模块 | 中 | 官方更新器不覆盖，反而容易与新 main.py 失配 |
| Tests | 新增/历史 | 中 | 官方更新器不覆盖，可能与被替换实现不一致 |
| `theme.css` | 高 | 高 | Neutral Dark 会被整个 static 替换 |
| 用户 `data/`、`assets/` | 非代码 | 极高影响 | 自动更新虽不直接列入覆盖范围，但错误迁移/人工清理会造成不可逆损失 |

## 8. 自动更新判定

**当前不能安全点击自动更新。**

更新器会完整替换 `main.py` 和整个 `static/`，正好命中所有高价值定制；它不是 Git merge，也不会检查 dirty worktree。它又不会同步 `providers/kie/` 和测试，容易形成一半新、一半旧的运行组合。即使存在自动备份，也不能把“可回滚”理解为“无风险升级”，因为升级与运行时迁移仍可能改动用户数据。

## 9. 建议的未来升级流程（本次未执行）

1. 停止付费任务，记录运行分支、HEAD、LaunchAgent 与端口。
2. 分别备份 `data/`、`assets/`、`workflows/`、`API/.env` 和所有未跟踪运行脚本；校验 SHA-256。
3. 在独立 `codex/` 升级分支 fetch 远端，不在运行分支直接操作。
4. 用 `git patch-id`/三方 diff 判断历史定制提交哪些已被远端吸收。
5. 按定制域逐组 rebase/cherry-pick：先共享后端与 Kie，再主题/交互，再 Picker。
6. 手工解决 `main.py`、两套 Canvas JS、API Settings 和 CSS 冲突。
7. 补回/修正 14 项日志清理回归，使全套测试全绿。
8. 运行语法、单测、HTTP、WebSocket、普通/智能浏览器 UI 验收；付费 smoke test 另行授权后只做最小一次。
9. 对比用户数据备份 SHA-256，再切换 LaunchAgent 到新分支。

## 10. 不应丢失的验收清单

- 普通与智能画布都能看到并使用 `输入图 | 资产库 | 画布参考`。
- Picker 选择时不建 Edge，首次运行才物化；顺序、上限、循环、去重、Undo/Redo 不变。
- GPT Image 2/Nano Banana Pro 的 UI 名与内部 Kie routing ID 不混淆。
- Kie polling 只走 `/api/v1/jobs/recordInfo`。
- Kie 本地参考图必须规范化并上传成匿名 HTTPS URL。
- 自建 OpenAI-compatible 仍能区分 generations/edits，且不改 Base URL、模型 ID 或 Key。
- 普通/智能画布都保留 Marquee、右键/中键/Space Pan、Quick Connect、磁性 Handle、Anchor Center、扩大编辑器。
- Neutral Dark 与 Hover Preview 保留。
- Canvas 生成分辨率/比例参数不回退。
- Gemini Provider 保持原生 `generateContent`、不重复拼接版本路径，并只收录 `gemini-*` 模型。
- Smart Composer 的中间区域可收缩并内部滚动，底部模型参数与生成按钮不被推出主容器。
- Smart Canvas 拖动节点只更新相关 Edge；选中 Edge 可用 Delete / Backspace 删除，输入控件内按键不会误删连线。
- 用户画布、Workflow、素材、Prompt、Provider 配置、历史和对话不被升级覆盖。
