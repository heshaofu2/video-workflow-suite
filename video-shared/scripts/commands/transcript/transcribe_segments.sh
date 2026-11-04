#!/usr/bin/env bash
set -euo pipefail

# Segment-based transcription wrapper.
# - Splits long audio/video into segments (default 30m)
# - Transcribes segment-by-segment via transcribe.py (resume-friendly)
# - Produces per-segment outputs + a combined transcript.segments.txt
#
# Recognition uses the project-selected MLX model; segments are resumable.

usage() {
  cat <<'EOF'
Usage:
  transcribe_segments.sh <input_media> --output-dir <dir> [options]

Required:
  <input_media>            Video or audio file
  --output-dir DIR         Project output directory

Options:
  --output-prefix NAME     Output prefix (default: transcript)
  --segment-minutes N      Segment length in minutes (default: 30)
  --backend mlx           Local MLX recognition only
  --model NAME             (default: mlx-community/whisper-large-v3-turbo)
  --language LANG          (default: zh)

Outputs:
  <output-dir>/segments/audio_<N>m/seg_000.m4a ...
  <output-dir>/outputs/transcript_segments_<N>m/seg_000.{json,txt,srt,ass} ...
  <output-dir>/outputs/transcript_segments_<N>m/<prefix>.segments.txt
  <output-dir>/logs/transcribe_segments_<N>m/{status.log,run.log,segment.log}

Resume:
  Re-run the same command; finished segments (json+txt present) will be skipped.
EOF
}

if [ $# -lt 1 ]; then
  usage
  exit 1
fi

IN="$1"; shift

OUTPUT_DIR=""
PREFIX="transcript"
SEG_MIN=30
BACKEND="mlx"
MODEL="mlx-community/whisper-large-v3-turbo"
LANG="zh"

while [ $# -gt 0 ]; do
  case "$1" in
    --output-dir) OUTPUT_DIR="$2"; shift 2;;
    --output-prefix) PREFIX="$2"; shift 2;;
    --segment-minutes) SEG_MIN="$2"; shift 2;;
    --backend) BACKEND="$2"; shift 2;;
    --model) MODEL="$2"; shift 2;;
    --language) LANG="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [ -z "$OUTPUT_DIR" ]; then
  echo "--output-dir is required" >&2
  exit 2
fi

if [ ! -f "$IN" ]; then
  echo "Input file not found: $IN" >&2
  exit 1
fi

SEG_SECONDS=$((SEG_MIN * 60))
SEG_DIR="$OUTPUT_DIR/segments/audio_${SEG_MIN}m"
OUT_DIR="$OUTPUT_DIR/outputs/${PREFIX}_segments_${SEG_MIN}m"
LOG_DIR="$OUTPUT_DIR/logs/transcribe_segments_${SEG_MIN}m"

mkdir -p "$SEG_DIR" "$OUT_DIR" "$LOG_DIR"

if [ "$BACKEND" != "mlx" ]; then
  echo "Only the local MLX backend is supported" >&2
  exit 2
fi

# 1) extract audio to m4a if input is video (or keep if already audio)
AUDIO="$OUTPUT_DIR/source/${PREFIX}.audio.m4a"
mkdir -p "$OUTPUT_DIR/source"
if [ ! -f "$AUDIO" ]; then
  echo "[audio] extracting 16k mono m4a..." | tee "$LOG_DIR/status.log"
  ffmpeg -hide_banner -y -i "$IN" \
    -vn -ac 1 -ar 16000 -c:a aac -b:a 64k \
    "$AUDIO" \
    >"$LOG_DIR/extract_audio.log" 2>&1
fi

# 2) split audio (once)
if ! ls "$SEG_DIR"/seg_*.m4a >/dev/null 2>&1; then
  echo "[split] segmenting ${SEG_MIN}m into $SEG_DIR" | tee -a "$LOG_DIR/status.log"
  ffmpeg -hide_banner -y -i "$AUDIO" \
    -map 0 -c copy \
    -f segment -segment_time "$SEG_SECONDS" -reset_timestamps 1 \
    -segment_format m4a \
    "$SEG_DIR/seg_%03d.m4a" \
    >"$LOG_DIR/segment.log" 2>&1
fi

TOTAL=$(ls "$SEG_DIR"/seg_*.m4a | wc -l | tr -d ' ')
echo "[run] total segments: $TOTAL" | tee -a "$LOG_DIR/status.log"

idx=0
for f in "$SEG_DIR"/seg_*.m4a; do
  base=$(basename "$f")
  stem=${base%.m4a}

  if [ -s "$OUT_DIR/$stem.json" ] && [ -s "$OUT_DIR/$stem.txt" ]; then
    echo "[skip] $stem already done" | tee -a "$LOG_DIR/status.log"
    idx=$((idx + 1))
    continue
  fi

  start=$((idx * SEG_SECONDS))
  end=$(((idx + 1) * SEG_SECONDS))
  h1=$((start / 3600)); m1=$(((start % 3600) / 60)); s1=$((start % 60))
  h2=$((end / 3600)); m2=$(((end % 3600) / 60)); s2=$((end % 60))

  echo "[seg $((idx+1))/$TOTAL] $stem  window ${h1}:$(printf '%02d:%02d' $m1 $s1) → ${h2}:$(printf '%02d:%02d' $m2 $s2)" | tee -a "$LOG_DIR/status.log"

  "${VIDEO_TRANSCRIPT_PYTHON:-python3}" "$(dirname "$0")/transcribe.py" \
    --output-dir "$OUT_DIR" \
    --output-name "$stem" \
    --backend "$BACKEND" \
    --model "$MODEL" \
    --language "$LANG" \
    "$f" \
    >>"$LOG_DIR/run.log" 2>&1

  {
    echo ""
    echo "===================="
    echo "SEGMENT $stem  (${h1}:$(printf '%02d:%02d' $m1 $s1) → ${h2}:$(printf '%02d:%02d' $m2 $s2))"
    echo "===================="
    echo ""
    cat "$OUT_DIR/$stem.txt"
  } >> "$OUT_DIR/${PREFIX}.segments.txt"

  echo "[done] $stem" | tee -a "$LOG_DIR/status.log"
  idx=$((idx + 1))
done

echo "[all done]" | tee -a "$LOG_DIR/status.log"
