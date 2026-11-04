#!/usr/bin/env python3
r"""
把一份完整字幕重切成「短句字幕」——每条 ≤N 字、断在语义处、时间用词级时间戳精确对齐。

设计动机（为什么不能只用其中一种信息）：
- 只按字数硬切：会把词/短语从中间劈开，且时间按字数均摊会漂移。
- 只用词级时间戳：whisperX 对中文常把数字/百分比等 token 对齐失败而丢字，文本不完整。
- 正解 = 文本以「完整 SRT」为准（不丢字）+ 时间用「词级时间戳」对齐（不漂移）+
         断句用「agent 产出的语义分段」（断得通顺）。

用法：
  python3 resegment_subtitles.py \
    --srt transcript.srt \            # 权威完整文本（字数为准）
    --words transcript.json \         # whisperX 输出，取词级时间戳（可选，缺省用 cue 内均分）
    --segmented segmented.txt \       # agent 产出的 |-分隔语义分段（可选，缺省按字数切）
    --output transcript.fine.srt \
    --max-chars 10

segmented.txt 约定：把 --srt 的完整文本用 `|` 分段，**只插入竖线、不改动任何字符**；
本脚本会强校验「去掉竖线 == 完整文本」，不一致会报出首个差异位置并退出。
"""
import argparse, json, re, difflib, sys


def load_srt(path):
    subs = []
    for b in re.split(r"\n\s*\n", open(path, encoding="utf-8").read().strip()):
        L = b.strip().splitlines()
        if len(L) < 2:
            continue
        m = re.search(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)", L[1])
        g = list(map(int, m.groups()))
        s = (g[0] * 3600 + g[1] * 60 + g[2]) * 1000 + g[3]
        e = (g[4] * 3600 + g[5] * 60 + g[6]) * 1000 + g[7]
        subs.append((s / 1000, e / 1000, "".join(L[2:]).strip()))
    return subs


def ts(sec):
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_char_times(subs, words_json):
    """给完整文本的每个字配 {c,s,e}：先 cue 内均分兜底，再用词级时间戳覆盖，缺失字插值。"""
    chars = []
    for s, e, t in subs:
        n = len(t) or 1
        dur = e - s
        for i, c in enumerate(t):
            chars.append({"c": c, "s": s + dur * i / n, "e": s + dur * (i + 1) / n})
    full = "".join(x["c"] for x in chars)

    if words_json:
        # 词 → 字级（whisperX 中文多为单字；多字词按时长均分到每个字）
        wchars = []
        for seg in json.load(open(words_json, encoding="utf-8")).get("segments", []):
            for w in seg.get("words", []):
                if "start" not in w or "end" not in w:
                    continue
                word = w.get("word", w.get("text", ""))
                ws, we = float(w["start"]), float(w["end"])
                m = len(word) or 1
                for k, ch in enumerate(word):
                    wchars.append({"c": ch, "s": ws + (we - ws) * k / m, "e": ws + (we - ws) * (k + 1) / m})
        wtext = "".join(x["c"] for x in wchars)

        sm = difflib.SequenceMatcher(None, full, wtext, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    chars[i1 + k]["s"] = wchars[j1 + k]["s"]
                    chars[i1 + k]["e"] = wchars[j1 + k]["e"]
                    chars[i1 + k]["exact"] = True

        exact = [i for i, c in enumerate(chars) if c.get("exact")]
        if exact:
            for i in range(len(chars)):
                if "exact" in chars[i]:
                    continue
                l = max((j for j in exact if j < i), default=None)
                r = min((j for j in exact if j > i), default=None)
                if l is not None and r is not None:
                    chars[i]["s"], chars[i]["e"] = chars[l]["e"], chars[r]["s"]
                elif l is not None:
                    chars[i]["s"], chars[i]["e"] = chars[l]["e"], chars[l]["e"] + 0.2
                elif r is not None:
                    chars[i]["s"], chars[i]["e"] = max(0, chars[r]["s"] - 0.2), chars[r]["s"]
        # 单调修正
        for i in range(1, len(chars)):
            if chars[i]["s"] < chars[i - 1]["s"]:
                chars[i]["s"] = chars[i - 1]["s"]
            if chars[i]["e"] < chars[i]["s"]:
                chars[i]["e"] = chars[i]["s"] + 0.05
    return chars, full


def pieces_from_segmented(seg_path, full):
    seg = open(seg_path, encoding="utf-8").read().strip()
    stripped = seg.replace("|", "")
    if stripped != full:
        for i in range(min(len(stripped), len(full))):
            if stripped[i] != full[i]:
                sys.exit(f"[错误] 分段与原文首个差异 @ {i}:\n  原文: ...{full[max(0,i-12):i+12]}...\n  分段: ...{stripped[max(0,i-12):i+12]}...")
        sys.exit(f"[错误] 分段与原文长度不同：原文 {len(full)}，分段去竖线 {len(stripped)}")
    return [p for p in seg.split("|") if p]


def pieces_by_length(full, max_chars):
    return [full[i:i + max_chars] for i in range(0, len(full), max_chars)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--srt", required=True, help="权威完整 SRT（文本以此为准）")
    ap.add_argument("--words", help="whisperX JSON（取词级时间戳；缺省用 cue 内均分）")
    ap.add_argument("--segmented", help="agent 产出的 |-分隔语义分段文件；缺省按 --max-chars 硬切")
    ap.add_argument("--max-chars", type=int, default=10)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()

    subs = load_srt(a.srt)
    chars, full = build_char_times(subs, a.words)
    pieces = pieces_from_segmented(a.segmented, full) if a.segmented else pieces_by_length(full, a.max_chars)

    cues, idx = [], 0
    for p in pieces:
        seg = chars[idx:idx + len(p)]
        s, e = seg[0]["s"], seg[-1]["e"]
        if e <= s:
            e = s + 0.3
        cues.append([s, e, p])
        idx += len(p)
    assert idx == len(chars), f"字数没对上 {idx} vs {len(chars)}"

    for i in range(len(cues) - 1):
        if cues[i][1] > cues[i + 1][0]:
            cues[i][1] = cues[i + 1][0]
        if cues[i][1] <= cues[i][0]:
            cues[i][1] = min(cues[i + 1][0], cues[i][0] + 0.3)

    out = "\n\n".join(f"{i}\n{ts(s)} --> {ts(e)}\n{t}" for i, (s, e, t) in enumerate(cues, 1)) + "\n"
    open(a.output, "w", encoding="utf-8").write(out)

    lens = [len(t) for _, _, t in cues]
    over = sum(1 for l in lens if l > a.max_chars)
    print(f"生成 {len(cues)} 条 → {a.output}")
    print(f"每条字数 最长{max(lens)} 平均{sum(lens)/len(lens):.1f} 超{a.max_chars}字{over}条")
    print(f"覆盖 {ts(cues[0][0])} → {ts(cues[-1][1])}")


if __name__ == "__main__":
    main()
