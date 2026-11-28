# 慧川 ComfyUI 工作台

把局域网里的 ComfyUI（`http://192.168.0.172:7177`）包成一套类似即梦/labnana 的深色工作台：首页对话式生成、生成过程流、资产库、共享盘、文档词库、内嵌原版 ComfyUI 工作流页。

前端是单文件 `comfy_web/index.html`，后端是纯标准库的 `comfy_web/server.py`（Python 3.9+，无第三方依赖）。

所有记录、文档都存在**后端这一台机器**上（`comfy_web/data/`），同一局域网里任何电脑打开 `http://<这台机器IP>:8787` 登录后，看到的是**同一份生成记录和同一批文档**；每条记录还会标注是哪台机器发起的（`origin` 字段）。

## 目录

```
comfy_web/
  server.py            后端：出图、任务轮询、资产库、共享盘、文档解析、/comfy/* 反代
  index.html           前端（单文件，无构建）
  config.example.json  配置模板，复制成 config.local.json 后填真实值
  data/                运行数据（records.json、auth.json、docs/），不进仓库
comfy_wf/              从 ComfyUI 拉下来的 16 个工作流（后端启动时同步）
comfy_gen.py           ComfyUI API 客户端 + UI 工作流转 API 格式
docs/index.html        GitHub Pages 版（由 tools/build-docs.ps1 生成）
tools/                 构建与发布脚本
```

## 本地跑起来

```powershell
cd comfy_web
copy config.example.json config.local.json   # 填 ComfyUI 地址 / 账号 / 密码 / 共享盘
python server.py --port 8787
```

或直接双击根目录的 `更新-本地.cmd`（会做语法自检 + 重启服务）。

浏览器打开 `http://127.0.0.1:8787`，用 ComfyUI 的 Basic Auth 账号登录。

**同局域网其它电脑**：服务默认监听 `0.0.0.0`，启动日志会打印 `局域网访问: http://192.168.x.x:8787`，别的电脑直接开这个地址（同源访问，没有跨域和 https 混合内容问题）。首次需要在 Windows 防火墙放行 8787 入站：

```powershell
netsh advfirewall firewall add rule name="Comfy Workbench 8787" dir=in action=allow protocol=TCP localport=8787
```

只给本机用时加 `--host 127.0.0.1`。

前端默认跟着「打开页面的那个地址」走：用 `http://192.168.x.x:8787` 打开就是同源，不会再去连 `127.0.0.1`；如果页面是从别的端口（比如临时起的静态服务）打开的，会自动推到同一台主机的 `8787`。所以别的电脑不要用 GitHub Pages 的 https 地址登录——https 页面连不上 http 后端，浏览器会直接拦掉，只会在登录页显示「连不上」。

## 配置

所有敏感信息都只在 `comfy_web/config.local.json`（已 gitignore），字段：

| 字段 | 说明 |
| --- | --- |
| `server` | ComfyUI 地址，如 `http://192.168.0.172:7177` |
| `user` / `password` | ComfyUI 的 Basic Auth 账号密码 |
| `share_server` | 共享盘地址（`http://192.168.0.172:8080`），留空则共享页自动置灰 |
| `ai_server` | AI 聊天平台地址（`http://192.168.0.172:8787`），AI 页 iframe 和探活都用它 |
| `port` | 服务端口，默认 8787 |
| `cors` | 允许跨域的前端来源，`*` 表示不限；线上直连时按域名收紧更安全 |

环境变量优先于文件：`COMFY_SERVER` / `COMFY_USER` / `COMFY_PASS` / `SHARE_SERVER` / `PORT` / `CW_CORS` / `CW_CONFIG`。

## 线上怎么跑（重要）

`192.168.0.172` 是局域网地址，**公网浏览器摸不到**；而 `server.py` 必须跑在一台能访问它的机器上。所以「线上站」有两种形态，别混着理解：

**A. GitHub Pages 只当前端壳子**

Pages 只能放静态文件，跑不了 Python。`docs/index.html` 是同一套界面，默认后端地址为空，需要这样指过去：

```
https://<你的账号>.github.io/<仓库>/?api=https://<你的后端域名>
```

后端得先能被公网访问（Cloudflare Tunnel / frp / 路由器端口映射随便一种），且必须是 **HTTPS** —— Pages 是 https，调 http 会被浏览器按混合内容拦掉。

**B. 后端跑在能连 ComfyUI 的机器上**

在同一台内网机器上跑 `server.py`，再用隧道把它暴露出去，Pages 前端就指向这个隧道域名。流程：本机 `python server.py` → 隧道 `cloudflared tunnel --url http://127.0.0.1:8787` → 拿到 https 域名 → 写进 Pages 的 `?api=`。

两侧靠 token 鉴权：登录接口除了写 Cookie，还会返回 `token`，前端存 localStorage 并带 `X-CW-Token` 头；图片走 `?t=` 参数。跨域请求由后端 `cors` 配置放行。

## 本地 / 线上互不覆盖

两套更新是两条独立通道，不会互相拉取或强制同步：

- **本地更新**：`更新-本地.cmd`（`tools/local-restart.ps1`）—— 只做语法自检、重启本机服务，不碰 GitHub。
- **线上更新**：`更新-线上.cmd`（先 `tools/build-docs.ps1` 生成 Pages 版，再 `tools/publish.ps1` 推送）—— 只推 GitHub，不重启本地服务。

推送不需要装 git：`publish.ps1` 用 GitHub REST API，逐文件算 git blob SHA 与远程对比，只上传有变化的文件，并同步删除本地已删掉的文件。

准备一次 token：GitHub → Settings → Developer settings → Personal access tokens，勾 `repo`（私有仓库）或 `public_repo`（公开仓库）+ `workflow`，写进根目录 `gh_token.txt`（已 gitignore），或设环境变量 `GH_TOKEN`。

```powershell
# 首次：建仓库 + 开 Pages
pwsh tools/build-docs.ps1
pwsh tools/publish.ps1 -Repo tinghai0724-cmd/huichuan-workbench -Create -Pages -Message "首次上线"

# 之后：只推改动
pwsh tools/publish.ps1 -Repo tinghai0724-cmd/huichuan-workbench -Message "首页改版"
```

## 排版与交互

- 纯黑底 + 蓝色强调色，主色 `#023a91`，主按钮渐变 `#023a91 → #1f5cc0`；另有一套白色主题，可在顶栏状态区或侧栏底部的 ☀/☾ 按钮全局切换（记忆在 `localStorage.cw_theme`，默认黑色）
- 侧栏：首页 / 资产库 / 生成 / 工作流 / 文档 / 共享 / AI / 状态
- 「生成」是左右分栏：左边生成过程卡片流，右边保留完整生成抽屉
- 「工作流」内嵌原版 ComfyUI（`/comfy/*` 反代，自动代填 Basic Auth）
- 只加载 ComfyUI 里**打了书签（星标）**的工作流：读 `userdata/workflows/.index.json` 的 `favorites`；`only_favorites: false` 可关掉（或环境变量 `CW_ONLY_FAVORITES=0`）改成全量。改完书签点 `POST /api/refresh-workflows` 即时重载，不用重启；书签里的流程若缺插件会单独标记为不可用，不影响其它流程。
- 工作流内容**以 ComfyUI 上保存的版本为准**（`sync_from_comfy: true`，默认开）：每次启动 / 刷新都会把 `userdata/workflows/*.json` 拉到本地 `comfy_wf/`，内容有变就覆盖并重新探测。所以你在 ComfyUI 里改了节点（比如把 Sage Attention Patch 换成 `ModelAttentionBackend`）、存了盘，这边刷新一下就跟着更新。配成 `false` 则只用本地副本。
- `node_swaps` 可做节点替换：把跑不起来的节点换成语义等价的实现（默认 `MiniMaxH3MemoryEfficientSageAttentionPatch → MiniMaxLowVRAMAttention`，新节点缺的必填参数用 `object_info` 的默认值补齐）；配成 `{}` 关掉。
- 「共享」页不再自己画文件列表，直接把 `share_server` 上那套原生界面（FileShare Pro）用 iframe 嵌进来：头部只留一行状态（地址 / 文件数 / 连接）和「＋ 分享作品」「↻ 刷新」「⇗ 新窗口」按钮；共享地址取 `/api/state` 的 `share_server`，没配就按当前主机名 + `:8080` 兜底。
- 「＋ 分享作品」打开资产库多选弹层：只列已完成的作品（图 / 视频都带缩略图），可勾选、全选、清空，点「⇪ 分享所选」走 `POST /api/share/push-batch` 批量推到共享盘，成功后自动刷新 iframe 里的列表。资产库大图里的「⇪ 分享到局域网共享」仍是单张快分享（`/api/share/push`）。
- 首页右上角有「📢 公告」：点开是弹窗，默认内容是「视频建议 1080p / 1MP（约 16 分钟）」和「图片视频不要同时跑」两条。弹窗里点「✎ 编辑」可以一条一个文本域地改、删、以及「＋ 添加一条公告」，保存走 `POST /api/notice` 存到 `comfy_web/data/notice.json`；各端每 5 秒轮询 `GET /api/notice`，改完几秒内所有人看到新内容（不自动弹窗、不显示红点，点按钮才看）
- 侧栏新增「AI」页：直接 iframe 嵌局域网 AI 聊天平台（默认 `http://192.168.0.172:8787`，配置项 `ai_server`，可用 `AI_SERVER` 环境变量覆盖）。头部只有地址、`GET /api/ai/state` 探活（在线 + 延迟）和「↻ 刷新」「⇗ 新窗口」；切到别的页面会自动收起。8081 那边的「慧川 AI 交流台」已按 `dsh-claude-style` 主题（Claude Code Desktop 视觉语言）换肤：冰蓝白 / 墨蓝黑双底色 + 品牌蓝强调色（`#4D6BFE`，暗底 `#1D2028`、浅底 `#F4F9FF`、侧栏 `#EEF1F8`、次级蓝 `#8FA4FF`）+ 无衬线正文/衬线标题/等宽代码，右上角 ☾/☀ 一键切明暗并记忆。
- 点侧栏「AI」先进一层 AI 专属启动页：黑蓝 → 浅蓝的渐变光斑过渡 `#aiSplash`（只在 AI 页里出现，别的页面没有），iframe 加载完成自动淡出，15 秒兜底。
- 「文档」放词表/文案：xlsx、csv、tsv、txt、md、json、docx 解析成行，按行搜索、复制或一键填进生成对话框；pdf/doc/xls 只存原文不解析
- 首页 / 生成页有 **图像 / 视频** 模式开关：图像模式只列图像类流程（视频流程全部隐藏），视频模式只列 `video_workflows` 里的流程（默认 `minimax h3 图生视频`、`MINIMax H3 文生视频`、`minimax H3 首尾帧`），整站换成彩色主题——主按钮走 `紫→青→粉→橙` 渐变动画，首页铺一层低透明度动态彩色光斑
- 视频模式下多一个「⏱ 时长」参数：档位由 `video_duration` 配置（默认 `5 / 10 / 15` 秒，受 MiniMax H3 限制），写进流程里 `PrimitiveFloat(秒)` 节点，后面的 `ComfyMathExpression` 负责折算成帧并对齐 17 帧音频块；流程没有时长节点时显示「时长固定」。首页快捷条、生成过程页底栏和「＋ 新建生成」抽屉里都有这个参数（抽屉里是按档位排的 5 / 10 / 15 秒按钮，换流程会回到该流程自己的默认秒数）
- 画幅比例是按截图做的可搜索下拉：`1:1 / 3:2 / 3:4 / 4:3 / 9:16 / 16:9 / 21:9`（7 项，带放大镜搜索、选中打勾）；像素是 18 档阶梯 `0.2→4.0 MP`（对齐 32 倍数），滑块标签同时显示实际输出像素（如 `1.0 MP · 1344 × 768`、`4.0 MP · 2688 × 1504`）。拉到 `4.0 MP` 时滑块下方出现「大图，生成耗时可能较长」的提示并弹一次 toast。工作流尺寸固定（`size_mode=none`）时，比例与像素都会置灰并在下面提示「不支持调节」
- 参考图最多 5 张：按顺序填进工作流里的 LoadImage 位（首尾帧流程会标成「首帧 / 尾帧」）；图比图像位多时，多出来的会各自挂一个「加载图像」节点进工作流（需要在 ComfyUI 里把节点连到对应输入才生效）
- 视频结果在卡片 / 生成流 / 大图里都按视频播放（`/api/img` 支持 Range，可拖动进度条）
- 种子：抽屉里留空即按 `-1` 提交（ComfyUI 的「随机」语义，占位提示也写着「留空 = -1（随机）」）。`-1` 在 API 里不合法，后端会换成同一颗随机正整数，并同时写进采样器和 `RandomNoise` 的 `noise_seed`；手填种子则全流程统一用这个值，可复现。
- 工作流里被旁路（mode=4）的节点，转换 API 时按 ComfyUI 的旁路语义把同类型输入透传给下游（否则 `SageAttentionPatch` 一类直通节点旁路后会断掉 `model` 连线、提交 400）
- 「加图即生成」的流程（有图像位、没有提示词通道，如 `BiRefNet-产品抠图`）：首页标签直接写「·加图即生成」，提示词框改成说明文案、生成按钮变绿；点「加图生成」或直接点生成若还没图，会自动拉起选图，选好就能跑，不需要写提示词
- 新版 ComfyUI 的**子图（subgraph）**工作流：转换 API 时会把子图节点就地展开，按同名插槽把子图输入/输出接回外层，内部节点 id 统一加 `外层id:` 前缀（`BiRefNet-产品抠图` 就是这种结构，展开后 `LoadImage → RemoveBackground → InvertMask → JoinImageWithAlpha → PreviewImage` 连线完整）
- 用系统默认光标（早期那套「跟随光点 + 蓝色缓动圆环 + 点击涟漪」已移除）

## 已知限制

- `/ws` 没做 WebSocket 转发（stdlib HTTP 服务器不支持协议升级），内嵌 ComfyUI 里跑任务时节点进度可能不实时；要看实时进度点「↗ 新窗口」。
- 共享盘依赖 `192.168.0.172:8080`（FileShare Pro），没配或不可达时共享页只剩空白 iframe + 头部「未连接」；iframe 里那套界面自己会连它的地址，所以从别的电脑打开工作台也能看到同一批文件。
- `records.json` 只在本地，仓库不含任何出图记录。
- **公网暴露前注意**：`/comfy/*` 反代目前不校验登录（iframe 没法带请求头），相当于把 ComfyUI 直接开给任何拿到后端地址的人。用隧道暴露后端时，请在隧道层加访问策略（Cloudflare Access、Basic Auth），或只在可信网络里开。`/api/*` 都是要登录的。
