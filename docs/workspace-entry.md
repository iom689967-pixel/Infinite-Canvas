# 登录后直接进入工作区

## 流程

原登录、注册表单跳转到 `/workspace`，该路由总是返回独立启动卡片；原 `/` 在 Instance 未运行或缺少 Instance Cookie 时也返回同一卡片。卡片完成 `/api/beta/enter` 后再次导航到 `/`。

现在表单直接进入 `/`，Gateway 在验证 Gateway Session 后返回原 `static/index.html`。`/workspace` 仅作为同一工作台的兼容入口，不再返回启动页。首次默认栏目仍为 Canvas，后续栏目选择继续使用现有用户命名空间。

Canvas iframe 提前显示原 `static/canvas-list.html` 的项目栏、工具栏和画布背景。Gateway 只提供程序 HTML 和白名单程序资源，不读取用户 Canvas、Provider 或媒体。HTML 保持 `no-store, private`；程序资源继续沿用内容版本和原缓存策略。

## 准备与认证

`workspace-startup.js` 在共享 Session 模块之前启动一次交接。先取得 Gateway CSRF，再请求 `/api/beta/enter`。Supervisor 是启动与容量的唯一 authority；既有 health、进程身份和单实例锁继续有效。健康 Instance 立即复用既有有效 Instance Session，或执行原一次性 handoff，不插入人为等待。

`instance-session.js` 让同一用户 iframe 继承父页面 ready。准备期间静态资源可立即读取，私人 API 等待实际 Instance Session；WebSocket 和后台栏目预热也等待 ready。准备完成后原 iframe 原地读取当前用户数据，不重设 src、不重新导航工作台。不同命名空间不能共享 ready。

支持 Web Locks 的浏览器按用户命名空间串行完成跨标签交接，避免多个 Cookie/CSRF 同时替换。没有 Web Locks 时，Supervisor 的服务端锁仍保证同一用户只有一个 Instance。

## 等待、失败与退出

- 300ms 内完成不显示 loading；超过后仅在 Canvas 区域显示小 spinner。准备期间不展示“暂无画布”等尚未证实的账户状态。
- 每次尝试最多一个 enter POST；不自动重发启动请求。Gateway 的只读 Session 查询最多三次，间隔 400/800ms。
- 一次尝试最长 60 秒（包括等待跨标签锁）。失败后保留工作台和 Gateway Session，在 Canvas 区域显示“工作区启动失败，请重试”。只有用户点击重试才发起新尝试。
- 401 或命名空间不匹配仍使登录失效。准备期间退出使用带 Gateway CSRF/Origin 校验的 `/api/beta/logout`；完成后沿用 Instance logout。多个 iframe 只通过父 Session 触发一次顶层退出导航。
- 未修改 Session 权限、Secure Cookie、SSRF、用户数据隔离、模型调用路径或容量限制。

## 冷启动端口检查

真实临时实例验收发现，刚停止的进程可能留下 TIME_WAIT 连接。原端口探针按默认 socket 语义绑定，可能把没有监听者的端口误判为被占用。现在探针使用与已安装 Uvicorn `Config.bind_socket` 相同的 `SO_REUSEADDR`；实际监听者仍被拒绝，进程身份、HMAC health 和 ownership 检查不变。专项测试同时覆盖停止后的端口复用与未知监听者拒绝。

参考：[Python socket 文档](https://docs.python.org/3.11/library/socket.html) 对 `SO_REUSEADDR` 和 TIME_WAIT 的说明。

## 验收方式与部署边界

完整回归使用 `./scripts/check.sh`。`tests/test_workspace_startup.py` 覆盖真实隔离 worker、认证和端口边界；Node 测试执行实际交接、Session、iframe 和 WebSocket 模块。

安装 Playwright/Chromium，并在项目 `.venv` 中准备依赖后，运行：

```sh
node tests/manual_workspace_startup.cjs
```

Playwright 在外部 Node 运行时中时，通过 `NODE_PATH` 指向其 `node_modules`。`MIO_BROWSER_OUTPUT` 可指定截图和安全摘要目录。脚本仅创建临时根目录和合成 A/B 账号，启动独立本地 Gateway/Supervisor；结束时回收这些测试实例。它不访问真实 Provider，不调用模型。测试端口范围为 45000–45031；自动回归使用不同范围，不能指向生产数据根。

浏览器场景包含 warm 登录、cold 登录（人为延迟 enter 请求 1200ms 以观察外壳）、原有 Canvas 自动出现、多标签、失败原地重试、临时 Gateway 重启后原 Instance PID/Session 保持和 WebSocket reconnect、A/B 隔离及 logout 401。耗时为本地单次观测，不代表公网 p50/p95；冷启动结果必须注明人工延迟。

本次只完成开发分支、CI 和临时实例验收，不修改生产 beta。后续生产部署需单独安排 Gateway 与 Supervisor 新代码的生效方式，不能在本轮借测试重启生产 Supervisor 或用户 Instance。
