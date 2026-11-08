---
name: insight-extractor
description: 从已有转写稿或长文本提炼话题、核心观点、原话金句、争议点和行动项，保留来源定位。适用于内容梳理、视频选段前分析；不自动转写、剪辑或改写原话。
metadata:
  requires:
    skills: [video-shared]
---

## 共享前置

读取 [video-shared](../video-shared/SKILL.md)。视频转写 JSON 或带时间筛选的素材统一通过 `transcript read` 获取，纯文字可直接读取。只有媒体而没有正文时，按 [转写材料契约](../video-shared/references/transcript-contract.md) 引导用户取得文字稿；仅做文字分析不强制时间戳或特定转写工具。

## 职责

梳理原文讲了什么，交付带来源定位的 `insights.md`：话题、核心论点、原话金句、争议与行动项。按 [提炼流程](references/extraction.md) 组织摘要；需挑选素材时读取 [价值判断](references/extraction-guide.md)。

- 引用保留原话，摘要与引用分开；来源没有说话人或时间时不编造。
- 区分讲述者观点和已证实事实。行动项的负责人和时间仅取自原文。
- 长文本分段阅读时保持话题衔接，不把未读内容当成已覆盖。

## 与其他能力的边界

CLI 负责提供素材，不自动提炼观点。实际片段取舍与切点由 `video-clipper` 决定，传播表达诊断由 `clip-quality-monitor` 负责；本 Skill 提供候选信息，不重复进行最终切片验收和传播评级。
