#!/usr/bin/env python3
"""
clip-boundary.py — 切片边界完整性检查

在将时间段写入 clips.list 之前，检查开头/结尾是否切在逻辑完整的位置。

功能:
1. 提取边界处的实际文字（opening/closing + 前后上下文）
2. 启发式检测边界风险（反问未回答、论点跨说话人、连词开头等）
3. 可选：自动建议调整后的端点

用法:
  # 检查单个时间段
  python3 clip-boundary.py --transcript transcript.json --start 0:44:52 --end 0:49:06

  # 带模式 + 自动建议
  python3 clip-boundary.py --transcript transcript.json --start 0:44:52 --end 0:49:06 --mode opinion --suggest

  # 批量检查 clips.list
  python3 clip-boundary.py --transcript transcript.json --clips clips.list

输出: JSON 到 stdout，警告信息到 stderr
"""

import json
import sys
import re
import argparse


# ── 启发式规则的关键词 ──

# 反问/设问结尾标志
QUESTION_PATTERNS = re.compile(r'(什么|吗|呢|谁|哪|怎么|为什么|是不是|有没有|能不能|对不对|好不好)\s*[？?]?\s*$')

# 下一句是回应的标志
RESPONSE_STARTERS = re.compile(r'^(那|那那|对|没错|是的|是这样|肯定|当然|不是|不行|不对|嗯|可是|但是)')

# 连词开头（暗示进入了论点中间）
CONJUNCTION_STARTERS = re.compile(r'^(但是|所以|因为|而且|然后|不过|那么|因此|否则|虽然)')

# 安全结尾标志（陈述完成）
SAFE_ENDING_PATTERNS = re.compile(r'(的|了|吧|嘛|啊|呀|哦|行|好|对|没错|是的|就这样|就是这样|就好了|可以了|没问题)\s*$')

# 停顿阈值：两个 segment 之间间隔超过此值视为自然断点
PAUSE_GAP_THRESHOLD = 1.5


def parse_time(t: str) -> float:
    """将 H:MM:SS 或 MM:SS 格式转换为秒数"""
    parts = t.strip().split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(t)


def fmt_time(seconds: float) -> str:
    """秒数转 H:MM:SS 格式"""
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    return f"{h}:{m:02d}:{s:02d}"


def load_transcript(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def get_segments_in_range(segments: list, start: float, end: float) -> list:
    """获取时间范围内的 segments"""
    return [s for s in segments if s['end'] > start and s['start'] < end]


def check_end_boundary(segments: list, end_sec: float, after_segs: list, mode: str) -> list:
    """检查结尾边界的风险"""
    warnings = []

    if not segments:
        return warnings

    last_seg = segments[-1]
    last_text = last_seg['text'].strip()
    last_speaker = last_seg.get('speaker', '')

    # 1. 反问/设问结尾，下一句是回答
    if QUESTION_PATTERNS.search(last_text):
        warnings.append(f"END-反问: 结尾含疑问句式，可能需要包含回答")
        if after_segs and RESPONSE_STARTERS.match(after_segs[0]['text'].strip()):
            warnings.append(f"END-回应缺失: 下一句是直接回应 → 强烈建议延伸")

    # 2. 下一句是同一说话人继续（话没说完）
    if after_segs:
        next_seg = after_segs[0]
        next_speaker = next_seg.get('speaker', '')
        next_text = next_seg['text'].strip()
        gap = next_seg['start'] - last_seg['end']

        if next_speaker == last_speaker and gap < PAUSE_GAP_THRESHOLD:
            warnings.append(f"END-续说: 同一说话人紧接着继续说（间隔{gap:.1f}s）→ 可能话没说完")

        # 3. 下一句以连词开头（论点延续）
        if CONJUNCTION_STARTERS.match(next_text):
            warnings.append(f"END-连词续接: 下一句以'{next_text[:4]}'开头 → 论点可能未完")

        # 4. 说话人 A 抛出论点，说话人 B 紧跟回应
        if next_speaker != last_speaker and gap < PAUSE_GAP_THRESHOLD:
            if RESPONSE_STARTERS.match(next_text):
                warnings.append(f"END-对话对: 说话人切换且紧跟回应 → 建议包含回应")

    # 5. 没有安全结尾标志
    if not SAFE_ENDING_PATTERNS.search(last_text):
        warnings.append(f"END-无收束: 结尾缺少陈述完成标志词")

    # 6. opinion 模式额外检查：是否有明确结论
    if mode == 'opinion' and not any(kw in last_text for kw in ['所以', '因此', '总之', '关键是', '核心是', '不行', '没问题', '就是这样']):
        if not SAFE_ENDING_PATTERNS.search(last_text):
            warnings.append(f"END-无结论: opinion 模式下结尾缺少结论性表述")

    return warnings


def check_start_boundary(segments: list, before_segs: list) -> list:
    """检查开头边界的风险"""
    warnings = []

    if not segments:
        return warnings

    first_seg = segments[0]
    first_text = first_seg['text'].strip()

    # 1. 连词开头（进入了论点中间）
    if CONJUNCTION_STARTERS.match(first_text):
        warnings.append(f"START-连词: 以'{first_text[:4]}'开头 → 观众缺少前文语境")

    # 2. 代词开头无先行词
    if re.match(r'^(他|她|它|这个|那个|这种|那种)', first_text):
        warnings.append(f"START-代词: 以'{first_text[:3]}'开头 → 观众不知道指代什么")

    return warnings


def suggest_end(segments_all: list, end_sec: float, max_extension: float = 60) -> dict | None:
    """建议调整后的结束时间"""
    # 找 end_sec 之后的 segments
    after = [s for s in segments_all if s['start'] >= end_sec]

    if not after:
        return None

    best_cut = None
    for i, seg in enumerate(after):
        if seg['start'] - end_sec > max_extension:
            break

        text = seg['text'].strip()

        # 安全切点条件
        is_safe = False
        reason = ""

        # 条件1: 有结论性表述
        if SAFE_ENDING_PATTERNS.search(text):
            is_safe = True
            reason = f"结尾有收束词"

        # 条件2: 后面有大停顿
        if i + 1 < len(after):
            gap = after[i + 1]['start'] - seg['end']
            if gap > PAUSE_GAP_THRESHOLD:
                is_safe = True
                reason = f"后接{gap:.1f}s停顿（自然断点）"

        # 条件3: 后面换了话题/说话人且不是回应
        if i + 1 < len(after):
            next_seg = after[i + 1]
            next_text = next_seg['text'].strip()
            if not RESPONSE_STARTERS.match(next_text) and not CONJUNCTION_STARTERS.match(next_text):
                if seg.get('speaker', '') != next_seg.get('speaker', ''):
                    is_safe = True
                    reason = f"说话人切换且非回应（话题转换）"

        if is_safe:
            best_cut = {
                "suggested_end": fmt_time(seg['end'] + 1),
                "suggested_end_sec": round(seg['end'] + 1, 2),
                "reason": reason,
                "closing_text_preview": text[:60],
                "extension_seconds": round(seg['end'] - end_sec, 1),
            }
            break  # 取最近的安全切点

    return best_cut


def check_one(transcript: dict, start_str: str, end_str: str, mode: str = "opinion",
              context_sec: float = 30, do_suggest: bool = False, name: str = "") -> dict:
    """检查一个时间段的边界完整性"""
    start_sec = parse_time(start_str)
    end_sec = parse_time(end_str)

    segments = transcript.get('segments', [])

    # 范围内的 segments
    in_range = get_segments_in_range(segments, start_sec, end_sec)

    # 前后上下文
    before_segs = [s for s in segments if s['end'] <= start_sec and s['end'] > start_sec - context_sec]
    after_segs = [s for s in segments if s['start'] >= end_sec and s['start'] < end_sec + context_sec]

    # 提取文本
    opening_text = ""
    closing_text = ""
    if in_range:
        opening_text = " ".join(s['text'].strip() for s in in_range[:2])[:120]
        closing_text = " ".join(s['text'].strip() for s in in_range[-2:])[-120:]

    # 检查
    warnings = []
    warnings.extend(check_start_boundary(in_range, before_segs))
    warnings.extend(check_end_boundary(in_range, end_sec, after_segs, mode))

    # 构建完整的逐段文字列表（含前后上下文）
    all_display_segs = []
    for s in before_segs[-2:]:
        all_display_segs.append({**s, "_zone": "before"})
    for s in in_range:
        all_display_segs.append({**s, "_zone": "in"})
    for s in after_segs[:3]:
        all_display_segs.append({**s, "_zone": "after"})

    timeline = []
    for s in all_display_segs:
        zone = s["_zone"]
        t = fmt_time(s['start'])
        speaker = s.get('speaker', '?')
        text = s['text'].strip()
        timeline.append({
            "time": t,
            "speaker": speaker,
            "text": text,
            "zone": zone,
        })

    result = {
        "name": name,
        "proposed": {"start": start_str, "end": end_str},
        "mode": mode,
        "segments_count": len(in_range),
        "opening_text": opening_text,
        "closing_text": closing_text,
        "timeline": timeline,
        "after_context": [
            {"time": fmt_time(s['start']), "speaker": s.get('speaker', '?'), "text": s['text'].strip()[:80]}
            for s in after_segs[:3]
        ],
        "warnings": warnings,
        "warning_count": len(warnings),
    }

    # 自动建议
    if do_suggest and warnings:
        suggestion = suggest_end(segments, end_sec)
        if suggestion:
            result["suggestion"] = suggestion

    return result


def print_visual(result: dict):
    """可视化输出一个 clip 的边界检查结果"""
    name = result.get('name', '')
    proposed = result['proposed']
    header = f"{'─' * 60}"

    print(header)
    if name:
        print(f"📎 {name}")
    print(f"   {proposed['start']} → {proposed['end']}  [{result['mode']}]  ({result['segments_count']} segments)")
    print(header)

    # 逐段文字，用不同前缀区分区域
    zone_markers = {
        "before": ("  ░", "（上文）"),
        "in":     ("  █", ""),
        "after":  ("  ░", "（下文）"),
    }
    prev_zone = None
    for seg in result.get('timeline', []):
        zone = seg['zone']
        marker, label = zone_markers[zone]

        # 切入/切出分隔线
        if prev_zone != zone:
            if prev_zone == "before" and zone == "in":
                print(f"  {'─' * 15} ✂️  切入点 {proposed['start']} {'─' * 15}")
            elif prev_zone == "in" and zone == "after":
                print(f"  {'─' * 15} ✂️  切出点 {proposed['end']} {'─' * 15}")
            prev_zone = zone

        speaker = seg.get('speaker', '?')
        # 简化 speaker 名称
        sp_short = speaker.replace('SPEAKER_', 'S') if speaker.startswith('SPEAKER_') else speaker
        text = seg['text']

        if label:
            print(f"{marker} [{seg['time']}] {sp_short}: {text}  {label}")
        else:
            print(f"{marker} [{seg['time']}] {sp_short}: {text}")

    print()

    # 警告
    if result['warnings']:
        print(f"  ⚠️  {result['warning_count']} 个风险:")
        for w in result['warnings']:
            print(f"     • {w}")
    else:
        print(f"  ✅ 边界检查通过")

    # 建议
    if 'suggestion' in result:
        s = result['suggestion']
        print(f"\n  💡 建议结束点: {s['suggested_end']} (+{s['extension_seconds']}s)")
        print(f"     原因: {s['reason']}")
        print(f"     结尾预览: ...{s['closing_text_preview']}")

    print()


def main():
    parser = argparse.ArgumentParser(description="切片边界完整性检查")
    parser.add_argument('--transcript', required=True, help='transcript.json 路径')
    parser.add_argument('--start', help='起始时间 (H:MM:SS 或 MM:SS)')
    parser.add_argument('--end', help='结束时间')
    parser.add_argument('--mode', default='opinion', choices=['opinion', 'punchline'])
    parser.add_argument('--context', type=float, default=30, help='上下文秒数')
    parser.add_argument('--suggest', action='store_true', help='自动建议调整端点')
    parser.add_argument('--clips', help='批量检查 clips.list')
    parser.add_argument('--name', default='', help='clip 名称')
    parser.add_argument('--json', action='store_true', help='输出 JSON 格式（默认可视化）')
    args = parser.parse_args()

    transcript = load_transcript(args.transcript)

    results = []

    if args.clips:
        with open(args.clips) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('|')
                if len(parts) < 3:
                    continue
                start, end, name = parts[0], parts[1], parts[2]
                r = check_one(transcript, start, end, args.mode, args.context, args.suggest, name)
                results.append(r)
    elif args.start and args.end:
        r = check_one(transcript, args.start, args.end, args.mode, args.context, args.suggest, args.name)
        results.append(r)
    else:
        parser.error("需要 --start/--end 或 --clips")

    if args.json:
        output = results[0] if len(results) == 1 else results
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print_visual(r)

    # stderr 汇总
    total_warnings = sum(r['warning_count'] for r in results)
    if total_warnings:
        print(f"⚠️ 共 {total_warnings} 个边界风险需要确认", file=sys.stderr)
    else:
        print("✅ 所有边界检查通过", file=sys.stderr)


if __name__ == '__main__':
    main()
