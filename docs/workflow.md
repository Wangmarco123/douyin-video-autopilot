# 端到端工作流

从一份共创 brief（PDF）到两条平台链接，标准流程 8 步。
每步都给出"产物"和"验收标准"，照着走不会漏。

```
brief.pdf ──①解析──▶ 指标清单
material/ ──②盘点──▶ 素材表
                        │
   ③写口播稿 ──────────┤
   ④TTS+字幕 ──────────┤
   ⑤时间线 JSON ───────┤
   ⑥渲染成片 ──────────┤
   ⑦音频质检 ──────────▶ 通过 / 打回重做
   ⑧发布+核验 ─────────▶ 抖音 / 视频号 链接
                        │
   ⑨回传问卷 ◀──────────┘
```

---

## ① 解析 brief

```powershell
python - <<'PY'
import pymupdf, pathlib
doc = pymupdf.open(r"brief.pdf")
text = "\n".join(f"==== PAGE {i+1} ====\n{p.get_text()}" for i, p in enumerate(doc))
pathlib.Path("brief.txt").write_text(text, encoding="utf-8")
print(len(doc), "pages")
PY
```
把文本读成**指标清单**（对照 [brief-checklist.md](brief-checklist.md)）：
平台 / 时间窗 / 时长 / 文案字数 / 话题 / 植入话术 / 特殊要求 / 奖励 / 回传时间。
**先确认平台**——有的活动只算抖音+视频号，发小红书/B 站不加分。

顺带把回传二维码解出来（见 [publishing.md](publishing.md) 第四节）。

## ② 盘点素材

- `ffprobe` 逐条看：分辨率、帧率、时长、**有没有音轨**、是否遮幅；
- 生成联系表（每 N 秒抽一帧拼图）决定用哪几条；
- 关键判断：**16:9 宽画幅 + 遮幅的 TVC 不适合竖屏**，优先选主体居中的产品空镜/内饰片。

## ③ 写口播稿

一行一句，共 7–10 句，总字数 ≈ 时长 × 4.2 字/秒（中文、+6% 语速）。
句式：钩子（价格/反差/疑问）→ 场景 → 3–4 个证据点 → 落点（价值主张）。
每句 ≤ 22 字，方便字幕切短。

## ④ 配音 + 字幕

```powershell
python scripts/tts_srt.py --script script.txt --out build/vo --voice zh-CN-YunyangNeural --rate +6%
```
产出 `vo.mp3` / `vo.srt` / `vo.json`（句级时间轴，用来对齐镜头）。

## ⑤ 写时间线

以句级时间轴为骨架切镜头，写到 `timeline.json`：
- 一句台词配 1–3 个镜头；
- 快切镜头 1.0–1.5s，主体展示 2–3s，卡片 2–3s；
- 卡点：重要信息（价格/加速/续航）落在对应镜头起点。

## ⑥ 渲染

```powershell
python scripts/build_vertical.py timeline.json
```
先 `--dry-run` 看帧数分配，再正式渲染。

## ⑦ 质检（强制门）

```powershell
python scripts/audio_qc.py build/final.mp4 --expect-duration 35.5
```
5 项全 PASS 才允许发布：音视频等长 / 片尾非静音 / 无削波 / 瞬态 ≤4 / 瞬态非高频噪声。

再人工看 20 帧联系表：确认没有黑帧、糊帧、字幕出安全区、封面可读。

## ⑧ 发布 + 核验

抖音（CDP 脚本）→ 视频号（`sau`）→ 两个平台都**截图核验**：
首条作品、状态「已发布」、时长一致、封面正常。
抖音记得换 3:4 竖封面（自动封面常是片头黑帧）。

## ⑨ 回传

按 brief 的回传窗口填问卷，链接逐条核对。错一个字都算无效。

---

## 时间预算（一条 30–40s 成片）

| 步骤 | 耗时 | 备注 |
|---|---|---|
| 解析 + 盘点 | 10–15 min | 联系表占大头 |
| 写稿 + TTS | 5 min | TTS 一次成型 |
| 时间线 + 首渲染 | 15 min | 主要返工点 |
| 质检返修 | 10 min | 通常 1–2 轮 |
| 发布 + 核验 | 10 min | 视频号可能卡在扫码 |
| **合计** | **约 1 小时** | 含一次返工 |
