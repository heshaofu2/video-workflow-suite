"""Behavioral checks for the local CLI contract and file protections."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CLI = Path(__file__).resolve().parents[2] / 'video-cli'


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='video-cli-test-')
        self.root = Path(self.temp.name)
        model = self.root / 'cached-model'
        model.mkdir()
        (model / 'config.json').write_text('{}')
        (model / 'weights.safetensors').write_bytes(b'test-only-placeholder')
        self.config = self.root / 'local.json'
        self.config.write_text(json.dumps({'transcription': {
            'engine': 'mlx_whisper', 'python': sys.executable, 'model_path': str(model),
        }}))

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)],
                                capture_output=True, text=True,
                                env={**os.environ, 'VIDEO_WORKFLOW_CONFIG': str(self.config)})
        data = json.loads(result.stdout)
        self.assertEqual(data['ok'], ok, result.stdout + result.stderr)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return data

    def transcript(self, name='transcript.json'):
        path = self.root / name
        path.write_text(json.dumps({'segments': [
            {'start': 0, 'end': 2, 'text': '第一段'},
            {'start': 2, 'end': 4, 'text': '第二段'},
            {'start': 4, 'end': 6, 'text': '第三段'},
        ]}, ensure_ascii=False))
        return path

    def test_capabilities_do_not_claim_agent_reasoning(self):
        data = self.run_cli('capabilities')['data']
        self.assertIn('insight-extractor', data['agent_skills'])
        self.assertTrue(all(x['mode'] == 'local' for x in data['commands']))

    def test_unknown_command_has_json_error(self):
        data = self.run_cli('clip', 'invented', ok=False)
        self.assertEqual(data['error']['code'], 'unknown_command')

    def test_missing_required_flags_have_json_error(self):
        data = self.run_cli('transcript', 'read', ok=False)
        self.assertEqual(data['error']['code'], 'invalid_arguments')

    def test_transcript_range_intersection(self):
        data = self.run_cli('transcript', 'read', '--input', self.transcript(),
                            '--start', '0:01', '--end', '0:04')['data']
        self.assertEqual([s['text'] for s in data['segments']], ['第一段', '第二段'])

    def test_text_cannot_be_filtered_as_timestamped_json(self):
        path = self.root / 'text.txt'
        path.write_text('没有时间戳')
        data = self.run_cli('transcript', 'read', '--input', path, '--start', '1', ok=False)
        self.assertEqual(data['error']['code'], 'timestamps_required')

    def test_nonfinite_timestamp_rejected(self):
        path = self.root / 'bad.json'
        path.write_text('{"segments":[{"start":0,"end":NaN,"text":"x"}]}')
        data = self.run_cli('transcript', 'read', '--input', path, ok=False)
        self.assertEqual(data['error']['code'], 'invalid_transcript')

    def test_transcription_dry_run_writes_nothing(self):
        source = self.root / 'a video.wav'
        source.write_bytes(b'placeholder')
        out = self.root / 'new-dir'
        data = self.run_cli('--dry-run', 'transcript', 'run', source, '--backend', 'mlx',
                            '--output-dir', out, '--output-name', 'test')['data']
        self.assertTrue(data['dry_run'])
        self.assertFalse(out.exists())
        self.assertEqual(source.read_bytes(), b'placeholder')
        self.assertIn(str(source), data['argv'])
        config = json.loads(self.config.read_text())
        self.assertEqual(data['argv'][0], config['transcription']['python'])

    def test_old_recognition_model_cannot_be_reenabled(self):
        source = self.root / 'source.wav'
        source.write_bytes(b'placeholder')
        data = self.run_cli('--dry-run', 'transcript', 'run', source,
                            '--model', 'large-v3', ok=False)
        self.assertEqual(data['error']['code'], 'unsupported_model')
        self.assertEqual(self.run_cli('capabilities')['data']['transcription_engine'], 'mlx_whisper')

    def test_merge_preserves_silent_tail_and_nested_word_offsets(self):
        segments = self.root / 'segments'
        segments.mkdir()
        for index in range(2):
            (segments / f'seg_{index:03d}.json').write_text(json.dumps({
                'duration': 10, 'language': 'zh', 'text': '测试',
                'segments': [{'start': 0, 'end': 2, 'text': '测试',
                              'words': [{'start': 0, 'end': 2, 'word': '测试'}]}],
                'word_segments': [{'start': 0, 'end': 2, 'word': '测试'}],
            }))
        output = self.root / 'merged.json'
        self.run_cli('transcript', 'merge', '--segments-dir', segments,
                     '--output-json', output, '--output-txt', self.root / 'merged.txt')
        data = json.loads(output.read_text())
        self.assertEqual(data['segments'][1]['start'], 10)
        self.assertEqual(data['segments'][1]['words'][0]['start'], 10)
        self.assertEqual(data['word_segments'][1]['start'], 10)

    def test_merge_without_media_duration_is_rejected(self):
        segments = self.root / 'segments'
        segments.mkdir()
        (segments / 'seg_000.json').write_text('{"segments":[]}')
        output = self.root / 'merged.json'
        self.run_cli('transcript', 'merge', '--segments-dir', segments,
                     '--output-json', output, '--output-txt', self.root / 'merged.txt', ok=False)
        self.assertFalse(output.exists())

    def test_transcription_refuses_existing_outputs(self):
        source = self.root / 'source.wav'
        source.write_bytes(b'placeholder')
        existing = self.root / 'source.json'
        existing.write_text('keep')
        data = self.run_cli('transcript', 'run', source, ok=False)
        self.assertEqual(data['error']['code'], 'output_exists')
        self.assertEqual(existing.read_text(), 'keep')

    def test_duplicate_flags_cannot_bypass_output_guard(self):
        data = self.run_cli('subtitle', 'resegment', '--output', self.root / 'new.srt',
                            '--output', self.root / 'existing.srt', ok=False)
        self.assertEqual(data['error']['code'], 'duplicate_flag')

    def test_resume_rejects_unverified_existing_directory(self):
        source = self.root / 'source.wav'
        source.write_bytes(b'placeholder')
        out = self.root / 'previous'
        out.mkdir()
        (out / 'old.json').write_text('{}')
        data = self.run_cli('--dry-run', 'transcript', 'segments', source,
                            '--output-dir', out, ok=False)
        self.assertEqual(data['error']['code'], 'unverified_resume')
        self.assertFalse((out / '.video-source.json').exists())

    def test_clip_name_cannot_escape_output_directory(self):
        source = self.root / 'source.mp4'
        source.write_bytes(b'placeholder')
        data = self.run_cli('clip', 'cut', '--input', source, '--start', '0:00', '--end', '0:01',
                            '--name', '../escape', '--output', self.root / 'clips', ok=False)
        self.assertEqual(data['error']['code'], 'invalid_clip_name')
        self.assertFalse((self.root / 'clips').exists())

    def test_drift_changes_copy_and_preserves_input(self):
        source = self.transcript()
        before = source.read_bytes()
        output = self.root / 'corrected.json'
        self.run_cli('transcript', 'fix-drift', source, '--factor', '2', '--output', output)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(json.loads(output.read_text())['segments'][-1]['end'], 12)
        self.assertTrue((self.root / 'corrected.raw.json').is_file())

    def test_subtitle_roundtrip_and_overwrite_rejection(self):
        srt = self.root / 'input.srt'
        srt.write_text('1\n00:00:00,000 --> 00:00:08,000\n今天学习人工智能\n')
        segmented = self.root / 'segmented.txt'
        segmented.write_text('今天|学习|人工智能')
        output = self.root / 'short.srt'
        args = ('subtitle', 'resegment', '--srt', srt, '--segmented', segmented,
                '--max-chars', '4', '--output', output)
        self.run_cli(*args)
        cues = output.read_text().strip().split('\n\n')
        self.assertEqual(''.join(c.splitlines()[2] for c in cues), '今天学习人工智能')
        before = output.read_bytes()
        data = self.run_cli(*args, ok=False)
        self.assertEqual(data['error']['code'], 'output_exists')
        self.assertEqual(output.read_bytes(), before)

    def test_child_error_keeps_stdout_as_one_json(self):
        data = self.run_cli('clip', 'boundary', '--transcript', self.root / 'missing.json',
                            '--start', '0:00', '--end', '0:01', ok=False)
        self.assertEqual(data['error']['code'], 'command_failed')
        self.assertNotEqual(data['error']['details']['exit_code'], 0)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), '需要本地 FFmpeg')
    def test_real_cut_and_dedup_through_unified_cli(self):
        source = self.root / 'source video.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=160x90:rate=25:duration=2', '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=48000:duration=2',
                        '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)],
                       check=True, capture_output=True)
        before = source.read_bytes()
        self.run_cli('clip', 'cut', '--input', source, '--start', '0:00.4', '--end', '0:01.4',
                     '--name', 'cut', '--output', self.root / 'clips', '--no-desilence')
        self.assertTrue((self.root / 'clips/cut.mp4').is_file())
        joined = self.root / "joined video's.mp4"
        self.run_cli('clip', 'join', '--input', self.root / 'clips/cut.mp4',
                     self.root / 'clips/cut.mp4', '--output', joined)
        self.assertTrue(joined.is_file())
        inspected = self.run_cli('dedup', 'inspect', source)['data']['result']
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps({'source': str(source), 'source_sha256': inspected['sha256'],
                                    'cuts': [{'id': 'remove', 'start_frame': 5, 'end_frame': 10}],
                                    'protected_ranges': [{'start_frame': 0, 'end_frame': 3}]}))
        self.run_cli('dedup', 'check', plan)
        output = self.root / 'dedup.mp4'
        self.run_cli('dedup', 'render', plan, '--output', output)
        self.assertTrue(output.is_file())
        self.assertEqual(source.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
