# 指定历史未知记录的单次维护中断

这是管理员 CLI 的受限补充，不改变 `drain`、`reconcile`、`restart_safe`。没有新增 HTTP 接口、常驻进程或模型能力。必须先进入现有 `draining`；只能在全部可观测本地活动结束、ledger/归属/进程覆盖正常且未知集合精确匹配本次明确批准的记录集合时封口。接受风险不等于远端完成或零费用。

## 私有授权清单

root 在受保护目录保存0600普通JSON文件。禁止从“现场所有未知记录”自动推导批准集合。必须使用既有、事先获批的私有清单：逐条核对真实ID及规范指纹，缺少任何匹配就停止。规范指纹保持原审计算法：对 `id, instance, pid, start, reason` 构成的对象执行 Python `json.dumps(row, sort_keys=True).encode()` 后SHA-256（保留默认分隔空格与ensure_ascii），不包含Prompt、Key或素材URL。

字段严格为：schema=1、window_id（32位小写hex且从未使用）、epoch（本次draining epoch）、source_revision/target_revision（完整40位Git SHA）、created_at/expires_at（Unix秒，最多两小时）、remote_status_and_cost_unknown=true、records（1至512个 `{id, fingerprint}`，ID为32位小写hex字符串、指纹为64位小写hex字符串，ID与指纹分别不得重复）。整个私有文件仍不得超过65536字节；512是有限输入保护上限，不是批准数量或默认批准集合。这是维护中断授权，不是reconcile证据。

原合法四条清单兼容。七条或其它数量只在管理员事先明确批准该精确集合时才可使用。原子锁内仍比较完整 `{record_id: fingerprint}` 字典相等；不接受仅数量相等、子集、超集、通配符或现场自动收集后自称获批。缺一条、多一条、替换一条或任一指纹改变都拒绝。

源/目标目录必须是独立干净Git检出，版本分别匹配清单；运行Gateway及所有运行Instance的procfs固定程序入口必须匹配源release，PID/starttime覆盖完整。Gateway工作目录须为源release；Instance正常切换工作目录到自己的隔离数据根，必须与注册表根目录一致，不能误当代码目录。工具从独立新release执行，旧进程无需先重启。回滚用新窗口、当前epoch、反向源/目标及同一批准历史集合；旧清单不可复用。

```sh
# 所有路径/epoch/SHA/记录均先现场核实。命令仅限root，拒绝--sandbox。
"$NEW_PY" "$NEW_RELEASE/public_beta_maintenance.py" --root "$CONTROL" draining
"$NEW_PY" "$NEW_RELEASE/public_beta_maintenance.py" --root "$CONTROL" seal-for-interruption \
  --gateway-db "$GATEWAY_DB" --instances-root "$INSTANCES" \
  --approval-file "$PRIVATE_APPROVAL" \
  --source-release "$OLD_RELEASE" --target-release "$NEW_RELEASE" --timeout 60
```

CLI没有创建批准集合的快捷命令，没有ignore-all/force。--timeout范围0至300秒，只等待已接收活动结束；任何其他阻塞立即停止。退出0仅表示受限中断已封口；退出3表示拒绝/等待超时；退出2表示证据或持久化错误。错误时核实实际门禁状态，不能据无输出/断开判断成功。

## 原子性、失败与有效期

与旧Gateway/Instance共用 `gate.lock`：核查状态/版本、覆盖、活动、全部账本quick_check及业务阻塞、精确未知集合，在锁内写入并fsync独立prepared审计，再用既有原子状态写入切换sealed，最后持久化committed审计，才返回成功。

state仍只有 `schema, phase, epoch`，sealed语义完全兼容f598及2c6c2df。风险审计位于release外 `CONTROL/interruption-audit/<window_id>.json`，目录root0700、文件root0600；原journal未知行和业务ledger不修改。审计准备失败则不封口；状态切换失败留下已消耗窗口；封口后commit审计失败则保持封口并返回失败，不报告已授权停启。重复命令拒绝，不能用同窗口覆盖prepared记录。

成功输出包含 local_quiescent=true、historical_unknowns_retained=实际精确核对数量、restart_safe=false、interruption_authorized=true；prepared/committed审计使用相同实际数量，并保留历史未知阻塞原因。默认status/drain仍报告llm_remote_status_requires_reconcile与restart_safe=false。

授权仅在输出的sealed epoch、批准截止时间和源/目标窗口内有效；open/draining改变epoch即失效。到期不会自动开放门禁。此命令只封口，不自动重启，管理员在每个后续操作前核对epoch/截止时间、未知指纹及没有新活动/阻塞。已消费记录永久保留作为审计，绝不是永久豁免。

## 发布步骤

1. 核实新候选CI、beta ancestry、实际进程/路径、容量、安全阀和本次事先获批的精确历史集合。任何未批准的新增或变更未知、当前任务、待落盘结果、坏ledger、归属问题先停止。
2. 通知保存，draining，按事先批准集合准备本窗口私有清单，执行受限封口。原始status输出和授权输出都保留，不能写无损排空。
3. sealed后SQLite online backup并integrity_check，普通文件前后hash核验；备份Gateway、全部Instance、维护state/journal/独立审计、env/systemd与旧release/venv信息。没有变化中复制冒充一致性备份。
4. Gateway/Supervisor使用新release；Supervisor先接管旧Instance，再通过既有UDS逐个正常stop/start，首个检查通过后继续。停止超时就停止，不强杀、不重提。未运行账号不用启动，但启动程序路径须指向新版本。
5. 核对新版本/PID/start、原数据根、Provider/凭证引用、画布/History/媒体、全部获批原记录的身份与指纹；转draining允许登录读取并核实前端。最后明确open，确认registration保持open和安全阀不变。
6. 失败保持门禁，按兼容程序/venv回滚，不回灌旧数据库、不清ledger。需要再次封口时用新的反向窗口授权；不能复用已open的旧清单。最终必须报告开放状态。

## 验证

`tests/test_maintenance_interruption.py`是合成记录的单元/并发/持久化故障测试；其中权限/源目录边界使用单元seam，不能当作实际root兼容证据。

CI以真实Linux root执行 `tests/rehearse_interruption_upgrade.py`：干净Git worktree提取真实f598，旧Gateway/Supervisor/Instance不打补丁；新CLI不使用sandbox，真实HTTPS/Session/CSRF、相同维护锁，慢保存与封口并发，CLI异常退出、old PID不变封口、online backup、UDS串行升级、新版读取/重登、open后旧授权拒绝、独立反向窗口代码回滚、原记录/数据保持。TLS Caddy只作为固定临时代理，无reload，无新长期设施。全部模型/素材为隔离mock，真实模型、上游上传和邮件均为0。

原f598四条演练保留，CI另执行以下真实2c6c2df七条演练，均包含新CLI在旧进程未重启时封口、备份、正常串行升级及独立反向窗口回滚：

```sh
sudo "$TEST_PY" -B tests/rehearse_interruption_upgrade.py \
  --source-revision 2c6c2df062efbf6443aa5fc8d31998fd725c0e3b --historical-count 7 \
  --caddy "$TEMP_CADDY" --evidence "$TEMP_EVIDENCE"
```

只在隔离Linux测试机执行；记录及批准文件全为临时合成数据，不复制生产ID。四条和七条均重复全部单元安全用例。测试契约从“非四条拒绝”改为“空/非法/超限集合拒绝，未经批准或不精确匹配的记录拒绝”；原“批准四条但新增第五条拒绝”保留，并新增“批准七条但新增第八条拒绝”，不是删除安全检查。保留原893项回归和原5449演练；新增候选必须取得自己的最终CI。该能力不构成生产七条记录的风险授权，需管理员针对最终候选另行批准。
