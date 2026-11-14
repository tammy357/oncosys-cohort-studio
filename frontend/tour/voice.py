"""Narration for the product tours (Gradium text-to-speech, checked with Gradium speech-to-text).

Two takes per step; each is transcribed back and the take whose transcript matches the script best is kept, trimmed
of silence, loudness-matched and written as a small MP3. Word timings of the kept take are saved, so the footage can
be cut to the voice.

    python frontend/tour/voice.py                 # every step in frontend/static/tour/tours.json
    python frontend/tour/voice.py canvas-fix      # only these step ids

Needs GRADIUM_KEY in the repository's .env (read with the project's own loader; never printed), ffmpeg on PATH,
and: pip install requests numpy scipy
Writes frontend/static/tour/<step>.mp3 and frontend/tour/voice.json.
"""
import difflib
import json
import os
import pathlib
import re
import subprocess
import sys
import time

import numpy as np
import requests
from scipy.io import wavfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from cohort_studio.env import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")
KEY = os.environ.get("GRADIUM_KEY") or os.environ.get("GRADIUM_API_KEY")
API = "https://api.gradium.ai/api"
HERE = pathlib.Path(__file__).resolve().parent
STATIC = REPO / "frontend" / "static" / "tour"
TAKES = HERE / ".takes"                      # raw takes (git-ignored by frontend/.gitignore rules below)
CFG = json.loads((STATIC / "tours.json").read_text(encoding="utf-8"))


def headers(extra=None):
    if not KEY:
        raise SystemExit("GRADIUM_KEY is not set in the repository's .env")
    return {"x-api-key": KEY, **(extra or {})}


def tts(text, out_path, tries=8):
    v = CFG["voice"]
    body = {"text": text, "voice_id": v["id"], "output_format": "wav", "only_audio": True,
            "json_config": json.dumps({"padding_bonus": v["speed"], "temp": v["temp"]})}
    for attempt in range(tries):
        r = requests.post(f"{API}/post/speech/tts", headers=headers({"Content-Type": "application/json"}), json=body, timeout=180)
        if r.status_code == 200:
            pathlib.Path(out_path).write_bytes(r.content)
            return out_path
        if ("Concurrency" in r.text or r.status_code >= 500) and attempt < tries - 1:
            time.sleep(2 + 2 * attempt)
            continue
        raise RuntimeError(f"Gradium TTS failed: HTTP {r.status_code}: {r.text[:200]}")


def transcribe(path, tries=6):
    """Words with start/end seconds, from Gradium speech-to-text."""
    for attempt in range(tries):
        try:
            words, cur = [], None
            with requests.post(f"{API}/post/speech/asr", params={"json_config": json.dumps({"language": "en"})},
                               data=pathlib.Path(path).read_bytes(), headers=headers({"Content-Type": "audio/wav"}),
                               stream=True, timeout=120) as r:
                r.raise_for_status()
                for raw in r.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    m = json.loads(raw)
                    if m.get("type") == "text":
                        cur = {"w": m["text"], "start": round(m.get("start_s", 0), 3)}
                        words.append(cur)
                    elif m.get("type") == "end_text" and cur is not None:
                        cur["end"] = round(m.get("stop_s", cur["start"]), 3)
                    elif m.get("type") == "error":
                        raise RuntimeError(m.get("message"))
            return words
        except (requests.HTTPError, requests.ConnectionError):
            time.sleep(3 + attempt)
    raise RuntimeError("Gradium speech-to-text kept failing")


def tokens(text):
    text = text.lower().replace("s.q.l.", "sql").replace("s q l", "sql").replace("x-ray", "xray")
    return re.findall(r"[a-z0-9]+", text)


def wer(ref, hyp):
    r, h = tokens(ref), tokens(hyp)
    sm = difflib.SequenceMatcher(a=r, b=h)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal") / max(1, len(r))


def load(path):
    sr, x = wavfile.read(path)
    x = x.astype(np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    peak = np.abs(x).max() or 1.0
    return sr, x / peak


def trim(sr, x, thresh_db=-42, pad=0.08):
    win = max(1, int(sr * 0.01))
    env = np.convolve(np.abs(x), np.ones(win) / win, mode="same")
    loud = np.where(env > 10 ** (thresh_db / 20))[0]
    if not len(loud):
        return x
    a, b = max(0, loud[0] - int(pad * sr)), min(len(x), loud[-1] + int(pad * sr * 2))
    y = x[a:b].copy()
    fade = int(sr * 0.012)
    y[:fade] *= np.linspace(0, 1, fade)
    y[-fade:] *= np.linspace(1, 0, fade)
    return y


def steps(only):
    for tour in CFG["tours"].values():
        for st in tour["steps"]:
            if not only or st["id"] in only:
                yield st


def main(only):
    TAKES.mkdir(parents=True, exist_ok=True)
    meta_path = HERE / "voice.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    for st in steps(only):
        say = st.get("say") or st["narration"]
        takes = []
        for n in (1, 2):
            raw = TAKES / f"{st['id']}_{n}.wav"
            tts(say, raw)
            heard = " ".join(w["w"] for w in transcribe(raw))
            takes.append({"path": raw, "heard": heard, "wer": wer(st["narration"], heard)})
        takes.sort(key=lambda t: t["wer"])
        best = takes[0]
        sr, x = load(best["path"])
        y = trim(sr, x)
        y = 0.89 * y / (np.abs(y).max() or 1.0)
        clean = TAKES / f"{st['id']}.wav"
        wavfile.write(clean, sr, (y * 32767).astype(np.int16))
        # loudness-matched MP3, mono: small enough to fetch when a step opens
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clean), "-af", "loudnorm=I=-16:TP=-1.5:LRA=7",
                        "-ar", "44100", "-ac", "1", "-b:a", "64k", str(STATIC / f"{st['id']}.mp3")], check=True)
        words = transcribe(clean)
        meta[st["id"]] = {"seconds": round(len(y) / sr, 3), "wer": round(best["wer"], 3), "heard": best["heard"],
                          "other_take_wer": [round(t["wer"], 3) for t in takes[1:]], "words": words}
        print(f"{st['id']:16s} {len(y) / sr:5.2f}s  wer {best['wer']:.2f}  heard: {best['heard']}")
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print("total narration", round(sum(m["seconds"] for m in meta.values()), 1), "s")


if __name__ == "__main__":
    main(set(sys.argv[1:]))
