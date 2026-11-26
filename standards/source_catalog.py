"""Подготовка непроверенного каталога из конкретного локального PDF.

Не универсальный OCR и не автоматическая нормативная верификация. Вход закреплён
хэшем; результат проверяется по независимой описи таблиц. База не изменяется.
Запуск: python -m standards.source_catalog --help
"""

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

SOURCE_SHA256 = "093169cb344efa7ccfb972b57de5509cb637fb46643855e4b2d4f32786e0b0b4"
TITLE = "ГОСТ Р 57580.1-2017 — издание 02.2020 — полный состав мер, импорт 1"
EDITION = "ГОСТ Р 57580.1-2017; издание февраля 2020 г., Поправка ИУС 4–2018"

# Номер таблицы -> семейство и включительные границы кодов. Никакого текста ГОСТ.
TABLE_RANGES = [
    ("УЗП", 1, 4),
    ("УЗП", 5, 21),
    ("УЗП", 22, 29),
    ("РД", 1, 16),
    ("РД", 17, 29),
    ("РД", 30, 38),
    ("РД", 39, 44),
    ("ФД", 1, 16),
    ("ФД", 17, 20),
    ("ФД", 21, 21),
    ("ИУ", 1, 6),
    ("ИУ", 7, 8),
    ("СМЭ", 1, 13),
    ("СМЭ", 14, 20),
    ("СМЭ", 21, 21),
    ("ВСА", 1, 13),
    ("ВСА", 14, 14),
    ("ЗВС", 1, 2),
    ("ЗБС", 1, 2),
    ("ЗБС", 3, 8),
    ("ЗБС", 9, 10),
    ("ЦЗИ", 1, 11),
    ("ЦЗИ", 12, 19),
    ("ЦЗИ", 20, 26),
    ("ЦЗИ", 27, 36),
    ("ЗВК", 1, 7),
    ("ЗВК", 8, 21),
    ("ЗВК", 22, 28),
    ("ПУИ", 1, 4),
    ("ПУИ", 5, 19),
    ("ПУИ", 20, 27),
    ("ПУИ", 28, 33),
    ("МАС", 1, 7),
    ("МАС", 8, 16),
    ("МАС", 17, 20),
    ("МАС", 21, 23),
    ("РИ", 1, 5),
    ("РИ", 6, 14),
    ("РИ", 15, 18),
    ("РИ", 19, 19),
    ("ЗСВ", 1, 12),
    ("ЗСВ", 13, 22),
    ("ЗСВ", 23, 31),
    ("ЗСВ", 32, 43),
    ("ЗУД", 1, 4),
    ("ЗУД", 5, 9),
    ("ЗУД", 10, 12),
    ("ПЗИ", 1, 5),
    ("РЗИ", 1, 16),
    ("КЗИ", 1, 8),
    ("КЗИ", 9, 12),
    ("СЗИ", 1, 4),
    ("ЖЦ", 1, 11),
    ("ЖЦ", 12, 14),
    ("ЖЦ", 15, 25),
    ("ЖЦ", 26, 28),
]
CONTEXTS = {
    "УЗП": (1, 1),
    "РД": (1, 2),
    "ФД": (1, 3),
    "ИУ": (1, 4),
    "СМЭ": (2, 1),
    "ВСА": (2, 2),
    "ЗВС": (2, 3),
    "ЗБС": (2, 4),
    "ЦЗИ": (3, None),
    "ЗВК": (4, None),
    "ПУИ": (5, None),
    "МАС": (6, 1),
    "РИ": (6, 2),
    "ЗСВ": (7, None),
    "ЗУД": (8, None),
}
DIRECTIONS = {"ПЗИ": 1, "РЗИ": 2, "КЗИ": 3, "СЗИ": 4}
STAGES = {
    53: "Создание (модернизация) АС",
    54: "Ввод в эксплуатацию АС",
    55: "Эксплуатация (сопровождение) АС",
    56: "Эксплуатация (сопровождение) и снятие с эксплуатации АС",
}
CONTINUATIONS = {"ЗУД.10": (47, 48), "РЗИ.9": (50, 51), "ЖЦ.8": (54, 55)}
EXPECTED_NOTES = {
    (8, "*"): {"ФД.15"},
    (22, "*"): {"ЦЗИ.8"},
    (24, "*"): {"ЦЗИ.26"},
    (28, "*"): {"ЗВК.24"},
    (35, "*"): {"МАС.18"},
    (42, "*"): {"ЗСВ.22"},
    (42, "1)"): {f"ЗСВ.{i}" for i in range(13, 23)},
    (48, "*"): {"ПЗИ.1"},
    (48, "**"): {"ПЗИ.5"},
    (49, "*"): {"РЗИ.8"},
    (49, "**"): {f"РЗИ.{i}" for i in range(11, 15)},
    (52, "*"): {"СЗИ.3"},
    (53, "*"): {"ЖЦ.3"},
    (53, "**"): {"ЖЦ.7"},
    (53, "***"): {"ЖЦ.8"},
    (53, "*4"): {"ЖЦ.11"},
    (54, "*"): {"ЖЦ.14"},
}
PREFIX_TRANSLATION = str.maketrans(
    {
        "A": "А",
        "B": "В",
        "C": "С",
        "E": "Е",
        "H": "Н",
        "K": "К",
        "M": "М",
        "O": "О",
        "P": "Р",
        "T": "Т",
        "X": "Х",
        "Y": "У",
        "3": "З",
    }
)
NUMBER_TRANSLATION = str.maketrans({"З": "3", "б": "6", "О": "0"})
CODE = re.compile(r"^\s*([А-ЯЁA-Z3]{2,5})(?:\s*\.\s*|\s+)([0-9ЗбО]+)\s+(.*)")
MODES = re.compile(r"\s+([Оо0OTТтНнH])\s+([Оо0OTТтНнH])\s+([Оо0OTТтНнH])\s*$")
HEADER = re.compile(r"(?:Таблица|Окончаниетаблицы|Продолжениетаблицы)(\d+)")
NOTE = re.compile(r"^\s*(\*+4?|1\))\s+(.*)")


class SourceError(ValueError):
    """Источник или извлечённая структура не совпали с проверенной описью."""


def normalize_code(prefix: str, number: str) -> str:
    return f"{prefix.translate(PREFIX_TRANSLATION)}.{number.translate(NUMBER_TRANSLATION)}"


def join_lines(lines: list[str]) -> str:
    """Снять мягкие переносы, сохранив дефисы и границы пунктов перечня."""
    result = ""
    for line in lines:
        if result.endswith("\u00ad"):
            result = result[:-1] + line
        elif line.startswith("- "):
            result += "\n" + line
        else:
            result += (" " if result else "") + line
    return result.replace("\u00ad", "")


def extract_layout(layout: str) -> tuple[list[dict], list[dict]]:
    """Разобрать строки pdftotext -layout, не угадывая недостающие значения."""
    rows, notes = [], []
    table = None
    for page, page_text in enumerate(layout.split("\f"), 1):
        active = note = None
        if not 15 <= page <= 57:
            continue
        for line_number, line in enumerate(page_text.splitlines(), 1):
            heading = HEADER.match(re.sub(r"\s+", "", line))
            if heading:
                table = int(heading[1])
                active = note = None
                continue
            code, modes = CODE.match(line), MODES.search(line)
            if code and modes:
                active = {
                    "table": table,
                    "code": normalize_code(code[1], code[2]),
                    "pdf_page": page,
                    "line": line_number,
                    "column": code.start(3),
                    "raw_code": line[: code.start(3)].strip(),
                    "raw_modes_3_2_1": list(modes.groups()),
                    "lines": [line[code.start(3) : modes.start()].strip()],
                    "levels": {
                        level: value.upper().replace("0", "О").translate(PREFIX_TRANSLATION)
                        for level, value in zip(("3", "2", "1"), modes.groups(), strict=True)
                    },
                }
                rows.append(active)
                note = None
                continue
            if modes:
                raise SourceError(f"Столбцы уровней без кода: PDF {page}, строка {line_number}")
            marker = NOTE.match(line)
            if marker and table != 1:
                note = {
                    "table": table,
                    "marker": marker[1],
                    "pdf_page": page,
                    "lines": [marker[2]],
                }
                notes.append(note)
                active = None
                continue
            if note:
                if (
                    not line.strip()
                    or line.strip().isdigit()
                    or re.match(r"^\s*\d+[.)]", line)
                    or "ГОСТ Р 57580" in line
                ):
                    note = None
                else:
                    note["lines"].append(line.strip())
                    continue
            if active and line.strip():
                column = len(line) - len(line.lstrip())
                if active["column"] - 2 <= column <= active["column"] + 2:
                    active["lines"].append(line.strip())
                else:
                    active = None
    return rows, notes


def merge_rows(rows: list[dict]) -> dict[str, dict]:
    merged = {}
    for row in rows:
        code = row["code"]
        if code in merged:
            previous = merged[code]
            pages = (*previous["pdf_pages"], row["pdf_page"])
            if (
                pages != CONTINUATIONS.get(code)
                or previous["table"] != row["table"]
                or previous["levels"] != row["levels"]
            ):
                raise SourceError(f"Неожиданный повтор или конфликт строки {code}")
            previous["lines"].extend(row["lines"])
            previous["pdf_pages"].append(row["pdf_page"])
        else:
            merged[code] = {**row, "lines": list(row["lines"]), "pdf_pages": [row["pdf_page"]]}
    return merged


def validate_structure(merged: dict[str, dict], notes: list[dict]) -> None:
    expected = {
        f"{prefix}.{number}": table
        for table, (prefix, start, end) in enumerate(TABLE_RANGES, 1)
        for number in range(start, end + 1)
    }
    if set(merged) != set(expected):
        raise SourceError(
            f"Неполный набор кодов: отсутствуют {sorted(set(expected) - set(merged))}; "
            f"лишние {sorted(set(merged) - set(expected))}"
        )
    for code, row in merged.items():
        if row["table"] != expected[code]:
            raise SourceError(f"{code}: неверная таблица {row['table']}")
        if set(row["levels"]) != {"1", "2", "3"} or set(row["levels"].values()) - {"О", "Т", "Н"}:
            raise SourceError(f"{code}: неверные уровни")
        if not join_lines(row["lines"]).strip():
            raise SourceError(f"{code}: пустое содержание")
        if code in CONTINUATIONS and tuple(row["pdf_pages"]) != CONTINUATIONS[code]:
            raise SourceError(f"{code}: потеряно продолжение на другой странице")
    note_keys = [(n["table"], n["marker"]) for n in notes]
    if len(note_keys) != len(set(note_keys)) or set(note_keys) != set(EXPECTED_NOTES):
        raise SourceError("Состав сносок отличается от описи")
    for (table, marker), codes in EXPECTED_NOTES.items():
        if marker == "1)":  # Сноска к заголовку всей таблицы 42, не к одной мере.
            continue
        actual = {
            code
            for code, row in merged.items()
            if row["table"] == table and marker in re.findall(r"\*+4?", join_lines(row["lines"]))
        }
        if actual != codes:
            raise SourceError(f"Таблица {table}, сноска {marker}: неверная привязка {actual}")


def build_catalog(layout: str) -> tuple[dict, dict]:
    rows, notes = extract_layout(layout)
    merged = merge_rows(rows)
    validate_structure(merged, notes)
    requirements, review = [], []
    for code, row in merged.items():
        prefix = code.split(".")[0]
        kind = (
            "selection"
            if prefix in CONTEXTS
            else ("management" if prefix in DIRECTIONS else "lifecycle")
        )
        process, subprocess_number = CONTEXTS.get(prefix, (None, None))
        body = join_lines(row["lines"])
        # Два обычных дефиса вместо мягкого переноса в закреплённом PDF.
        # Исходные строки сохраняются в протоколе; перечень правок конечен.
        if code == "ПУИ.25":
            body = body.replace("перезакре- плении", "перезакреплении")
        if code == "ЖЦ.8":
            body = body.replace("отноше- нии", "отношении")
        applied = [n for n in notes if code in EXPECTED_NOTES[(n["table"], n["marker"])]]
        pages = sorted(set(row["pdf_pages"] + [n["pdf_page"] for n in applied]))
        for n in applied:
            scope = "к заголовку таблицы" if n["marker"] == "1)" else "к отмеченному фрагменту"
            body += f"\n\nСноска {n['marker']} {scope} (таблица {n['table']}): " + join_lines(
                n["lines"]
            )
        section = {"selection": 7, "management": 8, "lifecycle": 9}[kind]
        printed = ", ".join(str(p - 4) for p in pages)
        pdf_pages = ", ".join(map(str, pages))
        requirements.append(
            {
                "code": code,
                "text": body,
                "kind": kind,
                "process": process,
                "subprocess": subprocess_number,
                "direction": DIRECTIONS.get(prefix),
                "stage": STAGES.get(row["table"], ""),
                "citation": (
                    f"ГОСТ Р 57580.1-2017, разд. {section}, табл. {row['table']}, "
                    f"с. {printed} (PDF {pdf_pages}); изд. 02.2020"
                ),
                "levels": row["levels"],
            }
        )
        review.append(
            {
                "code": code,
                "table": row["table"],
                "pdf_pages": pages,
                "source_lines": row["lines"],
                "footnotes": applied,
                "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "manual_verification": "pending",
            }
        )
    payload = {
        "schema": 1,
        "title": TITLE,
        "synthetic": False,
        "source": (
            f"{EDITION}. Источник: docs/sources/gost-57580.1-1.pdf; "
            f"SHA-256 {SOURCE_SHA256}. Таблицы 1–56 разделов 7–9: 408 мер. "
            "Перенос из текстового слоя PDF; требуется ручная сверка текста, сносок и уровней. "
            "Обозначения О/Т/Н — по п. 7.1.6; Н не является решением об исключении. "
            "Общие положения и приложения читаются по источнику."
        ),
        "requirements": requirements,
    }
    manifest = {
        "source_sha256": SOURCE_SHA256,
        "edition": EDITION,
        "counts": dict(Counter(r["kind"] for r in requirements)),
        "physical_rows": len(rows),
        "unique_codes": len(merged),
        "implementation_values": sum(len(r["levels"]) for r in requirements),
        "table_coverage": {
            str(t): sum(r["table"] == t for r in merged.values()) for t in range(1, 57)
        },
        "normalization_log": [
            {k: r[k] for k in ("code", "raw_code", "raw_modes_3_2_1", "pdf_page", "line")}
            for r in rows
        ],
        "records": review,
        "limitations": [
            "Набор полный по описи кодов таблиц 1–56; посимвольная сверка не завершена.",
            "OCR-ошибки текста возможны; структурная проверка не подтверждает содержание.",
            "Ни каталог, ни меры не получают признак verified автоматически.",
            "М-01: нормативное правило расчётной обработки Н остаётся открытым.",
        ],
    }
    return payload, manifest


def prepare(source: Path, output: Path) -> dict:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise SourceError("PDF не соответствует закреплённому экземпляру; нужна новая сверка")
    result = subprocess.run(
        ["pdftotext", "-layout", str(source), "-"],
        check=True,
        capture_output=True,
    )
    payload, manifest = build_catalog(result.stdout.decode("utf-8"))
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    if len(encoded) > 2 * 1024 * 1024:
        raise SourceError("Импортный файл превышает ограничение формы 2 МиБ")
    manifest["payload_sha256"] = hashlib.sha256(encoded).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    # Не перезаписываем ручную сверку или ранее подготовленный пакет.
    for name, data in (
        ("catalog.json", encoded),
        ("review.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()),
    ):
        target = output / name
        if target.exists() and target.read_bytes() != data:
            raise SourceError(f"{target}: уже существует другой файл; выберите новый каталог")
    for name, data in (
        ("catalog.json", encoded),
        ("review.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()),
    ):
        (output / name).write_bytes(data)
    return {"counts": manifest["counts"], "size_bytes": len(encoded), "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("docs/sources/gost-57580.1-1.pdf"))
    parser.add_argument("--output", type=Path, default=Path("local-catalogs/gost-57580.1-ed2020"))
    args = parser.parse_args()
    try:
        summary = prepare(args.source, args.output)
    except (SourceError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Подготовка остановлена: {exc}\n")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
