# 发布链路（抖音 + 视频号）

两条腿：**抖音**走本地 CDP 脚本（创作者平台 UI 自动化），**视频号**走 `sau` CLI（social-auto-upload）。
两者都是"登录态 + 浏览器自动化"，没有官方开放接口，所以**登录态维护**是最大的运维成本。

---

## 一、抖音

### 1. 前置：登录态

本地浏览器 profile 持久化登录（一次扫码，长期有效；实测可维持数周）。

检查登录态（不发布）：
```js
// check-login.mjs
import { createDouyinSession, disconnect } from './src/index.js';
const { ops } = await createDouyinSession();
const probe = await ops.probe();           // { isCreatorPage, sideNav }
const login = await ops.checkLogin({});    // { phase: 'logged_in' | 'qrcode' | 'sms_...' }
console.log(probe, login);
disconnect();
```
- `phase: qrcode` → 出二维码，手机抖音扫；
- `phase: sms_verification` → 自动点了"接收短信"，拿到验证码后带 `smsCode` 再调一次。

**首次获取 Daemon 可能超时**：`browser.js` 会在需要时自启守护进程，第一次调用给 60s 以上超时；失败重试一次通常就过。

### 2. 发布

```js
import { createDouyinSession, disconnect } from './src/index.js';
const { ops } = await createDouyinSession();
const res = await ops.publishVideo('/abs/path/final.mp4', {
  title: '标题（≤30字）',
  description: '描述 + #话题',
  timeout: 300_000,      // 上传等待上限
});
console.log(res);        // { ok, file, elapsed, coverSelected }
```
要点：
- **文件用绝对路径**；
- `title` 与 `description` 都会写入作品，但**列表里显示的是描述开头**——把最抓人的一句放描述第一句；
- 内置流程会自动选"AI 推荐封面"，但片头有淡入时大概率选到黑帧，**必须事后手动换封面**；
- 返回 `ok:true` 不等于上线，可能只检测到 5s 内没有 toast 就放行 → **必须去内容管理页截图核验**。

### 3. 发布后核验

```js
await ops.navigateTo('https://creator.douyin.com/creator-micro/content/manage');
await new Promise(r => setTimeout(r, 8000));
await ops.screenshot({ path: 'verify.png', fullPage: false });
```
在截图里确认：作品在首条、状态为「已发布」、时长与成片一致、封面不是黑块。

### 4. 换封面（发布后）

`douyin_publish_video` 没有封面参数。走 UI：
1. 内容管理 → 目标作品行「编辑作品」（文字按钮，不用悬停）；
2. 编辑器左侧「封面」区，点**竖封面 3:4** 槽位（类名 `coverControl-*`，尺寸约 90×142）；
3. 弹窗里点「上传封面」→ 选图（1080×1440）→「完成」；
4. 底部「提交修改」→ 作品进入「修改审核中」，几分钟后恢复「已发布」。

坑：描述框会弹 #话题 联想下拉挡住下面元素 → 操作前先 `reload()`。

### 5. 接口能力边界

| 要做的事 | 能不能 |
|---|---|
| 发布视频 / 图文 | ✅ |
| 指定自定义封面 | ❌（只能事后 UI 改） |
| 删除作品 | ❌（只能 UI / App） |
| 改已发布作品的描述/话题 | ❌（只能 UI） |
| 读作品数据 | 部分（截图内容管理页） |

---

## 二、视频号

用 `sau`（social-auto-upload）CLI，cookie 存在
`<repo>/cookies/tencent_<account>.json`（本机只有 `sessionid` + `wxuin` 两个 cookie）。

### 1. 检查 / 登录

```powershell
sau tencent check --account channels_main          # 只验 cookie 文件有效性
sau tencent login --account channels_main --headed # 失效时出二维码，手机微信扫
```
⚠️ **`check` 说 valid 不代表能发**：实测出现"check 有效、upload 打开页面被弹回登录页"的情况（sessionid 服务端已失效）。判断标准是**上传前的登录检查**通过。

### 2. 发布

```powershell
sau tencent upload-video --account channels_main `
  --file "D:\out\final.mp4" `
  --title "标题（会进描述区第一行）" `
  --desc  "正文" `
  --tags  "话题1,话题2,话题3" `
  --short-title "10字内短标题" `
  --thumbnail "D:\out\cover_1080x1440.jpg" `
  --headless
```
- `--tags` 逗号分隔，会以 `#话题` 追加；
- `--thumbnail` 要 **3:4**（1080×1440）；
- `--short-title` 只保留字母数字，**标点会被吃掉**（`26.99万起` → `2699万起`），别把价格放进去；
- 平台自动勾选「含 AI 生成内容」标注（AI 配音时应保留）。

### 3. 核验

`sau` 输出 `视频发布成功` 只是提交成功。用 Playwright 带 cookie 打开
`https://channels.weixin.qq.com/platform`，看「最近视频」首条 + 「视频」总数 +1：

```python
cookies = json.loads(Path(cookie_file).read_text(encoding="utf-8"))["cookies"]
ctx = await browser.new_context()
await ctx.add_cookies(cookies)
await page.goto("https://channels.weixin.qq.com/platform")
```

---

## 三、cookie / 登录态寿命（实测）

| 平台 | 失效周期 | 恢复成本 |
|---|---|---|
| 抖音（CDP profile） | 数周 | 扫码一次 |
| 视频号（`sau` cookie） | **约 7–14 天** | 扫码一次（二维码 60s 有效） |

**排期建议**：发布日当天先跑一次 `check`，失败就立刻扫码；不要等到晚上卡在最后一步。

---

## 四、回传

共创活动一般要求把作品链接回传到问卷（`wjx.cn`）。PDF 里的回传二维码可以用脚本解出来：

```python
import cv2, numpy as np
img = cv2.imdecode(np.fromfile(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
img = cv2.copyMakeBorder(img, 60, 60, 60, 60, cv2.BORDER_CONSTANT, value=255)
img = cv2.resize(img, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST)
print(cv2.QRCodeDetector().detectAndDecode(img)[0])
```
（小图直接识别常常失败，**必须加白边 + 放大 5 倍**。）
