"""Download the public German test data for the Dolmi test bench.

    python eval/fetch_public.py            # FLEURS de_de test split (~570 MB) + English references

FLEURS (google/fleurs, CC BY 4.0) comes from the official Hugging Face dataset via huggingface_hub; the
download stays in the Hugging Face cache (outside git) and the WAVs are unpacked to
eval/data/fleurs/de_de/test/ (git-ignored). English references are the en_us transcriptions of the
same FLoRes sentence ids. eval/manifest.json records the source files and their sha256.

CoVoST 2 de<->en is not fetched: its audio is Common Voice 4, which is gated (an account and accepted
terms on the Mozilla site), so it cannot be downloaded unattended. See eval/README.md.
"""
import argparse
import csv
import hashlib
import json
import shutil
import struct
import sys
import tarfile
import wave
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))
import paths  # noqa: E402

OUT = paths.data_dir() / "fleurs" / "de_de" / "test"
MANIFEST = EVAL / "manifest.json"
REPO = "google/fleurs"
FILES = ["data/de_de/test.tsv", "data/de_de/audio/test.tar.gz", "data/en_us/test.tsv"]
CAP_BYTES = 2 * 1024 ** 3


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def remote_sizes():
    from huggingface_hub import HfApi
    info = HfApi().dataset_info(REPO, files_metadata=True)
    return {s.rfilename: s.size or 0 for s in info.siblings if s.rfilename in FILES}


def check_disk(need, *paths):
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(p).free
        if free < need:
            raise SystemExit(f"Not enough free disk at {p}: {free / 1e9:.1f} GB free, {need / 1e9:.1f} GB needed")


def read_tsv(path):
    """FLEURS rows: id, file name, raw transcription, normalized transcription, ..., samples, gender."""
    with open(path, encoding="utf-8", newline="") as f:
        return [r for r in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE) if len(r) >= 3]


def decode_wav(data):
    """(sample rate, mono float32 samples) from WAV bytes: 16-bit PCM or 32-bit float (FLEURS uses float)."""
    import numpy as np
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt, raw = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            raw = body
        pos += 8 + size + (size & 1)
    if not fmt or raw is None:
        raise ValueError("WAV without fmt/data chunk")
    tag, channels, rate, _, _, bits = fmt
    if tag == 0xFFFE:   # WAVE_FORMAT_EXTENSIBLE: trust the bit depth
        tag = 3 if bits == 32 else 1
    if tag == 3 and bits == 32:
        a = np.frombuffer(raw[:len(raw) // 4 * 4], "<f4").astype(np.float32)
    elif tag == 1 and bits == 16:
        a = np.frombuffer(raw[:len(raw) // 2 * 2], "<i2").astype(np.float32) / 32768
    else:
        raise ValueError(f"unsupported WAV format {tag}/{bits} bit")
    if channels > 1:
        a = a[:len(a) // channels * channels].reshape(-1, channels).mean(axis=1)
    return rate, a


def write_pcm16(path, rate, audio):
    import numpy as np
    if rate != 16000:
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def safe_extract_wavs(archive, dest):
    """Unpack only plain .wav files, by base name, as 16 kHz 16-bit PCM (archive members are untrusted:
    no paths, no links, and nothing is executed)."""
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar:
            name = Path(m.name).name
            if not m.isfile() or not name.lower().endswith(".wav") or name.startswith("."):
                continue
            target = dest / name
            if target.exists() and target.stat().st_size > 44:
                n += 1
                continue
            with tar.extractfile(m) as src:
                rate, audio = decode_wav(src.read())
            write_pcm16(target, rate, audio)
            n += 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cap-gb", type=float, default=CAP_BYTES / 1024 ** 3, help="refuse downloads above this")
    args = ap.parse_args(argv)
    from huggingface_hub import hf_hub_download
    from huggingface_hub.constants import HF_HUB_CACHE

    sizes = remote_sizes()
    total = sum(sizes.values())
    if total > args.cap_gb * 1024 ** 3:
        raise SystemExit(f"FLEURS files are {total / 1e9:.2f} GB, above the {args.cap_gb} GB cap")
    check_disk(2 * total + 500 * 1024 ** 2, Path(HF_HUB_CACHE), OUT)   # archive + unpacked WAVs
    print(f"Downloading {len(FILES)} files ({total / 1e6:.0f} MB) from {REPO} into the Hugging Face cache ...")
    local = {f: Path(hf_hub_download(REPO, f, repo_type="dataset")) for f in FILES}

    print(f"Unpacking WAVs to {OUT / 'audio'} ...")
    n_wavs = safe_extract_wavs(local["data/de_de/audio/test.tar.gz"], OUT / "audio")
    en = {}
    for r in read_tsv(local["data/en_us/test.tsv"]):
        en.setdefault(r[0], r[2])
    rows = []
    for r in read_tsv(local["data/de_de/test.tsv"]):
        wav = OUT / "audio" / r[1]
        if not wav.exists():
            continue
        with wave.open(str(wav), "rb") as w:
            dur = w.getnframes() / w.getframerate()
        rows.append({"id": f"fleurs-{r[0]}-{Path(r[1]).stem}", "file": paths.name_of(wav),
                     "text": r[2], "en": en.get(r[0]), "duration": round(dur, 2)})
    (OUT / "index.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")

    man = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"version": 1, "sets": {}}
    man.setdefault("sets", {})["fleurs"] = {
        "source": f"https://huggingface.co/datasets/{REPO}", "licence": "CC BY 4.0", "config": "de_de",
        "split": "test", "generator": "eval/fetch_public.py",
        "files": [{"path": f, "bytes": local[f].stat().st_size, "sha256": sha256(local[f])} for f in FILES],
        "clips": len(rows), "with_english": sum(1 for x in rows if x["en"]),
        "hours": round(sum(x["duration"] for x in rows) / 3600, 2),
        "index": paths.name_of(OUT / "index.jsonl"),
        "note": "references come from the TSVs at run time (index.jsonl is built locally, not committed)",
    }
    man["sets"]["covost2"] = {"status": "skipped",
                              "reason": "audio is Common Voice 4 (gated download, needs an account and accepted terms)"}
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(rows)} clips ({n_wavs} WAVs), {sum(1 for x in rows if x['en'])} with English references, "
          f"{man['sets']['fleurs']['hours']} h -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
