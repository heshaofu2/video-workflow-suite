## 统一模型与运行环境

`transcript run` 与长视频分段识别统一使用 MLX Whisper large-v3-turbo，默认语言为中文，并保留词级时间。现有解释器、模型快照和可选分人环境由 `video-shared/local.json` 登记。

CLI 从已存在的本地快照加载，识别子进程设置离线模式。缓存失效或环境移动时停止并修正路径，不自动下载，不切换到 faster-whisper large-v3。`doctor` 查看当前实际选择。

## 单文件识别

```bash
"$VIDEO_CLI" transcript run "$VIDEO" \
  --output-dir "$OUTDIR" --output-name transcript --language zh
```

无需指定模型或后端；显式传参时仅接受 MLX Turbo 和 `--backend mlx`。产物包含 TXT、JSON、SRT、ASS；JSON 标明模型、引擎、源文件、媒体时长，并同时提供段内与顶层词级时间。

## 可选说话人分离

需要按人分析时加 `--diarize`。识别仍由 MLX 完成；分人单独复用已登记环境中的 WhisperX/pyannote，不调用 large-v3 识别权重。所需现有授权缺失或分人失败会报错，不能声称已完成分人。

只补标签时输出新名称：

```bash
"$VIDEO_CLI" transcript run "$VIDEO" --diarize-only \
  --existing-json "$OUTDIR/transcript.json" \
  --output-dir "$OUTDIR" --output-name transcript-speakers
```

不会重跑语音识别，也不会覆盖输入转写。

## 分段与时间校正

长视频见 [分段转写](segmented-transcription.md)。合并使用各段记录的实际媒体时长，保留段尾静音，且同时移动段落与词级时间。没有时长的旧结果先核对媒体再补齐，不用最后一句的结束时间猜测。

```bash
"$VIDEO_CLI" transcript merge \
  --segments-dir "$OUTDIR/outputs/transcript_segments_30m" \
  --output-json "$OUTDIR/transcript.json" \
  --output-txt "$OUTDIR/transcript.txt"
```

已核实的线性时间误差可在副本中校正：

```bash
"$VIDEO_CLI" transcript fix-drift "$OUTDIR/transcript.json" \
  --factor 1.0056 --output "$OUTDIR/transcript-corrected.json"
```

不把缺段、漏词或非线性偏移当成线性误差。长任务的 stdout 保存为结果 JSON，stderr 保存为运行日志；检查实际开头、结尾、分段交界和输出文件。
