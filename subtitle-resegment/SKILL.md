---
name: subtitle-resegment
description: 将已有 SRT 字幕按语义重分为较短的字幕条，结合词级时间戳保留完整文字和时间位置。适用于短句字幕、一行字幕和竖屏字幕分段；不自动转写、剪片或烧录字幕。
metadata:
  requires:
    skills: [video-shared]
---

## 共享前置

执行前读取 [video-shared](../video-shared/SKILL.md) 与 [转写和字幕时间契约](../video-shared/references/transcript-contract.md)。基础字幕生成归转写，本 Skill 只负责最终字幕的意群与阅读分段。

## 输入与执行

输入完整 SRT、可用的词级时间 JSON、目标字数与语义分段；交付新的短句 SRT。缺少对应成片的转写时可直接调用 `video-to-text`，不另写识别流程。

具体操作见 [字幕重分段](references/resegment.md)。运行 `subtitle resegment --help` 后使用该命令执行：

- 以 SRT 拼接的完整正文形成 `segmented.txt`，只插入 `|`，不改字；时间标记和说话人标签不属于字幕正文。
- 按自然意群断句，保护专名和完整短语。不可拆专名超过目标字数时保留完整性并说明。
- 时间插值或按字数降级切分需如实说明精度和阅读限制，不声称已经逐字精确对齐。

## 验收

检查文字覆盖、数字专名、字幕长度、时间顺序和过短字幕条的可读性。交付字幕文件；样式、烧录和发布按用户另外指定的目标处理。
