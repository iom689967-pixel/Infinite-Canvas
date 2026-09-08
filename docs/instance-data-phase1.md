# 多实例数据路径基础（第一阶段）

本阶段是两个本地进程的数据隔离基础，不是登录、权限系统或远程安全方案。
不得因此向局域网、Tailscale 或公网开放服务。不得使用现有 owner 数据目录测试。

后续第二阶段已增加强制认证；本文保留第一阶段路径设计记录。
当前分支的账号初始化、登录与启动步骤请使用 [第二阶段说明](instance-auth-phase2.md)，
下面的第一阶段启动示例不再足以启动显式实例。

## 启动配置

没有任何 `INSTANCE_*` 启动配置时，仍使用原来的单实例目录与默认端口，
不会清理环境、迁移文件、添加进程锁或改变现有启动脚本。

只要提供任一 `INSTANCE_*` 配置，就进入显式模式，身份、数据根、Host、Port
四项必须完整。配置只在服务端导入模块时读取；HTTP 参数、Header、
Provider 配置或实例 `.env` 不能覆盖实例身份、根目录和监听地址。

| 配置 | 规则 |
| --- | --- |
| `PROGRAM_ROOT` | 自动定位程序文件目录；若设置，必须与安装目录一致 |
| `INSTANCE_ID` | 1–64 位字母、数字、下划线或连字符，首位为字母/数字 |
| `INSTANCE_DATA_ROOT` | 绝对路径，新建空目录，或该实例之前初始化过的目录 |
| `INSTANCE_HOST` | 本阶段必须是 `127.0.0.1` |
| `INSTANCE_PORT` | 1024–65535，由管理员选择空闲端口 |
| `INSTANCE_MOCK_UPSTREAMS` | 可选，逗号分隔的 `127.0.0.1:端口`，仅供本地 mock |

默认禁止出站连接；只有管理员在启动时列出的 mock 地址可连接。
不能把 owner 或另一个实例端口加入 mock 列表。此列表是受信任的服务端配置，
不是允许浏览器修改的 Provider 地址白名单。

示例（仅临时实例，执行会创建临时数据；不运行项目原有启停脚本）：

```sh
instance_a=$(mktemp -d /tmp/canvas-a.XXXXXX)
INSTANCE_ID=assistant01 INSTANCE_DATA_ROOT="$instance_a" \
INSTANCE_HOST=127.0.0.1 INSTANCE_PORT=43101 \
/absolute/path/to/python /absolute/path/to/worktree/main.py
```

成功标志：实例只监听指定 loopback 端口，目录出现 `.instance.json`、
`.runtime/process.json`、`.runtime/logs/server.log`，原有 owner 服务继续运行。
若端口占用，服务启动失败；不要运行会杀掉 3000 端口进程的旧启动脚本。

## 路径映射

| 范围 | 显式实例路径 |
| --- | --- |
| 内置静态资源、providers 源码、内置 Workflow | `PROGRAM_ROOT/static`、`providers`、`workflows`（只读） |
| Canvas、Projects、Conversations | `INSTANCE_DATA_ROOT/data/canvases`、`data/projects.json`、`data/conversations` |
| Asset Library、Prompt Library、分类提示词 | `data/asset_library.json`、`data/prompt_libraries.json`、`data/asset_classification_prompt.txt` |
| 上传、生成媒体、素材库、local-assets | `assets/input`、`assets/output`、`assets/library`、`assets/uploads` |
| legacy 输出、History | `output`、`history.json` |
| 自定义 Workflow、内置 Workflow 设置覆盖 | `workflows/custom`、`workflows/自定义`、实例 `workflows/*.config.json` |
| Provider 与运行配置 | `API/.env`、`data/api_providers.json`、`global_config.json` |
| Storage Settings、Shared Folders、RunningHub 保存状态 | `data/storage_settings.json`、`data/shared_folders.json`、`data/runninghub_workflows.json` |
| 预览与 Kie 缓存 | `data/media_previews`、`data/kie_reference_cache.json` |
| 临时文件、HOME、日志、进程记录 | `.runtime/tmp`、`.runtime/home`、`.runtime/logs`、`.runtime/process.json` |

除第一行外，以上相对路径都基于 `INSTANCE_DATA_ROOT`。
媒体 URL 仍为 `/assets/...`、`/output/...`、`/api/storage-files/...`；
每个进程的 StaticFiles 挂载、预览、下载、导入、导出和日志清理分别解析自己的文件。
自定义 Storage/Shared/Export 路径只能在本实例目录内。

## 保护与失败策略

- 拒绝缺失/非法配置、源码目录、另一个 owner checkout 内的目录、嵌套实例、
  非空未标记数据、身份不匹配及启动时发现的符号链接。不做隐式迁移或回退。
- 根目录使用操作系统独占文件锁，在任何运行数据初始化前获取；保持到进程退出，
  第二个进程拒绝启动。锁文件不删除、不替换；正常退出或崩溃由内核释放锁。
- 显式模式用环境白名单清除所有继承的未知凭证/代理，并重定向 HOME/TEMP。
  只加载自己的 `API/.env`。不会查 owner 配置或主目录 Codex 认证文件。
- 端点路径解析检查实例范围和符号链接；Python audit hook 额外覆盖实际 open、
  删除、重命名、目录操作和出站连接，防止漏改某个文件写入点。
- 更新、回滚、重启和全局 CLI 登录入口被拒绝；源码只读。发行清单纳入
  `instance_paths.py`，但没有提升 VERSION 或发布 main/stable。
- 使用进程启动配置运行单 worker；显式模式禁止 fork/exec/派生原生子进程。

## 有意保留的限制

- **不是操作系统沙箱**：不防同一 OS 用户手工修改文件、恶意修改程序/依赖、
  原生扩展漏洞、文件描述符/硬链接攻击及并发本机文件替换。未来对不可信用户部署
  仍需独立 OS 身份或容器等边界，不能只靠这里的 Python 防护。
- **原生 CLI/媒体子进程暂不启用**：Codex/Gemini CLI/Jimeng 和 ffmpeg/ffprobe
  不继承 Python 的文件/网络限制，故本阶段失败关闭。视频文件本身的上传、
  静态访问、下载仍按实例工作；视频抽帧、转码、剪辑需要后续受限媒体 worker。
- HTTP 生成链路本阶段仅允许指定本地 mock，不启用真实付费 Provider、共享凭证
  或现有 ComfyUI 服务。旧单实例保持原功能。
- 尚无登录、服务端用户权限、CSRF/CORS 加固、配额或远程访问保护。
- 两套进程各有队列/锁；同一个实例内部原有 JSON 并发语义未在本阶段重构。
- 本轮本机验证 macOS 文件锁和进程行为；Windows 分支未作真实 Windows 验收。

## 验证

`tests/test_instance_isolation.py` 启动两个真正独立的 Python/uvicorn 进程，
分别使用空临时数据根与空闲端口，连接父测试进程提供的本地 HTTP mock。
Canvas、媒体、预览、下载、库、配置和清理测试通过真实 HTTP；History/Kie 初始
假数据由子进程调用正式 writer 创建，不替换全局路径、不使用生产数据。
测试结束只停止自己创建的子进程并清理自己创建的临时数据。

运行专项测试和完整检查（使用具备项目依赖的 Python/Node）：

```sh
python -m unittest discover -s tests -p test_instance_isolation.py -v
./scripts/check.sh
```

成功标志：专项测试全部 OK，完整脚本输出 `All checks passed.`，生产数据摘要
及生产服务 PID/启动时间与测试前相同。新正式运行文件必须先加入 Git index，
现有 Release Manifest 测试才会将其识别为受版本控制的发行内容。
