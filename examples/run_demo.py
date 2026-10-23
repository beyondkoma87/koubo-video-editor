#!/usr/bin/env python3
"""Reproducible local synthetic demo. No speech, real people, models or uploads."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def run_demo(output, font=None):
    from PIL import __version__ as pillow_version
    import effects_renderer
    import layer_renderer
    import subtitle_renderer
    import video_pipeline as video

    output = Path(output).expanduser().resolve()
    if output.exists():
        raise FileExistsError('Use a new output directory; existing demo data will not be overwritten')
    for command in ('ffmpeg', 'ffprobe'):
        if not shutil.which(command):
            raise RuntimeError(f'{command} must be installed and on PATH')
    font = subtitle_renderer.choose_font(font)
    output.mkdir(parents=True)
    source = output / 'synthetic-source.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=360x640:rate=24:duration=4', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=48000:duration=4',
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k', '-shortest', str(source)
    ], check=True)
    edit = {
        'source': str(source),
        'keep': [
            {'start': .25, 'end': 1.25, 'reason': 'synthetic test range A'},
            {'start': 2, 'end': 3, 'reason': 'synthetic test range B'}
        ],
        'notes': ['Synthetic pattern and sine tone; no speech or editorial decisions.']
    }
    plan = video.make_plan(edit, 4, {'width': 360, 'height': 640}, 24)
    video.write(output / 'edit.json', edit)
    video.write(output / 'plan.json', plan)
    base = output / 'base.mp4'
    video.render_base(plan, base, output / 'base-cache', 'libx264')

    plan['visualEffects'] = [{
        'id': 'synthetic-focus', 'type': 'focus', 'start': .25, 'end': .75,
        'box': [.1, .1, .8, .55], 'dim': .3, 'shape': 'rectangle',
        'feather': 0, 'animation': 'none'
    }]
    effects = output / 'effects.mp4'
    effects_renderer.render(plan, base, effects, output / 'effects-work', font)
    plan['layers'] = [
        {'id': 'moving-synthetic-card', 'type': 'solid', 'start': 0, 'end': 2,
         'box': [.05, .65, .2, .1], 'color': '#25C090',
         'keyframes': [{'t': 0, 'cx': .2, 'cy': .7},
                       {'t': 1, 'cx': .8, 'cy': .7, 'ease': 'linear'},
                       {'t': 2, 'cx': .2, 'cy': .7, 'ease': 'linear'}]},
        {'id': 'synthetic-video-insert', 'type': 'video', 'start': .5, 'end': 1.5,
         'path': str(source), 'offset': 0, 'box': [.55, .1, .35, .35]}
    ]
    layers = output / 'layers.mp4'
    layer_renderer.render(plan, effects, layers, output / 'layers-work', font)

    # These are demonstration labels, explicitly not aligned spoken captions.
    plan['captions'] = [
        {'start': .125, 'end': .875, 'text': '合成示例 / DEMO'},
        {'start': 1.125, 'end': 1.875, 'text': '未对齐草稿 / DRAFT'}
    ]
    video.write(output / 'draft-plan.json', plan)
    final = output / 'demo-DRAFT-unverified.mp4'
    defaults = video.read(ROOT / 'assets/defaults.json')
    subtitle_renderer.render(plan, layers, final, output / 'subtitle-work',
                             defaults, font, draft_unverified=True)
    video.srt(plan, output / 'demo-DRAFT-unverified.srt', draft_unverified=True)
    check = video.verify(final, plan)
    assert check['actualFrames'] == 48
    assert check['audioPresent']
    report = {
        'fixture': 'Generated testsrc2 + 440Hz sine tone; no speech or people',
        'environment': {
            'python': platform.python_version(), 'platform': platform.system(),
            'pillow': pillow_version,
            'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0]
        },
        'technical_checks': check,
        'caption_alignment': 'unverified draft labels; not speech evidence',
        'semantic_editing_reviewed': False,
        'speech_alignment_tested': False,
        'asr_tested': False,
        'vision_person_segmentation_tested': False,
        'playback_reviewed': False,
        'audio_listened': False
    }
    video.write(output / 'demo-report.json', report)
    print(json.dumps({'output': str(final), 'frames': check['actualFrames'],
                      'fullDecode': check['fullDecode'], 'speech_alignment_tested': False},
                     ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, help='New local output directory')
    parser.add_argument('--font', help='Existing local Chinese font, if autodetection fails')
    args = parser.parse_args()
    run_demo(args.out, args.font)
