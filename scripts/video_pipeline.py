#!/usr/bin/env python3
"""Local media helpers. Semantic editing remains an editorial decision."""
import argparse, hashlib, json, math, os, subprocess, tempfile
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def export_defaults():
    return read(Path(__file__).resolve().parent.parent / 'assets/defaults.json')['export']


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def call(args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def probe(path):
    return json.loads(call(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def streams(info):
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    a = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)
    return v, a


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def delivery_fps(source_fps=None):
    """Keep native cadence, including 50 and 60000/1001; do not pad to 60."""
    if source_fps is None:
        return float(export_defaults()['fps'])
    rate=float(source_fps)
    if not math.isfinite(rate) or rate<=0: raise ValueError('invalid source fps')
    return min(rate,60.0)


def validate_retakes(plan):
    for decision in plan.get('retakeDecisions',[]):
        selected=decision['selected']
        if not decision.get('reviewed'):
            raise ValueError('retake decision has not been reviewed')
        if not 0<=selected['start']<selected['end']:
            raise ValueError('invalid selected retake interval')
        if not any(min(c['sourceEnd'],selected['end'])>max(c['sourceStart'],selected['start'])
                   for c in plan['clips']):
            raise ValueError('selected final retake is not retained')
        for old in decision.get('superseded',[]):
            if not 0<=old['start']<old['end']<=selected['start']:
                raise ValueError('selected retake must come after superseded versions')
            if any(min(c['sourceEnd'],old['end'])-max(c['sourceStart'],old['start'])>.001
                   for c in plan['clips']):
                raise ValueError('superseded earlier retake remains in the edit')


def validate(plan):
    fps = float(plan['fps'])
    if fps <= 0 or not math.isfinite(fps):
        raise ValueError('fps must be positive')
    total = 0
    for c in plan['clips']:
        values = [c['sourceStart'], c['sourceEnd'], c['duration'], c['outputStart']]
        if not all(math.isfinite(float(x)) for x in values):
            raise ValueError('non-finite clip time')
        if c['sourceStart'] < 0 or c['sourceEnd'] <= c['sourceStart'] or c['frames'] < 1:
            raise ValueError('invalid source range')
        if abs(c['outputStart'] - total) > 0.0001:
            raise ValueError('output timeline has a gap or overlap')
        if abs(c['duration'] - c['frames'] / fps) > 0.0001:
            raise ValueError('clip duration is not frame aligned')
        if abs(c['sourceEnd'] - c['sourceStart'] - c['duration']) > 0.0001:
            raise ValueError('source and output durations differ')
        total += c['duration']
    if not plan['clips'] or abs(total - plan['duration']) > 0.0001:
        raise ValueError('invalid total duration')
    previous = 0
    for c in plan.get('captions', []):
        if not isinstance(c['text'], str) or not c['text'].strip():
            raise ValueError('empty caption')
        if not all(math.isfinite(float(c[k])) for k in ('start', 'end')):
            raise ValueError('non-finite caption time')
        if c['start'] < previous - .001 or c['end'] <= c['start'] or c['end'] > total + .001:
            raise ValueError('captions overlap or exceed the timeline')
        previous = c['end']
    validate_retakes(plan)
    return plan


def make_plan(edit, source_duration=None, geometry=None, source_fps=None):
    fps = float(edit.get('fps', delivery_fps(source_fps)))
    if fps <= 0 or not math.isfinite(fps):
        raise ValueError('invalid fps')
    clips = []
    total = 0
    for entry in edit['keep']:
        a, b = (entry['start'], entry['end']) if isinstance(entry, dict) else entry
        a, b = float(a), float(b)
        if not all(math.isfinite(x) for x in (a, b)) or a < 0 or b <= a:
            raise ValueError('invalid keep interval')
        if source_duration is not None and b > source_duration + .001:
            raise ValueError('keep interval exceeds source duration')
        # Never extend a reviewed keep range into rejected speech.
        frames = math.floor((b - a) * fps + 1e-6)
        if frames < 1:
            raise ValueError('keep interval shorter than a frame')
        duration = frames / fps
        clips.append(dict(sourceStart=a, sourceEnd=a + duration, outputStart=total,
                          duration=duration, frames=frames,
                          reason=entry.get('reason', '') if isinstance(entry, dict) else ''))
        total += duration
    captions = edit.get('captions', [])
    if captions and edit.get('source_captions'):
        raise ValueError('use output captions OR source_captions, not both')
    if edit.get('source_captions'):
        captions = []
        for cap in edit['source_captions']:
            pieces = []
            for c in clips:
                x, y = max(cap['start'], c['sourceStart']), min(cap['end'], c['sourceEnd'])
                if y > x:
                    pieces.append((c['outputStart'] + x - c['sourceStart'],
                                   c['outputStart'] + y - c['sourceStart']))
            if not pieces:
                continue
            clipped = abs(sum(y - x for x, y in pieces) - (cap['end'] - cap['start'])) > .001
            if len(pieces) > 1 or clipped:
                raise ValueError('caption crosses a cut: rebuild captions from the locked final voice; do not reuse full source text')
            mapped = dict(cap)
            mapped.update(start=pieces[0][0], end=pieces[-1][1])
            captions.append(mapped)
        captions.sort(key=lambda c: c['start'])
    plan = dict(version=1, source=str(Path(edit['source']).expanduser().resolve()), fps=fps,
                duration=total, clips=clips, captions=captions,
                events=edit.get('events', []), notes=edit.get('notes', []))
    plan.update(geometry or {})
    # These fields already use the locked output timeline; never silently drop
    # a caller's subtitle style or executable effects when creating a plan.
    for key in ('subtitleStyle', 'visualEffects', 'audioEffects', 'layers', 'canvasColor',
                'subtitleSync', 'retakeDecisions'):
        if key in edit:
            plan[key] = edit[key]
    plan['frameRateDecision']={'sourceFps':source_fps,'outputFps':fps,
        'conversion':'native cadence' if source_fps is not None and abs(fps-source_fps)<.001 else 'conversion must be reviewed',
        'interpolationPerformed':False}
    return validate(plan)


def stamp(t):
    ms = round(t * 1000)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'


def srt(plan, path, force=False, draft_unverified=False):
    if plan.get('captions') and not draft_unverified:
        from caption_sync import audit
        audit(plan)
    mode = 'w' if force else 'x'
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open(mode, encoding='utf-8') as f:
        for i, c in enumerate(validate(plan).get('captions', []), 1):
            f.write(f'{i}\n{stamp(c["start"])} --> {stamp(c["end"])}\n{c["text"]}\n\n')


def concat_quote(path):
    value = str(Path(path).resolve())
    if '\n' in value or '\r' in value:
        raise ValueError('concat path contains a newline')
    return "'" + value.replace("'", "'\\''") + "'"


def encoder_args(encoder, intermediate=False):
    settings = export_defaults()
    if intermediate:
        return ['-c:v', 'libx264', '-preset', settings['intermediate_preset'],
                '-crf', str(settings['intermediate_crf'])]
    if encoder == 'h264_videotoolbox':
        return ['-c:v', encoder, '-b:v', settings['hardware_video_bitrate']]
    if settings.get('rate_control') == 'high_bitrate_vbr':
        return ['-c:v','libx264','-preset',settings['preset'],
                '-b:v',settings['video_bitrate'],'-maxrate',settings['video_maxrate'],
                '-bufsize',settings['video_bufsize']]
    return ['-c:v', 'libx264', '-preset', settings['preset'], '-crf', str(settings['crf'])]


def encode(args, target, encoder='libx264', intermediate=False):
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y'] + args
    try:
        call(cmd + encoder_args(encoder, intermediate) + ['-pix_fmt', 'yuv420p', str(target)])
    except subprocess.CalledProcessError:
        if encoder != 'h264_videotoolbox':
            raise
        # GPU/VideoToolbox may be unavailable in a sandbox. Software fallback.
        call(cmd + encoder_args('libx264', intermediate) + ['-pix_fmt', 'yuv420p', str(target)])


def render_base(plan, output, work, encoder, force=False):
    validate(plan)
    output = Path(output)
    if output.exists() and not force:
        raise FileExistsError(f'output exists: {output}')
    info = probe(plan['source'])
    v, a = streams(info)
    for c in plan['clips']:
        if c['sourceEnd'] > float(info['format']['duration']) + .001:
            raise ValueError('clip exceeds current source')
    signature = hashlib.sha256((json.dumps(plan, sort_keys=True) + digest(plan['source'])
                                + json.dumps(encoder_args(encoder, True))).encode()).hexdigest()
    cache = Path(work) / signature[:20]
    cache.mkdir(parents=True, exist_ok=True)
    fps = plan['fps']
    width, height = plan.get('width', v['width']), plan.get('height', v['height'])
    # The plan command supplies display geometry, including rotation.
    if 'width' not in plan:
        rotation = next((float(s.get('rotation', 0)) for s in v.get('side_data_list', []) if 'rotation' in s), 0)
        if round(rotation) % 180:
            width, height = height, width
    files = []
    for i, c in enumerate(plan['clips']):
        target = cache / f'clip-{i:04}.mp4'
        valid = False
        if target.exists():
            try:
                cv, _ = streams(probe(target))
                valid = (int(cv.get('nb_frames', -1)) == c['frames']
                         and abs(float(cv['duration']) - c['duration']) <= 1 / fps)
            except (ValueError, KeyError, subprocess.CalledProcessError):
                pass
        if not valid:
            encode(['-ss', str(c['sourceStart']), '-i', plan['source'], '-t', str(c['duration']),
                    '-vf', f'setpts=PTS-STARTPTS,fps=fps={fps}:start_time=0,trim=end_frame={c["frames"]},'
                           f'setpts=N/({fps}*TB),scale={width}:{height},setsar=1',
                    '-frames:v',str(c['frames']),'-fps_mode','cfr','-an',
                    '-map', '0:v:0', '-map_metadata', '-1'], target, encoder, intermediate=True)
        files.append(target)
        print(f'picture {i + 1}/{len(plan["clips"])}', flush=True)
    listing = cache / 'concat.txt'
    listing.write_text(''.join(f'file {concat_quote(f)}\n' for f in files), encoding='utf-8')
    wav = cache / 'edited.wav'
    if a and not wav.exists():
        # Stitch raw audio once, avoiding per-segment AAC padding and drift.
        graph = []
        for i, c in enumerate(plan['clips']):
            fade = min(.006, c['duration'] / 4)
            graph.append(f'[0:a:0]atrim=start={c["sourceStart"]}:duration={c["duration"]},'
                         f'asetpts=PTS-STARTPTS,afade=t=in:d={fade},'
                         f'afade=t=out:st={c["duration"] - fade}:d={fade}[a{i}]')
        graph.append(''.join(f'[a{i}]' for i in range(len(plan['clips']))) +
                     f'concat=n={len(plan["clips"])}:v=0:a=1[a]')
        call(['ffmpeg', '-v', 'error', '-y', '-i', plan['source'], '-filter_complex', ';'.join(graph),
              '-map', '[a]', '-c:a', 'pcm_s16le', '-ar', '48000', str(wav)])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='koubo-', dir=output.parent) as temp:
        staged = Path(temp) / 'base.mp4'
        cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing)]
        if a:
            cmd += ['-i', str(wav), '-map', '0:v:0', '-map', '1:a:0', '-c:a', 'aac', '-b:a', export_defaults()['audio_bitrate']]
        else:
            cmd += ['-map', '0:v:0', '-an']
        call(cmd + ['-c:v', 'copy', '-t', str(plan['duration']), '-movflags', '+faststart',
                    '-map_metadata', '-1', str(staged)])
        if output.exists() and not force:
            raise FileExistsError('output appeared during render; refusing to replace it')
        os.replace(staged, output)
    voice=str(wav.resolve()) if a else None
    write(cache / 'manifest.json', dict(signature=signature, duration=plan['duration'], output=str(output.resolve()),voiceReference=voice))
    return dict(output=str(output.resolve()), duration=plan['duration'], cache=str(cache.resolve()),voiceReference=voice)


def bitrate_measurement(path, duration):
    packets=json.loads(call(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','packet=pts_time,size','-of','json',str(path)]))['packets']
    buckets={};total=0
    for packet in packets:
        size=int(packet['size']);total+=size
        if 'pts_time' in packet:
            second=math.floor(float(packet['pts_time']))
            buckets[second]=buckets.get(second,0)+size
    return {'videoPacketBytes':total,'averageVideoBitrateBps':round(total*8/duration),
            'peakOneSecondBucketBitrateBps':max(buckets.values(),default=0)*8,
            'measurement':'video packets only; fixed 1-second bins, not rolling peak or whole-file bitrate'}


def cadence_samples(path,duration):
    results=[]
    for start in sorted(set(round(max(0,min(t,duration-1)),3) for t in (0,duration/2,duration-1))):
        raw=call(['ffmpeg','-v','error','-ss',str(start),'-i',str(path),'-t','1',
                  '-vf','crop=iw*.6:ih*.5:iw*.2:ih*.15,scale=96:64,format=gray',
                  '-an','-fps_mode','passthrough','-f','rawvideo','-pix_fmt','gray','-'])
        size=96*64;frames=[raw[i:i+size] for i in range(0,len(raw),size) if len(raw[i:i+size])==size]
        still=0;longest=0;run=0
        for a,b in zip(frames,frames[1:]):
            mad=sum(abs(x-y) for x,y in zip(a,b))/size
            run=run+1 if mad<.35 else 0
            still+=mad<.35;longest=max(longest,run)
        results.append({'start':start,'frames':len(frames),
            'nearDuplicateCandidateFraction':round(still/max(1,len(frames)-1),4),
            'longestNearDuplicateCandidateRun':longest})
    return {'samples':results,'region':'central subject region, excluding lower captions',
            'interpretation':'screening only; natural stillness and compression can affect differences; compare source motion and play joins',
            'motion_playback_reviewed':False}


def verify(path, plan=None, decode=True):
    info = probe(path)
    v, a = streams(info)
    result = dict(file=str(Path(path).resolve()), width=v['width'], height=v['height'],
                  fps=v['r_frame_rate'], averageFps=v['avg_frame_rate'],codec=v['codec_name'],
                  duration=float(info['format']['duration']),
                  videoStart=float(v.get('start_time', 0)), audioPresent=a is not None)
    result['bitrate']=bitrate_measurement(path,float(v.get('duration',result['duration'])))
    if plan:
        validate(plan)
        if abs(result['duration'] - plan['duration']) > 2 / plan['fps']:
            raise ValueError('render duration differs from plan')
        if abs(float(v['duration']) - plan['duration']) > 2 / plan['fps']:
            raise ValueError('video duration differs from plan')
        num, den = map(float, v['r_frame_rate'].split('/'))
        if abs(num / den - plan['fps']) > .001:
            raise ValueError('frame rate differs from plan')
        if 'width' in plan and (v['width'], v['height']) != (plan['width'], plan['height']):
            raise ValueError('geometry differs from plan')
    if a:
        result['audioStart'] = float(a.get('start_time', 0))
        result['startOffsetSeconds'] = abs(result['videoStart'] - result['audioStart'])
        if result['startOffsetSeconds'] > .05:
            raise ValueError('audio/video start mismatch')
    if decode:
        call(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'])
        result['fullDecode'] = 'passed'
        if plan:
            frame_info = json.loads(call(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(path)]))
            times = [float(f['best_effort_timestamp_time']) for f in frame_info['frames']]
            expected = sum(c['frames'] for c in plan['clips'])
            if len(times) != expected:
                raise ValueError('decoded frame count differs from locked timeline')
            deviation = max(abs(t - times[0] - i / plan['fps']) for i, t in enumerate(times))
            if any(b <= a for a, b in zip(times, times[1:])) or deviation > .1 / plan['fps']:
                raise ValueError('decoded timestamps are not on the locked constant frame grid')
            result.update(actualFrames=len(times), constantFrameRate='passed',
                          maxFrameGridDeviationSeconds=deviation)
        result['motionCadenceScreening']=cadence_samples(path,result['duration'])
    result['subtitleSyncCheck']='not_performed'
    if plan and plan.get('captions') and plan.get('subtitleSync'):
        from caption_sync import audit
        result['subtitleSyncCheck']=audit(plan)
    result['audio_reviewed']=False
    result['motion_playback_reviewed']=False
    result['editorialAndVisualReview'] = 'not_performed_by_this_script'
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('probe'); p.add_argument('source')
    p = sub.add_parser('plan'); p.add_argument('edit'); p.add_argument('--out', required=True); p.add_argument('--long-edge', type=int, default=1920)
    p = sub.add_parser('srt'); p.add_argument('plan'); p.add_argument('--out', required=True); p.add_argument('--force', action='store_true'); p.add_argument('--draft-unverified',action='store_true')
    p = sub.add_parser('render-base'); p.add_argument('plan'); p.add_argument('--out', required=True); p.add_argument('--work', required=True); p.add_argument('--encoder', choices=['libx264', 'h264_videotoolbox'], default='libx264'); p.add_argument('--force', action='store_true')
    p = sub.add_parser('verify'); p.add_argument('video'); p.add_argument('--plan'); p.add_argument('--out'); p.add_argument('--skip-decode', action='store_true')
    args = ap.parse_args()
    if args.command == 'probe': result = probe(args.source)
    elif args.command == 'plan':
        if args.long_edge <= 0: raise ValueError('long edge must be positive')
        edit = read(args.edit); info = probe(edit['source']); v, _ = streams(info)
        width, height = v['width'], v['height']
        rotation = next((float(s.get('rotation', 0)) for s in v.get('side_data_list', []) if 'rotation' in s), 0)
        if round(rotation) % 180: width, height = height, width
        scale = min(1, args.long_edge / max(width, height))
        geometry = dict(width=max(2, round(width * scale / 2) * 2), height=max(2, round(height * scale / 2) * 2))
        n,d=map(float,v['r_frame_rate'].split('/'))
        if n<=0 or d<=0: n,d=map(float,v['avg_frame_rate'].split('/'))
        result = make_plan(edit, float(info['format']['duration']), geometry, n/d)
        result['frameRateDecision'].update(sourceNominalFps=v['r_frame_rate'],sourceAverageFps=v['avg_frame_rate'])
        write(args.out, result)
    elif args.command == 'srt': srt(read(args.plan), args.out, args.force,args.draft_unverified); result = dict(output=args.out,draft_unverified=args.draft_unverified)
    elif args.command == 'render-base': result = render_base(read(args.plan), args.out, args.work, args.encoder, args.force)
    else:
        result = verify(args.video, read(args.plan) if args.plan else None, not args.skip_decode)
        if args.out: write(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
