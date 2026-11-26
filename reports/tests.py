from io import BytesIO
from zipfile import ZipFile

from django.test import SimpleTestCase

from .export import build_xlsx


class XlsxExportTests(SimpleTestCase):
    def test_workbook_contains_required_sheets_and_is_valid_zip(self):
        data = {
            "title": "Проверка",
            "organization": {"name": "Синтетическая организация"},
            "period": ["2026-01-01", "2026-01-31"],
            "scope": "Тестовая область",
            "contours": [],
            "systems": [],
            "inputs": {"answers": [], "findings": []},
            "result": {"processes": [], "result": "0.00"},
            "recommendations": [],
            "adjustments": [],
        }
        body = build_xlsx(data)
        with ZipFile(BytesIO(body)) as archive:
            self.assertIsNone(archive.testzip())
            sheets = [
                name for name in archive.namelist() if name.startswith("xl/worksheets/sheet")
            ]
            self.assertEqual(len(sheets), 23)
            workbook = archive.read("xl/workbook.xml").decode()
            self.assertIn("Общие сведения", workbook)
            self.assertIn("История корректировок", workbook)
