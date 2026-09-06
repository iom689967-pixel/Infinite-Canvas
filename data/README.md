# Runtime data

`data/` 同时容纳可版本化的示例文件和本机用户运行数据。Git 只跟踪前者。

## 不进入 Git 的运行数据

- `api_providers.json`：本机 Provider 配置，密钥另存于 `API/.env`。
- `asset_library.json`：用户素材库索引。
- `canvases/`、`projects.json`：用户画布和项目数据。
- `media_previews/`：可重建的媒体预览缓存。
- `conversations/`、`prompt_libraries.json`：对话和提示词库。
- `runninghub_workflows.json`：用户 RunningHub 工作流设置。
- `shared_folders.json`、`storage_settings.json`：本机路径和存储设置。
- `update_backups/`：应用更新回滚备份。
- `asset_classification_prompt.txt`、`kie_reference_cache.json`：本机运行配置和缓存。

用户素材本体位于仓库根目录的 `assets/`，生成输出位于 `output/`；它们也不进入 Git。

## 示例与密钥

`api_providers.example.json` 只展示通用结构，不会被应用当作真实配置读取。首次运行后，应用会在 `api_providers.json` 中维护本机配置。Provider Key、Token 和账户凭据必须保存在已忽略的 `API/.env` 或对应本机凭据文件中，不要写入 example 文件。

## 备份与恢复

停止 Infinite-Canvas 服务后，将 `data/`、`assets/`、`history.json`、`API/.env` 和 `.runtime/backups/` 复制到仓库外的备份目录。恢复时也应先停止服务，确认备份完整后再覆盖对应运行文件。

请不要用 `git clean` 清理这些目录：它们是用户数据，不是可丢弃的源码临时文件。
