#!/usr/bin/env python3
"""加密 session 文件并输出 base64 字符串，作为 GitHub Secret (SESSION_B64)。

用法:
    python encrypt_session.py [session文件路径]
    默认加密 session/yue.session，按提示设置加密密码（SESSION_PASS）。
"""
import base64
import getpass
import gzip
import os
import sys

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

SALT_LEN = 16
PBKDF2_ITERATIONS = 200_000


def derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def main() -> None:
    session_path = sys.argv[1] if len(sys.argv) > 1 else "session/yue.session"
    if not os.path.exists(session_path):
        print(f"[错误] 找不到 session 文件: {session_path}")
        print("请先运行 python login.py 登录生成 session。")
        sys.exit(1)

    passphrase = os.environ.get("SESSION_PASS") or getpass.getpass("设置加密密码 (至少 8 位，需记住): ")
    if len(passphrase) < 8:
        print("[提示] 密码建议至少 8 位")
    confirm = os.environ.get("SESSION_PASS") or getpass.getpass("再次输入确认: ")
    if passphrase != confirm:
        print("[错误] 两次输入不一致")
        sys.exit(1)

    with open(session_path, "rb") as f:
        data = f.read()

    comp = gzip.compress(data)  # 先压缩, 让加密串 <48KB 可放入 GitHub Secret
    salt = os.urandom(SALT_LEN)
    key = derive_key(passphrase, salt)
    token = Fernet(key).encrypt(comp)
    b64 = base64.b64encode(salt + token).decode("utf-8")

    print(f"\n[OK] 已加密 session ({len(data)} bytes -> {len(b64)} 字符)")
    print("\n把下面整串内容复制为 GitHub Secret 的 SESSION_B64 值:\n")
    print(b64)
    print("\n同时把密码填入 Secret 的 SESSION_PASS。")


if __name__ == "__main__":
    main()
