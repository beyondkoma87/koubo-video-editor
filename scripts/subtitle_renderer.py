#!/usr/bin/env python3
"""Render a cached transparent caption strip; requires Pillow and FFmpeg."""
import argparse, hashlib, json, os, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from video_pipeline import call, digest, encode, probe, read, streams, validate


def choose_font(explicit=None):
    choices = [explicit] if explicit else [
        '/System/Library/Fonts/STHeiti Medium.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansSC-Bold.ttf']
    for p in choices:
        if p and Path(p).is_file(): return p
    raise FileNotFoundError('Supply a Chinese font with --font; no suitable font was found')


def render(plan, base, output, work, defaults, font=None, encoder='libx264', force=False,
           draft_unverified=False):
    validate(plan)
    sync_report={'timing_evidence_check':'unverified draft'}
    if plan.get('captions') and not draft_unverified:
        from caption_sync import audit
        sync_report=audit(plan)
    output = Path(output)
    if output.exists() and not force: raise FileExistsError(output)
    info = probe(base); v, a = streams(info); width, height = v['width'], v['height']
    if abs(float(v['duration']) - plan['duration']) > 2 / plan['fps']:
        raise ValueError('base video does not match plan duration')
    if 'width' in plan and (width, height) != (plan['width'], plan['height']):
        raise ValueError('base geometry differs from plan')
    num,den=map(float,v['avg_frame_rate'].split('/'))
    if abs(num/den-plan['fps'])>.001:
        raise ValueError('base fps differs from locked plan; rebuild from source instead of padding final frames')
    style = dict(defaults['subtitles']); style.update(plan.get('subtitleStyle', {}))
    font = choose_font(font)
    font_index = style.get('font_index', 0)
    if not isinstance(font_index, int) or isinstance(font_index, bool) or not 0 <= font_index <= 64:
        raise ValueError('font_index must be an integer from 0 to 64')
    # TTC files can contain distinct simplified/traditional faces or weights.
    ImageFont.truetype(font, 16, index=font_index)
    signature = hashlib.sha256((json.dumps([plan['captions'], style, width, height, plan['fps']], sort_keys=True)
                                + digest(font)).encode()).hexdigest()
    cache = Path(work) / signature[:20]; cache.mkdir(parents=True, exist_ok=True)
    layer = cache / 'subtitles.mov'; total = sum(c['frames'] for c in plan['clips'])
    scale = min(width / 1080, height / 1920)
    strip_height = min(height, max(100, round(300 * scale)))
    baseline = round(height * style.get('baseline_fraction', .825))
    baseline = min(height - round(40 * scale), max(round(80 * scale), baseline))
    top = max(0, min(height - strip_height, baseline - round(185 * scale)))
    local_baseline = baseline - top
    blank = Image.new('RGBA', (width, strip_height)); fonts = {}

    def getfont(size):
        if size not in fonts: fonts[size] = ImageFont.truetype(font, size, index=font_index)
        return fonts[size]

    def picture(cap, pop=1):
        keyword = cap.get('keyword', '')
        if keyword and keyword not in cap['text']: raise ValueError('keyword absent from caption')
        lines = cap['text'].split('\n')
        if len(lines) > 2: raise ValueError('split caption into shorter cues: more than two lines')
        size = max(12, round(style['highlight_size' if keyword else 'font_size'] * scale * pop))
        minimum = max(12, round(48 * scale))
        f = getfont(size); maxwidth = width * style.get('max_width_fraction', .8)
        while max(f.getlength(line) for line in lines) > maxwidth and size > minimum:
            size -= 1; f = getfont(size)
        if max(f.getlength(line) for line in lines) > maxwidth:
            raise ValueError('caption too long for readable type; split semantically before rendering')
        im = blank.copy(); d = ImageDraw.Draw(im)
        for line_index, text in enumerate(lines):
            x = (width - f.getlength(text)) / 2
            y = local_baseline - (len(lines) - line_index - 1) * round(size * 1.4)
            color = cap.get('color', style['colors'][0])
            if keyword and keyword in text:
                j = text.index(keyword)
                parts = [(text[:j], style['text_color']), (keyword, color),
                         (text[j + len(keyword):], style['text_color'])]
            else: parts = [(text, style['text_color'])]
            for chunk, fill in parts:
                if not chunk: continue
                stroke = max(1, round(style['stroke_width'] * scale))
                d.text((x + 2, y + 3), chunk, font=f, fill='#000000AA', stroke_width=stroke + 1,
                       stroke_fill='#000000AA', anchor='ls')
                d.text((x, y), chunk, font=f, fill=fill, stroke_width=stroke,
                       stroke_fill=style['stroke_color'], anchor='ls')
                advance = f.getlength(chunk)
                if keyword and chunk == keyword and style.get('keyword_underline', True):
                    d.rounded_rectangle((x, y + max(4, round(12 * scale)), x + advance,
                                         y + max(7, round(19 * scale))), radius=max(1, round(3 * scale)), fill=fill)
                x += advance
        return im.tobytes()

    valid_layer = False
    if layer.exists():
        try:
            lv, _ = streams(probe(layer))
            valid_layer = int(lv.get('nb_frames', -1)) == total
        except (ValueError, KeyError, subprocess.CalledProcessError): pass
    if not valid_layer:
        with (cache / 'layer.log').open('wb') as log:
            proc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgba',
                                     '-video_size', f'{width}x{strip_height}', '-framerate', str(plan['fps']),
                                     '-i', '-', '-an', '-c:v', 'qtrle', str(layer)], stdin=subprocess.PIPE, stderr=log)
            caps = plan.get('captions', []); index = 0; current = -1; images = {}; empty = blank.tobytes()
            try:
                for n in range(total):
                    t = n / plan['fps']
                    while index < len(caps) and t >= caps[index]['end']: index += 1
                    if index < len(caps) and caps[index]['start'] <= t < caps[index]['end']:
                        if current != index: current = index; images.clear()
                        cap = caps[index]; age = t - cap['start']
                        pop = (1.08 if age < .07 else 1.035) if cap.get('keyword') and age < style.get('keyword_pop_seconds', .17) else 1
                        if pop not in images: images[pop] = picture(cap, pop)
                        raw = images[pop]
                    else: raw = empty
                    proc.stdin.write(raw)
                    if n % max(1, round(plan['fps'] * 30)) == 0: print(f'subtitles {n}/{total}', flush=True)
                proc.stdin.close()
                if proc.wait(): raise RuntimeError(f'subtitle encoding failed; see {cache / "layer.log"}')
            except BaseException:
                if proc.stdin and not proc.stdin.closed: proc.stdin.close()
                if proc.poll() is None: proc.terminate(); proc.wait()
                layer.unlink(missing_ok=True)
                raise
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='koubo-', dir=output.parent) as temp:
        staged = Path(temp) / 'final.mp4'
        args = ['-i', str(base), '-i', str(layer), '-filter_complex',
                f'[0:v]setpts=PTS-STARTPTS[v0];[v0][1:v]overlay=x=0:y={top}:format=auto[v]',
                '-map', '[v]']
        args += ['-map', '0:a:0', '-c:a', 'copy'] if a else ['-an']
        args += ['-t', str(plan['duration']),'-frames:v',str(total),'-r',str(plan['fps']),
                 '-fps_mode','cfr','-movflags', '+faststart', '-map_metadata', '-1']
        encode(args, staged, encoder)
        if output.exists() and not force: raise FileExistsError('output appeared during render')
        os.replace(staged, output)
    return dict(output=str(output.resolve()), subtitleLayer=str(layer.resolve()),
                baselinePixels=baseline, visualReview='required',subtitleSync=sync_report,
                draft_unverified=draft_unverified,audio_reviewed=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('plan'); ap.add_argument('--base', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--work', required=True); ap.add_argument('--font'); ap.add_argument('--force', action='store_true')
    ap.add_argument('--draft-unverified',action='store_true',help='Work previews only: bypass missing caption evidence; never label as sync verified')
    ap.add_argument('--encoder', choices=['libx264', 'h264_videotoolbox'], default='libx264')
    ap.add_argument('--defaults', default=str(Path(__file__).resolve().parent.parent / 'assets/defaults.json'))
    args = ap.parse_args()
    result = render(read(args.plan), args.base, args.out, args.work, read(args.defaults), args.font, args.encoder, args.force,args.draft_unverified)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
