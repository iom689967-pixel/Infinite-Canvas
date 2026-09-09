# 完整 API Settings 与实例私有存储统一

基线：c15ef124ad504474cea8f5d521eedfe59f58a28a。开发分支：codex/unified-api-settings。

## 收敛方式

`static/api-settings.html`、`static/js/api-settings.js`、现有 CSS、i18n、模型选择器是唯一编辑器。实例认证中间件不再替换成简化脚本。`instance-api-settings.js` 仅保留无行为的兼容资源。

页面继续使用 `instance-session.js` 初始化 Session、给写请求附加 CSRF，并通过当前身份的 Provider Settings API 读写数据。owner 单机入口保留原存储；实例入口仅操作 `INSTANCE_DATA_ROOT/.auth/model-access.json` 的 `personal_providers` 和同根私有凭证文件，不读取 owner 配置或环境变量凭证。

完整输入模型抽到 `provider_schema.ApiProviderPayload`。实例存储适配器复用 `main.normalize_provider`，不另造精简 schema。旧个人 API 的写入和目录发现也进入相同适配器，旧客户端修改名称不会删除已保存的高级配置或遗留辅助凭证文件。

## 原简化编辑器缺失的维度

原有 id/name/base_url/protocol/enabled、单一 `models[{id,purpose}]` 和 Key 操作；缺少独立图片请求模式、编辑路由、端点覆盖、image/chat/video 数组、模型别名/协议覆盖、默认平台、LoRA/RH 配置及 Ark 项目/区域和辅助凭证。

现在按正式 schema 保存这些字段，保留原始分类顺序；运行时的模型权限表由服务端编译。`protocol` 与 `image_request_mode` 独立保存。Kie 维持正式适配器的 `protocol=kie`、`image_request_mode=openai`，不是 OpenAI 文本协议。此前 JS 已引用但 HTML/schema 缺失的编辑路由已接通；启用/默认和端点输入也接到同一表单，保存不再清空端点。

## 凭证和探测

- API Key、RH 辅助 Key、Ark AK/SK 仅作为替换/明确清除命令。凭证文件为私有 0600；公共 GET 仅返回 has_* 布尔状态，不返回凭证引用、Key preview 或环境变量名。
- 认证目标、协议、图片路由/模式等变化时，已存凭证须重新输入或明确清除；整个集合先验证后提交，失败撤销新建凭证。
- `provider_probes.probe_settings` 为两种存储入口共用的只读探测器，复用既有模型 URL、分类和 Gemini 网关目录补充逻辑。所有网络探测仅 GET；不以空生成 POST 验证兼容性。
- 无模型目录/无确定协议证据时返回未确认，保留手动协议，允许手工模型管理。异步协议的证据来自不存在任务的 GET 查询；普通 404 不算支持异步协议。
- Kie 模型目录/协议检查为静态白名单，不能证明假 Key 有效。地址验证只访问公共根地址，不发送 Key。
- 实例探测走现有 GuardedClient、DNS/连接与 redirect 校验、私网保护和响应上限；单次 HTTP timeout 15 秒，整体 30 秒。未修改安全 transport 或 Kie gzip 修复。

## 保留的运行权限边界

配置能力统一不增加生成适配器权限。当前隔离运行层仍只开放原有 OpenAI/Gemini 文本与 Kie 图片；其他图片/视频协议可以完整保存和恢复，但 UI 明确显示当前实例尚未开放运行。未将这些模型假标为 runnable。CLI/Shell 选项在普通实例隐藏，服务端继续拒绝。

## 验收记录

全部采用临时用户、假 Key、本地 mock；真实 Kie upload/createTask/生成均为 0。

- A：正式完整首页 → API Settings → 新增 Kie mock → 选择协议/输入假 Key → 保存 → 获取两个模型 → 分类选择 → 保存 → 刷新。ID、协议稳定，Key 显示已配置；gpt-image-2/nano-banana-pro 均可运行。
- A：新建 Smart Canvas，平台菜单选择个人 Kie，模型菜单可选择上述两个模型；没有点击运行。
- A：OpenAI mock 地址验证、协议识别、模型拉取成功，gpt-image-2 / gpt-chat / veo-video 分别进入 image/chat/video。图片/视频的未开放运行状态准确显示。
- A：临时服务进程重启并重新登录，Provider、分类模型和 Smart Canvas 仍存在。
- B：同一正式设置页面，个人 Provider 列表为空，看不到 A 的 Provider/凭证；A 的 Canvas 不出现在 B 项目中。
- A logout 后用无 Cookie 的新 HTTP 请求：个人 Provider Settings、Provider、模型目录、Canvas API 均 401。
- 精确 `https://api.kie.ai` + 假 Key 在独立 live-policy 测试中完成保存、静态目录检查、重启恢复及 runnable 断言，无外部请求。浏览器配置连接地址为受控 loopback mock。
- 浏览器使用独立 Chrome 会话；内嵌浏览器旧控制会话曾出现按钮操作无响应，未作为通过证据。Smart Canvas 菜单通过正常键盘交互完成选择。

`./scripts/check.sh` 最终通过：493/493，141.424 秒。原有 475 项完整保留，新增 18 项专项；Python 编译、JS 语法和 git diff 检查同时通过。专项包括五种协议/模式组合持久化、Key 操作/原子性、辅助凭证、旧 API 兼容、A/B/401/CSRF、只读协议识别、SSRF redirect、实际前端函数草稿保留与探测失败不改协议。

owner 原进程 PID、源码 HEAD 和工作区保持原状；原 assistant 与此前验收实例均未替换或重启。本提交不包含部署、VERSION、注册、域名或付费调用。
