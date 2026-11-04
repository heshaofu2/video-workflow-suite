#!/usr/bin/env python3
"""Inspect, check, and apply reviewed frame cuts to a simple local CFR video."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from fractions import Fraction
import re
import shutil
import subprocess
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def fingerprint(path):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            '源文件在读取期间发生变化。')
    return {'size': after.st_size, 'mtime_ns': after.st_mtime_ns,
            'sha256': digest.hexdigest()}


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_streams',
                           '-show_format', '-of', 'json', str(path)]))


def inspect(path):
    require(path.is_file(), f'源片不存在：{path}')
    return {'source': str(path), **fingerprint(path), 'media': probe(path)}


def eligible_media(path, media):
    streams = media['streams']
    videos = [x for x in streams if x['codec_type'] == 'video']
    audios = [x for x in streams if x['codec_type'] == 'audio']
    require(len(videos) == 1 and len(audios) == 1 and len(streams) == 2,
            '辅助脚本只支持一条视频、一条音频且无其他轨道；请使用合适的剪辑工具保留全部轨道。')
    video, audio = videos[0], audios[0]
    rate = Fraction(video.get('avg_frame_rate', '0/1'))
    require(rate > 0 and rate == Fraction(video.get('r_frame_rate', '0/1')),
            '帧率信息不符合原生恒定帧率要求；不要强行改变速度。')
    require(all(abs(float(x.get('start_time', 'nan'))) < 0.001 for x in streams),
            '音视频不是零起点；需使用可保留原始偏移的时间线工具。')
    rotations = [x.get('rotation', 0) for x in video.get('side_data_list', [])]
    rotations.append(video.get('tags', {}).get('rotate', 0))
    require(all(float(x) % 360 == 0 for x in rotations), '请先明确旋转显示的处理方式。')
    require(video.get('color_transfer') not in ('smpte2084', 'arib-std-b67'),
            'HDR 源片应使用可保留 HDR 的工作流。')
    require(video['width'] % 2 == 0 and video['height'] % 2 == 0,
            '当前 MP4 编码要求偶数宽高；不能静默裁切或缩放原片。')
    require(audio.get('channels') in (1, 2), '特殊声道布局需使用能保留布局的工作流。')
    # Matching rate declarations alone do not establish a constant frame timeline.
    raw = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
               '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'csv=p=0', str(path)])
    points = [float(line.split(',')[0]) for line in raw.splitlines() if line.strip()]
    require(len(points) >= 2, '视频帧时间不足。')
    require(all(math.isfinite(t) and abs(t - i / float(rate)) < 0.001
                for i, t in enumerate(points)),
            '实际帧时间不是从零开始的恒定帧率；改用支持真实时间线的剪辑工具。')
    if video.get('nb_frames', 'N/A') != 'N/A':
        require(int(video['nb_frames']) == len(points), '声明帧数与实际帧数不一致。')
    frame_count = len(points)
    duration = frame_count / float(rate)
    require(abs(float(audio['duration']) - duration) < max(0.1, 2 / float(rate)),
            '源片音视频时长差异较大，需先核对原始同步。')
    return video, audio, rate, frame_count


def interval(item, frame_count):
    a, b = item.get('start_frame'), item.get('end_frame')
    require(type(a) is int and type(b) is int and 0 <= a < b <= frame_count,
            f'非法帧区间：{item}')
    return a, b


def prepare(plan_path):
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    source = (plan_path.parent / plan['source']).resolve()
    state = inspect(source)
    expected_hash = plan.get('source_sha256', '')
    require(isinstance(expected_hash, str) and re.fullmatch('[0-9a-fA-F]{64}', expected_hash),
            '方案必须包含 inspect 返回的 source_sha256。')
    require(state['sha256'] == expected_hash.lower(), '源片 SHA-256 与方案不符，停止。')
    video, audio, rate, count = eligible_media(source, state['media'])
    cuts = plan.get('cuts')
    require(isinstance(cuts, list) and cuts, 'cuts 必须是非空列表。')
    cuts = [dict(x) for x in cuts]
    ids = [x.get('id') for x in cuts]
    require(all(isinstance(x, str) and x for x in ids) and len(set(ids)) == len(ids),
            '剪点 id 必须非空且唯一。')
    for cut in cuts:
        interval(cut, count)
    cuts.sort(key=lambda x: x['start_frame'])
    protected = [interval(x, count) for x in plan.get('protected_ranges', [])]
    keeps, last, output_frame = [], 0, 0
    for cut in cuts:
        a, b = interval(cut, count)
        require(a >= last, '删除区间重叠，请消解重复记录后再渲染。')
        require(not any(a < end and b > start for start, end in protected),
                f'剪点 {cut["id"]} 删除了保护内容。')
        if a > last:
            keeps.append([last, a])
            output_frame += a - last
        cut['output_join_frame'] = output_frame
        cut['output_join_seconds'] = output_frame / float(rate)
        last = b
    if last < count:
        keeps.append([last, count])
        output_frame += count - last
    require(output_frame > 0, '剪辑后没有保留画面。')
    return {'source': str(source), 'source_state': {k: state[k] for k in ('size', 'mtime_ns', 'sha256')},
            'fps': str(rate), 'source_frames': count, 'output_frames': output_frame,
            'output_duration': output_frame / float(rate), 'cuts': cuts, 'keep_ranges': keeps,
            'video': video, 'audio': audio}


def render(plan_path, output):
    output = output.resolve()
    source_plan = json.loads(plan_path.read_text(encoding='utf-8'))
    source = (plan_path.parent / source_plan['source']).resolve()
    require(output not in (source, plan_path.resolve()), '不能覆盖源片或方案文件。')
    require(output.suffix.lower() == '.mp4', '输出必须使用新的 .mp4 文件名。')
    partial = output.with_name(output.stem + '.partial.mp4')
    report = output.with_suffix('.edit.json')
    require(output.parent.is_dir(), '输出目录不存在。')
    require(not any(x.exists() for x in (output, partial, report)),
            '输出、报告或中间文件已存在，拒绝覆盖。')
    data = prepare(plan_path)
    rate, sample_rate = Fraction(data['fps']), int(data['audio']['sample_rate'])
    expr = '+'.join(f'gte(n,{x["start_frame"]})*lt(n,{x["end_frame"]})' for x in data['cuts'])
    filters = [f"[0:v]select='not({expr})',setpts=N/(({rate.numerator}/{rate.denominator})*TB)[v]"]
    keeps = data['keep_ranges']
    filters.append(f'[0:a]asplit={len(keeps)}' + ''.join(f'[s{i}]' for i in range(len(keeps))))
    for i, (a, b) in enumerate(keeps):
        duration = float(Fraction(b - a, 1) / rate)
        fade = min(0.005, duration / 4)
        start_sample = round(Fraction(a * sample_rate, 1) / rate)
        end_sample = round(Fraction(b * sample_rate, 1) / rate)
        filters.append(f'[s{i}]atrim=start_sample={start_sample}:end_sample={end_sample},'
                       f'asetpts=PTS-STARTPTS,afade=t=in:st=0:d={fade:.9f},'
                       f'afade=t=out:st={duration-fade:.9f}:d={fade:.9f}[a{i}]')
    filters.append(''.join(f'[a{i}]' for i in range(len(keeps))) + f'concat=n={len(keeps)}:v=0:a=1[a]')
    print('开始导出独立文件；原片只读。', flush=True)
    run(['ffmpeg', '-n', '-hide_banner', '-loglevel', 'warning', '-nostats', '-i', str(source),
         '-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[a]',
         '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
         '-r', str(rate), '-fps_mode', 'cfr', '-c:a', 'aac', '-b:a', '192k',
         '-ar', str(sample_rate), '-ac', str(data['audio']['channels']),
         '-map_metadata', '-1', '-movflags', '+faststart', str(partial)])
    out_media = probe(partial)
    video = next(x for x in out_media['streams'] if x['codec_type'] == 'video')
    audio = next(x for x in out_media['streams'] if x['codec_type'] == 'audio')
    require(int(video['nb_frames']) == data['output_frames'], '输出帧数不符合方案。')
    require((video['width'], video['height']) == (data['video']['width'], data['video']['height']),
            '输出尺寸发生变化。')
    require(Fraction(video['avg_frame_rate']) == rate, '输出帧率发生变化。')
    require(int(audio['sample_rate']) == sample_rate and audio['channels'] == data['audio']['channels'],
            '输出采样率或声道数发生变化。')
    require(abs(float(video['duration']) - data['output_duration']) < 0.01,
            '视频时长不符合方案。')
    require(abs(float(audio['duration']) - float(video['duration'])) < 0.05,
            '输出音视频时长差异超过 50 毫秒。')
    run(['ffmpeg', '-v', 'error', '-i', str(partial), '-f', 'null', '-'])
    require(fingerprint(source) == data['source_state'], '源片状态发生变化，保留中间文件并停止。')
    # link() does not replace a file created by another process while rendering.
    os.link(partial, output)
    partial.unlink()
    data.update({'output': str(output), 'output_size': output.stat().st_size,
                 'checks': {'full_decode': True, 'source_unchanged': True,
                            'frame_count': True, 'audio_video_duration': True,
                            'manual_listening': 'not performed by this script',
                            'semantic_visual_review': 'not performed by this script'}})
    with report.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    return {'output': str(output), 'duration': data['output_duration'], 'report': str(report)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('inspect').add_argument('source', type=Path)
    sub.add_parser('check').add_argument('plan', type=Path)
    rendering = sub.add_parser('render')
    rendering.add_argument('plan', type=Path)
    rendering.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        require(all(shutil.which(x) for x in ('ffmpeg', 'ffprobe')), '需要 ffmpeg 与 ffprobe。')
        if args.command == 'inspect':
            result = inspect(args.source.resolve())
        elif args.command == 'check':
            result = prepare(args.plan.resolve())
        else:
            result = render(args.plan.resolve(), args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError, ZeroDivisionError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        print(f'错误：{detail}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
