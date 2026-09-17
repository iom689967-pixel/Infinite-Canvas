# 稳定性候选契约与部署边界

本轮只开发与 mock 验证。完整个人 API/维护基线 f598c15 保留；没有新协议、没有 owner 执行回退。运行 owner 只读快照 4de408c；真实 Provider、素材和密钥没有复制进测试。

| 根因 | 修复边界 | 主要可重复验证 |
|---|---|---|
| D02 | 固定分类与服务端事件编号；Canvas/provider HTTP 状态分别记录，无法确认的 upstream 状态=null；任意异常 code/body 不进入安全日志 | test_model_diagnostics、test_stability_execution；浏览器 routing503→Canvas502 |
| D03 | llm_contracts 无身份副作用；owned Provider、凭证与 GuardedClient 仍由 Instance 注入 | 独立 URL/messages/参数 golden；same-input replay；test_personal_network_adapters |
| D04 | 空选择才初始化默认；已有 Provider/模型精确身份、加载失败保留；重绘和执行同样校验 | test_stability_selection；真实两种 Canvas 保存/刷新/重登录、跨标签 |
| D05 | JSON 结构/业务错误/拒绝/空文本分开；SSE 可靠终止才 done/verified；显式关闭连接/生产任务 | test_llm_contracts、真实 ASGI、Gateway→Instance→mock TCP、浏览器流停止 |
| D06 | 文本连接10秒、首响应/读空闲300秒、总1800秒；清理5秒；Gateway总预算额外30秒；普通读取/探测另设预算 | test_stability_tcp_lifecycle 缩短同一预算常量，覆盖慢首token、首token超时、读空闲、总超时、两次断开后槽位可用 |
| D07 | 2.5输入模板；已确认Kie模型的20k前置门槛；Focus100k；按Provider/model缓存并发Promise | test_stability_kie、严格Kie回放；未知模型/其他协议不套用20k |
| D08 | nonce、owner、Provider revision、原结果恢复、CDN无Key、History去重保持 | 原有隔离/幂等/恢复测试全部保留；严格网络mock与浏览器原任务恢复 |

## 明确保留的差异

- 原图策略：公网向 LLM 使用已有原字节策略（MIME、透明度、尺寸/内容指纹保留），没有偷偷缩图或转 JPEG；32 MiB 请求/响应保护、32M 像素解码保护等原资源边界保留。
- 模型 ID 精确传递；既有 Kie UI 预设映射到已确认 Market 路由。手动自定义精确 ID 借用模板时不改名，也不继承未知的模型上限。
- 图片/视频的后台 ledger、用户归属、节点绑定和结果下载恢复继续独立于纯协议函数；下载失败不能返回 owner 式“成功但未落盘”。
- 不直接复制 owner `_canvas_llm_impl`；不开放 Shell、CLI、服务器文件、全局凭证。SSRF/DNS/连接、Session/Origin/CSRF、并发/维护保护不降低。
- 文本 response 保留文字空白和数字 usage 白名单；默认不新增 temperature/token 字段，显式参数保留。RunningHub 只有已确认官方来源可使用固定官方文本目标，任意自定义中转不获得跨域送 Key 权限。
- 用户取消的是本地等待。流 EOF、网络错误、超时不会等同远端已结束；本地槽位和活动可释放，同时保留远端不确定记录。
- 维护拒绝继续使用 code=maintenance、原固定中文说明、503及既有门禁行为；仅附加安全诊断元数据与事件编号。旧断言改为保留原code/message检查并另验事件格式，没有放宽接单或排空规则。

## 9条原失败断言

3条public（HTTP200业务错误、空文本、无终止SSE）必须修复；6条owner（业务错误、空文本、JSON数组、SSE业务错误、无终止SSE、下载失败仍成功）只记录。`before-assertions.json`及原快照不修改。独立32项回放断言的目标是public16/16，owner10/16且精确保留这6条已知缺陷，不能把owner差异算作候选通过。

## 严格验证入口

```sh
./scripts/check.sh
.venv/bin/python -m pip check
.venv/bin/python tools/stability/replay.py --owner-archive /path/to/immutable-owner.tar --output /new/replay-directory
.venv/bin/python tools/stability/verify_replay.py /new/replay-directory
NODE_PATH=/path/to/existing/playwright/node_modules MIO_BROWSER_OUTPUT=/new/browser-evidence node tests/manual_stability_browser.cjs
```

浏览器是临时A/B账号、真实Instance和真实产品函数；上游只允许loopback mock。测试密码和Key是临时生成/固定假值。浏览器错误、未匹配mock请求、History重复、恢复重提均使验收失败。原有网络测试不再有“未知POST返回成功图片”的默认分支；方法/path/解析契约按fixture清单拒绝未知请求，关键body由独立断言校验。

CI仍执行全部原回归、新增用例、pip check和真实5449旧代码首次维护演练。没有跳过失败项。浏览器证据为本地Chromium临时环境，不能代替真实上游生成验收。

## 后续部署需要单独授权

1. 重新核实生产 release、beta ancestry、候选CI、运行实例/PID、容量、安全阀和任务状态；本报告的历史数字不是部署时现场事实。
2. 按 `docs/public-beta-maintenance.md` 进入维护、备份并验证排空。已有未核对远端记录不清理、不伪造reconcile、不因本候选通过测试就判定可重启；需独立核对或另获明确风险授权。
3. 独立release和匹配venv，pip check；只有授权后才快进beta。保留旧release/venv；不改owner/main/stable/VERSION、生产容量或存储阈值。
4. Gateway需重新加载预算/诊断；Supervisor切换新release以保证未来实例使用新程序。先接管旧实例，再通过既有管理接口逐个正常停启Instance，使 Python执行器/能力/错误/流处理实际加载；首个验收通过后才继续。不要求PID不变，不强杀、不自动重提旧任务。
5. 数据根不变；不回灌旧数据库。新老账号目录/节点/History/媒体/凭证引用、读取/登录/WebSocket与维护拒绝验收后才开放。浏览器刷新加载新资源，必要时正常重新登录。
6. 本候选只新增可兼容任务诊断字段和可选conversation不完整标记，无迁移/破坏性schema变更。程序回滚保留数据库与ledger；旧版不能识别新诊断元数据，回滚前重新审查活动/未知记录，保持外部门禁直到确认安全。不要承诺无损或零费用。

D01仍是外部授权问题：新诊断不能补回旧关联证据。未刷新OAuth、未修改Sub2API，不声明当前账号已恢复，也不声明已把所有历史浏览器失败逐一关联。
