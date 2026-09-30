# Public Beta 每账号任务并发 10

Public Beta 的默认 `MAX_CONCURRENT_GENERATIONS` 为 10；显式配置仍只接受 1～10 的整数。
`MAX_RUNNING_INSTANCES=4`、用户上限、存储、上传、磁盘保护、单批张数、参考数量及上游限制均不变。
本配置不增加队列；图片、视频、LLM 继续使用各实例的同一任务池。

## 配置与加载

- Gateway / Supervisor：`BetaConfig.from_env()` 读取生产 env，缺省为 10。
- 新账号：`Supervisor.provision()` 将同一个 `config.concurrency` 写入
  `.auth/model-access.json` 的 `max_concurrent` 和 `.auth/public-beta.json` 的
  `max_concurrent_generations`。未运行或停用账号也必须纳入管理员迁移清单。
- Instance：`ModelPolicy` 启动时分别检查两层 1～10 范围，然后取 `min()`；
  只改 env 不会更新现存文件，更不会改变运行进程内的 policy。
- 保存 Provider：读取原配置、只替换 Provider 字段并重新加载 `ModelPolicy`；
  并发字段由管理员持有。没有给个人设置新增修改并发的入口。
- 无配置的非公测 Instance 保持原保守缺省；本地 owner 不在本次变更范围。

## 发布验收

沿用 [维护流程](public-beta-maintenance.md) 和
[精确未知记录批准集合](bounded-maintenance-interruption.md)。本文件不授权中断、核销或重试历史任务。
新的未知记录、pending ledger、未结束活动及缺失批准仍阻塞封口；不得因扩容而清记录。

1. 从实际 systemd、procfs 和注册表核实 env、release、venv 及每个实例的数据根。
2. 通过最终 CI；满足原维护边界后 draining、封口并完成全实例一致性备份。
3. 只修改 env 的并发项及每个实例上述两个并发字段，保留其他 JSON 内容、UID/GID、权限；
   同目录临时文件写入、fsync、原子替换并 fsync 目录。两个文件全部完成后才能启动实例。
4. Gateway / Supervisor 加载目标 release 和 env，先接管旧 Worker；
   逐个正常 stop/start 运行实例，首个验收通过再继续。停用账号保持停用且不启动。
5. 运行实例从正常认证的 `GET /api/instance/providers` 核对进程内 `max_concurrent`；
   停止实例只报告已验证配置及下次启动路径，不伪造不存在的进程值。
6. 按现有 `outstanding() + llm_active` 口径报告占用和剩余；
   维护 journal 的历史未知不自动等于该实例的任务池占位，也不因为扩容而解决。
7. 最后确认 maintenance / registration 都为 open。

## 隔离验收

`tests/test_public_beta_concurrency.py` 使用真实 Supervisor、Worker、认证 HTTP、
私有账本和仅绑定 loopback 的 mock：同时提交 20 个任务只接收 10 个，拒绝请求不到上游；
确认一个任务完成后补入一个；A/B 各自 10 个，图片/视频/LLM 混合共池；
验证 Provider 保存、重启、既有停用账号的配置迁移以及旧未知占位保留。
两层配置各自覆盖 1～10 和非法类型/范围。生产不运行生成测试。

设置 `MIO_CONCURRENCY_EVIDENCE` 可保存内存、持有的 mock 连接、Linux 文件描述符、
结果文件数量/字节、SQLite integrity 等证据。CI 上传 `mio-concurrency-evidence`。
所有素材及凭证均合成，真实模型、上游素材上传、邮件调用均为 0。
