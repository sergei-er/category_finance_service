"""Чистые проверки переноса; нормативный текст в тестах не распространяется."""

import copy
import tempfile
import unittest
from pathlib import Path

from standards.source_catalog import (
    CONTINUATIONS,
    EXPECTED_NOTES,
    TABLE_RANGES,
    SourceError,
    extract_layout,
    join_lines,
    merge_rows,
    normalize_code,
    prepare,
    validate_structure,
)


class SourceCatalogTests(unittest.TestCase):
    def test_pdf_columns_are_3_2_1_and_code_ocr_is_local(self):
        page = (
            "Т а б л и ц а 2 — Учебная таблица\n"
            "     УЗП.З       Учебное описание       Н     О     Т\n"
            "                 продолжение описания\n"
            "     7.2.1.4 Следующий пункт\n"
            "Текст вне таблицы\n"
        )
        rows, _ = extract_layout("\f" * 14 + page)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "УЗП.3")
        self.assertEqual(rows[0]["levels"], {"1": "Т", "2": "О", "3": "Н"})
        self.assertEqual(rows[0]["lines"], ["Учебное описание", "продолжение описания"])
        self.assertEqual(normalize_code("3CB", "ЗО"), "ЗСВ.30")
        self.assertEqual(normalize_code("MAC", "23"), "МАС.23")

    def test_wrap_and_list_preserve_meaningful_hyphen(self):
        self.assertEqual(
            join_lines(
                ["Функционально-техническое описа\u00ad", "ние:", "- пункт один;", "- пункт два"]
            ),
            "Функционально-техническое описание:\n- пункт один;\n- пункт два",
        )

    def test_continuation_merges_but_duplicate_or_conflict_fails(self):
        a = {
            "code": "ЖЦ.8",
            "table": 53,
            "pdf_page": 54,
            "lines": ["Начало"],
            "levels": {"1": "О", "2": "О", "3": "Н"},
        }
        b = {**a, "pdf_page": 55, "lines": ["окончание"]}
        merged = merge_rows([a, b])
        self.assertEqual(merged["ЖЦ.8"]["lines"], ["Начало", "окончание"])
        self.assertEqual(a["lines"], ["Начало"])
        for bad in (a, {**b, "levels": {"1": "Т", "2": "О", "3": "Н"}}, {**b, "table": 54}):
            with self.subTest(bad=bad), self.assertRaises(SourceError):
                merge_rows([a, bad])

    @staticmethod
    def structural_fixture():
        rows = {}
        for table, (prefix, start, end) in enumerate(TABLE_RANGES, 1):
            for number in range(start, end + 1):
                code = f"{prefix}.{number}"
                markers = [
                    m for (_, m), codes in EXPECTED_NOTES.items() if code in codes and m != "1)"
                ]
                rows[code] = {
                    "code": code,
                    "table": table,
                    "levels": {"1": "О", "2": "О", "3": "О"},
                    "lines": ["Учебное описание " + " ".join(markers)],
                    "pdf_pages": list(CONTINUATIONS.get(code, (15,))),
                }
        notes = [{"table": t, "marker": m} for t, m in EXPECTED_NOTES]
        return rows, notes

    def test_missing_code_wrong_table_lost_continuation_and_note_fail(self):
        rows, notes = self.structural_fixture()
        self.assertEqual(len(rows), 408)
        validate_structure(rows, notes)
        missing = copy.deepcopy(rows)
        del missing["УЗП.2"]
        wrong_table = copy.deepcopy(rows)
        wrong_table["УЗП.2"]["table"] = 2
        lost_page = copy.deepcopy(rows)
        lost_page["ЗУД.10"]["pdf_pages"] = [47]
        for bad_rows, bad_notes in (
            (missing, notes),
            (wrong_table, notes),
            (lost_page, notes),
            (rows, notes[:-1]),
        ):
            with self.subTest(), self.assertRaises(SourceError):
                validate_structure(bad_rows, bad_notes)

    def test_changed_pdf_rejected_without_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.pdf"
            source.write_bytes(b"Different PDF")
            output = Path(temporary) / "out"
            with self.assertRaises(SourceError):
                prepare(source, output)
            self.assertFalse(output.exists())

    def test_footnote_does_not_include_page_number(self):
        page = "Таблица 42 — Учебная таблица\n      1) Условие\n продолжение\n    40\n"
        _, notes = extract_layout("\f" * 43 + page)
        self.assertEqual(notes[0]["lines"], ["Условие", "продолжение"])


if __name__ == "__main__":
    unittest.main()
