#!/usr/bin/env python3
"""Run in an existing Python environment containing whisper or mlx_whisper."""
import argparse, importlib.util, json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('audio'); p.add_argument('--backend', choices=['mlx', 'whisper'], required=True)
    p.add_argument('--model', required=True, help='Existing local model directory or checkpoint file')
    p.add_argument('--out', required=True); p.add_argument('--language', default='zh')
    p.add_argument('--context', default='', help='Known vocabulary; do not seed uncertain sentences as truth')
    p.add_argument('--force', action='store_true')
    a = p.parse_args(); output = Path(a.out)
    if output.exists() and not a.force: raise FileExistsError(output)
    model = Path(a.model).expanduser().resolve()
    if not model.exists(): raise FileNotFoundError('Local model missing; this helper does not download models')
    name = 'mlx_whisper' if a.backend == 'mlx' else 'whisper'
    if importlib.util.find_spec(name) is None:
        raise RuntimeError(f'Run with an existing Python environment containing {name}')
    if a.backend == 'mlx':
        import mlx_whisper
        result = mlx_whisper.transcribe(a.audio, path_or_hf_repo=str(model), language=a.language,
                                       word_timestamps=True, initial_prompt=a.context or None, verbose=False)
    else:
        import whisper
        m = whisper.load_model(str(model), device='cpu')
        result = m.transcribe(a.audio, language=a.language, word_timestamps=True,
                              initial_prompt=a.context or None, fp16=False, verbose=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(output.resolve()), 'segments': len(result['segments'])}))


if __name__ == '__main__': main()
