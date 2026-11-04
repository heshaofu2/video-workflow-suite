#!/usr/bin/env python3
"""Join compatible, already prepared clips without modifying the inputs."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', nargs='+', required=True, help='按播放顺序排列、编码参数匹配的片段')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    sources = [Path(value).expanduser().resolve() for value in args.input]
    output = Path(args.output).expanduser().resolve()
    if output.exists() or not all(path.is_file() for path in sources):
        raise ValueError('需要已有输入文件和不存在的输出路径')
    # Generated safe names avoid embedding user paths in FFmpeg's concat syntax.
    with tempfile.TemporaryDirectory(prefix='video-cli-join-') as folder:
        root = Path(folder)
        lines = []
        for index, source in enumerate(sources):
            name = f'clip-{index:04d}.media'
            (root / name).symlink_to(source)
            lines.append(f"file '{name}'")
        listing = root / 'concat.txt'
        listing.write_text('\n'.join(lines) + '\n')
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'concat', '-safe', '0',
                        '-i', str(listing), '-c', 'copy', '-n', str(output)], check=True)
    print(json.dumps({'output': str(output), 'inputs': [str(path) for path in sources]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
