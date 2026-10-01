# Public Beta 原 Kie 图片结果的 detached 恢复

基于并发 10 候选 `3512e4779dc4046d9fb5691348b6a4d5b70c377d`。本修复只改变原图片结果恢复和相关 UI，不改变每账号并发 10、图片/视频/LLM 共用计数、实例上限或维护协议。开发及验收只用隔离合成 fixture，不访问生产样本。

## 状态机变化

| 条件 | 修改前 | 修改后 |
|---|---|---|
| 原任务 owned、Provider/凭证版本匹配，绑定完整 | query_only 恢复，正常终态 | 保持原行为，`binding_status=attached` |
| 原 Canvas/节点/生成版本已删除 | validate_binding 先返回 404/409，不能查询/下载 | 原结果所有权与节点回填分开验证；`binding_status=detached`，仅恢复 output/同一 task/History |
| 原任务未绑定节点 | 原账户结果恢复 | `binding_status=unbound`，保持原账户结果恢复 |
| 上游 success，下载/媒体校验/History 写入失败 | 可能仅凭 remote_done 就释放占位 | `result_recovery_required`、local pending，保留 outstanding，包括重新启动后 |
| 已登记原批次全部安全落盘，History 写入成功 | succeeded | `succeeded` / `completed_local`，local completed、upstream success、outstanding=false、recovery 清空；detached 保留明确标记 |
| 已完成且本地文件完整，再 refresh | 仍先检查原绑定 | owned 校验后返回同一已完成任务；不启动 runner、不查询、不下载、不重复 History |

新任务创建仍严格要求有效原绑定。恢复入口不接收 Provider、节点、任务、owner 等覆盖参数，不能重建 Canvas/节点，不能匹配“相似节点”。Canvas owner 展示标签不参与授权；当前实例私有账本的 owner 和会话 subject 决定原结果所有权。

## 恢复和占位边界

对尚未完成的 Kie 图片任务，必须同时满足：当前 subject == task owner；本实例私有账本 exact task ID；原 Provider 存在且启用；原 model/purpose 仍被允许；原 Provider/credential revision 完全匹配；params 的 Provider/model/purpose 与原任务一致；submission_uncertain=false；非空唯一 upstream IDs 数量与原 n 完全相同。

提交状态未知、缺失 ID、缺失批次直接返回 409，不借其他 Key 查询、不补交；query_only 仍在传输层阻止 POST/PUT/PATCH/DELETE。恢复调用只查询已经登记的原 ID，完成子任务跳过，未完成子任务逐项恢复。每个 success 返回的结果数量被保存为 result_count；后续结果列表数量变化不会被当作完整恢复。

所有已登记子任务均须明确 remote_done，全部原结果经过现有 GuardedClient 的媒体地址/大小/格式/像素校验并安全落盘，每个 entry 标记 done 且本地文件存在，才能幂等写 History 并进入成功终态、释放占位。下载部分成功后中断保留已经落盘的 local 前缀，下一次只下载剩余原结果；History 写入失败保留占位，下一次复用已落盘文件。

waiting/generating、未知状态、401/403/404/超时等查询失败均不释放。单任务明确上游失败仍走原正常失败终态；多任务批次未逐项确认时保守保留占位。本地停止仍是 local-only，不声称供应商取消、退款或零费用。

## 保存位置与 UI

复用现有 `Execution.save_image` / `save_ai_image_to_output` 和当前用户的 generated/output 配置；公测默认路径为 `<instance_data_root>/assets/output/controlled_<uuid>.<原格式后缀>`，不会保存到其他实例目录。原 task ledger 在 `.auth/model-tasks.sqlite3`，History 在 `<instance_data_root>/history.json`，仍使用原 task_id 作为 identity_key。

任务 API、result 和 History 增加 binding_status 与 recovery_message。detached 仍兼容 status=succeeded，不代表节点回填成功。Online History 卡片和详情显示固定安全提示：

> 原生成节点已不存在，结果已恢复到历史记录，不会自动写入其他节点。

Smart Canvas 阻止 detached 完成结果向任何节点回填，也不通过恢复操作保存旧画布。原节点/生成版本存在时沿用现有回填；`finalizeSmartPendingTask` 未修改，cancelled/其他终态与 superseded 版本保护保持不变，不能强行改为 success。

## 合成验证

`tests/test_instance_detached_recovery.py` 覆盖完整绑定、删除节点/生成版本/软删除及清除 Canvas、重复和重启幂等、提交未知、缺失 ID/批次、运行中/未知/失败查询、凭证版本变化、A/B 和账本 owner 不匹配、无效媒体、部分下载、多上游子任务、结果列表缩短、History 写入失败及 cancelled 原版本。

该文件还使用真实隔离 Public Beta Gateway/worker 和 loopback Kie fixture 验证 max_concurrent 两层均为 10：一个 detached 原任务占位加九个原任务占满 10，第 11 个 429；通过正常恢复释放原占位，数量降为 9，新的第 10 个经正常提交被接收，再次超额 429。不为通过测试直接写 outstanding=false。

前端测试覆盖 detached 提示、禁止向同 ID 的陈旧节点或其他节点回填、禁止恢复时保存画布，以及原 cancelled 终态保护。完整 checks 还保留并发 10 的共享池、争抢、A/B、新注册/停用/重启和资源验证。
