---
name: douyin-video-autopilot
description: 抖音/视频号竖屏成片全链路自动化：共创 brief 解析 → 素材盘点 → AI 口播(edge-tts)+字幕 → 时间线混剪(裁切/变速/调色/字卡) → 音频质检门 → 抖音(CDP)/视频号(sau) 发布与核验。当用户要做品牌共创视频、竖屏混剪、批量短视频出片，或问"这条片怎么发抖音/视频号、怎么判低质、封面怎么设"时使用。
---

# 抖音视频自动化（douyin-video-autopilot）

## 什么时候用

- 手上有一份**品牌共创 brief**（PDF）+ 一堆官方素材，要在**今晚之前**交出一条竖屏成片并发布；
- 需要批量产出同结构短视频（不同车型/不同卖点，同一套版式）；
- 已经发出去但被判"低质/不计分"，要定位原因；
- 想知道抖音/视频号到底怎么发、封面怎么换、cookie 多久失效。

不适用：需要真人出镜拍摄的实拍片；需要平台官方 API 的批量发布（目前只有 UI 自动化）。

## 铁律（违反必返工）

### 音频
1. **不要给每个切点加合成音效**。宽带噪声 whoosh 的频谱重心在 8kHz 以上，压在人声下就是"噗呲噗呲"，实测 17 个切点就会毁掉整条片。
2. 非要加：只放在 2–3 个大段落转折，**单条**、扫频带通 800–4000Hz、电平 ≤ **-24dB**。
3. 优先用**素材自带音频**；没有才铺 BGM。
4. 混音必须 `apad` 到成片全长 + `amix duration=longest`，否则片尾静音（`duration=first` 是最常见的坑）。
5. 响度用**两遍 loudnorm**，不要再叠 `volume` + `limiter`。

### 画面
6. 16:9 素材不要硬裁 9:16：实拍用 `pan` 跟随主体，带字海报用 `mode:"pad"`（模糊底 + 前景），封面用"模糊底 + 完整前景"。
7. 图片先转 JPG 再进 `-loop 1`（部分 PNG 会让 ffmpeg 卡死）。
8. 慢放不低于 **0.7×**（30fps 素材再慢就抖）。
9. 遮幅（2.39:1）TVC 不适合竖屏。

### 发布
10. 抖音自动封面常是**片头黑帧**，发布后必须换 3:4 自定义封面。
11. 接口返回 `ok:true` ≠ 上线，**必须去内容管理/最近视频截图核验**。
12. 视频号 cookie 约 **7–14 天**失效，`check` 说 valid 也可能在发布时被弹回登录页。
13. 视频号短标题会丢标点（`26.99万起`→`2699万起`），别把价格写进去。
14. 抖音列表显示的是**描述开头**，不是 title 字段。

## 标准流程

```powershell
# 0) 环境自检
ffmpeg -version; ffprobe -version
python -c "import edge_tts, PIL, numpy; print('ok')"

# 1) 解析 brief → 指标清单（对照 docs/brief-checklist.md）
#    平台 / 时间窗 / 时长 / 文案字数 / 话题 / 植入话术 / 回传时间

# 2) 素材盘点：分辨率/帧率/时长/有无音轨/是否遮幅 + 联系表选镜

# 3) 口播稿（一行一句，7-10 句，总字数 ≈ 时长×4.2）

# 4) 配音 + 字幕
python scripts/tts_srt.py --script script.txt --out build/vo --voice zh-CN-YunyangNeural --rate +6%

# 5) 写时间线（以句级时间轴为骨架）
#    python scripts/build_vertical.py timeline.json --dry-run

# 6) 渲染
python scripts/build_vertical.py timeline.json

# 7) 质检门（5 项全 PASS 才发）
python scripts/audio_qc.py build/final.mp4 --expect-duration 35.5

# 8) 封面 / 尾卡
python scripts/gen_cards.py cards.json

# 9) 发布 → 核验 → 回传
#    抖音：CDP 脚本 publishVideo + 内容管理页截图
#    视频号：sau tencent upload-video + 平台首页截图
```

## 交付前自检（打印出来勾）

- [ ] 时长 ≥ brief 下限，且有内容不是黑屏凑的
- [ ] 文案字数达标、必带话题齐全、植入话术到位
- [ ] 封面有标题、字完整、3:4
- [ ] `audio_qc.py` 5 项 PASS
- [ ] 两个平台都截图核验为"已发布"
- [ ] 回传链接记录好，按窗口提交

## 参考

- [docs/workflow.md](docs/workflow.md) — 端到端 9 步 + 时间预算
- [docs/pitfalls.md](docs/pitfalls.md) — 15 条真实踩坑（现象/根因/做法）
- [docs/publishing.md](docs/publishing.md) — 抖音 CDP / 视频号 sau 的全部命令与能力边界
- [docs/brief-checklist.md](docs/brief-checklist.md) — brief 硬指标核查表
- [examples/](examples/) — 时间线 / 卡片 / 口播稿 / brief 拆解样例

## 环境约定

- Windows + PowerShell 7；Python 3.10+；ffmpeg/ffprobe 在 PATH；
- 字体默认 `C:\Windows\Fonts\msyhbd.ttc`（可改 `overlays[].font_file`）；
- 抖音登录态靠本地 Chrome profile（CDP，端口约定见 publishing.md）；
- 视频号靠 `sau` CLI + `cookies/tencent_<account>.json`；
- 所有"绝对路径"都写在 `timeline.json` 里，脚本本身不含个人路径。
