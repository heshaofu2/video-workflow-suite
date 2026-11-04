#!/usr/bin/env python3
"""校正 whisperX 转写时间轴的线性漂移（常见于 VFR 可变帧率视频）。

whisperX 对 VFR 视频的长音频转写，时间戳会相对视频播放时间线性漂移
（开头几乎不差，越往后偏移越大）。本脚本对 transcript.json 的所有时间戳
乘一个系数，把时间轴整体对齐回视频真实时间。

用法:
  # 已知系数（real_video_time = transcript_time × factor）
  python3 fix-timeline-drift.py <transcript.json> --factor 1.0056

  # 用一个锚点自动算系数（transcript_sec:real_video_sec）
  python3 fix-timeline-drift.py <transcript.json> --anchor 5510:5541

原始文件备份为 <transcript>.raw.json（已存在则不覆盖）。
"""
import json
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", help="transcript.json 路径")
    ap.add_argument("--factor", type=float,
                    help="real_video_time = transcript_time × factor")
    ap.add_argument("--anchor",
                    help="transcript_sec:real_video_sec，用一个对齐锚点算系数")
    args = ap.parse_args()

    if args.anchor:
        ts, rs = args.anchor.split(":")
        factor = float(rs) / float(ts)
    elif args.factor:
        factor = args.factor
    else:
        sys.exit("需要 --factor 或 --anchor")

    with open(args.transcript, encoding="utf-8") as f:
        d = json.load(f)

    # 备份原始（仅首次）
    raw = args.transcript.rsplit(".json", 1)[0] + ".raw.json"
    if not os.path.exists(raw):
        with open(raw, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

    def scale(o):
        for k in ("start", "end"):
            v = o.get(k)
            if isinstance(v, (int, float)):
                o[k] = round(v * factor, 3)

    for seg in d.get("segments", []):
        scale(seg)
        for w in seg.get("words", []):
            scale(w)
    for w in d.get("word_segments", []):
        scale(w)

    with open(args.transcript, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    segs = d.get("segments", [])
    tail = f"{segs[-1].get('end'):.1f}s" if segs else "?"
    print(f"✅ 时间轴已校正 ×{factor:.5f}")
    print(f"   原始备份: {raw}")
    print(f"   校正后末段 end: {tail}（应接近视频时长）")


if __name__ == "__main__":
    main()
