# Public Beta 工作台首次加载

## 行为

工作台仍只使用 `static/index.html` 和原来的十个 iframe。当前栏目立即加载；其 load 完成后，后台每次等待 300ms，再通过 requestIdleCallback 启动一个栏目。没有该 API 时沿用 300ms setTimeout。后台最多一个导航在途，前台点击可立即插队；页面隐藏时暂停调度，恢复可见后继续。不销毁 iframe，不重复写 src。

顺序：canvas、asset-manager、api-settings、online、gpt-chat；之后 zimage、enhance、klein、angle、comfyui-settings。无个人 Provider 管理权限时跳过 api-settings。正在等待的前台栏目超过 150ms 显示“正在打开…”。已加载栏目立即切换，不做旧版 500ms 缩放/模糊动画；侧栏首次入场 120ms。

预热模块不调用 API。加载的页面只进行原有的只读初始化，不提交生成、上传、Provider 探测或保存。API 设置仍需用户明确点击保存/拉取模型。页面内既有只读定时器继续使用；认证定时检查由顶层执行，隐藏时暂停。

## 身份初始化

GET/HEAD 立即发出，服务器逐请求验证 Session；写请求等待 ready 后附加 CSRF。相同源且相同 storage_namespace 的子 frame 复用父页面 ready，避免十次认证 bootstrap 和重复心跳。独立页面仍单独初始化。401 和响应 namespace mismatch 仍退出，logout 的广播和本地命名空间停用逻辑保留。

## 缓存边界

`workspace_assets.py` 仅从已审查的程序资源清单选取 JS/CSS/font/icon/image/vendor，不按 `/static/*` 整目录放行。额外两项个人 API 设置脚本仍沿用已有服务端权限检查。

资源包指纹包含缓存转换器源码以及清单中每项文件的路径、内容 SHA-256；不依赖 VERSION 或文件 mtime。HTML 的脚本/样式/图片引用、CSS 内字体引用以及 i18n 的动态加载器使用该指纹。转换只在响应内进行，不改写磁盘文件。部署需同时重启 Gateway/其管理的 worker，让静态内容和指纹对应同一个快照。

| 响应 | Cache-Control |
| --- | --- |
| 清单内资源，v 等于当前内容指纹，GET/HEAD 200/304，无 Set-Cookie | public, max-age=31536000, immutable |
| 清单内资源，无版本或旧版本 | public, max-age=300, must-revalidate |
| 所有身份化 HTML、API、媒体、401/403/失败响应 | no-store, private |

程序资源带表示内容 ETag，Gateway 传递 If-None-Match/If-Modified-Since 及 ETag。200/HEAD/304 均测试。公共代码响应不附带用户 namespace/会话 Cookie；新的未认证网络请求仍须通过原认证守卫。浏览器已有的公共程序缓存可跨 A/B、跨登录会话复用，私人 HTML 和数据始终不进入公共缓存。

Caddy 的 Mio 站点增加 `encode zstd gzip`；不对其他站点或私人响应添加 public cache。部署时先备份配置并 caddy validate，再 reload Caddy。

## 2026-09-13 本地真实浏览器验收

环境：Codex 内置 Chromium，两个独立临时 Beta 用户；独立站点端口建立空 HTTP cache，资源保留在同一浏览器中测重登。没有清除用户其他网站缓存。基线 b7168f7。未启用网络/CPU 限速、未压缩的 loopback。以下不是公网 RTT 下的承诺。Resource Timing 字节包含协议估算开销；表内首屏为工作台 Document 导航至 load，不包含密码校验与实例启动。

| 指标 | 原版空缓存 | 优化后空缓存 | 同浏览器再次登录 |
| --- | ---: | ---: | ---: |
| 工作台导航 load | 354 ms | 294 ms | 126 ms |
| 默认 Canvas frame load | 168 ms | 105 ms | 54 ms |
| 全部栏目公共资源传输 | 17,359,057 B | 4,334,291 B | 0 B |
| 资源缓存命中（Resource Timing） | 0 | 后续 frame 复用公共包 | 全部公共资源 |
| 各 iframe 单独 auth/me bootstrap | 各自执行 | 0（共用顶层） | 0（共用顶层） |

全栏目合计传输减少 75.0%。空缓存下用户仍需首次下载共享程序，但后续所有栏目自动预热，无需人工依次点开。B 登录的完整预热在工作台导航后约 3.57s 结束。顶层首次 auth/me 13.8ms，重登 11.6ms；后续顶层心跳仍存在，不是所有认证请求都消失。

| 栏目 | 原版首次点击到 frame load + 两次绘制 | 优化后预热点击 | 第二轮切回 |
| --- | ---: | ---: | ---: |
| 素材库 | 177ms | 27.0ms | 31.0ms |
| API 设置 | 229ms | 28.9ms | 29.3ms |
| 在线生图 | 231ms | 31.9ms | 31.9ms |
| GPT 对话 | 198ms | 31.1ms | 31.2ms |
| 文生图 | 197ms | 32.0ms | 31.9ms |
| 细节增强 | 230ms | 31.8ms | 31.6ms |
| 图片编辑 | 214ms | 32.1ms | 32.4ms |
| 角度控制 | 286ms | 29.8ms | 32.2ms |
| 无限画布 | 默认页 | 31.5ms | 31.6ms |
| 工作流设置 | 217ms | 自动预热成功 | 无重建（调度回归覆盖） |

预热点击 p50 31.5ms / p95 32.1ms；第二轮 p50 31.6ms / p95 32.4ms（9 个栏目，nearest-rank）。使用两次 requestAnimationFrame 作为可见时间的保守代理。旧版还有独立的 500ms CSS 过渡，本表未额外加总该动画。第二轮 10 个 iframe 全部仍是同一个 Document，每个 navigation entry 数量为 1；服务器无新的 iframe HTML 请求。两个登录周期 + B 登录总计正好 30 个栏目 HTML 导航。

CDP 在 B 登录观察到 127 个公共请求的 memory cache 事件、0 个 disk cache 标记；Resource Timing 证实重登全部公共资源 transferSize=0。未重启整个用户浏览器，故不声称独立验证了浏览器进程退出后的磁盘缓存复用。

A 在正式 UI 创建项目，退出重登仍可见；B 登录仅有默认项目，看不到 A 项目，A 的侧栏和项目选择本地状态未传给 B。新的无 Cookie 请求：auth/me、Canvas、Projects、History、Conversations、Provider、reference/output media 均 401 + private/no-store。有效私人媒体和 Provider/Key 的 A/B 直接请求在全量测试中覆盖。

预热静态请求错误数 0。临时浏览器唯一业务写入是上述手动创建的测试项目；其余写请求只有登录、SSO 交接和退出。真实生成/上传/Provider 调用均为 0。全量 check.sh：565/565（此前实际基线 554，新增 11 项；含节点调度/认证 JS 回归）。
