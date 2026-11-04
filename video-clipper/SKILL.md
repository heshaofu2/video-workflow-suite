---
name: video-clipper
description: 从长视频按话题、说话人或已确认时间范围选段，批量粗剪成短视频，支持可选去静音与多段拼接。适用于长剪短和视频切片；不负责语义去重、字幕或包装。
metadata:
  requires:
    skills: [video-shared]
---

## 共享前置

执行前读取 [video-shared](../video-shared/SKILL.md)。输入输出保护、转写和时间轴使用共用规则；本 Skill 只维护选段和粗剪规则。

## 输入与交付

输入视频，以及现有转写、观点摘要或用户已确定的范围；交付候选、确认后的 `clips.list` 和粗剪片段。已有范围足以执行时不强制跑摘要；缺转写可直接调用 [video-to-text](../video-to-text/SKILL.md)，需要梳理长内容时再调用 [insight-extractor](../insight-extractor/SKILL.md)。

## 命令与判断

- 选段与确认规则只以 [选段和粗剪流程](references/clipping.md) 为准；[技术说明](references/clipping-guide.md) 仅补充静音处理和后续处理注意事项。
- `clip boundary` 检查转写边界，`clip cut` 执行已确认的选段，`clip join` 拼接编码参数匹配的片段；参数以对应 `--help` 为准。
- 判断完整话题、金句和实际首尾切点；摘要的时间范围只作候选，不能直接当成最终切点。
- 用户未要求去静音时传 `--no-desilence`。音量阈值处理不负责判断重录或重复讲述。

## 验收与边界

核对实际片段的开头、结尾、独立完整性和音画同步。语义去重交给 `speech-video-dedup`，传播表达评价交给 `clip-quality-monitor`；不在本 Skill 复制它们的标准。组合任务由 Workflow 编排，单项任务无需交回 Workflow。
