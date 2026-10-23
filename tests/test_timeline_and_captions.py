"""Invariant tests with synthetic fixtures, never evidence of real speech quality."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import caption_sync as sync
import video_pipeline as video


class TimelineTests(unittest.TestCase):
    def edit(self, **kwargs):
        return dict(source='/synthetic/source.mp4', keep=[[0, 1], [2, 3]], **kwargs)

    def test_preserves_fractional_native_rate(self):
        fps = 60000 / 1001
        plan = video.make_plan(self.edit(), source_fps=fps)
        self.assertEqual(plan['fps'], fps)
        self.assertEqual(plan['clips'][0]['frames'], 59)
        self.assertLessEqual(plan['clips'][0]['sourceEnd'], 1)

    def test_does_not_extend_reviewed_range(self):
        plan = video.make_plan(dict(source='fixture.mp4', keep=[[.1, .26]], fps=24))
        self.assertEqual(plan['clips'][0]['frames'], 3)
        self.assertLessEqual(plan['clips'][0]['sourceEnd'], .26)

    def test_rejects_caption_cut_in_half(self):
        edit = self.edit(source_captions=[{'start': .5, 'end': 2.5, 'text': 'fixture'}])
        with self.assertRaisesRegex(ValueError, 'crosses a cut'):
            video.make_plan(edit, source_fps=24)

    def test_rejects_superseded_take(self):
        edit = self.edit(retakeDecisions=[{
            'reviewed': True, 'selected': {'start': 2, 'end': 3},
            'superseded': [{'start': 0, 'end': 1}]
        }])
        with self.assertRaisesRegex(ValueError, 'superseded earlier retake'):
            video.make_plan(edit, source_fps=24)

    def test_rejects_timeline_gap(self):
        plan = video.make_plan(self.edit(), source_fps=24)
        plan['clips'][1]['outputStart'] += .1
        with self.assertRaisesRegex(ValueError, 'gap or overlap'):
            video.validate(plan)

    def test_rejects_source_overrun(self):
        with self.assertRaisesRegex(ValueError, 'source duration'):
            video.make_plan(self.edit(), source_duration=2.5, source_fps=24)


class CaptionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / 'synthetic-silence.wav'
        with wave.open(str(self.audio), 'wb') as audio:
            audio.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 96000)
        self.plan = video.make_plan({'source': str(self.root / 'fixture.mp4'),
                                     'keep': [[0, 2]], 'fps': 24})
        self.evidence = {
            'version': 1,
            'alignment_method': 'SYNTHETIC UNIT TEST ONLY; not acoustic word evidence',
            'audio_file': str(self.audio), 'audio_sha256': video.digest(self.audio),
            'timeline_sha256': sync.timeline_id(self.plan),
            'words': [{'id': 0, 'text': '测试', 'start': .25, 'end': .5},
                      {'id': 1, 'text': '数值50', 'start': .75, 'end': 1.0}],
            'review': {'text_reviewed': True, 'timing_reviewed': False,
                       'audio_reviewed': False, 'ambiguous_word_ids': [],
                       'notes': ['Synthetic fixture checks bindings, not sound or alignment.']}
        }
        self.path = self.root / 'evidence.json'
        video.write(self.path, self.evidence)
        self.aligned = sync.build(self.plan, self.evidence,
                                  [{'word_ids': [0, 1], 'text': '测试数值50'}], self.path)

    def test_accepts_consistent_fixture_without_claiming_listening(self):
        result = sync.audit(self.aligned)
        self.assertEqual(result['words'], 2)
        self.assertEqual(result['timing_evidence_check'], 'passed')
        self.assertFalse(result['review']['audio_reviewed'])
        self.assertIn('does not certify', result['limitation'])

    def test_rejects_changed_audio(self):
        with wave.open(str(self.audio), 'wb') as audio:
            audio.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            audio.writeframes(struct.pack('<h', 1) * 96000)
        with self.assertRaisesRegex(ValueError, 'audio missing or changed'):
            sync.audit(self.aligned)

    def test_rejects_changed_timeline(self):
        changed = copy.deepcopy(self.aligned)
        changed['source'] = str(self.root / 'different.mp4')
        with self.assertRaisesRegex(ValueError, 'different edit/fps timeline'):
            sync.audit(changed)

    def test_rejects_changed_number(self):
        self.aligned['captions'][0]['text'] = '测试数值500'
        with self.assertRaisesRegex(ValueError, 'differs from the timed spoken words'):
            sync.audit(self.aligned)

    def test_rejects_dropped_word(self):
        self.aligned['captions'][0].update(word_ids=[0], text='测试', end=.5833333333333334)
        with self.assertRaisesRegex(ValueError, 'omitted, duplicated or reordered'):
            sync.audit(self.aligned)

    def test_rejects_missing_final_evidence(self):
        self.aligned.pop('subtitleSync')
        with self.assertRaisesRegex(ValueError, 'missing final-voice'):
            video.srt(self.aligned, self.root / 'rejected.srt')

    def test_rejects_unreviewed_text(self):
        self.evidence['review']['text_reviewed'] = False
        with self.assertRaisesRegex(ValueError, 'have not been reviewed'):
            sync.audit(self.aligned, self.evidence)

    def test_srt_refuses_existing_output(self):
        target = self.root / 'captions.srt'
        video.srt(self.aligned, target)
        first = target.read_bytes()
        with self.assertRaises(FileExistsError):
            video.srt(self.aligned, target)
        self.assertEqual(first, target.read_bytes())


if __name__ == '__main__':
    unittest.main()
