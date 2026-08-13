#!/usr/bin/env python3
"""本地登录 Telegram 账号，保存 session 文件（只需运行一次）。

用法:
    python login.py
    默认使用 Telegram 官方公开测试凭据 (api_id=2040)，直接回车即可。
    也可通过环境变量 TG_API_ID / TG_API_HASH 使用自己申请的凭据。
    代理默认走本机 Clash (127.0.0.1:7897)，可用 TG_PROXY 覆盖。
"""
import asyncio
import getpass
import os
import sys

from python_socks import ProxyType
from telethon import TelegramClient

DEFAULT_API_ID = 2040
DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"


def get_proxy():
    """解析代理。格式: socks5://host:port / http://host:port / 空则不用代理。"""
    url = os.environ.get("TG_PROXY", "socks5://127.0.0.1:7897").strip()
    if not url:
        return None
    if "://" in url:
        scheme, host_port = url.split("://", 1)
    else:
        scheme, host_port = "socks5", url
    host, _, port = host_port.rpartition(":")
    if not host or not port.isdigit():
        print(f"[错误] 无法解析 TG_PROXY: {url} (应为 socks5://host:port)")
        sys.exit(1)
    ptype = ProxyType.HTTP if scheme.startswith("http") else ProxyType.SOCKS5
    return (ptype, host, int(port))


async def main() -> None:
    print("=== Yue.to 悦通自动签到 - Telegram 登录 ===")
    api_id = os.environ.get("TG_API_ID") or input(
        f"API ID [回车用公开测试 {DEFAULT_API_ID}]: "
    ).strip() or str(DEFAULT_API_ID)
    api_hash = os.environ.get("TG_API_HASH") or input(
        f"API Hash [回车用公开测试: {DEFAULT_API_HASH[:8]}...]: "
    ).strip() or DEFAULT_API_HASH
    phone = os.environ.get("TG_PHONE") or input("手机号 (含国家码，如 +8613800138000): ").strip()

    if not api_id.isdigit():
        print("[错误] API ID 必须是纯数字。")
        sys.exit(1)
    if not api_hash:
        print("[错误] API Hash 不能为空。")
        sys.exit(1)
    if not phone:
        print("[错误] 手机号不能为空。")
        sys.exit(1)

    proxy = get_proxy()
    if proxy:
        print(f"[代理] {proxy[0].name}://{proxy[1]}:{proxy[2]}")

    os.makedirs("session", exist_ok=True)
    client = TelegramClient("session/yue", int(api_id), api_hash, proxy=proxy)

    try:
        await client.start(
            phone=phone,
            code_callback=lambda: input("Telegram 验证码 (查收 TG 客户端/短信): ").strip(),
            password=lambda: getpass.getpass("两步验证密码 (没有则直接回车): "),
        )
    except Exception as e:
        print(f"[错误] 登录失败: {type(e).__name__}: {e}")
        print("提示: 如果用公开 api_id=2040 登录被限流，可改用自己申请的 api_id (TG_API_ID/TG_API_HASH 环境变量)。")
        sys.exit(1)

    me = await client.get_me()
    print(f"\n[OK] 登录成功: {me.first_name} (@{me.username or '无用户名'})")
    print("[OK] session 已保存到 session/yue.session\n")

    # 顺便验证悦通 bot 可达
    try:
        bot = await client.get_entity("yuetoo_bot")
        print(f"[OK] 已找到 @yuetoo_bot (ID: {bot.id})")
    except Exception as e:
        print(f"[警告] 连接 @yuetoo_bot 失败: {type(e).__name__}: {e}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
