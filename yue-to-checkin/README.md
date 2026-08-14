# Yue.to 悦通机场 · Telegram 每日自动签到 + 小游戏

通过 **Telethon (userbot)** 模拟你的 Telegram 账号，每天自动在悦通 Mini App 完成签到和所有小游戏/活动（答题、竞猜、翻牌、免费抽、宝箱、养鸡场、抽卡、UNO 等），全程在 **GitHub Actions** 免费运行，无需常开电脑。

## 原理

```
每天 00:00 (北京时间)
        │
        ▼
GitHub Actions 定时触发
        │
        ▼
解密 SESSION_B64 (加密的登录态) ──► Telethon 登录你的 TG 账号
        │
        ▼
RequestWebView 获取悦通 WebApp 的 tgWebAppData (initData 认证)
        │
        ▼
POST https://yue.yuebao.website/miniapp/api/sign/do   ← 执行签到
        │
        ▼
结果由 bot 推送到你的 Telegram
```

签到接口直接调用悦通 WebApp 后端（`/sign/state` 查状态、`/sign/do` 签到），不依赖按钮模拟，更稳定。

## 前提

- 有 GitHub 账号
- 本地电脑能跑一次 Python（只需这一次，之后全自动）
- 账号支持登录 Telegram API（正常账号均可）
- **Telegram 账号已绑定悦通账号**（未绑定无法签到，见步骤 3）

## 目录说明

本目录是仓库 `69yun69` 下的子目录，与仓库根目录的 AM-CHECK-IN (Node.js) 项目互不影响。签到由 `.github/workflows/checkin.yml` 独立触发。

## 配置步骤

### 1. 申请 Telegram API

1. 打开 <https://my.telegram.org> 并登录
2. 进入 **API development tools** → 新建应用
3. 记下 **api_id**（纯数字）和 **api_hash**

### 2. 本机登录（只此一次）

```powershell
cd yue-to-checkin
pip install -r requirements.txt
$env:TG_API_ID = "你的api_id"
$env:TG_API_HASH = "你的api_hash"
python login.py
```

按提示输入手机号、验证码（如开了两步验证再输密码）。成功后生成 `session/yue.session`。

> ⚠️ 登录后请先手动去 Telegram 里正常使用一会儿，避免新登录被风控。
> 本机无法直连 Telegram 时，设置代理再登录：`$env:TG_PROXY = "socks5://127.0.0.1:7897"`（示例为 Clash 默认端口）。

### 3. 绑定悦通账号（必须，否则无法签到）

1. 在 **悦通面板 / YueLink 客户端**里找到「绑定 Telegram」入口
2. 生成一个**一次性绑定码**
3. 私聊 `@yuetoo_bot`，发送 `/bind 一次性绑定码`
4. 收到绑定成功提示即可。可用 `/me` 确认状态

### 4. 加密 session（防止明文泄露）

```powershell
python encrypt_session.py
```

按提示设置一个加密密码，然后它会输出一大串 base64（记作 **SESSION_B64**），密码记作 **SESSION_PASS**。

> ⚠️ `session/yue.session` 和 `session_b64.txt` 都不要推送到 GitHub 仓库，请把加密串放到 Secrets（见步骤 5）。

### 5. 配置 GitHub Secrets

进入仓库 **Settings → Secrets and variables → Actions → New repository secret**，添加：

| Secret 名 | 值 |
|---|---|
| `TG_API_ID` | api_id |
| `TG_API_HASH` | api_hash |
| `SESSION_B64` | encrypt_session.py 输出的一大串 base64 |
| `SESSION_PASS` | 加密密码 |
| `NOTIFY_TO` | 你的 Telegram 数字 ID（如 `6357312260`），即 bot 要推送给你的对象 |
| `BOT_TOKEN` | 推送 bot 的 token（如 `8845394128:AAF...`），设置后结果由 bot 主动推送给你 |

### 6. 手动测试一次

进入 **Actions → Yue.to Daily Check-in & Games → Run workflow**，点按钮手动触发。

约 1 分钟后查看运行日志：如果显示签到、答题、竞猜、宝箱、养鸡场、抽卡、UNO 等均已执行且推送到你 Telegram 即成功。若日志显示 `账号未绑定悦通账号`，请先完成步骤 3 再重试。

### 7. 以后全自动

设置好之后，每天 **北京时间 00:00** 自动签到，结果自动由 bot 推送到你 Telegram，无需任何操作。

## 目录结构

```
69yun69/
├── yue-to-checkin/
│   ├── login.py            # 本机登录生成 session（跑一次）
│   ├── checkin.py          # 基础签到与 API 封装（被 games.py 复用）
│   ├── games.py            # Actions 每日执行：解密→登录→签到→全部小游戏→推送
│   ├── encrypt_session.py  # 加密 session 输出 SESSION_B64
│   ├── requirements.txt
│   └── README.md
└── .github/workflows/
    ├── fetch.yml           # 原 AM-CHECK-IN 签到
    └── checkin.yml         # 悦通签到，每天 00:00 触发
```

## 常见问题

**Q: GitHub Actions 运行时提示账号被限制 / 需要验证码？**
A: Actions 每次运行换新 IP，Telegram 可能临时要求验证。此时在本地重跑 `login.py` 登录并重新执行 `encrypt_session.py` 更新 Secret 即可。

**Q: 北京时间 00:00 就是 Actions 的 16:00？**
A: 是的，`cron: '0 16 * * *'` 是 UTC 时间，Actions 调度使用 UTC。

**Q: 日志提示"账号未绑定悦通账号"？**
A: 说明 TG 账号还没绑定悦通账号。按步骤 3 生成绑定码发给 `@yuetoo_bot`（`/bind 绑定码`），完成后重新触发一次即可。

**Q: 签到需要每天都重新获取 initData 吗？**
A: 是的，`games.py` 每次运行都会通过 RequestWebView 实时获取最新 `tgWebAppData`（含时间戳签名），后端校验通过后才允许执行签到与各小游戏。
