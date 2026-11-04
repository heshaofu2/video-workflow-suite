#!/usr/bin/env python3
"""
Video/Audio → Text transcription with cached MLX Whisper large-v3-turbo.
Supports word timestamps, optional speaker attribution, and multiple output formats.

Usage:
    python3 transcribe.py <input_file> [options]

Options:
    --output-dir DIR    Output directory (default: same as input)
    --output-name NAME  Output filename stem (default: derived from input)
    --language LANG     Language code (default: zh)
    --model MODEL       Unified MLX Whisper large-v3-turbo model
    --diarize           Enable speaker diarization (requires HF_TOKEN env var)
    --device DEVICE     cpu or cuda (default: cpu)

Output:
    <name>.txt   - Human-readable transcript with speaker labels and timestamps
    <name>.json  - MLX output with segment and word-level timestamps
    <name>.srt   - Soft subtitles (SRT)
    <name>.ass   - Soft subtitles (ASS, simple style)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile


def extract_audio(video_path, audio_path):
    """Extract 16kHz mono WAV while retaining the media's timestamp spacing."""
    print(f"Extracting audio from {video_path}...")
    cmd = [
        "ffmpeg", "-i", video_path, "-vn", "-af", "aresample=async=1",
        "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "-y", audio_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"Audio extracted: {audio_path}")

    # 自检：提取音频时长应与视频接近。aresample 已填充，这里再兜底校验一次，
    # 若仍差异 >0.5%（罕见的难处理视频），告警提示时间轴可能漂移。
    def _probe_dur(p):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", p],
            capture_output=True, text=True)
        try:
            return float(r.stdout.strip())
        except ValueError:
            return None
    vdur, adur = _probe_dur(video_path), _probe_dur(audio_path)
    if vdur and adur and abs(vdur - adur) / vdur > 0.005:
        print(f"⚠️  提取音频 {adur:.1f}s 与视频 {vdur:.1f}s 差异 >0.5%，转写时间轴"
              f"可能仍有漂移；可用 fix-timeline-drift.py 校正或检查源视频",
              file=sys.stderr)


def transcribe(audio_path, language):
    """Recognize with the one configured, already cached MLX Turbo model."""
    import mlx_whisper
    from runtime import MODEL, load_runtime

    config = load_runtime()
    print(f"Transcribing with {MODEL} via MLX...")
    result = mlx_whisper.transcribe(
        audio_path, path_or_hf_repo=config['model_path'], language=language,
        word_timestamps=True, verbose=False, condition_on_previous_text=False,
    )
    # One recognition supplies both consumers' word layouts without re-running ASR.
    result['word_segments'] = [dict(word) for segment in result.get('segments', [])
                               for word in segment.get('words', [])]
    result['engine'] = 'mlx_whisper'
    result['model'] = MODEL
    return result, audio_path


def diarize(result, audio_path, device):
    """Run speaker diarization."""
    import importlib.util
    if importlib.util.find_spec('whisperx') is None:
        from runtime import load_runtime
        python = load_runtime().get('diarization_python')
        if not python or not os.path.isfile(python):
            raise RuntimeError('说话人分离环境不可用；识别仍统一为 MLX，不能改回其他识别模型')
        with tempfile.TemporaryDirectory(prefix='video-diarization-') as directory:
            existing = os.path.join(directory, 'input.json')
            with open(existing, 'w', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False)
            subprocess.run([python, __file__, audio_path, '--diarize-only',
                            '--existing-json', existing, '--output-dir', directory,
                            '--output-name', 'speakers', '--device', device], check=True)
            with open(os.path.join(directory, 'speakers.json'), encoding='utf-8') as stream:
                return json.load(stream)
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        # 环境变量没有则回退到本地 HuggingFace token 文件
        token_file = os.path.expanduser("~/.cache/huggingface/token")
        if os.path.isfile(token_file):
            with open(token_file) as f:
                hf_token = f.read().strip()
    if not hf_token:
        raise RuntimeError('说话人分离缺少现有 Hugging Face 授权，不能声称已完成分人')

    print("Running speaker diarization...")
    try:
        from whisperx.diarize import DiarizationPipeline, assign_word_speakers
    except ImportError:
        # Fallback for older whisperx versions
        import whisperx
        DiarizationPipeline = whisperx.DiarizationPipeline
        assign_word_speakers = whisperx.assign_word_speakers

    try:
        import inspect
        sig = inspect.signature(DiarizationPipeline.__init__)
        if "token" in sig.parameters:
            diarize_model = DiarizationPipeline(token=hf_token, device=device)
        else:
            diarize_model = DiarizationPipeline(use_auth_token=hf_token, device=device)
        diarize_segments = diarize_model(audio_path)
        result = assign_word_speakers(diarize_segments, result)
        print("Diarization done!")
    except Exception as e:
        raise RuntimeError(f"Diarization failed: {e}") from e

    return result


def format_transcript(result):
    """Format segments into human-readable transcript with speaker grouping."""
    lines = []
    current_speaker = None
    current_texts = []

    for seg in result.get("segments", []):
        speaker = seg.get("speaker", None)
        text = seg.get("text", "").strip()
        start = seg.get("start", 0)

        ts = f"[{int(start // 60):02d}:{int(start % 60):02d}]"

        if speaker != current_speaker:
            if current_texts:
                prefix = f"{current_speaker}: " if current_speaker else ""
                lines.append(prefix + " ".join(current_texts))
            current_speaker = speaker
            current_texts = [ts + " " + text]
        else:
            current_texts.append(text)

    if current_texts:
        prefix = f"{current_speaker}: " if current_speaker else ""
        lines.append(prefix + " ".join(current_texts))

    return "\n\n".join(lines)


def _sec_to_srt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0
    ms = int(round((sec - int(sec)) * 1000))
    t = int(sec)
    h = t // 3600
    m = (t % 3600) // 60
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _sec_to_ass_ts(sec: float) -> str:
    if sec < 0:
        sec = 0
    cs = int(round((sec - int(sec)) * 100))
    t = int(sec)
    h = t // 3600
    m = (t % 3600) // 60
    s = t % 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _group_words_to_cues(words, max_chars=18, max_dur=6.0, pause_split=0.7):
    """Group whisperx word_segments into subtitle cues."""
    cues = []
    cur = []
    cur_start = None
    last_end = None

    def norm_word(w: str) -> str:
        return (w or "").replace(" ", "").strip()

    def flush():
        nonlocal cur, cur_start, last_end
        if not cur:
            return
        start = cur_start if cur_start is not None else cur[0]["start"]
        end = last_end if last_end is not None else cur[-1].get("end", cur[-1]["start"])
        text = "".join(norm_word(x.get("word", "")) for x in cur)
        text = re.sub(r"\s+", "", text)
        if text:
            cues.append({"start": float(start), "end": float(end), "text": text})
        cur = []
        cur_start = None
        last_end = None

    for w in words or []:
        ww = norm_word(w.get("word", ""))
        if not ww:
            continue
        s = float(w.get("start", 0.0))
        e = float(w.get("end", s))

        if cur and last_end is not None and s - last_end >= pause_split:
            flush()

        if not cur:
            cur_start = s

        cur.append({"word": ww, "start": s, "end": e})
        last_end = e

        text_now = "".join(x["word"] for x in cur)
        dur_now = (last_end - cur_start) if (cur_start is not None and last_end is not None) else 0

        # soft punctuation split
        if re.search(r"[。！？!?]$", text_now) and len(text_now) >= 8:
            flush()
            continue

        if len(text_now) >= max_chars or dur_now >= max_dur:
            flush()

    flush()
    return cues


def write_srt_from_result(result, out_path: str):
    words = result.get("word_segments") or []
    cues = _group_words_to_cues(words)
    lines = []
    for i, c in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{_sec_to_srt_ts(c['start'])} --> {_sec_to_srt_ts(c['end'])}")
        lines.append(c["text"])
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")


def write_ass_from_result(result, out_path: str, video_w=1080, video_h=1920):
    words = result.get("word_segments") or []
    cues = _group_words_to_cues(words)

    # simple, neutral style (no highlights). Hard-sub style lives in clip-subtitles.py.
    font = "PingFang SC"
    fs = 46
    primary = "&H00FFFFFF"  # white
    outline = "&H00000000"  # black
    back = "&H80000000"     # semi-transparent black

    header = f"""[Script Info]
; Script generated by video-to-text/transcribe.py
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fs},{primary},&H00000000,{outline},{back},0,0,0,0,100,100,0,0,3,2,0,2,120,120,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def ass_escape(s: str) -> str:
        return (s or "").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")

    events = []
    for c in cues:
        start = _sec_to_ass_ts(c["start"])
        end = _sec_to_ass_ts(c["end"])
        txt = ass_escape(c["text"])
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{txt}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")


def _resolve_output_paths(args, input_path):
    """Resolve output directory, stem, and file paths from args."""
    if args.output_dir:
        out_dir = args.output_dir
    else:
        out_dir = os.path.dirname(input_path)
    os.makedirs(out_dir, exist_ok=True)

    if args.output_name:
        stem = args.output_name
    else:
        stem = os.path.splitext(os.path.basename(input_path))[0]

    return out_dir, stem, {
        "json": os.path.join(out_dir, f"{stem}.json"),
        "txt": os.path.join(out_dir, f"{stem}.txt"),
        "srt": os.path.join(out_dir, f"{stem}.srt"),
        "ass": os.path.join(out_dir, f"{stem}.ass"),
    }


def _extract_audio_for_input(input_path, out_dir, stem):
    """Extract audio from video if needed. Returns (audio_path, temp_path_or_None)."""
    audio_exts = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
    ext = os.path.splitext(input_path)[1].lower()

    if ext in audio_exts:
        return input_path, None

    temp_audio_path = os.path.join(out_dir, f"{stem}.audio.wav")
    extract_audio(input_path, temp_audio_path)
    return temp_audio_path, temp_audio_path


def _save_outputs(result, paths):
    """Save transcript JSON, TXT, SRT, ASS."""
    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    txt = format_transcript(result)
    with open(paths["txt"], "w", encoding="utf-8") as f:
        f.write(txt)

    try:
        write_srt_from_result(result, paths["srt"])
        write_ass_from_result(result, paths["ass"])
    except Exception as e:
        print(f"Subtitle export skipped: {e}", file=sys.stderr)

    print(f"\nDone!")
    print(f"  Text:  {paths['txt']}")
    print(f"  JSON:  {paths['json']}")
    print(f"  SRT:   {paths['srt']}")
    print(f"  ASS:   {paths['ass']}")
    n_seg = len(result.get("segments", []))
    speakers = {s.get("speaker") for s in result.get("segments", []) if s.get("speaker")}
    print(f"  Segments: {n_seg}")
    if speakers:
        print(f"  Speakers: {sorted(speakers)}")


def run_diarize_only(args):
    """Diarize-only mode: load existing transcript.json, run speaker diarization, update outputs."""
    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        print(f"File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    out_dir, stem, paths = _resolve_output_paths(args, input_path)

    # Load existing transcript JSON
    existing_json = args.existing_json
    if not existing_json:
        existing_json = paths["json"]
    existing_json = os.path.abspath(existing_json)

    if not os.path.exists(existing_json):
        print(f"Existing transcript JSON not found: {existing_json}", file=sys.stderr)
        print("Run full transcription first, or specify --existing-json.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading existing transcript: {existing_json}")
    with open(existing_json, "r", encoding="utf-8") as f:
        result = json.load(f)

    n_seg = len(result.get("segments", []))
    has_speaker = any(s.get("speaker") for s in result.get("segments", []))
    print(f"  Loaded {n_seg} segments (existing speaker labels: {has_speaker})")

    # Extract audio for pyannote (needs wav)
    audio_path, temp_audio_path = _extract_audio_for_input(input_path, out_dir, stem)

    try:
        result = diarize(result, audio_path, args.device)
        _save_outputs(result, paths)
    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            try:
                os.unlink(temp_audio_path)
            except Exception:
                pass


def run_full_transcribe(args):
    """Full transcription mode (original behavior)."""
    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        print(f"File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    out_dir, stem, paths = _resolve_output_paths(args, input_path)
    audio_path, temp_audio_path = _extract_audio_for_input(input_path, out_dir, stem)
    try:
        result, audio_file = transcribe(audio_path, args.language)
        duration = subprocess.check_output(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=nk=1:nw=1', audio_file], text=True,
        )
        result['duration'] = float(duration.strip())
        result['source'] = input_path
        result['time_basis'] = 'source_media_seconds'

        if args.diarize:
            result = diarize(result, audio_file, args.device)

        _save_outputs(result, paths)

    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            try:
                os.unlink(temp_audio_path)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="Video/Audio → Text transcription")
    parser.add_argument("input", help="Input video or audio file")
    parser.add_argument("--output-dir", help="Output directory")
    parser.add_argument("--output-name", help="Output filename stem")
    parser.add_argument("--language", default="zh", help="Language code (default: zh)")
    from runtime import MODEL
    parser.add_argument("--model", default=MODEL, choices=[MODEL, 'large-v3-turbo'], help="统一的 MLX Turbo 模型")
    parser.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    parser.add_argument("--diarize-only", action="store_true",
                        help="Only run speaker diarization on existing transcript (skip transcription)")
    parser.add_argument("--existing-json", help="Path to existing transcript JSON (for --diarize-only, defaults to <output-dir>/<output-name>.json)")
    parser.add_argument("--device", default="cpu", help="cpu or cuda")
    parser.add_argument("--backend", default="mlx", choices=["mlx"], help="统一使用本地 MLX，不回退其他引擎")
    args = parser.parse_args()

    if args.diarize_only:
        run_diarize_only(args)
    else:
        run_full_transcribe(args)


if __name__ == "__main__":
    main()
