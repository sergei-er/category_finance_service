"""Согласованная копия БД/свидетельств; проверка БД в отдельной временной базе."""

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compose(*args, data=None):
    return subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, input=data, capture_output=True, check=True
    ).stdout


def backup():
    os.umask(0o077)
    target = ROOT / "backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target.mkdir(parents=True, exist_ok=False)
    compose("stop", "app", "nginx")
    try:
        database = compose(
            "exec", "-T", "db", "sh", "-c", 'pg_dump -U "$POSTGRES_USER" -Fc "$APP_DB_NAME"'
        )
        (target / "database.dump").write_bytes(database)
        files = compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "app",
            "tar",
            "-C",
            "/app/private-evidence",
            "-cf",
            "-",
            ".",
        )
        (target / "evidence.tar").write_bytes(files)
        (target / "COMPLETE").write_text("БД и свидетельства сняты при остановленной записи.\n")
    finally:
        compose("start", "app", "nginx")
    print(target.relative_to(ROOT))


def verify(path):
    path = path.resolve()
    if not (path / "COMPLETE").exists():
        raise ValueError("Копия не имеет отметки COMPLETE")
    name = "restore_check_" + uuid.uuid4().hex
    compose("exec", "-T", "db", "createdb", "-U", "postgres", name)
    try:
        compose(
            "exec",
            "-T",
            "db",
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "-U",
            "postgres",
            "-d",
            name,
            data=(path / "database.dump").read_bytes(),
        )
        count = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "postgres",
            "-d",
            name,
            "-tAc",
            "SELECT count(*) FROM django_migrations",
        )
        if int(count.strip()) < 1:
            raise ValueError("Нет миграций в восстановленной базе")
        rows = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "postgres",
            "-d",
            name,
            "-tAc",
            "SELECT COALESCE(json_agg(json_build_object('path',file,'sha256',sha256)), '[]') "
            "FROM evidence_evidence",
        )
        with tarfile.open(path / "evidence.tar") as archive:
            members = {m.name.removeprefix("./"): m for m in archive.getmembers()}
            for entry in json.loads(rows):
                member = members.get(entry["path"])
                if member is None or not member.isfile():
                    raise ValueError("Свидетельство отсутствует в архиве")
                stream = archive.extractfile(member)
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                if checksum != entry["sha256"]:
                    raise ValueError("Хэш свидетельства не совпал с восстановленной БД")
    finally:
        # Удаляется только временная база с уникальным именем, созданная выше.
        compose("exec", "-T", "db", "dropdb", "-U", "postgres", name)
    print("БД восстановлена отдельно; хэши всех свидетельств из архива сверены с ней.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["create", "verify"])
    parser.add_argument("path", type=Path, nargs="?")
    options = parser.parse_args()
    if options.operation == "create":
        backup()
    elif options.path:
        verify(options.path)
    else:
        parser.error("Для verify укажите каталог копии")
