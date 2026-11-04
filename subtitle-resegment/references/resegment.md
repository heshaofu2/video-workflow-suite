## 什么时候用

转写得到的 `transcript.srt` 是按 whisper 的 ~3 秒段切的，每条十几二十个字，
放到竖屏短视频里会**折成两行、字太多**。本能力把它重切成**每条约 10 字以内、一行一句**的短字幕。

## 为什么不能只用一种信息（三选一都不行）

| 只用… | 问题 |
|-------|------|
| 只按字数硬切 | 把词/短语从中间劈开（「向量召回」→「向量召」+「回」）；时间按字数均摊会漂移 |
| 只用词级时间戳 | whisperX 对中文常把**数字/百分比**等 token 对齐失败而**丢字**，文本不完整 |

**正解 = 三者合一：**
- **文本**以「完整 SRT」为准 → 不丢字（数字、百分比都在）
- **时间**用「词级时间戳」对齐 → 不漂移；whisperX 没对上的字（数字等）在相邻时间里插值
- **断句**用「语义分段」→ 断得通顺，不切词

> 补充：本片素材实测语速快、几乎无停顿（1058 字仅 6 处 >0.3s 间隔），且 whisper 对中文**不输出标点**。
> 所以「靠停顿/标点自动断句」信号很弱——**语义断句这一步必须由 agent（语言模型）来做**，这正是本流程的价值点。

## 操作流程（agent 主导）

### 前置：拿到完整的词级时间戳

`subtitle resegment` 的时间来自 whisperX 的 `transcript.json`（`segments[].words[]`）。
**注意 whisperX 有「尾部丢弃」的毛病**：VAD 可能吞掉结尾十几秒的语音，导致 json/srt 缺尾。
若发现缺尾（`transcript.srt` 末条时间 < 视频时长且尾部有说话声），先补转写尾部再合并：

```bash
# 1) 切尾部音频（从缺失点前几秒到结尾），单独转写拿词级时间戳
"$VIDEO_CLI" clip cut --input "$VIDEO" \
  --start <H:MM:SS起点> --end <H:MM:SS终点> \
  --name tail --output "$OUTDIR/tail-source" --no-desilence
"$VIDEO_CLI" transcript run "$OUTDIR/tail-source/tail.mp4" \
  --output-dir "$OUTDIR" --output-name tail --language zh --backend mlx
# 2) 把 tail.json 的 words 加上偏移量，与主 transcript.json 在接缝处合并成完整词流
#    （接缝：主体保留到最后一句正确内容的结束时刻，尾部取该时刻之后）
```

需要补转写时读取相邻的 [video-to-text](../../video-to-text/SKILL.md)。完整 SRT 与词级 JSON 都应覆盖相同内容。

### Step 1：产出语义分段 `segmented.txt`

agent 以 SRT 拼接的完整字幕正文为准，参考文字稿理解语义，再用 `|` 分段；时间标记与说话人标签不属于正文：
- 每段是一个**自然意群**（短语/小句），长度 **≤ 目标字数**（如 10）；
- **只插入 `|`，绝不改动、增删任何字符**（错别字也原样保留，校对是另一件事）；
- 英文专有名词（如 `embedding`）当作不可切的整体，必要时单独成段。

写完自检：`去掉所有竖线，必须逐字等于 SRT 拼接的完整正文`。脚本会强校验并报出首个差异位置。

### Step 2：跑重切脚本

```bash
"$VIDEO_CLI" subtitle resegment \
  --srt "$OUTDIR/transcript.srt" \
  --words "$OUTDIR/transcript.json" \
  --segmented "$OUTDIR/segmented.txt" \
  --max-chars 10 \
  --output   "$OUTDIR/transcript.fine.srt"
```

产出 `transcript.fine.srt`：每条 ≤N 字、语义断句、词级时间。导入剪映即可。

### 降级用法

- **没做语义分段**（省事版）：不传 `--segmented`，脚本按 `--max-chars` 纯按字数切（时间仍走词级对齐，但断句不保证通顺）。
- **没有词级 json**：不传 `--words`，时间退化为「原 cue 内按字均分」（会有零点几秒漂移）。

## 已知权衡

- **过短条**：语速快的地方单条可能短到 0.2–0.4s（如「去测」0.18s），闪得快。这是**真实语速**，
  时间是准的。若要更易读，可把过短条并入相邻条（牺牲一点断句精度），按需处理。
- **数字段**：数字/百分比是插值时间（whisperX 没对上），可能有零点几秒误差，二次剪辑可微调。

## 更省心的替代

若不追求外部 SRT，**剪映自带「文本 → 智能字幕 → 识别字幕」本质就是这套**
（词级对齐 + 中文分词 + 标点 + 字数上限），在剪映当前时间轴上直接生成、天然对齐、零导入问题。
whisper 这份可留作**文字稿**校对参考。
