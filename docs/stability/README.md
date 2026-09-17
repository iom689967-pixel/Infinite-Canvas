# Public Beta 稳定性候选

开发基线 f598c15fb46d81968d3f80c2a66e646f2d2ab685；只读 owner 对照4de408cdb4a1c7ca0e6002683f54e9ae837c8a43。不部署、不更新owner或生产、不调用真实模型/上传/邮件。

原审计目录、两份Git archive和before结果不修改。audit-manifest.json固定原材料hash；tests/fixtures/stability-before保存合成输入的原始结果。生产账号/凭证/真实素材不进入仓库。

原9条失败：3条public（业务error200、空文本、无终止SSE）修复；6条owner（前两条加JSON数组、SSE业务error/无终止、下载失败仍成功）仅登记，不修改运行owner。没有一条通过“改成与owner一样”修正。公网下载失败待恢复、nonce幂等、按用户隔离等必要差异另立不可退化测试。

feature-matrix.csv只纠正旧D编号引用：D06/D10→D03/D07，Kie D06/D08→D07/D08，运行维护D05/D09→D05/D06；结论及未验证范围不变。

离线差分可重复运行：

```sh
python tools/stability/replay.py --owner-archive /path/to/original-owner.tar --output /new/isolated/replay-directory
```

必须使用新的输出目录；不导入真实owner，不修改原archive。httpx外部传输换为fixture，socket真实connect拒绝；核心业务函数不替换。旧差分保留观察性mock的历史行为；新增严格契约测试必须对未知URL/method/body失败，不能依赖旧宽松fixture证明修复。

本文件随各批记录before/after、测试与未覆盖内容，最终候选CI通过后停止，另等部署授权。

## 第一批 D04 / D02

失效ID不再替换；新节点的默认选择只来自空选择。两种Canvas在素材处理/提交前执行同一能力检查，目录加载失败保留原选择；Smart不再按自定义Provider ID `modelscope`注入全局路径。已删除Provider/模型仍显示原ID与原因。

公共错误附加服务端事件ID、分类、Canvas/provider状态；未知upstream状态为null，不从任意错误文本推定路由失败。Instance错误出口仅放行当前服务端请求上下文实际签发的安全detail，不能借伪造事件ID透出原始响应。诊断日志仅安全固定字段；上游关联未确认时明确标记not_confirmed。

旧断言唯一语义变化：`comfly`作为已有精确ID也需保留；以前将它当默认哨兵，现只有空ID才选默认。旧cleanup/batch测试补齐真实选择校验依赖，未移除用例。第一批单元/selector/旧前端20项与真实Instance严格HTTP2项通过（新增事件签发隔离测试另纳最终回归）。完整TCP流取消与浏览器生命周期在后批验收。

## 第二批 D05 / D06

JSON业务错误/空结果/错误结构分别拒绝，不再把200等同成功；SSE EOF不等同完成，只有明确终止契约才done/verified。取消/异常时保存用户输入与标记incomplete的已显示部分，不把未完成内容写成完整assistant。HTTP生产/ContextVar只由同一个producer任务管理，响应关闭显式cancel并join，client close幂等；本地槽位释放不会清除remote uncertainty。

真实ASGI断开：llm_active before=1 / after=0，未出现跨context退出错误。真实隔离Gateway→Instance→mock TCP：连续两次流断开后活动归零，两个未知记录仍在，后续两个并发文本请求正常；慢首响应、idle/total timeout和无终止EOF均验收。测试只将时间预算缩小（.25秒读空闲/.65秒总时长）以重现同一逻辑，不改业务执行结果、不增加测试HTTP接口。正式TEXT为连接10秒、首响应/读空闲300秒、总1800秒；Gateway总预算留清理余量，普通读取180秒、探测60秒，不无限等待。

17项ASGI/JSON/错误/维护回归通过；TCP独立3项加入最终完整回归。历史owner缺陷仅保留before结果。本地owner进程/源码未修改。

## 第三批 D03

新增无身份副作用的 URL/messages/参数构造与响应解析；Instance 继续注入 owned Provider、私有凭证和 GuardedClient。bare base 补版本，已有版本不重复；模型精确透传，system/history/current 顺序固定，Gemini 视频不重复追加。公网参考图保留原字节策略，没有模仿 owner 的缩图转码。显式 token/temperature 保留。RunningHub 官方文本跨域只有已列官方来源和固定 HTTPS 文本目标，自定义中转保持原域。

独立 golden 断言覆盖 URL、消息、MIME、图片顺序、参数和凭证目标；真实 Instance 严格 TCP mock 覆盖原 PNG、显式参数与自定义 modelscope Provider ID。46 项网络执行器回归通过；此前 27 项文本/取消/超时测试通过。未调用真实模型。

## 第四批 D07（及端到端复核补齐 D02/D04）

Kie 2.5 Flare/Sunburst 使用各自 text-to-image/image-to-image 路由、input_urls 与 background 模板。已确认的 20k 只绑定 Kie GPT Image 2 和 Nano Banana Pro 的实际路由；2.5/未知模型没有编造上限，借用模板的自定义精确模型 ID 不继承模板名限制。同名 OpenAI-compatible 模型也不套 Kie 限制。按公网最终原文（含空白、Unicode code point）前置检查，参考上传/提交均在其后；通用编辑与保存 100k 不降级。非 public-beta 通用图片/视频 schema 默认由20k/4k升到100k；public-beta已有请求体安全上限保留。

Focus 输入100k；并发能力请求共享 pending Promise，刷新使旧结果失效，缓存键保留 Provider ID 大小写。Kie 参数仅初始化空值；明确选择的比例/分辨率不再静默替换，提交前解释冲突。历史的通用 outputFormat=png 在没有格式参数的 Kie 模板中不下发，也不改写保存设置。

实际浏览器复核发现普通 LLM 重绘、Smart 图片/视频参数重绘仍有旧 fallback，已补齐：现有模型与 Provider 原 ID 保留，缺失项有原因且允许主动重选。自定义 volcengine/modelscope ID 不再触发非个人版本的特殊重写。

任务执行错误安全分类贯通到原任务查询/恢复；CDN失败归本地保存，不冒充 Provider 原始状态。保留真实可知状态、未知upstream=null；事件编号随同一任务失败保留，查询仍校验ownership。聊天流 UI 必须见 done 才完成，取消/EOF保留输入和标识部分输出；在线生图/聊天页不再把结构化错误显示为对象字符串。

旧断言更新清单：Kie预设目录由2→4（两个设置发现测试及三个Kie测试），依据已审计2.5模板；LLM503笼统code=upstream→upstream_unavailable且provider_status=503，依据D02；前端提取函数测试加载新增的真实校验依赖。无测试删除/跳过。
