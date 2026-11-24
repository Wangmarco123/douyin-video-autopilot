#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_vertical.py — 用一份时间线 JSON 把素材拼成竖屏成片（1080x1920）。

包含：逐镜裁切/变速/平移、硬切拼接、调色、动态字卡、字幕烧录、
配音+BGM+音效混音（apad 防片尾静音 + 两遍 loudnorm）。

用法:
    python scripts/build_vertical.py timeline.json
    python scripts/build_vertical.py timeline.json --dry-run

时间线 JSON（完整示例见 examples/timeline.example.json）:
{
  "output": "build/final.mp4",
  "width": 1080, "height": 1920, "fps": 30, "total": 35.5,
  "work_dir": "build/_work",
  "grade": "eq=contrast=1.10:saturation=1.12",
  "subtitles": {"srt": "build/vo.srt", "font": "Microsoft YaHei", "size": 50, "margin_v": 250},
  "overlays": [{"text": "26.99万起", "size": 116, "y": 1080, "t0": 0.4, "t1": 3.3}],
  "shots": [
    {"src": "a.mp4", "in": 1.5, "dur": 2.2, "speed": 1.0, "pan": [0.35, 0.70]},
    {"src": "b.jpg", "mode": "photo", "dur": 2.0, "pan": [0.2, 0.8]},
    {"src": "poster.png", "mode": "pad", "dur": 3.0}
  ],
  "audio": {"voice": "vo.mp3", "voice_gain": 1.9, "bgm": "bgm.mp3",
            "bgm_gain": 0.12, "sfx": null, "sfx_gain": 0.0,
            "target_lufs": -14.0, "limit_db": -1.5}
}

镜头模式:
  video  默认。-ss 入点 + 变速(setpts) + 竖屏中心裁切，可加 pan 横向跟踪
  photo  静帧 Ken Burns（--loop 1），pan=[a,b] 或省略
  pad    海报卡：模糊背景铺满 + 前景居中（保留海报文字，不裁字）
  card   整图铺满（用于 1080x1920 的片尾卡）

设计约束（都是踩过坑换来的，见 docs/pitfalls.md）:
  · 每段按"绝对时间轴"取整帧，累加不漂移；
  · ffmpeg 全部 -nostdin + stderr 落盘，避免管道塞满卡死；
  · PNG 先转 JPG 再进 -loop 1（部分 RGBA PNG 会让解码卡死）；
  · 混音必须 apad 到成片全长 + amix duration=longest，否则片尾没声音；
  · 响度用两遍 loudnorm，不再叠加额外 gain/limiter。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def run(cmd: list[str], ctx: str = "") -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        sys.stderr.write(f"=== {ctx}\n" + (p.stderr or "")[-3000:] + "\n")
        raise SystemExit(f"ffmpeg 失败: {ctx}")


def probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def esc_text(s: str) -> str:
    return (s.replace("\\", r"\\").replace(":", r"\:")
             .replace("'", r"\'").replace("%", r"\%").replace(",", r"\,"))


def prep_sources(paths: list[Path], prep_dir: Path) -> dict[Path, Path]:
    """图片统一转 JPG（避开 PNG/alpha 解码坑），视频原样使用。"""
    from PIL import Image

    prep_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[Path, Path] = {}
    for src in paths:
        if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            dst = prep_dir / (src.stem + ".jpg")
            im = Image.open(src)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGB")
            w, h = im.size
            m = max(w, h)
            if m > 2600:
                r = 2600 / m
                im = im.resize((int(w * r), int(h * r)), Image.LANCZOS)
            im.save(dst, quality=95, subsampling=0)
            mapping[src] = dst
        else:
            mapping[src] = src
    return mapping


def build_shot(i: int, shot: dict, ctx: dict, mapping: dict[Path, Path]) -> Path:
    W, H, fps = ctx["width"], ctx["height"], ctx["fps"]
    n = ctx["frames"][i]
    dur = n / fps
    src = mapping[Path(shot["src"])]
    out = ctx["seg_dir"] / f"{i:02d}.mp4"
    mode = shot.get("mode", "video")
    base = [FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]
    is_still = src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp")

    if mode in ("card", "pad"):
        if mode == "pad":  # 海报卡：模糊背景 + 前景（竖版按宽、横版按 0.8 高）
            sw, sh = probe_size(src)
            portrait = (sw / sh) <= (W / H)
            fg = (f"scale={W}:-2:flags=lanczos" if portrait
                  else f"scale=-2:{int(H * 0.8)}:flags=lanczos")
            fc = (f"[0:v]split[bg][fg];"
                  f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"gblur=sigma=42,eq=brightness=-0.14[bg2];"
                  f"[fg]{fg}[fg2];"
                  f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]")
            vf_args = ["-filter_complex", fc, "-map", "[v]"]
        else:
            vf_args = ["-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                              f"crop={W}:{H},setsar=1,format=yuv420p"]
        cmd = base + ["-loop", "1", "-t", f"{dur + 0.15:.3f}", "-i", str(src),
                      *vf_args, "-frames:v", str(n), "-r", str(fps),
                      "-c:v", "libx264", "-preset", "fast", "-crf", "18", str(out)]
        run(cmd, f"shot{i:02d} {src.name}")
        return out

    speed = float(shot.get("speed", 1.0))
    pan = shot.get("pan")
    if mode == "photo" or (is_still and mode == "video"):
        cmd_in = ["-loop", "1", "-t", f"{dur + 0.15:.3f}", "-i", str(src)]
    else:
        src_dur = dur * speed
        cmd_in = ["-ss", f"{float(shot.get('in', 0.0)):.3f}",
                  "-t", f"{src_dur + 0.25:.3f}", "-i", str(src)]

    chain = f"scale=-2:{H}:flags=lanczos,crop={W}:{H}"
    if pan:
        a, b = float(pan[0]), float(pan[1])
        chain += f":x='(in_w-{W})*({a}+({b}-{a})*min(1,t/{max(dur, 0.01):.3f}))':y=0"
    else:
        chain += f":x='(in_w-{W})/2':y=0"
    if speed != 1.0:
        chain += f",setpts=PTS/{speed:.4f}"
    chain += f",fps={fps},setsar=1,format=yuv420p"

    cmd = base + cmd_in + ["-vf", chain, "-frames:v", str(n), "-r", str(fps), "-an",
                           "-c:v", "libx264", "-preset", "fast", "-crf", "18", str(out)]
    run(cmd, f"shot{i:02d} {src.name}")
    return out


def mix_audio(cfg: dict, total: float, work: Path) -> Path:
    """配音/BGM/音效 → 一条 wav（不做响度归一化，留给两遍 loudnorm）。"""
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []

    voice = cfg.get("voice")
    if not voice:
        raise SystemExit("audio.voice 必填（没有口播也要提供一条音轨）")
    inputs += ["-i", str(Path(voice))]
    # 关键：apad 到成片全长，否则片尾会静音（amix duration=first 的老坑）
    filters.append(f"[0:a]aresample=48000,volume={float(cfg.get('voice_gain', 1.0)):.3f},"
                   f"apad=whole_dur={total:.3f}[vo]")
    labels.append("[vo]")
    idx = 1

    for key, gain_key, extra in (("bgm", "bgm_gain", "highpass=f=60,lowpass=f=13000"),
                                 ("sfx", "sfx_gain", None)):
        p = cfg.get(key)
        if not p:
            continue
        inputs += ["-i", str(Path(p))]
        g = float(cfg.get(gain_key, 0.12))
        chain = (f"[{idx}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                 f"aresample=48000,volume={g:.3f}")
        if extra:
            chain += f",{extra}"
        if key == "bgm":
            chain += f",afade=t=in:st=0:d=0.8,afade=t=out:st={max(total - 2.0, 0):.2f}:d=2.0"
        chain += f"[{key}]"
        filters.append(chain)
        labels.append(f"[{key}]")
        idx += 1

    filters.append("".join(labels) +
                   f"amix=inputs={len(labels)}:duration=longest:normalize=0[a]")
    out = work / "audio_mix.wav"
    run([FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(filters), "-map", "[a]", "-t", f"{total:.3f}",
         "-ar", "48000", "-ac", "2", str(out)], "audio-mix")
    return out


def measure_loudness(mix: Path, target: float, tp: float) -> dict:
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(mix),
                        "-af", f"loudnorm=I={target}:TP={tp}:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    txt = p.stderr or ""
    start, end = txt.rfind("{"), txt.rfind("}")
    if start < 0 or end < 0:
        raise SystemExit("loudnorm 测量失败")
    return json.loads(txt[start:end + 1])


def main() -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(description="时间线 JSON → 竖屏成片")
    ap.add_argument("timeline")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不渲染")
    args = ap.parse_args()

    tl_path = Path(args.timeline)
    tl = json.loads(tl_path.read_text(encoding="utf-8-sig"))
    base_dir = tl_path.parent

    W = int(tl.get("width", 1080))
    H = int(tl.get("height", 1920))
    fps = int(tl.get("fps", 30))
    total = float(tl["total"])
    out_path = base_dir / tl["output"]
    work = base_dir / tl.get("work_dir", "build/_work")
    seg_dir = work / "seg"
    seg_dir.mkdir(parents=True, exist_ok=True)

    shots = tl["shots"]
    starts = [0.0]
    for s in shots:
        starts.append(starts[-1] + float(s["dur"]))
    frames = [round(starts[i + 1] * fps) - round(starts[i] * fps)
              for i in range(len(shots))]
    total_frames = sum(frames)
    if abs(total_frames / fps - total) > 0.05:
        print(f"[warn] shots 累计 {total_frames / fps:.2f}s 与 total {total:.2f}s 不一致，按 total 对齐")
        total = total_frames / fps

    for s in shots:
        if not (base_dir / s["src"]).exists():
            raise SystemExit(f"缺素材: {base_dir / s['src']}")

    ctx = {"width": W, "height": H, "fps": fps, "frames": frames, "seg_dir": seg_dir}
    if args.dry_run:
        for i, s in enumerate(shots):
            print(f"  shot{i:02d} {s['src']} mode={s.get('mode', 'video')} "
                  f"{frames[i]}f ({frames[i] / fps:.2f}s)")
        print(f"  合计 {total_frames} 帧 / {total_frames / fps:.2f}s → {out_path}")
        return 0

    raw2prep = prep_sources([base_dir / s["src"] for s in shots], work / "src")
    mapping = {Path(s["src"]): raw2prep[base_dir / s["src"]] for s in shots}

    with ThreadPoolExecutor(max_workers=3) as ex:
        segs = list(ex.map(lambda i: build_shot(i, shots[i], ctx, mapping),
                           range(len(shots))))
    for i in range(len(shots)):
        print(f"[shot{i:02d}] {shots[i]['src']} {frames[i]}f", flush=True)

    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="ascii")
    concat = work / "concat.mp4"
    run([FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(concat)], "concat")

    audio_cfg = dict(tl.get("audio", {}))
    target = float(audio_cfg.get("target_lufs", -14.0))
    limit = float(audio_cfg.get("limit_db", -1.5))
    for k in ("voice", "bgm", "sfx"):
        if audio_cfg.get(k):
            audio_cfg[k] = str(base_dir / audio_cfg[k])
    mix = mix_audio(audio_cfg, total, work)
    meas = measure_loudness(mix, target, limit)
    print(f"[loudness] 测量 I={meas['input_i']} LUFS / TP={meas['input_tp']} dBTP")

    parts: list[str] = []
    if tl.get("grade"):
        parts.append(tl["grade"])
    for ov in tl.get("overlays", []):
        a = (f"min(1,max(0,(t-{float(ov['t0']):.2f})/0.22))*"
             f"min(1,max(0,({float(ov['t1']):.2f}-t)/0.22))")
        box = ":box=1:boxcolor=0x000000@0.35:boxborderw=22" if ov.get("box", True) else ""
        font_file = ov.get("font_file", r"C\:/Windows/Fonts/msyhbd.ttc")
        parts.append(
            f"drawtext=fontfile='{font_file}':text='{esc_text(str(ov['text']))}':"
            f"fontsize={int(ov.get('size', 72))}:fontcolor={ov.get('color', 'white')}:"
            f"borderw=0{box}:x=(w-text_w)/2:y={int(ov.get('y', 1100))}:alpha='{a}'")
    sub_cfg = tl.get("subtitles")
    if sub_cfg and sub_cfg.get("srt"):
        srt_ff = str(base_dir / sub_cfg["srt"]).replace("\\", "/").replace(":", r"\:")
        style = (f"FontName={sub_cfg.get('font', 'Microsoft YaHei')},"
                 f"FontSize={int(sub_cfg.get('size', 50))},"
                 f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,"
                 f"Outline=3,Shadow=1,PlayResX={W},PlayResY={H},"
                 f"MarginL={int(sub_cfg.get('margin_l', 80))},"
                 f"MarginR={int(sub_cfg.get('margin_r', 80))},"
                 f"MarginV={int(sub_cfg.get('margin_v', 250))},Bold=1")
        parts.append(f"subtitles=f='{srt_ff}':force_style='{style}'")
    parts.append("fade=t=in:st=0:d=0.25")
    parts.append(f"fade=t=out:st={max(total - 0.5, 0):.2f}:d=0.5")
    vchain = ",".join(parts)

    fc = (f"[0:v]{vchain}[v];"
          f"[1:a]loudnorm=I={target}:TP={limit}:LRA=11:"
          f"measured_I={meas['input_i']}:measured_LRA={meas['input_lra']}:"
          f"measured_TP={meas['input_tp']}:measured_thresh={meas['input_thresh']}:"
          f"offset={meas['target_offset']}:linear=true,"
          f"aresample=48000,alimiter=limit={limit - 0.3:.2f}dB:level=false,"
          f"aformat=sample_rates=48000:channel_layouts=stereo[a]")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run([FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-i", str(concat), "-i", str(mix), "-filter_complex", fc,
         "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}", "-r", str(fps),
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-movflags", "+faststart", str(out_path)], "final")
    print(f"[done] {out_path} {out_path.stat().st_size / 1e6:.1f} MB")
    print(f"[next] python scripts/audio_qc.py \"{out_path}\" --expect-duration {total:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
