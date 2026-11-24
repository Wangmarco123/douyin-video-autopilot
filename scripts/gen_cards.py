#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_cards.py — 从素材截帧生成「片尾卡」与「3:4 封面」，文案由 JSON 驱动。

用法:
    python scripts/gen_cards.py cards.json

cards.json:
{
  "background": "assets/hero.jpg",       // 视频或图片；视频用 at 指定截帧秒数
  "at": 2.0,
  "out_dir": "build/cards",
  "theme": {"accent": [198, 32, 40], "gold": [255, 214, 120]},
  "endcard": {"tag": "11.12 正式上市", "title": "腾势N8", "big": "26.99",
              "big_suffix": "万元起", "sub": "纯电 / 插混 共 6 款车型",
              "highlight": "科技豪华 成就宽适人生", "foot": "1003km · 2.98s · 5分钟"},
  "cover": {"tag": "腾势N8 · 正式上市", "line1": "26.99万起", "line2": "六边形战士",
            "sub": "1003km 续航 · 2.98s 零百", "foot": "科技豪华 成就宽适人生",
            "foot2": "11.12 上市发布会"}
}

产出:
  build/cards/endcard.png          1080x1920 片尾卡（模糊底 + 大字 + 权益/价格）
  build/cards/cover_1080x1440.jpg  3:4 封面（16:9 画面完整展示 + 模糊背景，不裁车）

为什么封面要"贴"而不是"裁": 16:9 素材按 3:4 直接裁会把车头/车尾切掉；
用模糊背景 + 完整前景，既保住车又填满画布（见 docs/pitfalls.md）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def font(size: int, light: bool = False):
    try:
        return ImageFont.truetype(FONT_REG if light else FONT_BOLD, size)
    except OSError:
        return ImageFont.load_default()


def grab(src: Path, at: float, crop: str, out: Path) -> Image.Image:
    vf = ("scale=-2:1920:flags=lanczos,crop=1080:1920" if crop == "9x16"
          else "scale=1920:-2")
    subprocess.run(["ffmpeg", "-y", "-nostdin", "-v", "error", "-ss", f"{at:.2f}",
                    "-i", str(src), "-frames:v", "1", "-vf", vf, str(out)], check=True)
    return Image.open(out).convert("RGB").copy()


def gradient(im: Image.Image, start: float = 0.0, strength: int = 235,
             dark=(4, 8, 16)) -> Image.Image:
    w, h = im.size
    ov = Image.new("L", (1, h), 0)
    for y in range(h):
        t = max(0.0, (y - h * start) / (h * (1 - start) or 1))
        ov.putpixel((0, y), int(strength * (t ** 0.85)))
    return Image.composite(Image.new("RGB", (w, h), dark), im, ov.resize((w, h)))


def endcard(im: Image.Image, cfg: dict, theme: dict, out: Path) -> Path:
    W, H = 1080, 1920
    im = im.resize((W, H), Image.LANCZOS) if im.size != (W, H) else im
    im = im.filter(ImageFilter.GaussianBlur(10))
    im = Image.blend(im, Image.new("RGB", (W, H), (5, 9, 18)), 0.60)
    im = gradient(im, 0.0, 200)
    d = ImageDraw.Draw(im)
    accent = tuple(theme.get("accent", (198, 32, 40)))
    gold = tuple(theme.get("gold", (255, 214, 120)))

    tag = cfg.get("tag", "")
    if tag:
        d.rectangle([70, 250, 70 + 38 * len(tag) + 40, 250 + 68], fill=accent)
        d.text((92, 264), tag, font=font(32), fill="white")
    d.text((68, 380), cfg.get("title", ""), font=font(74), fill="white")
    d.text((68, 490), cfg.get("big", ""), font=font(170), fill="white")
    if cfg.get("big_suffix"):
        d.text((560, 560), cfg["big_suffix"], font=font(62), fill="white")
    if cfg.get("sub"):
        d.text((72, 720), cfg["sub"], font=font(36, light=True), fill=(226, 232, 244))
    d.rectangle([74, 810, 74 + 240, 818], fill=accent)
    if cfg.get("highlight"):
        d.text((72, 870), cfg["highlight"], font=font(56), fill=gold)
    if cfg.get("foot"):
        d.text((72, 970), cfg["foot"], font=font(32, light=True), fill=(196, 204, 220))
    p = out / "endcard.png"
    im.save(p)
    return p


def cover(im: Image.Image, cfg: dict, theme: dict, out: Path) -> Path:
    W, H = 1080, 1440
    fg = im.resize((W, int(W * im.height / im.width)), Image.LANCZOS)
    bg = fg.resize((W, H)).filter(ImageFilter.GaussianBlur(26))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (4, 8, 16)), 0.55)
    im2 = bg.copy()
    im2.paste(fg, (0, (H - fg.height) // 2 + 30))
    im2 = gradient(im2, 0.0, 235)
    d = ImageDraw.Draw(im2)
    accent = tuple(theme.get("accent", (198, 32, 40)))
    gold = tuple(theme.get("gold", (255, 214, 120)))

    tag = cfg.get("tag", "")
    if tag:
        d.rectangle([60, 60, 60 + 34 * len(tag) + 36, 60 + 60], fill=accent)
        d.text((78, 72), tag, font=font(30), fill="white")
    d.text((56, 160), cfg.get("line1", ""), font=font(118), fill="white")
    d.text((56, 300), cfg.get("line2", ""), font=font(104), fill=gold)
    d.rectangle([60, 440, 60 + 270, 448], fill=accent)
    if cfg.get("sub"):
        d.text((58, 500), cfg["sub"], font=font(40, light=True), fill=(232, 238, 248))
    if cfg.get("foot"):
        d.text((58, 1424 - 150), cfg["foot"], font=font(52), fill=gold)
    if cfg.get("foot2"):
        d.text((58, 1424 - 78), cfg["foot2"], font=font(30, light=True), fill=(214, 222, 236))
    p = out / "cover_1080x1440.jpg"
    im2.save(p, quality=94, subsampling=0)
    return p


def main() -> int:
    _utf8_console()
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    spec_path = Path(sys.argv[1])
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    base = spec_path.parent

    out = base / spec.get("out_dir", "build/cards")
    out.mkdir(parents=True, exist_ok=True)
    theme = spec.get("theme", {})

    bg_path = base / spec["background"]
    tmp = out / "_frame.png"
    frame = grab(bg_path, float(spec.get("at", 0.0)),
                 "auto" if spec.get("keep_full_frame", True) else "9x16", tmp)

    made = []
    if spec.get("cover"):
        made.append(cover(frame, spec["cover"], theme, out))
    if spec.get("endcard"):
        made.append(endcard(frame, spec["endcard"], theme, out))
    for m in made:
        print(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
