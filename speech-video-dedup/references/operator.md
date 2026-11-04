## 工具选择与本地转写

统一读取 [转写与时间轴契约](../../video-shared/references/transcript-contract.md)，需要取得或补充转写时调用 [video-to-text](../../video-to-text/SKILL.md)。本文件仅维护去重方案、精确删段与核对页要求。

## 剪接方案格式

`dedup render` 的执行实现使用标准库、FFmpeg 和 FFprobe。只负责原顺序删段，不负责内容判断、移动原句或制作转写。

```json
{
  "source": "/absolute/path/input.mp4",
  "source_sha256": "用 inspect 返回的 sha256 填写",
  "cuts": [
    {"id": "restart", "start_frame": 150, "end_frame": 210, "reason": "删除未完成起句"}
  ],
  "protected_ranges": [
    {"start_frame": 90, "end_frame": 150, "reason": "第一遍独有的有效补充"}
  ]
}
```

这里是 30 帧源片的示意，数字不是固定剪点。帧区间均为半开区间 `[start_frame, end_frame)`，不是成片时间。以真实源片帧率换算；先用局部检查确定再写入方案，不把转写秒数机械四舍五入后直接剪。

`source` 可以相对方案文件定位。源片哈希必须来自本次核查，不能用文档示例值。`cuts` 不允许空列表、重复 id、重叠、越界、非整数帧或不留下任何画面的剪法；与保护区间相交会拒绝。相邻区间可以合并，但不要擅自改变已确认的区间边界。

## 命令

```bash
"$VIDEO_CLI" dedup inspect /absolute/path/input.mp4
"$VIDEO_CLI" dedup check /absolute/path/plan.json
"$VIDEO_CLI" dedup render /absolute/path/plan.json --output /absolute/path/edited.mp4
```

脚本会验证源片哈希，检查恒定帧率、零起点、一条视频和一条音频、正常朝向及 FFmpeg 可用性。它保留尺寸、帧率、采样率和声道数，按帧删视频、按对应采样点拼音频；接点仅做 5 毫秒的极短淡化。源片时长按实际视频帧数计算，尾部不足一帧的音频可能相应裁齐。

可变帧率、多音轨、旋转显示、非零音视频起点或特殊音频布局不应被偷偷转换。脚本会明确停止：改用支持真实时间线的剪辑工具，或在独立副本中做有记录的预处理，保护原片，再检查同步。不要为了套脚本无声地丢弃音轨或改变速度。

输出已存在时拒绝覆盖。渲染先写到独立的 `.partial.mp4`，验证帧数、音视频时长、尺寸和完整解码，再检查源片大小、修改时间与 SHA-256，成功后才交付目标文件。失败时保留中间文件供检查，不自动重试或覆盖它。`--output` 必须使用新的 `.mp4` 名称，不能是原片或方案文件。

导出结果旁边会生成 `<stem>.edit.json`，含保留区间、剪后接点和客观验证结果。检查报告不是主观听感验收；仍要按 SKILL.md 检查实际成片的声音和画面。

## 本地核对页

准备一个只用于呈现的 `review.json`，原片路径和每个已导出预览都可以相对该文件：

```json
{
  "source": "input.mp4",
  "items": [
    {
      "id": "A",
      "title": "同一概念的两次开场",
      "source_start": 4.0,
      "source_end": 14.0,
      "comparison": "第一遍没讲完，第二遍补全定义。",
      "proposal": "保留第一遍引入，接第二遍结论。",
      "unique_information": "第一遍的例子已单独保留。",
      "visual_notes": "课件相同，但需连续复核手势和鼠标。",
      "preview": "preview.mp4",
      "continuation_preview": "preview-with-next-section.mp4",
      "frames": ["before-after.jpg"]
    }
  ]
}
```

```bash
"$VIDEO_CLI" dedup review /absolute/path/review.json --output /absolute/path/review.html
```

核对页只引用本地原片、真实预览和对照图，默认不播放，不执行原片跳过模拟，不上传文件。没有导出预览时显示未生成，不能把空按钮写成验证已通过。`source_start/end` 为原片时间，浏览器定位精度不等于最终帧精度。

预览可从原片裁取候选周边，使用相同最终剪点导出独立文件；需要接后续时延长最后保留的连续原片。不要先剪成几个已经有淡出的短片再简单接上，造成原来没有的停顿或重复淡化。

## 有意义的验证

- 运行结构验证：`quick_validate.py <skill-folder>`。若当前 Python 无 YAML 依赖，使用现有带 PyYAML 的环境，不为了验证全局安装包。
- 用短测试视频验证真实剪接：帧数等于原片帧数减去删除帧数、相应片段的颜色/图案映射正确、音频仍有采样、源片哈希不变。
- 验证非法输入：重叠区间、删除保护内容、错误源片哈希、原片作为输出、已有输出均拒绝。
- 在真实任务中，核对导出文件本身的接点，不只复核导出前拼出的转写；画面可对比源帧与成片帧，但这种对应不能代替判断讲解与画面是否匹配。
- 候选、批准记录和最终方案分开保存。调整剪点后重做受影响的检查，无变化且已通过的检查无需机械重复。
