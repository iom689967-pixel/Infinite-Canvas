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
