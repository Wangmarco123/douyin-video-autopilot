#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_qc.py — 成片音频自动质检门（发布前必跑）。

用法:
    python scripts/audio_qc.py final.mp4 --expect-duration 35.5
    python scripts/audio_qc.py final.mp4 --json > qc.json

检查项与判定（任一 FAIL 则退出码 1）:
  1. 音频长度 == 视频长度（±0.30s）      —— 防 amix duration=first 造成的片尾静音
  2. 尾段（最后 10%）RMS > -60 dBFS      —— 防片尾黑屏/静音被判低质
  3. 采样峰值 ≤ -1.0 dBFS 且无连续削波   —— 防爆音
  4. 周期性瞬态突刺 ≤ --max-transients   —— 防"每个切点一条 whoosh"造成的重复呲声
  5. 突刺窗口频谱重心 < 6 kHz            —— 高频噪声型 whoosh 是"噗呲"的主要来源

背景：这套判定来自真实返工（见 docs/pitfalls.md「音频铁律」）——
17 个切点各叠两条合成噪声 whoosh（频谱重心 8.3kHz），压在人声下就是"噗呲噗呲"。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 48000


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def decode_audio(path: Path, sr: int = SR) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path),
           "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    if not raw:
        raise SystemExit(f"解码失败或没有音轨: {path}")
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def probe_duration(path: Path, stream: str | None = None) -> float:
    target = ["-select_streams", stream] if stream else []
    cmd = ["ffprobe", "-v", "error", *target,
           "-show_entries", "format=duration:stream=duration",
           "-of", "json", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    data = json.loads(out or "{}")
    if stream:
        for s in data.get("streams", []):
            if s.get("duration"):
                return float(s["duration"])
    return float(data.get("format", {}).get("duration", 0.0))


def analyse(x: np.ndarray) -> dict:
    peak = float(np.abs(x).max()) if len(x) else 0.0
    peak_db = 20 * np.log10(peak + 1e-9)
    clip_runs = 0
    if len(x):
        mask = np.abs(x) > 0.985
        clip_runs = int((mask[1:] & ~mask[:-1]).sum())

    hop = int(SR * 0.02)
    n = len(x) // hop
    env = (np.array([np.sqrt(np.mean(x[i * hop:(i + 1) * hop] ** 2)) for i in range(n)])
           if n else np.array([]))
    d = np.diff(env) if len(env) > 2 else np.array([])
    spikes: list[int] = []
    centroid = 0.0
    if len(d) > 20:
        thr = np.percentile(np.abs(d), 99.5)
        idx = np.where(np.abs(d) > max(thr, 1e-4))[0]
        merged: list[int] = []
        for i in idx:  # 0.2s 内的突刺算同一次
            if not merged or i - merged[-1] > 10:
                merged.append(int(i))
        spikes = merged
        cents = []
        for i in spikes[:8]:
            seg = x[max(0, i * hop - hop): i * hop + hop * 3]
            if len(seg) < 256:
                continue
            spec = np.abs(np.fft.rfft(seg))
            fr = np.fft.rfftfreq(len(seg), 1 / SR)
            if spec.sum() > 0:
                cents.append(float((spec * fr).sum() / spec.sum()))
        centroid = float(np.mean(cents)) if cents else 0.0

    tail = x[int(len(x) * 0.9):] if len(x) else x
    tail_rms_db = 20 * np.log10(np.sqrt(np.mean(tail ** 2)) + 1e-9) if len(tail) else -99.0
    return {
        "peak_dbfs": round(peak_db, 2),
        "clip_runs": clip_runs,
        "transients": len(spikes),
        "transient_centroid_hz": round(centroid, 1),
        "tail_rms_dbfs": round(tail_rms_db, 1),
    }


def main() -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(description="成片音频质检门")
    ap.add_argument("video")
    ap.add_argument("--expect-duration", type=float, default=None,
                    help="期望时长（默认取视频流时长）")
    ap.add_argument("--max-transients", type=int, default=4,
                    help="允许的周期性瞬态数（默认 4）")
    ap.add_argument("--centroid-limit", type=float, default=6000.0,
                    help="突刺频谱重心上限 Hz")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"找不到文件: {video}")

    v_dur = probe_duration(video, "v:0")
    a_dur = probe_duration(video, "a:0")
    x = decode_audio(video)
    m = analyse(x)
    expect = args.expect_duration if args.expect_duration is not None else v_dur

    checks = []

    def add(name: str, ok: bool, detail: str):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("音视频时长一致", abs(a_dur - expect) <= 0.30,
        f"audio={a_dur:.2f}s expect={expect:.2f}s")
    add("片尾非静音", m["tail_rms_dbfs"] > -60.0, f"tail_rms={m['tail_rms_dbfs']}dBFS")
    add("无削波", m["peak_dbfs"] <= -1.0 and m["clip_runs"] == 0,
        f"peak={m['peak_dbfs']}dBFS clip_runs={m['clip_runs']}")
    add("瞬态数量受控", m["transients"] <= args.max_transients,
        f"transients={m['transients']} (limit {args.max_transients})")
    add("瞬态不是高频噪声", m["transient_centroid_hz"] < args.centroid_limit,
        f"centroid={m['transient_centroid_hz']}Hz (limit {args.centroid_limit:.0f})")

    failed = [c for c in checks if not c["ok"]]
    if args.json:
        print(json.dumps({"file": str(video), "video_duration": v_dur,
                          "checks": checks, "passed": not failed},
                         ensure_ascii=False, indent=2))
    else:
        print(f"文件: {video.name}")
        print(f"视频 {v_dur:.2f}s / 音频 {a_dur:.2f}s")
        for c in checks:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['check']}: {c['detail']}")
        if failed:
            print("\n未通过。修复提示：")
            print("  · 片尾静音/时长不符 → 混音加 apad=whole_dur=<成片时长> + amix duration=longest")
            print("  · 瞬态过多 → 去掉每个切点的合成音效，只留 1-2 个大段落转折")
            print("  · 瞬态高频 → 换 800-4000Hz 扫频带通，电平压到 -24dB 以下")
        else:
            print("\n全部通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
