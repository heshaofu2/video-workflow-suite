## 命令边界

`video-cli` 是项目内的稳定调用入口；`video-shared/scripts/commands/` 保存执行实现。领域 Skill 不直接绕过入口调用这些内部文件。

CLI 目前提供：

- `transcript run / segments / merge / fix-drift / read`
- `clip cut / boundary / join`
- `dedup inspect / check / render / review`
- `subtitle resegment`
- `doctor` 与 `capabilities`

完整参数以具体命令的 `--help` 为准，不在多个 Skill 里重复维护参数清单。剪辑、读取已有转写和字幕处理不读取 `local.json`，也不加载识别模型。`doctor` 只检查剪辑环境；`doctor --transcription` 才额外报告可选 MLX 状态，顶层 `status` 仍表示剪辑环境，不能把转写未就绪误判为剪辑不可用。

仅当选择内置识别时，`transcript run` 和 `segments` 使用 `--backend mlx` 与 `mlx-community/whisper-large-v3-turbo`，从 `local.json` 指定的现有环境与本地快照加载。它们不提供其他识别后端；选择外部转写工具时直接接入产物，不给这些命令猜测新的 backend。可选说话人分离复用已有独立环境。

## 输出契约

业务命令的 stdout 只输出一条 JSON，运行日志实时写到 stderr。`--help` 输出普通帮助文字。成功要求退出码为 0 且 `ok` 为 `true`；错误为非零退出码及 `ok: false`。不要用顶层 `code == 0` 判断成功。

```json
{"ok": true, "data": {"command": "dedup inspect", "result": {}, "outputs": []}}
```

```json
{"ok": false, "error": {"code": "output_exists", "message": "拒绝覆盖已有文件"}}
```

`doctor`、`capabilities`、`transcript read` 和 `--dry-run` 的结果直接位于 `data`；执行命令的底层结构化结果位于 `data.result`。底层仅打印文本时，结果保留为 `stdout`、`stderr` 字符串。`data.outputs` 是本次声明且实际存在的输出路径，不代表内容已通过主观验收。

底层退出码为 0 只能说明命令运行完成。转写可能缺尾、字幕可能被底层跳过、视频可能有不自然接点；按领域要求检查实际产物。`warnings` 中的限制不能忽略。

## 失败与预览

- `unknown_command` 或参数错误：回到 `capabilities`、逐级 `--help`，不猜命令变体。
- `output_exists`：复用已核对产物或换新输出路径，不加覆盖选项重跑。
- `source_mismatch`、`unverified_resume`：不能把当前目录当作同源缓存。核实来源，或使用新产物目录。
- `command_failed`：查看 `error.details.exit_code`、底层日志与已生成文件。失败可能留下部分产物，不自动重复执行。
- `--dry-run` 只证明当时的预检与计划，不证明模型可运行，也不证明剪辑效果。

长转写可在已有后台执行机制中运行，日志落在项目目录。不要因为任务长而创建周期提醒或修改全局配置。
