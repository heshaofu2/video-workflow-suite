#!/usr/bin/env python3
"""One local command surface for the video's deterministic operations."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading


ROOT = Path(__file__).resolve().parent / 'commands'
sys.path.insert(0, str(ROOT / 'transcript'))
from runtime import MODEL, load_runtime

COMMANDS = {
    ('transcript', 'run'): ('transcript/transcribe.py', (), '转写音视频并生成基础字幕'),
    ('transcript', 'segments'): ('transcript/transcribe_segments.sh', (), '分段转写，可恢复已完成段'),
    ('transcript', 'merge'): ('transcript/merge_segmented_transcripts.py', (), '按媒体时长合并分段转写'),
    ('transcript', 'fix-drift'): ('transcript/fix-timeline-drift.py', (), '在独立副本上校正线性时间偏移'),
    ('clip', 'cut'): ('clip/clip.sh', (), '按确认范围粗剪，可选去静音'),
    ('clip', 'boundary'): ('clip/clip-boundary.py', (), '检查转写中的选段边界'),
    ('clip', 'join'): ('clip/join.py', (), '按顺序拼接编码参数匹配的已准备片段'),
    ('dedup', 'inspect'): ('dedup/cut_video.py', ('inspect',), '检查原片规格和校验值'),
    ('dedup', 'check'): ('dedup/cut_video.py', ('check',), '校验去重方案和保护区间'),
    ('dedup', 'render'): ('dedup/cut_video.py', ('render',), '按已核对的方案渲染删段视频'),
    ('dedup', 'review'): ('dedup/build_review.py', (), '生成引用真实预览的本地核对页'),
    ('dedup', 'report'): ('dedup/build_report.py', (), '从方案与成片生成剪辑报告 HTML'),
    ('subtitle', 'resegment'): ('subtitle/resegment_subtitles.py', (), '把现有字幕重分为短句'),
}


class CLIError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CLIError('invalid_arguments', message)


def emit(data=None, error=None, warnings=None):
    result = {'ok': error is None}
    if error:
        result['error'] = {'code': error.code, 'message': str(error)}
        if error.details is not None:
            result['error']['details'] = error.details
    else:
        result['data'] = data
    if warnings:
        result['warnings'] = warnings
    print(json.dumps(result, ensure_ascii=False))


def option(args, flag, default=None):
    for index, value in enumerate(args):
        if value == flag:
            if index + 1 >= len(args):
                raise CLIError('invalid_arguments', f'{flag} 缺少值')
            return args[index + 1]
        if value.startswith(flag + '='):
            return value.split('=', 1)[1]
    return default


def positional(args, booleans=()):
    values = []
    index = 0
    while index < len(args):
        value = args[index]
        if value in booleans or '=' in value and value.startswith('--'):
            index += 1
        elif value.startswith('--'):
            index += 2
        elif value.startswith('-'):
            raise CLIError('invalid_arguments', '请先查看该命令的 --help')
        else:
            values.append(value)
            index += 1
    return values


def input_file(value):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise CLIError('input_missing', f'输入文件不存在：{path}')
    return path


def new_output(value):
    path = Path(value).expanduser().resolve()
    if path.exists():
        raise CLIError('output_exists', f'拒绝覆盖已有文件：{path}')
    return path


def clip_parser():
    parser = Parser(prog='video-cli clip cut', description='粗剪执行；选段由 Skill 判断。')
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True, help='输出目录')
    parser.add_argument('--clips', help='每行 开始|结束|名称 的已确认清单')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--name', default='clip')
    parser.add_argument('--noise', default='-30dB')
    parser.add_argument('--silence-dur', default='0.5')
    parser.add_argument('--no-desilence', action='store_true', help='保留原停顿')
    return parser


def drift_parser():
    parser = Parser(prog='video-cli transcript fix-drift')
    parser.add_argument('transcript')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--factor', type=float)
    group.add_argument('--anchor', help='转写秒数:原片秒数')
    parser.add_argument('--output', required=True, help='新 JSON；不修改输入')
    return parser


def time_value(value):
    try:
        fields = [float(x) for x in value.split(':')]
        if not 1 <= len(fields) <= 3 or any(not math.isfinite(x) or x < 0 for x in fields):
            raise ValueError
        if len(fields) > 1 and any(x >= 60 for x in fields[1:]):
            raise ValueError
        return sum(x * 60 ** i for i, x in enumerate(reversed(fields)))
    except ValueError:
        raise argparse.ArgumentTypeError('时间请用非负秒数或 H:MM:SS')


def read_parser():
    parser = Parser(prog='video-cli transcript read')
    parser.add_argument('--input', required=True, help='已有 JSON 或纯文字稿')
    parser.add_argument('--start', type=time_value)
    parser.add_argument('--end', type=time_value)
    return parser


def read_transcript(args):
    opts = read_parser().parse_args(args)
    path = input_file(opts.input)
    if opts.start is not None and opts.end is not None and opts.start >= opts.end:
        raise CLIError('invalid_range', '结束时间必须晚于开始时间')
    if path.suffix.lower() != '.json':
        if opts.start is not None or opts.end is not None:
            raise CLIError('timestamps_required', '按时间取段需要带 segments 的 JSON')
        return {'input': str(path), 'text': path.read_text(encoding='utf-8')}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('segments'), list):
        raise CLIError('invalid_transcript', 'JSON 必须含 segments 列表')
    selected = []
    for segment in data['segments']:
        if not isinstance(segment, dict):
            raise CLIError('invalid_transcript', '每个 segment 必须是对象')
        start, end = segment.get('start'), segment.get('end')
        if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in (start, end)) or end < start:
            raise CLIError('invalid_transcript', '段落缺少有效的 start/end 秒数')
        if opts.start is not None and end <= opts.start:
            continue
        if opts.end is not None and start >= opts.end:
            continue
        selected.append(segment)
    return {'input': str(path), 'requested_range': {'start': opts.start, 'end': opts.end},
            'segments': selected, 'text': '\n'.join(str(s.get('text', '')) for s in selected),
            'selection': '包含与请求区间相交的完整段落，不是逐字裁切'}


def preflight(key, args):
    """Return side effects to perform only after dry-run has been handled."""
    outputs, copies, manifests = [], [], []
    if key == ('clip', 'cut'):
        opts = clip_parser().parse_args(args)
        input_file(opts.input)
        if opts.clips:
            if opts.start or opts.end:
                raise CLIError('invalid_arguments', '--clips 与 --start/--end 不能同时使用')
            rows = []
            for line in input_file(opts.clips).read_text(encoding='utf-8').splitlines():
                if not line.strip() or line.startswith('#'):
                    continue
                fields = line.split('|')
                if len(fields) != 3 or not all(fields):
                    raise CLIError('invalid_clips', '每行必须为 开始|结束|名称')
                rows.append(fields)
        else:
            if not opts.start or not opts.end:
                raise CLIError('invalid_arguments', '需要 --clips，或 --start 和 --end')
            rows = [(opts.start, opts.end, opts.name)]
        if not rows:
            raise CLIError('invalid_clips', '切片清单为空')
        for start, end, name in rows:
            if not name.strip() or name in ('.', '..') or any(x in name for x in ('/', '\\', "'", '\n', '\r')):
                raise CLIError('invalid_clip_name', '片段名称不能包含路径、单引号或换行')
            try:
                if time_value(end) <= time_value(start):
                    raise CLIError('invalid_range', '结束时间必须晚于开始时间')
            except argparse.ArgumentTypeError as error:
                raise CLIError('invalid_range', str(error))
            outputs.append(new_output(Path(opts.output) / (name + '.mp4')))
        if len(outputs) != len(set(outputs)):
            raise CLIError('duplicate_output', '切片名称不能重复')
    elif key == ('transcript', 'run'):
        if option(args, '--backend', 'mlx') != 'mlx':
            raise CLIError('unsupported_backend', '现在统一使用 --backend mlx，不再使用 WhisperX 识别或服务回退')
        if option(args, '--model', MODEL) not in (MODEL, 'large-v3-turbo'):
            raise CLIError('unsupported_model', '现在统一使用 MLX Whisper large-v3-turbo')
        values = positional(args, ('--diarize', '--diarize-only'))
        if len(values) != 1:
            raise CLIError('invalid_arguments', '需要一个音视频输入；先查看 --help')
        source = input_file(values[0])
        directory = Path(option(args, '--output-dir', str(source.parent)))
        stem = option(args, '--output-name', source.stem)
        if Path(stem).name != stem or stem in ('.', '..'):
            raise CLIError('invalid_output_name', '输出名称不能含目录')
        outputs = [new_output(directory / (stem + suffix)) for suffix in ('.json', '.txt', '.srt', '.ass', '.audio.wav', '.audio.m4a')]
    elif key == ('transcript', 'segments'):
        config = load_runtime()
        if option(args, '--backend', 'mlx') != 'mlx' or option(args, '--model', MODEL) not in (MODEL, 'large-v3-turbo'):
            raise CLIError('unsupported_model', '分段识别也统一使用本地 MLX Whisper large-v3-turbo')
        values = positional(args)
        if len(values) != 1 or not option(args, '--output-dir'):
            raise CLIError('invalid_arguments', '需要输入文件和 --output-dir')
        source = input_file(values[0])
        directory = Path(option(args, '--output-dir')).expanduser().resolve()
        marker = directory / '.video-source.json'
        prefix = option(args, '--output-prefix', 'transcript')
        if not prefix or Path(prefix).name != prefix or prefix in ('.', '..'):
            raise CLIError('invalid_output_name', '输出前缀不能含目录')
        identity = {'source': str(source), 'size': source.stat().st_size, 'mtime_ns': source.stat().st_mtime_ns,
                    'prefix': prefix, 'minutes': option(args, '--segment-minutes', '30'),
                    'model': MODEL, 'language': option(args, '--language', 'zh'),
                    'backend': 'mlx', 'model_path': config['model_path']}
        if marker.exists():
            if json.loads(marker.read_text()) != identity:
                raise CLIError('source_mismatch', '续跑目录的源文件或转写参数已变化，请使用新目录')
        elif directory.exists() and any(directory.iterdir()):
            raise CLIError('unverified_resume', '现有目录缺少来源记录，不能自动复用；请使用新目录')
        else:
            manifests.append((marker, identity))
    elif key == ('transcript', 'merge'):
        if not option(args, '--segments-dir'):
            raise CLIError('invalid_arguments', '需要 --segments-dir')
        directory = Path(option(args, '--segments-dir', '')).expanduser()
        if not directory.is_dir() or not list(directory.glob('seg_*.json')):
            raise CLIError('input_missing', '需要包含 seg_*.json 的 --segments-dir')
        for flag in ('--output-json', '--output-txt'):
            value = option(args, flag)
            if not value:
                raise CLIError('invalid_arguments', f'需要 {flag}')
            outputs.append(new_output(value))
        if outputs[0] == outputs[1]:
            raise CLIError('duplicate_output', 'JSON 与 TXT 输出必须不同')
    elif key == ('transcript', 'fix-drift'):
        opts = drift_parser().parse_args(args)
        source = input_file(opts.transcript)
        if opts.factor is not None and (not math.isfinite(opts.factor) or opts.factor <= 0):
            raise CLIError('invalid_factor', '系数必须是正有限数')
        if opts.anchor:
            try:
                start, end = map(float, opts.anchor.split(':'))
                if any(not math.isfinite(x) or x <= 0 for x in (start, end)):
                    raise ValueError
            except ValueError:
                raise CLIError('invalid_anchor', '锚点必须为两个正有限秒数：转写秒数:原片秒数')
        target = new_output(opts.output)
        if target.suffix.lower() != '.json':
            raise CLIError('invalid_output', '输出必须是新的 .json 文件')
        outputs = [target, new_output(str(target).rsplit('.json', 1)[0] + '.raw.json')]
        copies.append((source, target))
        args = [str(target)] + (['--factor', str(opts.factor)] if opts.factor is not None else ['--anchor', opts.anchor])
    elif key in (('subtitle', 'resegment'), ('dedup', 'review'), ('dedup', 'report'), ('dedup', 'render'), ('clip', 'join')):
        value = option(args, '--output')
        if not value:
            raise CLIError('invalid_arguments', '需要 --output')
        outputs.append(new_output(value))
        if key == ('subtitle', 'resegment'):
            for flag in ('--srt', '--words', '--segmented'):
                value = option(args, flag)
                if value:
                    input_file(value)
    return args, outputs, copies, manifests


def run_child(argv, env=None):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    stdout, stderr = [], []

    def collect(stream, lines):
        for line in stream:
            lines.append(line)
            sys.stderr.write(line)
            sys.stderr.flush()

    threads = [threading.Thread(target=collect, args=(process.stdout, stdout)),
               threading.Thread(target=collect, args=(process.stderr, stderr))]
    for thread in threads:
        thread.start()
    try:
        status = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        raise
    finally:
        for thread in threads:
            thread.join()
    out, err = ''.join(stdout), ''.join(stderr)
    if status:
        raise CLIError('command_failed', '底层命令执行失败', {'exit_code': status, 'stderr': err, 'stdout': out})
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {'stdout': out, 'stderr': err}


def capabilities():
    return {'commands': [{'command': ' '.join(key), 'description': value[2], 'mode': 'local'}
                         for key, value in COMMANDS.items()] + [
                             {'command': 'transcript read', 'description': '读取已有转写或按时间提取完整段落', 'mode': 'local'}],
            'agent_skills': ['insight-extractor', 'clip-quality-monitor', 'video-workflow'],
            'transcription_engine': 'mlx_whisper', 'transcription_model': MODEL,
            'chatcut': '通过官方 Skill 和 MCP 使用，不伪装成本 CLI 的本地功能'}


def help_text(domain=None):
    print('video-cli [--dry-run] <领域> <命令> [参数]\n')
    print('doctor：检查本地依赖；capabilities：列出实际能力；<领域> <命令> --help：查看真实用法。\n')
    for item in capabilities()['commands']:
        if domain is None or item['command'].startswith(domain + ' '):
            print(f"  {item['command']:<25} {item['description']}")
    print('\n业务执行的 stdout 为 JSON 信封，运行日志在 stderr；--help 输出普通帮助文字。')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = bool(args and args[0] == '--dry-run')
    if dry_run:
        args.pop(0)
    try:
        if not args or args == ['--help'] or args == ['-h']:
            help_text()
            return 0
        if args == ['capabilities']:
            emit(capabilities())
            return 0
        if args == ['doctor']:
            config = load_runtime()
            check = subprocess.run([config['python'], '-c',
                                    'import importlib.util; print(int(importlib.util.find_spec("mlx_whisper") is not None))'],
                                   capture_output=True, text=True, check=True)
            emit({'python': sys.executable, 'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe'),
                  'transcription_python': config['python'], 'mlx_whisper': check.stdout.strip() == '1',
                  'transcription_engine': 'mlx_whisper', 'transcription_model': MODEL,
                  'model_path': config['model_path'],
                  'scripts_present': all((ROOT / value[0]).is_file() for value in COMMANDS.values())})
            return 0
        domains = {key[0] for key in COMMANDS}
        if args[0] in domains and (len(args) == 1 or args[1:] in (['--help'], ['-h'])):
            help_text(args[0])
            return 0
        key = tuple(args[:2])
        tail = args[2:]
        if key == ('transcript', 'read'):
            if dry_run:
                opts = read_parser().parse_args(tail)
                emit({'dry_run': True, 'command': 'transcript read', 'input': opts.input})
            else:
                emit(read_transcript(tail))
            return 0
        if key not in COMMANDS:
            raise CLIError('unknown_command', '未知命令；请运行 video-cli --help 或 capabilities')
        script, prefix, _ = COMMANDS[key]
        if '--help' in tail or '-h' in tail:
            if key == ('clip', 'cut'):
                clip_parser().print_help()
            elif key == ('transcript', 'fix-drift'):
                drift_parser().print_help()
            else:
                help_args = ['__help__', '--help'] if script.endswith('transcribe_segments.sh') else ['--help']
                subprocess.run((['bash'] if script.endswith('.sh') else [sys.executable]) + [str(ROOT / script), *prefix, *help_args], check=True)
            return 0
        flags = [value.split('=', 1)[0] for value in tail if value.startswith('--')]
        if len(flags) != len(set(flags)):
            raise CLIError('duplicate_flag', '不能重复传入同一选项，以免预检与实际执行参数不一致')
        tail, outputs, copies, manifests = preflight(key, tail)
        python, env = sys.executable, None
        if key in (('transcript', 'run'), ('transcript', 'segments')):
            config = load_runtime()
            if '--diarize-only' in tail and not config.get('diarization_python'):
                raise CLIError('optional_dependency_missing', '未登记可选说话人环境；普通 MLX 转写不受影响')
            python = config['diarization_python'] if '--diarize-only' in tail else config['python']
            env = {**os.environ, 'VIDEO_TRANSCRIPT_PYTHON': config['python'],
                   'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'}
        command = (['bash'] if script.endswith('.sh') else [python]) + [str(ROOT / script), *prefix, *tail]
        if dry_run:
            emit({'dry_run': True, 'command': ' '.join(key), 'argv': command,
                  'outputs': [str(path) for path in outputs],
                  'copies': [{'source': str(a), 'output': str(b)} for a, b in copies],
                  'resume_records': [str(path) for path, _ in manifests]})
            return 0
        for path in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
        for source, target in copies:
            with target.open('xb') as stream, source.open('rb') as original:
                shutil.copyfileobj(original, stream)
        for path, data in manifests:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
        result = run_child(command, env=env)
        emit({'command': ' '.join(key), 'result': result,
              'outputs': [str(path) for path in outputs if path.is_file()]})
        return 0
    except CLIError as error:
        emit(error=error)
        return 1
    except (OSError, ValueError, TypeError, subprocess.CalledProcessError) as error:
        emit(error=CLIError('operation_failed', str(error)))
        return 1
    except KeyboardInterrupt:
        emit(error=CLIError('interrupted', '任务已中断；请检查日志和已生成产物'))
        return 130


if __name__ == '__main__':
    sys.exit(main())
