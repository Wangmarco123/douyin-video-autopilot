# 踩坑记录（Pitfalls）

按"会不会毁掉一条片子"排序。每条都附现象、根因、正确做法。

---

## 一、音频

### 1. 每个切点加合成 whoosh → 全片"噗呲噗呲"

**现象**：成片压着人声有周期性"呲"声，听感像破音，但测出来没有削波。

**根因**：给每个切点塞了两条自合成的噪声 whoosh。实测该音效**频谱重心 8335Hz、62.7% 能量在 4kHz 以上**——它不是"呼"的气流声，而是高频噪声。17 个切点 × 2 条 = 34 次"呲"。

**正确做法**：
- 默认**不加**此类音效；
- 必须加时只放在 2–3 个大段落转折，**单条**、扫频带通 800–4000Hz（带共振）、电平 ≤ **-24dB**；
- 合成任何音效前先算频谱重心，落在 **2–6kHz** 才用；
- 出片跑 `scripts/audio_qc.py`，瞬态数 > 4 直接拦。

### 2. `amix duration=first` → 片尾静音

**现象**：35.5s 的片子，音频只有 33.4s，最后 2s 完全没声（-99dBFS）。

**根因**：`amix=inputs=N:duration=first` 以**第一路输入**的长度为准。第一路是配音，配音一结束整条音轨就断了。

**正确做法**：
```
[1:a]aresample=48000,volume=1.0,apad=whole_dur=<成片时长>[vo]
...
[vo][bgm][sfx]amix=inputs=3:duration=longest:normalize=0[a]
```
输出端再加 `-t <成片时长>` 兜底。

### 3. 响度叠了三层增益

**现象**：声音忽大忽小、副歌处发闷。

**根因**：`loudnorm` + 手动 `volume=+2.6dB` + `alimiter` 混在一起，动态被反复挤压。

**正确做法**：两遍 loudnorm——先 `print_format=json` 测出 `input_i/input_lra/input_tp/input_thresh/target_offset`，第二次带 `measured_*` 参数应用；只在末尾留一个 `alimiter` 防瞬时过载。

### 4. 素材是静音的

`ffprobe` 的 `mean_volume: -91.0 dB` = 这条素材没有有效音轨。别指望"用原声"，直接走 BGM。

---

## 二、视频

### 5. `-loop 1` + 特定 PNG → ffmpeg 卡死

**现象**：一段 3 秒的静帧渲染 10 分钟不结束，输出文件只有 48 字节；同时报 `Invalid PNG signature 0x20202020`。

**根因**：部分 RGBA/带色彩配置的大尺寸 PNG 在 `-loop 1` 下解码异常，且错误刷屏把 stderr 管道塞满（配合 `subprocess.capture_output` 直接死锁）。

**正确做法**：
- 所有图片先转 JPG 再进 `-loop 1`（`scripts/build_vertical.py` 的 `prep_sources()` 已内置）；
- ffmpeg 一律 `-nostdin -hide_banner -loglevel error`，stderr 落文件而不是长管道；
- 同时保留 `-frames:v N` 与 `-t`，只靠 `-t` 浮点容易多帧少帧。

### 6. `crop` 不能动画尺寸，`zoompan` 又慢

**现象**：Ken Burns 推拉（zoom）卡顿，甚至卡死。

**正确做法**：竖屏混剪优先用**裁切窗平移**（`crop` 的 x/y 是逐帧求值的）：
```
scale=-2:1920:flags=lanczos,crop=1080:1920:x='(in_w-1080)*(0.15+0.7*min(1,t/DUR))':y=0
```
比 `zoompan` 快一个数量级，且没有抖动。真要 zoom 再单独处理。

### 7. 16:9 素材硬裁 9:16 → 把车切掉

**现象**：横屏素材直接中心裁 9:16，车头/车尾没了；海报里的文字被切边。

**正确做法**：
- **实拍/空镜**：按主体位置给 `pan=[a,b]`（0~1 对应可用宽度比例），让运镜跟着主体；
- **带字海报**：用 `mode:"pad"`——模糊背景铺满 + 前景按比例居中，一个字都不裁；
- **封面**：用 `gen_cards.py` 的"模糊底 + 完整前景"，别直接裁。

### 8. 电影感 TVC 的遮幅

官方 TVC 常是 2.39:1 遮幅（16:9 里有上下黑边）。裁竖屏前先 `crop=iw:ih*0.757:0:ih*0.1215` 去掉黑边，否则裁出来是"黑边里的一条"。遮幅素材信息量本来就少，竖屏能不用就不用。

### 9. 变速后用 `fps` 重采样会顿

30fps 素材 `setpts=PTS/0.55` 再 `fps=30`，每帧被复制约 1.8 次，动作会抖。**慢放别低于 0.7×**，要更慢就先补帧（minterpolate，代价高）。

### 10. 抖音自动封面是黑帧

**现象**：发布后列表封面是一块黑。

**根因**：片头有 0.25s 淡入，平台取的是第 0 帧。

**正确做法**：发布后进"编辑作品 → 竖封面 3:4 → 编辑封面 → 上传封面"换成定制封面，再"提交修改"（会重新过审）。`douyin_publish_video` 本身没有封面参数。

---

## 三、工程 / 环境

### 11. PowerShell 写 JSON 带 BOM

`Set-Content -Encoding UTF8`（含 PowerShell 5.1 行为）会写 `EF BB BF`，`json.loads` 直接抛 `Unexpected UTF-8 BOM`。

**正确做法**：读取一律 `encoding="utf-8-sig"`（脚本已内置），或用 `[System.IO.File]::WriteAllText(path, s, New-Object System.Text.UTF8Encoding($false))`。

### 12. Windows 控制台 GBK

`print("全部通过 ✅")` 在 GBK 控制台直接 `UnicodeEncodeError` 退出。

**正确做法**：脚本开头 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；少用 emoji。

### 13. edge-tts 403

**现象**：`WSServerHandshakeError: 403` / `Invalid response status`。

**根因**：旧版 edge-tts 的 `Sec-MS-GEC` 校验被服务端拒。

**正确做法**：升级（实测 7.2.8 → **7.3.1** 恢复）。Upgrade 后事件类型变成 `SentenceBoundary`。

### 14. edge-tts 只有 SentenceBoundary

按 `WordBoundary` 收集会得到**空 SRT**。两者都要接。

### 15. 长视频的音频"看起来正常其实断了"

只 `ffprobe format.duration` 看不出音频轨短了。用 `-select_streams a:0 -show_entries stream=duration` 单独查，或直接跑 `audio_qc.py`。

---

## 四、平台（发布）

见 [publishing.md](publishing.md)。三条高频：
- 抖音内容管理列表显示的是**描述开头**，不是 title 字段；
- 视频号 cookie 大约 **7–14 天**失效，`sau ... check` 说 valid 不代表发布时不被弹回登录页；
- `sau` 会自动清洗视频号短标题里的标点，`26.99万起` 会变成 `2699万起`（别把价格放进短标题）。
