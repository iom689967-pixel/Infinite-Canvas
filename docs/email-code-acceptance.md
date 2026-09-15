# 邮箱验证码本地验收

2026-09-15。独立开发 worktree，Resend HTTPS MockTransport，loopback Gateway、临时真实 Supervisor/Instance。
未操作 VPS、生产 env/service、beta/main/stable/VERSION，也未发送真实邮件或调用真实模型。

## 自动检查

- 原有 681 项保留，新增 68 项验证码专项；`./scripts/check.sh` 最终 749/749，全部四个检查阶段通过。
- `.venv/bin/python -m pip check`：No broken requirements found。
- 覆盖 HMAC、前导 0、严格格式、TTL/边界、错误次数、冷却/多标签/直接请求、发送失败、乱序与相邻/非相邻 RNG 碰撞、历史/限速数据上限、原子并发验证、容量与唯一性 race。
- 覆盖 v1 原 token 到期冻结、重启续验、pending 清理、完整工作台、SSO、首次初始化失败重试、重复访问只有一个 Instance、原有 A/B/401/Provider/任务隔离回归。

## 浏览器观察

| 流程 | 结果 |
| --- | --- |
| Alice 注册、错误 code、正确 code | 错误提示正确；无再次密码输入，自动进入 `/` 完整 Canvas，私人 Canvas API 200 |
| Bob 重发 | 60 秒前按钮禁用、直接 POST 429；模拟时间后重发；旧 code 拒绝、新 code 登录，Canvas API 200 |
| Carol 五次错误 | 前四次错误提示，第五次失效；重发后新 code 可登录，Canvas API 200 |
| 属性/粘贴/前导 0 | numeric、one-time-code、maxlength=6；含空格/连字符复制内容规范化，前导 0 保留并成功验证 |
| 格式提示 | 字母触发“请输入 6 位数字验证码”；输入正确 code 后 validity 清空并可验证 |
| loading | 延迟 mock transport 中观察到按钮禁用和“正在注册…”/“正在发送…” |
| 刷新/多标签 | HttpOnly cookie 恢复遮罩邮箱/申请；第二标签无法提前重发 |
| A/B 工作区 | A 私有项目不在 B 页面；A 重新登录后项目仍存在，私人 Projects API 200 |
| logout | 退出后新的 Gateway、Canvas、Provider、media 请求均 401，cache=no-store |

只观察安全摘要，无 code、HMAC、密码、Key、正文、token/URL 原文。
最终 fixture：三个 code 用户均 active，加一个预设 legacy 用户；5 次 mock HTTPS 邮件请求，真实邮件/SMTP/模型为 0，fixture log_secret_free=true。
通过 `finish` 停止临时实例/daemon，删除临时目录；临时浏览器页关闭，无 orphan listener/process。

## 夹具修正

模拟时间只替换验证码模块 clock，不修改 Gateway/worker 的 SSO 时钟。之前全局修改 time.time 会导致交接签发时间偏移，此问题仅在测试夹具修正，未放宽认证。
旧链接测试通过明确预部署 fixture 注入已发行 token；生产新发送路径无法生成链接。
CI 由独立开发分支提交触发现有 Project checks（完整检查和 pip check），不触发生产部署。
