# Kie HTTP 200 response reader 本地诊断

## 结论与证据边界

确认并最小修复了 assistant 安全层的重复解压 Bug：GuardedClient 的 aiter_bytes() 已经解码 Content-Encoding；原实现随后保留 gzip 等原始响应头，用已解码 body 构建 httpx.Response(content=...)。httpx 0.28.1 构造函数会立即 read()，于是对普通 JSON 再解压，产生 httpx.DecodingError，上传适配器将其包装为 KieReferenceError。

本地真实 TCP HTTP/1.1 gzip fixture 在修复前同时使 reader 测试和上传适配器测试失败；修复后均通过。不是在退出 stream context 后才读 body：原实现的消费和重建都在 async with 内。

git blame 确认原重建行来自 b017be03（Add controlled assistant model access），不是 249ffd3 的 Provider 自配置新增。该 Bug 属于 assistant 安全层引入；尚不能将它等同于上次真实 Kie 请求的根因。

上次请求只保留 HTTP 200、application/json、无 redirect 等摘要，未保留 Content-Encoding、Transfer-Encoding、Content-Length、HTTP version 或底层异常类别。无法追溯补回。先前“第14步 body 读取失败”的表述应收窄为“第14步响应处理期间失败”，因为当时的观测点也包含 response 重建。新诊断区分 body_read 与 response_rebuild。真实历史底层类别仍未知，不冒充已经确认是 DecodingError。

异常链实际上通过 raise ... from exc 保留在内存，旧诊断/账本未记录它；本次不恢复原始 message 或 traceback，只按异常类身份白名单输出 outer/cause/context 和 httpx/httpcore family。伪造的类名/模块名归为 OtherException。

## owner 与 assistant 实际代码差异

owner 路径只读审阅当前正式源码，未读取其 Provider、凭证、图片或环境配置。

| 比较项 | owner 当前正式版 | assistant 安全实例 |
|---|---|---|
| HTTP 库 | httpx.AsyncClient | GuardedClient 包装 httpx.AsyncClient |
| sync/async | async | async |
| 生命周期 | prepare_kie_references 内创建，finally aclose | ControlledModels.run 创建，finally aclose |
| stream | 普通 await client.post，httpx 默认缓冲 | async with client.stream；显式遍历 aiter_bytes |
| 读取顺序 | post 完成读取/解码，随后 response.json | 读取/解码、大小检查、重建 httpx.Response，随后上传函数 json |
| context 提前关闭 | 未发现 | 未发现；故障点原本位于 stream scope 内 |
| timeout | connect/pool 20 秒，read/write 120 秒 | 任务路径全部 120 秒；上次独立诊断脚本 45 秒、总计60秒 |
| follow_redirects | True | False，3xx 拒绝；本轮未改 |
| HTTP/2 | 未启用，httpx 默认 False | 未启用，默认 False |
| Accept-Encoding | 当前共享依赖默认 gzip, deflate | 同样 gzip, deflate；未手动覆盖 |
| proxy/trust_env | 未指定，默认 trust_env=True | trust_env=False；未读取 owner 的代理环境 |
| 上传 JSON body 上限 | 普通 post 无显式 body cap（参考图大小限制另算） | 解码后最大 32 MiB，流式累积检查 |
| buffer | httpx 自身缓冲 | bytearray 增长缓冲，有上限；不是固定长度 buffer |
| 包装层 | 使用原 Response | SSRF/DNS/socket 守卫加有界流式读取，随后返回缓冲 Response |

旧重建：httpx.Response(..., headers=response.headers, content=bytes(data), ...)。修复仅从缓冲响应头移除已经消费的 content-encoding / transfer-encoding 和旧 content-length，由 httpx 生成正确解码后长度；保留真实 HTTP version。JSON schema、CDN URL parser、asset id parser、createTask payload 未改。

## 大小、协议和依赖

上限继续为 33,554,432 bytes（32 MiB），按解码后 body 计数。未知 Content-Length 不被提前拒绝；chunked 由 httpx/httpcore 正常处理；正常 EOF 无长度响应可读取。中断 Content-Length 响应会失败。read timeout 是每次读取的等待上限，不是整段下载总时长，未缩短或禁用。

当前本地依赖：httpx 0.28.1，httpcore 1.0.9；brotli、brotlicffi、zstandard 均未安装，默认请求不宣告 br/zstd。gzip 支持来自 Python zlib，fixture 证明首次解压正常。没有升级依赖。无法推断未记录的真实 Kie 压缩头或 HTTP version，也没有测试 HTTP/2 服务。

新增安全元数据：异常类型链、httpx/httpcore 家族、HTTP version、白名单 content-type / encoding / transfer-encoding、Content-Length 存在与合理整数值、streaming、headers_received、body_read_started、解码后 body_bytes_read、elapsed_ms。日志不会接收原始 message、URL、Query、Authorization、Cookie、body 或 payload。超限时不再向缓冲区追加超限 chunk；诊断计数包括已得到但被拒绝的 chunk。

## 完全本地 fixture

测试使用 asyncio TCP HTTP server，仅绑定 127.0.0.1 的临时端口，通过现有 mock 模式的精确 loopback allowlist，调用实际 GuardedClient 和共享上传适配器。没有另写 reader，也没有访问 Kie。

| fixture | 修复前 / 原因 | 修复后 |
|---|---|---|
| Content-Length JSON | 通过 | 通过并正常 json() |
| chunked JSON | 通过 | 通过，去除已消费的传输头 |
| gzip JSON | DecodingError，响应重建时二次解压 | 通过 |
| 无 Content-Length + 正常 EOF | 通过 | 通过 |
| HTTP200 后 body 中断 | httpx.RemoteProtocolError，由 httpcore.RemoteProtocolError 引发，上传包装为 KieReferenceError | 继续拒绝且记录安全类别 |
| 小 JSON | 通过 | 通过 |
| body read timeout | httpx.ReadTimeout，由 httpcore.ReadTimeout 引发 | 继续拒绝 |
| 解码后超过32MiB | HTTPException 安全拒绝 | 继续拒绝 |
| 损坏 gzip | httpx.DecodingError | 继续拒绝，不误放行 |

12 个专项测试还验证：关闭响应后 json 仍可用、长度与缓冲内容一致、合法 gzip 经上传适配器取得 URL 字段、错误链只记录安全类型、日志不含 URL/Key/payload/签名、响应元数据存在。

## 执行边界

本轮真实 upload 0；createTask 0；GPT Image 2/其他模型 0；无费用、无自动重试。累计真实上传仍为上轮授权的1次。没有修改或重启 owner，没有修改 Clash、依赖、安全策略或 VERSION。一次性上传防重标记保留，未执行 upload_once_probe.py。

验证结果：./scripts/check.sh 全部通过；469/469 tests，88.339 秒。Python 编译、JavaScript 语法和 git diff 检查通过。相对457基线新增12个专项测试。

提交范围：instance_model_policy.py、instance_model_tasks.py、providers/kie/uploads.py、instance_reference_diagnostics.py、tests/test_instance_reference_diagnostics.py、tests/test_instance_response_reader.py 和本报告。包含前轮尚未提交的安全诊断及其4个测试；本轮新增12个。一次性上传脚本、防重标记、旧安全摘要与旧调查记录保留在工作区，不纳入修复提交。
