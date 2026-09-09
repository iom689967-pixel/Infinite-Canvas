# Kie 原任务结果恢复

适用范围：显式认证实例的受控 Kie 图片任务，以及正式 Smart Canvas 的原节点恢复。Owner 单机任务保持原接口和行为。

## 状态与持久化

接口保留原 `status` 兼容性，并提供 `phase`、`upstream_status`、`local_result_status`。

| 情况 | phase | 本地状态 |
| --- | --- | --- |
| 已取得上游任务引用 | submitted | not_ready |
| 上游排队或生成 | processing | not_ready |
| 已确认上游成功 | upstream_success / result_pending_download | pending |
| 结果解析、DNS、下载或保存失败 | result_recovery_required | pending |
| 图片和 History 已保存 | completed_local（status=succeeded） | completed |
| 上游明确失败 | upstream_failed（status=failed） | not_ready |

成功状态在解析结果地址和本地 I/O **之前**写入当前 `INSTANCE_DATA_ROOT/.auth/model-tasks.sqlite3`，文件权限为 0600。该私有账本保存原上游 ID、当前用户 ownership、Provider identity/revision、模型、不可变 Canvas/node/generation 关联和恢复次数/时间；不保存 API Key。前端和 Canvas 只使用本地任务引用，不收到上游 ID 或远端结果 URL。

旧版明确 `remote_done` 且没有提交不确定性的记录可迁移为待恢复。缺失节点关联的旧任务不会猜测其他节点。部分批次存在未知提交时继续保留 `manual-reconcile`，重启不会自动重新提交。

## 恢复边界

`POST /api/canvas-image-tasks/{local_task_reference}/refresh` 只接受空请求（或 `{}`），拒绝 Provider、owner、上游任务和节点覆盖参数。服务端从当前用户账本取原任务，验证会话、实例数据根和原节点/版本，再查询原上游 ID。

- 恢复执行器禁止进入 createTask 分支；恢复专用 HTTP client 还会在读取凭证或连接前拒绝 GET/HEAD 以外的方法。
- DNS、公网判断、SSRF、redirect 拒绝、私网阻断、响应体上限和 timeout 均沿用安全 client。Fake-IP 不会被放行。
- 同一任务只有一个本地执行器。已完成任务重复恢复不查询、不下载、不新增 History。
- Provider 删除或禁用后，旧任务仍可读取，待恢复请求返回 409。Provider 配置或凭证 revision 变化也拒绝查询，不切换 Provider、不从环境变量取 Key。已完成本地任务不需要 Provider。
- 原节点或版本被删除后拒绝恢复，不把结果转移到别处；已取消或被新版本取代的生成不会覆盖当前采用结果。

## 原节点与 History

受控请求提交前保存 Canvas 并绑定实际目标节点。下载失败时保留该节点原 generation attempt 和本地任务引用，显示“生成已完成，结果下载待恢复”及“恢复结果”，不显示普通生成失败或持续生成叠层。

点击恢复只查询原任务。成功后使用原 completion 路径回填同一节点、同一 attempt，自动保存 Canvas，并显示原结果 Preview。History 按原本地 task identity 更新；采用临时文件原子替换，写入失败保留待恢复状态，已有本地结果不重复下载。

每页对明确待恢复任务最多读取一次**本地任务状态**；若另一页面已恢复则回填，仍待恢复则保留按钮。页面打开不会自动发起上游查询或下载，不存在后台无限重试。

## 验收证据（全部 mock）

`tests/manual_kie_result_recovery.py` 启动两个独立认证临时实例及本地 Kie fixture；stdin `ready` 解除模拟下载故障，`evidence` 输出安全计数，`finish` 清理。

正式完整 UI 已验证：模拟上游成功、首次结果被安全层拒绝 → 原节点待恢复 → 刷新保留 → 点击恢复 → 原节点回填 → Preview、History、Canvas 正常 → 刷新及重登录结果仍在。

该浏览器流程 mock create=1、query=2、Canvas 节点=1、生成 History=1，未手工导入结果。B 的画布列表为空；直接读取 A 的 Canvas/task/reference/result 均为 404，个人 Provider API 为 403；未认证新请求全部为 401。本轮真实上传和真实 createTask 均为 0。

自动测试包含成功、DNS 拒绝、timeout、中断响应、redirect、上游失败、恢复幂等、写入失败、重启、未知提交保留、绑定防篡改、Provider 删除/禁用/换 Key、日志脱敏和前端原节点回填。

最终 `./scripts/check.sh`：518/518，通过 Python 编译、JavaScript 语法及 Git diff 检查。相对 493 项基线新增 25 项测试，没有删除原有测试。
