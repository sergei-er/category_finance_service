"""Самоподписанный сертификат только для localhost; ключ не перезаписывается."""

import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / "secrets" / "tls"
os.umask(0o077)
target.mkdir(parents=True, exist_ok=True)
if (target / "key.pem").exists() or (target / "cert.pem").exists():
    raise SystemExit("Ключ/сертификат уже существует; автоматическая замена запрещена")
subprocess.run(
    [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:3072",
        "-sha256",
        "-nodes",
        "-days",
        "30",
        "-subj",
        "/CN=localhost",
        "-addext",
        "subjectAltName=DNS:localhost,IP:127.0.0.1",
        "-keyout",
        str(target / "key.pem"),
        "-out",
        str(target / "cert.pem"),
    ],
    check=True,
    capture_output=True,
)
print("Локальный TLS-сертификат создан на 30 дней; браузер потребует доверия к нему.")
