---
name: video-to-text
description: 将本地视频或音频转为带时间戳的文字稿和字幕，支持长录音分段转写及按需区分说话人。适用于视频转文字、录音转写、补说话人标签；不自动提炼观点或剪视频。
metadata:
  requires:
    skills: [video-shared]
---

## 共享前置

执行前读取 [video-shared](../video-shared/SKILL.md)；转写复用、引擎选择、局部补充与时间轴规则统一见 [转写契约](../video-shared/references/transcript-contract.md)。本 Skill 负责取得可供下游使用的转写，不另外维护一套通用规则。

## 取得转写材料

先检查已有材料和用户选择的转写工具。已有文字稿、字幕或 JSON 满足下游要求时直接复用；缺少时说明媒体、覆盖范围和所需格式，引导用户选择当前可用的独立转写能力或在其熟悉的软件中导出。不要求 Apple 芯片，也不把安装本地模型作为剪辑前置。转写选择和材料要求以共享契约为准。

若用户选择当前套件的内置转写，才使用下表的 MLX Whisper large-v3-turbo 实现。该实现需要 Apple Silicon Mac 与单独准备的环境和权重；其他工具的识别结果按共享数据契约接入，无需换成 MLX。内置环境未就绪时说明限制，并给出可用的转写替代方式；只有用户选择准备 MLX 时才执行安装包的 `init --local --prepare`。

## 可选内置命令

| 场景 | CLI 命令 | 参考 |
|---|---|---|
| 单文件识别，或补说话人标签 | `transcript run` | [转写操作](references/transcription.md) |
| 长视频分段识别与续跑 | `transcript segments` | [分段转写](references/segmented-transcription.md) |
| 合并分段、检查已知时间漂移 | `transcript merge / fix-drift` | [转写操作](references/transcription.md) |
| 读取已有转写或按时间取段 | `transcript read` | 具体参数运行 `--help` |

单文件识别可输出 TXT、JSON 与基础 SRT、ASS；分段模式保留各段结果，合并命令输出 JSON、TXT。按人分析或用户要求时补说话人信息，已有结果只缺标签则不重识别全片。补标签输出新目录或新名称，不能覆盖原结果。

## 验收

检查开头、结尾、分段交界和必要字段。分段合并后的偏移需要对照真实媒体核实。基础字幕导出可能被底层跳过，按实际文件说明交付，不能只凭命令退出成功声称所有字幕已经生成。

本 Skill 可独立处理或引导转写；去重、切片和 Workflow 可以直接使用其他工具的产物，无需先调用本 Skill。观点、选段和字幕意群判断由对应领域负责。
