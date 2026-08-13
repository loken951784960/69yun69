#!/usr/bin/env python3
"""GitHub Actions 中执行的每日签到脚本（悦通 WebApp API 直连版）。

流程:
    1. 从环境变量读取并解密 SESSION_B64 -> session/yue.session
    2. 用 Telethon 登录 (Session 已含 auth key, 无需验证码)
    3. RequestWebView 获取最新 tgWebAppData (initData)
    4. POST /sign/state 检查绑定与今日签到状态
    5. 未绑定 -> 报告并退出; 已签到 -> 报告; 否则 POST /sign/do 执行签到
    6. 把结果推送到你的 Telegram (Saved Messages 或 NOTIFY_TO 指定目标)

需要的环境变量 (GitHub Secrets):
    TG_API_ID      my.telegram.org 申请的 API ID
    TG_API_HASH    对应的 API Hash
    SESSION_B64    encrypt_session.py 输出的 base64 加密 session
    SESSION_PASS   加密 session 的密码
    NOTIFY_TO      可选, 结果通知目标 (用户名/群链接/ID), 留空则发到 Saved Messages

本地调试: 设置 TG_PROXY=socks5://127.0.0.1:7897 走代理。
"""
import asyncio
import base64
import os
import re
import sys
import urllib.parse

import requests
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from telethon import TelegramClient
from telethon.tl.functions.messages import RequestWebViewRequest

SALT_LEN = 16
PBKDF2_ITERATIONS = 200_000
BOT = "yuetoo_bot"
API_BASE = "https://yue.yuebao.website/miniapp/api"
WEBAPP_URL = "https://yue.yuebao.website/miniapp/v2?startapp=sign"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
}


def get_proxy():
    """Actions 中默认无代理；本地调试时设 TG_PROXY 即可走代理。"""
    url = os.environ.get("TG_PROXY", "").strip()
    if not url:
        return None
    from python_socks import ProxyType
    if "://" in url:
        scheme, host_port = url.split("://", 1)
    else:
        scheme, host_port = "socks5", url
    host, _, port = host_port.rpartition(":")
    if not host or not port.isdigit():
        print(f"[错误] 无法解析 TG_PROXY: {url}")
        sys.exit(1)
    ptype = ProxyType.HTTP if scheme.startswith("http") else ProxyType.SOCKS5
    return (ptype, host, int(port))


def get_requests_proxy():
    """requests 用的代理。本地调试设 TG_PROXY 即生效；Actions 中不设则直连。"""
    url = os.environ.get("TG_PROXY", "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "socks5h://" + url
    elif url.startswith("socks5://"):
        url = "socks5h://" + url[len("socks5://"):]
    return {"http": url, "https": url}


def decrypt_session() -> bytes:
    b64 = os.environ.get("SESSION_B64", "")
    passphrase = os.environ.get("SESSION_PASS", "")
    if not b64 or not passphrase:
        print("[错误] 缺少环境变量 SESSION_B64 或 SESSION_PASS")
        sys.exit(1)
    raw = base64.b64decode(b64)
    salt, token = raw[:SALT_LEN], raw[SALT_LEN:]
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
    return Fernet(key).decrypt(token)


async def fetch_init_data(client) -> str:
    """通过 RequestWebView 获取最新 tgWebAppData (initData)。"""
    bot = await client.get_input_entity(BOT)
    rv = await client(
        RequestWebViewRequest(peer=bot, bot=bot, platform="ios", url=WEBAPP_URL)
    )
    m = re.search(r"tgWebAppData=([^&]+)", rv.url)
    if not m:
        raise RuntimeError("未能从 WebView URL 提取 initData")
    return urllib.parse.unquote(m.group(1))


def api_post(path: str, init_data: str, **params) -> dict:
    """调用悦通 WebApp API。"""
    body = {"initData": init_data, **params}
    try:
        r = requests.post(
            API_BASE + path, json=body, headers=HEADERS,
            proxies=get_requests_proxy(), timeout=60,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"网络请求失败: {e}")
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError(f"响应不是 JSON: HTTP {r.status_code} {r.text[:200]}")
    if not j.get("ok"):
        raise RuntimeError(str(j.get("message") or j.get("error") or f"HTTP {r.status_code}"))
    return j.get("data") or {}


def reward_text(reward: dict) -> str:
    t = reward.get("type")
    if t == "balance":
        return f"+{reward.get('balanceYuan', 0)} 元余额"
    if t == "points":
        return f"+{reward.get('points', 0)} 积分"
    if t == "both":
        return f"+{reward.get('balanceYuan', 0)} 元 + {reward.get('trafficGb', 0)} GB"
    if t == "lucky":
        return f"🎉 幸运大奖 +{reward.get('trafficGb', 0)} GB"
    if t == "traffic":
        return f"+{reward.get('trafficGb', 0)} GB 流量"
    return "已签到（未知奖励类型）"


async def run_checkin(api_id: int, api_hash: str) -> str:
    """执行签到主流程，返回结果文本。"""
    client = TelegramClient("session/yue", api_id, api_hash, proxy=get_proxy())
    await client.start()
    logs: list[str] = []
    try:
        logs.append("[OK] 已登录 Telegram")

        init_data = await fetch_init_data(client)
        logs.append(f"[OK] 已获取 WebApp 认证数据 (len={len(init_data)})")

        state = api_post("/sign/state", init_data)
        streak = state.get("streak", 0)

        if not state.get("bound"):
            logs.append("❌ 账号未绑定悦通账号，无法签到。")
            logs.append("👉 请先在悦通面板生成一次性绑定码，再私聊 @yuetoo_bot 发送 /bind 绑定码，")
            logs.append("   绑定完成后重新触发一次本工作流即可。")
            return "\n".join(logs)

        if state.get("signed"):
            logs.append(f"✅ 今日已签到（连签 {streak} 天），无需重复操作")
            return "\n".join(logs)

        result = api_post("/sign/do", init_data)
        reward = result.get("reward") or {}
        lines = ["🎉 签到成功！"]
        lines.append(f"  奖励: {reward_text(reward)}")
        if result.get("streak"):
            lines.append(f"  连签: {result['streak']} 天")
        if result.get("streakMultiplier") and result["streakMultiplier"] > 1:
            lines.append(f"  连签加成: ×{result['streakMultiplier']}")
        if result.get("milestone"):
            ms = result["milestone"]
            headline = ms.get("headline") or f"连签 {ms.get('streak', '')} 天"
            lines.append(f"  🏆 里程碑: {headline}")
        logs.extend(lines)
    except Exception as e:
        logs.append(f"[错误] 签到流程异常: {type(e).__name__}: {e}")
    finally:
        await client.disconnect()
    return "\n".join(logs)


def notify_via_bot(text: str) -> None:
    """用 bot token (BOT_TOKEN) 通过 Bot API 主动推送消息给 NOTIFY_TO 指定的用户。"""
    token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = os.environ.get("NOTIFY_TO", "").strip()
    if not token or not chat_id:
        raise RuntimeError("缺少 BOT_TOKEN 或 NOTIFY_TO")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🔄 悦通每日签到\n\n{text}",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, proxies=get_requests_proxy(), timeout=30)
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError(f"Bot API 响应异常: HTTP {r.status_code} {r.text[:200]}")
    if not j.get("ok"):
        raise RuntimeError(f"Bot API 错误: {j.get('description')}")
    print(f"[OK] 已通过 bot 推送结果到 {chat_id}")


async def notify(api_id: int, api_hash: str, text: str) -> None:
    """把签到结果推送到用户 Telegram。

    优先使用 bot 推送 (设置 BOT_TOKEN + NOTIFY_TO=用户ID/用户名)；
    未设置 BOT_TOKEN 时，回退为用当前账号 (userbot) 发送。
    """
    if os.environ.get("BOT_TOKEN", "").strip():
        try:
            notify_via_bot(text)
        except Exception as e:
            print(f"[警告] bot 推送失败: {type(e).__name__}: {e}")
        return

    raw_target = os.environ.get("NOTIFY_TO", "").strip() or "me"
    try:
        target: object = int(raw_target)  # 纯数字 -> 数字 ID
    except ValueError:
        target = raw_target  # 否则按用户名/链接处理
    client = TelegramClient("session/yue", api_id, api_hash, proxy=get_proxy())
    await client.start()
    try:
        await client.send_message(target, f"🔄 悦通每日签到\n\n{text}")
        print(f"[OK] 已推送结果到 {raw_target}")
    except Exception as e:
        print(f"[警告] 推送失败: {type(e).__name__}: {e}")
    finally:
        await client.disconnect()


def main() -> None:
    api_id = int(os.environ.get("TG_API_ID", "0"))
    api_hash = os.environ.get("TG_API_HASH", "")
    if not api_id or not api_hash:
        print("[错误] 缺少 TG_API_ID / TG_API_HASH")
        sys.exit(1)

    os.makedirs("session", exist_ok=True)
    with open("session/yue.session", "wb") as f:
        f.write(decrypt_session())
    print("[OK] session 解密完成")

    result = asyncio.run(run_checkin(api_id, api_hash))
    print("\n=== 签到日志 ===")
    print(result)

    asyncio.run(notify(api_id, api_hash, result))
    sys.exit(0)


if __name__ == "__main__":
    main()
