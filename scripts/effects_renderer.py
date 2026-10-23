#!/usr/bin/env python3
"""Render timed punches, text/image overlays and existing audio; Pillow + FFmpeg.

Effects use output-timeline seconds. This does not infer editorial decisions,
generate covers, verify evidence, or certify visual quality.
"""
import argparse
import math
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps, ImageFilter
from subtitle_renderer import choose_font
from video_pipeline import encode, probe, read, streams, validate, write


def number(value, low, high, label):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'invalid {label}: {value}')
    return value


def check_effects(plan):
    validate(plan)
    ids = set()
    for group, types in [('visualEffects', {'punch', 'frame', 'focus', 'text', 'image'}),
                         ('audioEffects', {'music', 'sfx'})]:
        for effect in plan.get(group, []):
            if not effect.get('id') or effect['id'] in ids:
                raise ValueError('effect ids must be nonempty and unique')
            ids.add(effect['id'])
            if effect.get('type') not in types:
                raise ValueError(f'unsupported {group} type: {effect.get("type")}')
            start = number(effect['start'], 0, plan['duration'], 'effect start')
            end = number(effect['end'], start, plan['duration'], 'effect end')
            if round(end * plan['fps']) <= round(start * plan['fps']):
                raise ValueError('effect must span at least one frame')
            if effect['type'] == 'punch':
                number(effect.get('zoom', 1.08), 1, 1.3, 'zoom')
                for key in ['cx', 'cy']:
                    number(effect.get(key, .5), 0, 1, key)
            elif group == 'visualEffects':
                box = effect['box']
                if len(box) != 4:
                    raise ValueError('box must be [x, y, width, height] fractions')
                x, y, w, h = [number(v, 0, 1, 'box fraction') for v in box]
                if w <= 0 or h <= 0 or x + w > 1.000001 or y + h > 1.000001:
                    raise ValueError('box outside canvas')
                if effect['type'] == 'text' and not str(effect.get('text', '')).strip():
                    raise ValueError('empty effect text')
                if effect['type'] == 'focus':
                    number(effect.get('dim', .55), 0, .9, 'focus dim')
                    number(effect.get('feather', 8), 0, 100, 'focus feather')
                    if effect.get('shape', 'ellipse') not in {'ellipse', 'rectangle'}:
                        raise ValueError('focus shape must be ellipse or rectangle')
                if effect.get('animation', 'fade') not in {'none', 'fade', 'slide'}:
                    raise ValueError('animation must be none, fade or slide')
            else:
                number(effect.get('gain', .08), 0, 1, 'audio gain')
                number(effect.get('offset', 0), 0, 86400, 'audio offset')
            if effect['type'] in {'image', 'music', 'sfx'}:
                path = Path(effect['path'])
                if not path.is_absolute() or not path.is_file():
                    raise ValueError(f'effect requires existing absolute path: {path}')
    punches = sorted([e for e in plan.get('visualEffects', []) if e['type'] in {'punch', 'frame'}],
                     key=lambda e: e['start'])
    if any(a['end'] > b['start'] + 1e-8 for a, b in zip(punches, punches[1:])):
        raise ValueError('camera/frame intervals overlap')
    return punches


def tile(effect, width, height, font, target):
    x, y, w, h = effect['box']
    x, y, w, h = round(x * width), round(y * height), round(w * width), round(h * height)
    if min(w, h) < 2:
        raise ValueError('effect box too small')
    if effect['type'] == 'focus':
        mask = Image.new('L', (width, height), round(255*effect.get('dim', .55)))
        draw = ImageDraw.Draw(mask)
        hole = (x, y, min(width, x+w), min(height, y+h))
        if effect.get('shape', 'ellipse') == 'ellipse':
            draw.ellipse(hole, fill=0)
        else:
            draw.rectangle(hole, fill=0)
        feather = effect.get('feather', 8)*min(width/1080, height/1920)
        if feather:
            mask = mask.filter(ImageFilter.GaussianBlur(feather))
        im = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        im.putalpha(mask)
        im.save(target)
        return 0, 0
    im = Image.new('RGBA', (w, h))
    if effect['type'] == 'image':
        im.paste(ImageColor.getcolor(effect.get('background', '#101827'), 'RGBA'), (0, 0, w, h))
        with Image.open(effect['path']) as source:
            content = ImageOps.contain(ImageOps.exif_transpose(source).convert('RGBA'), (w, h))
        im.alpha_composite(content, ((w - content.width) // 2, (h - content.height) // 2))
    else:
        draw = ImageDraw.Draw(im)
        scale = min(width / 1080, height / 1920)
        size = max(12, round(number(effect.get('font_size', 108), 16, 300, 'font size') * scale))
        f = ImageFont.truetype(font, size, index=int(number(effect.get("font_index", 0), 0, 64, "font index")))
        stroke = max(1, round(3 * scale))
        pad = max(4, round(14 * scale))
        text = effect['text']
        bounds = draw.multiline_textbbox((0, 0), text, font=f, spacing=round(size * .2),
                                         stroke_width=stroke, align='center')
        if bounds[2] - bounds[0] > w - 2 * pad or bounds[3] - bounds[1] > h - 2 * pad:
            raise ValueError(f'text does not fit box for {effect["id"]}; split/reposition it')
        if effect.get('panel', False):
            draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=pad,
                                   fill=effect.get('background', '#101827E8'))
        draw.multiline_text(((w - bounds[2] + bounds[0]) / 2 - bounds[0],
                             (h - bounds[3] + bounds[1]) / 2 - bounds[1]), text,
                            font=f, fill=effect.get('color', '#FFD92F'),
                            stroke_width=stroke, stroke_fill='#121721',
                            spacing=round(size * .2), align='center')
    im.save(target)
    return x, y


def render(plan, base, output, work, font=None, encoder='libx264', force=False):
    punches = check_effects(plan)
    output = Path(output).resolve()
    if output == Path(base).resolve():
        raise ValueError('output must differ from input')
    if output.exists() and not force:
        raise FileExistsError(output)
    v, audio = streams(probe(base))
    width, height, fps = v['width'], v['height'], plan['fps']
    if abs(float(v['duration']) - plan['duration']) > 2 / fps:
        raise ValueError('base duration differs from plan')
    if 'width' in plan and (width, height) != (plan['width'], plan['height']):
        raise ValueError('base geometry differs from plan')
    num, den = map(float, v['r_frame_rate'].split('/'))
    if abs(num / den - fps) > .001:
        raise ValueError('base frame rate differs from plan')
    if plan.get('audioEffects') and not audio:
        raise ValueError('audio effects require a base voice track')
    font = choose_font(font) if any(e['type'] == 'text' for e in plan.get('visualEffects', [])) else None
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='effects-', dir=work) as scratch:
        scratch = Path(scratch)
        args = ['-i', str(base)]
        graph = []
        video = 'vbase'
        graph.append('[0:v]setpts=PTS-STARTPTS,setsar=1[vbase]')
        total_frames = sum(c['frames'] for c in plan['clips'])
        if punches:
            cuts = {0, total_frames}
            for e in punches:
                cuts.update([round(e['start'] * fps), round(e['end'] * fps)])
            cuts = sorted(cuts)
            graph.append(f'[vbase]split={len(cuts)-1}' + ''.join(f'[s{i}]' for i in range(len(cuts)-1)))
            labels = []
            for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
                e = next((e for e in punches if round(e['start']*fps) <= a < round(e['end']*fps)), {})
                if e.get('type') == 'frame':
                    x, y, w, h = e['box']
                    ratio = min(w, h)
                    sw, sh = max(2, int(width*ratio)//2*2), max(2, int(height*ratio)//2*2)
                    px = round(width*x + (width*w-sw)/2)
                    py = round(height*y + (height*h-sh)/2)
                    # Validate then serialize a color; never accept filter expressions.
                    color = ImageColor.getrgb(e.get('background', '#101827'))
                    matte = '0x' + ''.join(f'{c:02x}' for c in color[:3])
                    camera = f'scale={sw}:{sh},pad={width}:{height}:{px}:{py}:{matte}'
                else:
                    z = e.get('zoom', 1)
                    cw, ch = max(2, int(width / z) // 2 * 2), max(2, int(height / z) // 2 * 2)
                    cx, cy = round((width-cw)*e.get('cx', .5)), round((height-ch)*e.get('cy', .5))
                    camera = f'crop={cw}:{ch}:{cx}:{cy},scale={width}:{height}'
                graph.append(f'[s{i}]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,'
                             f'{camera},setsar=1[c{i}]')
                labels.append(f'[c{i}]')
            graph.append(''.join(labels) + f'concat=n={len(labels)}:v=1:a=0[vpunch]')
            video = 'vpunch'
        index = 1
        implemented = []
        for e in plan.get('visualEffects', []):
            implemented.append(e['id'])
            if e['type'] in {'punch', 'frame'}:
                continue
            png = scratch / f'overlay-{index}.png'
            x, y = tile(e, width, height, font, png)
            args += ['-loop', '1', '-framerate', str(fps), '-i', str(png)]
            start, end = e['start'], e['end']
            fade = min(.15, (end-start)/3)
            anim = e.get('animation', 'fade')
            fx = f'[{index}:v]format=rgba'
            if anim != 'none':
                fx += f',fade=t=in:st={start}:d={fade}:alpha=1,fade=t=out:st={end-fade}:d={fade}:alpha=1'
            graph.append(fx + f'[ol{index}]')
            yy = str(y) if anim != 'slide' else f'{y}+{round(height*.015)}*max(0,1-(t-{start})/{fade})'
            graph.append(f'[{video}][ol{index}]overlay=x={x}:y=\'{yy}\':'
                         f'enable=\'gte(t,{start})*lt(t,{end})\':eof_action=pass[v{index}]')
            video = f'v{index}'
            index += 1
        audio_label = '0:a:0'
        if plan.get('audioEffects'):
            graph.append('[0:a:0]asetpts=PTS-STARTPTS[voice]')
            labels = ['[voice]']
            for e in plan['audioEffects']:
                # FFmpeg validates the actual audio stream even for audio-only files.
                offset = e.get('offset', 0)
                if e['type'] == 'music':
                    args += ['-stream_loop', '-1']
                args += ['-i', e['path']]
                duration = e['end'] - e['start']
                fade = min(.2, duration / 3)
                graph.append(f'[{index}:a:0]atrim=start={offset}:duration={duration},asetpts=PTS-STARTPTS,'
                             f'volume={e.get("gain", .08)},afade=t=in:d={fade},'
                             f'afade=t=out:st={duration-fade}:d={fade},'
                             f'adelay={round(e["start"]*1000)}:all=1[au{index}]')
                labels.append(f'[au{index}]')
                implemented.append(e['id'])
                index += 1
            graph.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=first:normalize=0[amixed]')
            audio_label = '[amixed]'
        args += ['-filter_complex', ';'.join(graph), '-map', f'[{video}]']
        if audio:
            args += ['-map', audio_label]
            args += ['-c:a', 'aac', '-b:a', '192k'] if plan.get('audioEffects') else ['-c:a', 'copy']
        else:
            args += ['-an']
        args += ['-frames:v', str(total_frames), '-t', str(plan['duration']), '-r', str(fps),
                 '-movflags', '+faststart', '-map_metadata', '-1']
        staged = scratch / 'effects.mp4'
        try:
            encode(args, staged, encoder, intermediate=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(error.stderr.decode(errors='replace')[-6000:]) from error
        from video_pipeline import verify
        checks = verify(staged, plan)
        if output.exists() and not force:
            raise FileExistsError('output appeared during render')
        # The scratch and output directories can live on different volumes.
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.mp4', delete=False) as temporary:
            temp_path = Path(temporary.name)
            try:
                with staged.open('rb') as source:
                    import shutil
                    shutil.copyfileobj(source, temporary)
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
        os.replace(temp_path, output)
    result = dict(output=str(output), implemented=implemented, technicalChecks=checks,
                  visualReview='required', listeningReview='required',
                  note='No automatic face/occlusion check, evidence validation or loudness certification')
    result['technicalChecks']['file'] = str(output)
    write(work / 'effects-report.json', result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('plan')
    ap.add_argument('--base', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--font')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--encoder', choices=['libx264', 'h264_videotoolbox'], default='libx264')
    args = ap.parse_args()
    import json
    print(json.dumps(render(read(args.plan), args.base, args.out, args.work,
                            args.font, args.encoder, args.force), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
