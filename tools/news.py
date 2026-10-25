#!/usr/bin/env python3
"""Publish one daily brief for the study app.

  python3 tools/news.py brief.json [SITE_DIR]          narrate, encrypt with tools/public.pem, update news/index.json
  python3 tools/news.py brief.json SITE_DIR --plain    same, unencrypted (local preview only)

brief.json:
  {"date": "YYYY-MM-DD", "title": "one line",
   "items": [{"tag": "AI|Data|Policy|Auto|Standards|Business|Science", "h": "headline", "sum": "2 to 3 sentences",
              "why": "one sentence: why it matters for Jan", "src": [{"t": "Publisher: title", "u": "https://..."}]}],
   "script": ["spoken paragraph", "..."]}       # 550 to 750 words in total, about five minutes
Needs: kokoro-onnx, lameenc, numpy, cryptography; model files in $KOKORO_DIR (kokoro-v1.0.onnx, voices-v1.0.bin).
"""
import json, os, re, secrets, sys

BAD = {"—": "em dash", "–": "en dash", "‘": "curly quote", "’": "curly quote", "“": "curly quote", "”": "curly quote", "…": "ellipsis"}
TAGS = {"AI", "Data", "Policy", "Auto", "Standards", "Business", "Science"}


def check(b):
    errs = []
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", b.get("date", "")):
        errs.append("date must be YYYY-MM-DD")
    items = b.get("items") or []
    if not 3 <= len(items) <= 8:
        errs.append("3 to 8 items expected")
    for i, it in enumerate(items):
        if it.get("tag") not in TAGS:
            errs.append(f"item {i}: tag must be one of {sorted(TAGS)}")
        if not it.get("h") or not it.get("sum"):
            errs.append(f"item {i}: needs h and sum")
        if not it.get("src") or not all(str(s.get("u", "")).startswith("http") for s in it["src"]):
            errs.append(f"item {i}: needs at least one source with a URL")
    words = sum(len(p.split()) for p in b.get("script") or [])
    if not 300 <= words <= 950:
        errs.append(f"script has {words} words; aim for 550 to 750")
    elif not 500 <= words <= 800:
        print(f"WARNING script has {words} words; about five minutes is 550 to 750")
    for ch, name in BAD.items():
        if ch in json.dumps(b, ensure_ascii=False):
            errs.append(f"contains {name}")
    return errs


def speak(t):
    t = re.sub(r"\*\*|\*|`", "", t)
    t = t.replace("e.g.", "for example").replace("i.e.", "that is")
    t = re.sub(r"(\d)\s?%", r"\1 percent", t)
    return re.sub(r"\s+", " ", t).strip()


def narrate(paras):
    import numpy as np, lameenc
    from kokoro_onnx import Kokoro
    d = os.environ.get("KOKORO_DIR", "/tmp/kokoro")
    k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))
    SR = 24000
    parts, starts, pos = [np.zeros(int(SR * 0.3), dtype=np.float32)], [], 0.3
    for p in paras:
        a, _ = k.create(speak(p), voice="bf_emma", speed=1.0, lang="en-gb")
        starts.append(round(pos, 2)); parts.append(a.astype(np.float32)); pos += len(a) / SR
        parts.append(np.zeros(int(SR * 0.5), dtype=np.float32)); pos += 0.5
    pcm = (np.clip(np.concatenate(parts), -1, 1) * 32767).astype(np.int16).tobytes()
    enc = lameenc.Encoder(); enc.set_bit_rate(64); enc.set_in_sample_rate(SR); enc.set_channels(1); enc.set_quality(2)
    return enc.encode(pcm) + enc.flush(), starts, round(pos, 2)


def sealer(root):
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    pub = serialization.load_pem_public_key(open(os.path.join(root, "tools", "public.pem"), "rb").read())
    def enc(data):
        k, iv = secrets.token_bytes(32), secrets.token_bytes(12)
        wk = pub.encrypt(k, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        return b"AK1" + len(wk).to_bytes(2, "big") + wk + iv + AESGCM(k).encrypt(iv, data, None)
    return enc


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    plain = "--plain" in sys.argv
    b = json.load(open(args[0], encoding="utf-8"))
    site = args[1] if len(args) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    errs = check(b)
    if errs:
        print("\n".join("ERROR " + e for e in errs)); return 1
    mp3, starts, dur = narrate(b["script"])
    out = {"date": b["date"], "title": b.get("title", ""), "items": b["items"], "paras": b["script"], "s": starts, "d": dur}
    nd = os.path.join(site, "news"); os.makedirs(nd, exist_ok=True)
    if plain:
        open(os.path.join(nd, b["date"] + ".json"), "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False))
        open(os.path.join(nd, b["date"] + ".mp3"), "wb").write(mp3)
    else:
        enc = sealer(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        open(os.path.join(nd, b["date"] + ".enc"), "wb").write(enc(json.dumps(out, ensure_ascii=False).encode("utf-8")))
        open(os.path.join(nd, b["date"] + ".mp3.enc"), "wb").write(enc(mp3))
    ip = os.path.join(nd, "index.json")
    idx = json.load(open(ip)) if os.path.exists(ip) else {"days": []}
    days = {x["date"]: x for x in idx.get("days", [])}
    days[b["date"]] = {"date": b["date"], "d": dur}
    idx["days"] = sorted(days.values(), key=lambda x: x["date"], reverse=True)[:120]
    json.dump(idx, open(ip, "w"), indent=0)
    print(f"brief {b['date']}: {len(b['items'])} items, {dur:.0f}s audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
