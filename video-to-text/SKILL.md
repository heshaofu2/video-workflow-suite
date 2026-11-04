---
name: video-to-text
description: 将本地视频或音频转为带时间戳的文字稿和字幕，支持长录音分段转写及按需区分说话人。适用于视频转文字、录音转写、补说话人标签；不自动提炼观点或剪视频。
metadata:
  requires:
    skills: [video-shared]
---

## 共享前置

执行前读取 [video-shared](../video-shared/SKILL.md)；转写复用、引擎选择、局部补充与时间轴规则统一见 [转写契约](../video-shared/references/transcript-contract.md)。本 Skill 负责取得可供下游使用的转写，不另外维护一套通用规则。

## 命令选择

新识别统一为 MLX Whisper large-v3-turbo；CLI 自动使用项目已登记的现有 MLX 环境与缓存，不再运行 WhisperX 识别服务。

| 场景 | CLI 命令 | 参考 |
|---|---|---|
| 单文件识别，或补说话人标签 | `transcript run` | [转写操作](references/transcription.md) |
| 长视频分段识别与续跑 | `transcript segments` | [分段转写](references/segmented-transcription.md) |
| 合并分段、检查已知时间漂移 | `transcript merge / fix-drift` | [转写操作](references/transcription.md) |
| 读取已有转写或按时间取段 | `transcript read` | 具体参数运行 `--help` |

单文件识别可输出 TXT、JSON 与基础 SRT、ASS；分段模式保留各段结果，合并命令输出 JSON、TXT。按人分析或用户要求时补说话人信息，已有结果只缺标签则不重识别全片。补标签输出新目录或新名称，不能覆盖原结果。

## 验收

检查开头、结尾、分段交界和必要字段。分段合并后的偏移需要对照真实媒体核实。基础字幕导出可能被底层跳过，按实际文件说明交付，不能只凭命令退出成功声称所有字幕已经生成。

本 Skill 可独立处理转写，也可被去重、切片和 Workflow 直接调用。观点、选段和字幕意群判断由对应领域负责。
