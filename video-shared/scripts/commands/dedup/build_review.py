#!/usr/bin/env python3
"""Build a local review page that uses real pre-rendered previews."""

import argparse
import html
import json
import math
import os
from pathlib import Path
import sys
from urllib.parse import quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source_json, output = args.review.resolve(), args.output.resolve()
    try:
        if output.exists() or output.suffix.lower() != '.html':
            raise ValueError('请指定一个不存在的 .html 输出文件。')
        data = json.loads(source_json.read_text(encoding='utf-8'))
        source = (source_json.parent / data['source']).resolve()
        if not source.is_file() or not data.get('items'):
            raise ValueError('原视频和非空 items 列表必须存在。')
        refs = {source, source_json}

        def url(value):
            path = (source_json.parent / value).resolve()
            if not path.is_file():
                raise ValueError(f'引用文件不存在：{path}')
            refs.add(path)
            return html.escape(quote(os.path.relpath(path, output.parent), safe='/'), quote=True)

        cards = []
        source_url = url(data['source'])
        for index, item in enumerate(data['items']):
            a, b = item['source_start'], item['source_end']
            if not (type(a) in (int, float) and type(b) in (int, float)
                    and math.isfinite(a) and math.isfinite(b) and 0 <= a < b):
                raise ValueError('原片起止时间必须是有效的非负秒数。')
            title = html.escape(str(item.get('title', item.get('id', index + 1))))
            body = [f'<article><h2>{title}</h2><p>原片 {a:.2f}–{b:.2f} 秒</p>',
                    f'<video controls preload="none" src="{source_url}#t={a:.6f},{b:.6f}"></video>',
                    '<p class="hint">上方为原片定位，浏览器定位不代表最终剪点。</p>']
            for key, label in [('comparison', '重复与重录'), ('proposal', '建议接法'),
                               ('unique_information', '独有信息'), ('visual_notes', '画面核对')]:
                if item.get(key):
                    body.append(f'<p><b>{label}</b><br>{html.escape(str(item[key]))}</p>')
            for key, label in [('preview', '真实剪后预览'),
                               ('continuation_preview', '含后续内容的预览')]:
                if item.get(key):
                    href = url(item[key])
                    body.append(f'<h3>{label}</h3><video controls preload="none" src="{href}"></video>'
                                f'<p><a href="{href}">单独打开视频</a></p>')
                else:
                    body.append(f'<p class="hint">{label}：尚未生成</p>')
            for frame in item.get('frames', []):
                body.append(f'<img loading="lazy" src="{url(frame)}" alt="剪点画面对照">')
            cards.append(''.join(body) + '</article>')
        if output in refs:
            raise ValueError('不能将核对页写到输入或引用资源上。')
        page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>口播视频去重核对</title><style>
body{margin:0;background:#f3f2ed;color:#263a30;font:16px/1.7 system-ui,sans-serif}
main{max-width:960px;margin:auto;padding:28px}h1{font-size:29px}h2{font-size:23px}
article{background:white;border:1px solid #dce1d7;border-radius:14px;margin:22px 0;padding:24px}
video,img{width:100%;height:auto;border-radius:7px}video{background:#111;max-height:560px}
.hint{color:#69726b;font-size:14px}a{color:#275b3a}h3{font-size:18px}
</style><main><h1>口播视频去重核对</h1>
<p>对照原话、独有信息、画面和前后文，再判断剪接。此页面不修改或上传原视频。</p>
''' + ''.join(cards) + '</main></html>'
        with output.open('x', encoding='utf-8') as stream:
            stream.write(page)
        print(json.dumps({'output': str(output), 'items': len(cards),
                          'playback_tested': False}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'错误：{error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
