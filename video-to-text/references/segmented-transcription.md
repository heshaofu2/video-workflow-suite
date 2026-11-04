## 适用情况

长音频分段后由同一 MLX Turbo 模型逐段识别，让结果及时落盘，并跳过来源记录匹配且已有 JSON、TXT 的完成段。

```bash
"$VIDEO_CLI" transcript segments "$VIDEO" \
  --output-dir "$OUTDIR" --output-prefix transcript \
  --segment-minutes 30 --backend mlx
```

完整参数运行 `transcript segments --help`。同一命令可续跑；CLI 的来源记录不匹配或旧目录没有来源记录时会拒绝自动复用，按共享层的错误规则处理。

## 产物

- `source/transcript.audio.m4a`：提取的音频缓存。
- `segments/audio_30m/seg_XXX.m4a`：分段音频。
- `outputs/transcript_segments_30m/seg_XXX.json/.txt/.srt/.ass`：各段实际生成的转写。
- `logs/transcribe_segments_30m/`：底层进度和运行日志。

段内时间从零开始；全局合并通过 `transcript merge` 完成。核对分段时间的规则统一见 [共享转写契约](../../video-shared/references/transcript-contract.md)，不能因为各段都有文件就声称全片时间已精确对齐。
