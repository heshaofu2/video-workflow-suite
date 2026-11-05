#!/usr/bin/env python3
"""Build a fixed HTML edit report from a dedup plan and its rendered output."""

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


def ffprobe(path, entries):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=' + ','.join(entries),
         '-of', 'json', str(path)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f'ffprobe 无法读取：{path}')
    return json.loads(result.stdout).get('format', {})


def frame_rate(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=r_frame_rate,nb_frames,width,height,codec_name',
         '-of', 'json', str(path)],
        capture_output=True, text=True)
    stream = json.loads(result.stdout)['streams'][0]
    num, den = stream['r_frame_rate'].split('/')
    return float(num) / float(den), stream


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def fmt(t):
    m, s = divmod(int(t), 60)
    return f'{m:02d}:{s:05.2f}'


def timeline_svg(keeps, total, label_a, label_b):
    width, height = 1000, 34

    def track(items, span, label, color):
        parts = [f'<text x="0" y="{height // 2 + 5}" font-size="13" '
                 f'fill="#334155" font-family="PingFang SC">{html.escape(label)}</text>']
        x0, wd = 88, width - 88
        for start, end in items:
            x = x0 + start / span * wd
            w = max((end - start) / span * wd, 1.2)
            parts.append(f'<rect x="{x:.1f}" y="4" width="{w:.1f}" height="{height - 8}" '
                         f'rx="3" fill="{color}" opacity="0.85"/>')
        return ''.join(parts)

    kept_total = sum(e - s for s, e in keeps)
    return (f'<svg viewBox="0 0 {width} {height * 2 + 14}" width="100%" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<g transform="translate(0,0)">{track(keeps, total, label_a, "#0ea5e9")}</g>'
            f'<g transform="translate(0,{height + 14})">'
            f'{track([[0, kept_total]], kept_total, label_b, "#0ea5e9")}</g></svg>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path, help='去重方案 plan.json')
    parser.add_argument('--edited', type=Path, required=True, help='渲染后的成片')
    parser.add_argument('--fps', type=float, help='原片帧率（默认从成片读取）')
    parser.add_argument('--review', type=Path, help='可选 review.json，提供每个剪口的类型与理由')
    parser.add_argument('--output', type=Path, required=True, help='报告 .html 输出路径')
    args = parser.parse_args()

    plan_path, output = args.plan.resolve(), args.output.resolve()
    edited = args.edited.resolve()
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    source = (plan_path.parent / plan['source']).resolve()
    if not source.is_file():
        raise ValueError(f'原片不存在：{source}')
    if not edited.is_file():
        raise ValueError(f'成片不存在：{edited}')

    fps = args.fps or frame_rate(edited)[0]
    src_fmt = ffprobe(source, ['duration', 'size'])
    total = float(src_fmt['duration'])
    src_size = int(src_fmt['size'])

    cuts = sorted(
        ((c['start_frame'] / fps, c['end_frame'] / fps, c.get('id', f'cut{i:02d}'))
         for i, c in enumerate(plan['cuts'])), key=lambda x: x[0])
    removed = sum(e - s for s, e, _ in cuts)
    keeps, prev = [], 0.0
    for s, e, _ in cuts:
        if s - prev > 0.05:
            keeps.append([prev, s])
        prev = e
    if total - prev > 0.05:
        keeps.append([prev, total])
    kept = sum(e - s for s, e in keeps)

    reasons = {}
    if args.review:
        review = json.loads(args.review.resolve().read_text(encoding='utf-8'))
        for item in review.get('items', []):
            title = str(item.get('kind') or item.get('title', '')).split('（')[0].strip()
            reasons[item.get('id')] = (title,
                                       item.get('why') or item.get('proposal', ''))

    current = sha256(source)
    intact = not plan.get('source_sha256') or current == plan['source_sha256']

    edited_fmt = ffprobe(edited, ['duration', 'size'])
    edited_dur, edited_size = float(edited_fmt['duration']), int(edited_fmt['size'])
    fps_stream, stream = frame_rate(edited)
    expect_frames = round(kept * fps)
    actual_frames = int(stream.get('nb_frames') or 0)
    frames_ok = not actual_frames or abs(actual_frames - expect_frames) <= max(3, fps // 5)
    duration_ok = abs(edited_dur - kept) <= 0.5

    rows = []
    for s, e, cid in cuts:
        kind, why = reasons.get(cid, ('剪除段', '—'))
        rows.append(f'<tr><td class="t">{cid}</td><td>{fmt(s)}–{fmt(e)}</td>'
                    f'<td>{e - s:.1f}s</td><td>{html.escape(str(kind))}</td>'
                    f'<td>{html.escape(str(why))}</td></tr>')
    protected = ''.join(
        f'<li>{fmt(p["start_frame"] / fps)}–{fmt(p["end_frame"] / fps)}：'
        f'{html.escape(str(p.get("reason", "")))}</li>'
        for p in plan.get('protected_ranges', []))

    page = f'''<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>口播剪辑报告</title>
<style>
:root{{--ink:#0f172a;--sub:#64748b;--line:#e2e8f0;--bg:#f8fafc}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);line-height:1.7}}
.wrap{{max-width:960px;margin:32px auto;padding:0 20px}}
h1{{font-size:24px;margin-bottom:4px}}
.meta{{color:var(--sub);font-size:13px;margin-bottom:24px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.card .v{{font-size:22px;font-weight:700}}.card .l{{font-size:12px;color:var(--sub);margin-top:2px}}
.card .d{{font-size:12px;font-weight:600;margin-top:2px}}.up{{color:#dc2626}}.ok{{color:#059669}}
.sec{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:20px}}
.sec h2{{font-size:16px;margin-bottom:10px}}.sec h2 .n{{color:#0ea5e9;margin-right:6px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--sub);font-weight:600;font-size:12px}}
td.t{{font-family:ui-monospace,Menlo,monospace;white-space:nowrap;color:#0369a1}}
.note{{font-size:12px;color:var(--sub);margin-top:10px}}
ul{{padding-left:18px;font-size:13px}}li{{margin:4px 0}}
.badge{{display:inline-block;font-size:11px;padding:2px 10px;border-radius:999px;font-weight:600}}
.okb{{background:#dcfce7;color:#166534}}.badb{{background:#fee2e2;color:#991b1b}}
</style></head><body><div class="wrap">
<h1>口播剪辑报告</h1>
<div class="meta">源片：{html.escape(str(source))} · 方案：{html.escape(plan_path.name)} ·
原片保留状态：<span class="badge {'okb' if intact else 'badb'}">
{'未改动（SHA-256 一致）' if intact else '⚠ 校验值变化，请核查'}</span></div>
<div class="cards">
<div class="card"><div class="v">{fmt(total)}</div><div class="l">原片时长</div></div>
<div class="card"><div class="v">{fmt(edited_dur)}</div><div class="l">成片时长</div>
<div class="d ok">保留 {100 * kept / total:.1f}%</div></div>
<div class="card"><div class="v">{fmt(removed).replace(':', ':')}&nbsp;</div>
<div class="l">剪除时长</div><div class="d up">{len(cuts)} 个剪口</div></div>
<div class="card"><div class="v">{edited_size >> 20} MB</div><div class="l">成片体积</div>
<div class="d">原片 {src_size >> 20} MB</div></div>
</div>
<div class="sec"><h2><span class="n">▍</span>时间轴对照（蓝=保留）</h2>
{timeline_svg(keeps, total, f'原片 {fmt(total)}', f'成片 {fmt(edited_dur)}')}</div>
<div class="sec"><h2><span class="n">▍</span>剪口明细</h2>
<table><tr><th>编号</th><th>原片时间</th><th>时长</th><th>类型</th><th>处理依据</th></tr>
{''.join(rows)}</table>
<div class="note">明细中的类型与依据来自 review.json（如有），缺失时显示默认占位。</div></div>
{f'<div class="sec"><h2><span class="n">▍</span>保护区间（不可剪除的独有内容）</h2><ul>{protected}</ul></div>' if protected else ''}
<div class="sec"><h2><span class="n">▍</span>验证结果</h2><ul>
<li>原片 SHA-256：{'一致，未写入原文件' if intact else '与方案记录不一致'}<br>
<code style="font-size:11px">{current[:16]}…</code></li>
<li>成片时长 {edited_dur:.2f}s vs 方案保留 {kept:.2f}s：
{'一致（±0.5s 内）' if duration_ok else '⚠ 偏差超过 0.5s'}</li>
<li>帧数 {actual_frames or '未知'} vs 预期 {expect_frames}（{fps:g}fps）：
{'一致' if frames_ok else '⚠ 不一致'}</li>
<li>成片规格：{stream.get('width')}×{stream.get('height')} · {stream.get('codec_name')} ·
{fps_stream:g}fps</li>
<li>未覆盖项：接点主观听感需人工试看确认；本报告仅含客观技术校验。</li>
</ul></div>
</div></body></html>'''
    output.write_text(page, encoding='utf-8')
    print(f'报告已生成：{output}')
    print(f'剪口 {len(cuts)} 个 · 剪除 {removed:.1f}s · 保留 {100 * kept / total:.1f}% · '
          f'原片校验{"通过" if intact else "异常"}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        print(f'错误：{error}', file=sys.stderr)
        sys.exit(2)
