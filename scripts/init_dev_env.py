"""Создаёт локальный .env без вывода секретов; существующий файл не заменяет."""

import os
from pathlib import Path
from secrets import token_urlsafe

root = Path(__file__).resolve().parents[1]
target = root / ".env"
contents = (root / ".env.example").read_text(encoding="utf-8")
contents = contents.replace("DJANGO_SECRET_KEY=\n", f"DJANGO_SECRET_KEY={token_urlsafe(64)}\n")
contents = contents.replace("POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={token_urlsafe(32)}\n")
contents = contents.replace(
    "POSTGRES_ADMIN_PASSWORD=\n", f"POSTGRES_ADMIN_PASSWORD={token_urlsafe(32)}\n"
)
try:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit(".env уже существует: оставлен без изменений") from None
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    stream.write(contents)
print("Создан .env для localhost; секреты не выводятся")
