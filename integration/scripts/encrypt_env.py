#!/usr/bin/env python3
"""
Encrypt / decrypt .env credentials using AES-256-CBC via OpenSSL.

Commands:
    python scripts/encrypt_env.py keygen    # generate a random key file
    python scripts/encrypt_env.py encrypt   # .env → .env.enc
    python scripts/encrypt_env.py decrypt   # .env.enc → stdout

Files:
    .env        — plaintext  (add to .gitignore — NEVER commit)
    .env.enc    — encrypted  (safe to commit)
    .env.key    — master key (add to .gitignore — NEVER commit)

Only SENSITIVE_KEYS are encrypted; other vars are stored plaintext in .env.enc.
"""
from __future__ import annotations

import getpass
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"
ENC_FILE = ROOT / ".env.enc"
KEY_FILE = ROOT / ".env.key"

SENSITIVE_KEYS = {
    "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET", "ERPNEXT_WEBHOOK_SECRET",
    "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_WEBHOOK_SECRET",
    "MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_PASSKEY",
    "NIM_API_KEY", "SENTRY_DSN", "DATABASE_URL", "REDIS_URL",
}

MARKER_BEGIN = "# --- ENCRYPTED BEGIN ---"
MARKER_END = "# --- ENCRYPTED END ---"
MARKER_PLAIN = "# --- PLAINTEXT ENV ---"


def _encrypt(data: bytes, password: str) -> str:
    result = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2",
         "-iter", "600000", "-base64", f"-pass", f"pass:{password}"],
        input=data, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode())
    return result.stdout.decode()


def _decrypt(ciphertext: str, password: str) -> bytes:
    result = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-d", "-salt", "-pbkdf2",
         "-iter", "600000", "-base64", f"-pass", f"pass:{password}"],
        input=ciphertext.encode(), capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Wrong password or corrupt file: {result.stderr.decode()}")
    return result.stdout


def _load_password() -> str | None:
    return KEY_FILE.read_text().strip() if KEY_FILE.exists() else None


def cmd_keygen() -> None:
    key = secrets.token_urlsafe(40)
    KEY_FILE.write_text(key + "\n")
    KEY_FILE.chmod(0o600)
    print(f"Key saved to {KEY_FILE}  (chmod 600)")
    print(f"Add {KEY_FILE.name} to .gitignore — never commit it.")


def cmd_encrypt() -> None:
    if not ENV_FILE.exists():
        sys.exit(f"Not found: {ENV_FILE}")

    password = _load_password() or getpass.getpass("Master password: ")

    lines = ENV_FILE.read_text().splitlines()
    plain_lines: list[str] = []
    sensitive: dict[str, str] = {}

    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            if k.strip() in SENSITIVE_KEYS and v.strip():
                sensitive[k.strip()] = v.strip()
                plain_lines.append(f"{k.strip()}=<encrypted>")
                continue
        plain_lines.append(line)

    ciphertext = _encrypt(json.dumps(sensitive, indent=2).encode(), password)

    ENC_FILE.write_text(
        "# DEWMIX Hardware — encrypted credentials\n"
        "# Decrypt: python scripts/encrypt_env.py decrypt\n"
        f"{MARKER_BEGIN}\n"
        + ciphertext +
        f"{MARKER_END}\n"
        f"{MARKER_PLAIN}\n"
        + "\n".join(plain_lines) + "\n"
    )
    print(f"Encrypted → {ENC_FILE}")
    print(f"Encrypted keys: {', '.join(sorted(sensitive))}")
    print(f"\nDo NOT commit {ENV_FILE} or {KEY_FILE} — only commit {ENC_FILE}")


def cmd_decrypt() -> None:
    if not ENC_FILE.exists():
        sys.exit(f"Not found: {ENC_FILE}. Run encrypt first.")

    password = _load_password() or getpass.getpass("Master password: ")
    content = ENC_FILE.read_text()

    start = content.index(MARKER_BEGIN) + len(MARKER_BEGIN) + 1
    end = content.index(MARKER_END)
    ciphertext = content[start:end]

    env_start = content.index(MARKER_PLAIN) + len(MARKER_PLAIN) + 1
    plain_env = content[env_start:]

    sensitive: dict[str, str] = json.loads(_decrypt(ciphertext, password))

    result: list[str] = []
    for line in plain_env.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, _ = s.partition("=")
            if k.strip() in sensitive:
                result.append(f"{k.strip()}={sensitive[k.strip()]}")
                continue
        result.append(line)

    print("\n".join(result))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    match cmd:
        case "keygen":  cmd_keygen()
        case "encrypt": cmd_encrypt()
        case "decrypt": cmd_decrypt()
        case _: print(__doc__)


if __name__ == "__main__":
    main()
