#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tts_srt.py — 口播配音 + 逐句字幕 + 句级时间轴，一条命令产出三个文件。

依赖: pip install edge-tts   (需要联网，走微软 Edge 朗读接口)
用法:
    python scripts/tts_srt.py --script script.txt --out build/vo \
        --voice zh-CN-YunyangNeural --rate +6%

输入 script.txt: 一行一句口播（每行会被当成一个语义句，字幕按标点再切短）。
输出:
    build/vo.mp3    配音
    build/vo.srt    烧录用字幕（短句，≤ ~15 个汉字宽）
    build/vo.json   {"total": 33.3, "sentences": [{text,start,end}, ...]}

实测要点（详见 docs/pitfalls.md）:
1) edge-tts ≥ 7.3 对带标点的文本只发 SentenceBoundary（没有 WordBoundary），
   旧代码按 WordBoundary 收流会得到空 SRT —— 这里两者都收。
2) 字幕不能直接用整句：一句 30 字在 1080 宽屏上会折成三行。按标点切块后
   按"字宽"比例分配时间（英文/数字 0.55 字宽）。
3) 相邻 cue 要留 20ms 间隙，否则部分播放器显示抖动。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

TICKS = 10_000_000  # edge-tts 时间单位：100ns


def _utf8_console() -> None:
    """Windows 控制台默认 GBK，print 中文会崩，统一改成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def char_w(ch: str) -> float:
    """估算字幕字宽：中文 1.0，ASCII/数字 0.55。"""
    return 0.55 if re.match(r"[A-Za-z0-9.%]", ch) else 1.0


def split_cues(line: str, max_w: float) -> list[str]:
    """按标点切块并合并到不超过 max_w 字宽。"""
    parts = [p for p in re.split(r"(?<=[，、：；。,])", line) if p]
    out: list[str] = []
    cur, cw = "", 0.0
    for p in parts:
        pw = sum(char_w(c) for c in p)
        if cur and cw + pw > max_w:
            out.append(cur)
            cur, cw = p, pw
        else:
            cur += p
            cw += pw
    if cur:
        out.append(cur)
    return out


def ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


async def synth(text: str, voice: str, rate: str, mp3_path: Path):
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate)
    bounds: list[tuple[float, float, str]] = []
    with open(mp3_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                bounds.append(
                    (chunk["offset"] / TICKS, chunk["duration"] / TICKS, chunk["text"])
                )
    if not bounds:
        raise SystemExit("没有拿到任何边界事件，请检查 edge-tts 版本（>=7.3）与网络")
    return bounds


def build_timeline(lines: list[str], bounds: list[tuple[float, float, str]]):
    """把边界事件归并回脚本的每一行，得到句级时间轴。"""
    sentences, i = [], 0
    for line in lines:
        key = re.sub(r"\s", "", line)
        acc, start, end = "", None, None
        while i < len(bounds) and len(acc) < len(key):
            st, du, tx = bounds[i]
            start = st if start is None else start
            acc += re.sub(r"\s", "", tx)
            end = st + du
            i += 1
        if start is None:
            start, end = 0.0, 0.0
        sentences.append({"text": line, "start": round(start, 3), "end": round(end, 3)})
    total = bounds[-1][0] + bounds[-1][1]
    return sentences, total


def write_outputs(sentences, total, out_prefix: Path, max_w: float):
    cues = []
    for s in sentences:
        chunks = split_cues(s["text"], max_w)
        span = max(s["end"] - s["start"], 0.4)
        tw = sum(sum(char_w(c) for c in ch) for ch in chunks) or 1.0
        cur = s["start"]
        for ch in chunks:
            cw = sum(char_w(c) for c in ch)
            d = span * cw / tw
            cues.append((cur, cur + d, ch))
            cur += d

    srt_lines, prev_end = [], 0.0
    for n, (a, b, t) in enumerate(cues, 1):
        a = max(a, prev_end + 0.02)
        b = max(b, a + 0.4)
        prev_end = b
        srt_lines.append(f"{n}\n{ts(a)} --> {ts(b)}\n{t.strip().rstrip('。，、；：')}\n")

    srt_path = out_prefix.with_suffix(".srt")
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    json_path = out_prefix.with_suffix(".json")
    json_path.write_text(
        json.dumps({"total": round(total, 3), "sentences": sentences},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return srt_path, json_path, len(cues)


def main() -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(description="口播 TTS → mp3 + SRT + 句级时间轴")
    ap.add_argument("--script", required=True, help="口播稿，一行一句")
    ap.add_argument("--out", required=True, help="输出前缀，如 build/vo（生成 .mp3/.srt/.json）")
    ap.add_argument("--voice", default="zh-CN-YunyangNeural",
                    help="音色：zh-CN-YunyangNeural(男) / zh-CN-XiaoxiaoNeural(女)")
    ap.add_argument("--rate", default="+6%", help="语速，如 +6%% / -4%%")
    ap.add_argument("--max-chars", type=float, default=15.0, help="单条字幕最大字宽")
    args = ap.parse_args()

    script = Path(args.script)
    if not script.exists():
        raise SystemExit(f"找不到口播稿: {script}")
    lines = [l.strip() for l in script.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    if not lines:
        raise SystemExit("口播稿为空")

    out_prefix = Path(args.out)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    mp3 = out_prefix.with_suffix(".mp3")

    text = "\n".join(lines)
    print(f"[tts] {len(lines)} 句 / {len(text)} 字 → {mp3}")
    bounds = asyncio.run(synth(text, args.voice, args.rate, mp3))

    sentences, total = build_timeline(lines, bounds)
    srt, js, n_cues = write_outputs(sentences, total, out_prefix, args.max_chars)
    print(f"[done] 配音 {total:.2f}s，字幕 {n_cues} 条")
    print(f"   {mp3}\n   {srt}\n   {js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
