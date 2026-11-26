import io
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape


def build_docx(data):
    from docx import Document

    doc = Document()
    doc.add_heading("Внутренняя оценка / проект отчёта", 0)
    if data["synthetic"]:
        doc.add_paragraph(
            "СИНТЕТИЧЕСКИЙ УЧЕБНЫЙ ПРИМЕР. Не нормативный результат реальной организации."
        )
    doc.add_heading(data["title"], 1)
    doc.add_paragraph(f"Организация: {data['organization']['name']}")
    contours = data.get("contours") or [data.get("contour", {})]
    doc.add_paragraph(
        "Контуры: "
        + "; ".join(
            f"{c.get('name_snapshot', c.get('name', '—'))}, "
            f"уровень {c.get('level_snapshot', c.get('level', '—'))}"
            for c in contours
        )
    )
    doc.add_paragraph(f"Период: {' — '.join(data['period'])}")
    doc.add_paragraph(f"Область: {data['scope']}")
    doc.add_paragraph(f"Каталог: {data['inputs']['catalog_title']}")
    doc.add_paragraph(f"Проверяющий: {data['reviewer']['name']}; фиксация: {data['approved_at']}")
    doc.add_heading("Показатели", 1)
    table = doc.add_table(rows=1, cols=8)
    for cell, value in zip(
        table.rows[0].cells,
        [
            "Процесс",
            "Выбор",
            "Планирование",
            "Реализация",
            "Контроль",
            "Совершенствование",
            "E_i",
            "Уровень соответствия",
        ],
        strict=True,
    ):
        cell.text = value
    for process in data["result"]["processes"]:
        values = [
            str(process["process"]),
            process["selection"],
            *process["directions"],
            process["value"],
            process["quality"],
        ]
        for cell, value in zip(table.add_row().cells, values, strict=True):
            cell.text = str(value) if value is not None else "см. группы"
    for group in data["result"].get("groups", []):
        doc.add_heading(f"Группа контуров: уровень {group['level']}", 2)
        for process in group["processes"]:
            doc.add_paragraph(
                f"Процесс {process['process']}: выбор {process['selection']}; "
                f"направления {' / '.join(process['directions'])}; "
                f"оценка {process['value']}; {process['quality']}"
            )
        if group["lifecycle"] is not None:
            doc.add_paragraph(f"Жизненный цикл АС: {group['lifecycle']}")
    cycle = (
        data["result"]["lifecycle"] if data["result"]["lifecycle_included"] else "не оценивается"
    )
    doc.add_paragraph(f"Жизненный цикл: {cycle}")
    if not data["result"]["lifecycle_included"]:
        doc.add_paragraph(data["inputs"]["lifecycle_reason"])
    doc.add_paragraph(
        f"Количество учитываемых фактов Z: {data['result']['z']}; итог R: "
        f"{data['result']['result']}"
    )
    if data["result"]["negative"]:
        doc.add_paragraph(
            "Снижение за нарушения превышает среднюю оценку. Отрицательный итог не заменялся нулём."
        )
    doc.add_heading("Ответы, исключения, компенсации и опись свидетельств", 1)
    for answer in data["inputs"]["answers"]:
        m = answer["measure"]
        doc.add_heading(
            f"{m['code']} / уровень {m.get('level', '—')} / "
            f"процесс {m['process']} / версия {answer['number']}",
            2,
        )
        doc.add_paragraph(m["text"])
        doc.add_paragraph(f"Нормативная ссылка: {m['citation']}")
        doc.add_paragraph(answer["text"])
        doc.add_paragraph(f"Источники: {answer['sources']}")
        doc.add_paragraph(
            f"Решение: {answer['outcome']}; оценка: {answer['score']}; "
            f"основание: {answer['reason']}"
        )
        if answer["exclusion"]:
            doc.add_paragraph(f"Исключение: {answer['exclusion']}")
        if answer["compensation"]:
            doc.add_paragraph(f"Комплект компенсации: {answer['compensation']}")
        for evidence in answer["evidence"]:
            doc.add_paragraph(
                f"Файл №{evidence['id']}: {evidence['name']}; "
                f"{evidence['description']}; SHA-256 (внутренний контроль, не ГОСТ Р "
                f"34.11): {evidence['sha256']}"
            )
    doc.add_heading("Факты нарушений", 1)
    for fact in data["inputs"]["findings"]:
        doc.add_paragraph(
            f"№{fact['id']}: {fact['description']}; источник: {fact['source']}; "
            f"учитывать: {fact['include']}; дубликат: {fact['duplicate_of']}; "
            f"мотивировка: {fact['reason']}"
        )
    doc.add_heading("Протокол расчёта", 1)
    import json

    for step in data["result"]["trace"]:
        doc.add_paragraph(json.dumps(step, ensure_ascii=False, sort_keys=True))
    doc.add_paragraph(
        f"Алгоритм: {data['result']['algorithm']}; округление: "
        f"{data['result']['rounding']}; SHA-256 расчётного снимка: "
        f"{data['calculation_digest']}"
    )
    doc.add_paragraph(
        "Результат относится только к указанной области обследования. Документ не "
        "является заключением независимой проверяющей организации. "
        "Электронные записи авторства не заменяют подписи; официальный "
        "комплект приложений и его хэширование по ГОСТ этим экспортом не "
        "реализованы."
    )
    stamp = datetime.fromisoformat(data["approved_at"]).replace(tzinfo=None)
    doc.core_properties.created = stamp
    doc.core_properties.modified = stamp
    doc.core_properties.author = data["reviewer"]["name"]
    stream = io.BytesIO()
    doc.save(stream)
    # DOCX — ZIP: фиксируем метаданные контейнера для воспроизводимых байтов.
    result = io.BytesIO()
    with (
        zipfile.ZipFile(stream) as source,
        zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, source.read(name))
    return result.getvalue()


def build_xlsx(data):
    """Сформировать компактную XLSX-книгу без макросов и внешних связей."""
    sheet_names = [
        "Общие сведения",
        "Контуры и системы",
        "Итоговые результаты",
        "Расчёты по группам",
        "Управление доступом",
        "Защита сетей",
        "Контроль целостности",
        "Вредоносный код",
        "Предотвращение утечек",
        "Управление инцидентами",
        "Защита виртуализации",
        "Удалённый доступ",
        "Планирование",
        "Реализация",
        "Контроль",
        "Совершенствование",
        "Жизненный цикл АС",
        "Свидетельства",
        "Исключённые меры",
        "Компенсирующие меры",
        "Нарушения",
        "Рекомендации",
        "История корректировок",
    ]

    def row_xml(values, row_number):
        cells = []
        for column, value in enumerate(values, 1):
            letters = ""
            number = column
            while number:
                number, remainder = divmod(number - 1, 26)
                letters = chr(65 + remainder) + letters
            cells.append(
                f'<c r="{letters}{row_number}" t="inlineStr"><is><t>'
                f"{escape(str(value if value is not None else ''))}</t></is></c>"
            )
        return f'<row r="{row_number}">' + "".join(cells) + "</row>"

    answers = data.get("inputs", {}).get("answers", [])
    rows_by_sheet = {
        "Общие сведения": [
            ["Наименование", data.get("title", "")],
            ["Организация", data.get("organization", {}).get("name", "")],
            ["Период", " — ".join(data.get("period", []))],
            ["Область", data.get("scope", "")],
        ],
        "Контуры и системы": [["Контур", "Уровень"]]
        + [
            [c.get("name_snapshot", c.get("name", "")), c.get("level_snapshot", c.get("level", ""))]
            for c in data.get("contours", [])
        ]
        + [["Система", "Контур"]]
        + [[s.get("name", ""), s.get("contour_id", "")] for s in data.get("systems", [])],
        "Итоговые результаты": [
            [
                "Процесс",
                "Выбор",
                "Планирование",
                "Реализация",
                "Контроль",
                "Совершенствование",
                "E_i",
                "Уровень",
            ]
        ]
        + [
            [p["process"], p["selection"], *p["directions"], p["value"], p["quality"]]
            for p in data.get("result", {}).get("processes", [])
        ]
        + [["R", data.get("result", {}).get("result", "")]],
        "Свидетельства": [["Мера", "Файл", "Описание", "SHA-256"]]
        + [
            [a["measure"]["code"], e["name"], e["description"], e["sha256"]]
            for a in answers
            for e in a.get("evidence", [])
        ],
        "Исключённые меры": [["Мера", "Обоснование"]]
        + [[a["measure"]["code"], a["exclusion"]] for a in answers if a.get("exclusion")],
        "Компенсирующие меры": [["Мера", "Описание"]]
        + [[a["measure"]["code"], a["compensation"]] for a in answers if a.get("compensation")],
        "Нарушения": [["Идентификатор", "Описание", "Учитывается"]]
        + [
            [f["id"], f["description"], f["include"]]
            for f in data.get("inputs", {}).get("findings", [])
        ],
        "Рекомендации": [["Наименование", "Описание", "Статус", "Срок"]]
        + [
            [
                r.get("title", ""),
                r.get("description", ""),
                r.get("status", ""),
                r.get("due_date", ""),
            ]
            for r in data.get("recommendations", [])
        ],
        "История корректировок": [["Версия ответа", "Исходная", "Новая", "Обоснование", "Статус"]]
        + [
            [
                a.get("revision_id", ""),
                a.get("original_score", ""),
                a.get("proposed_score", ""),
                a.get("reason", ""),
                a.get("status", ""),
            ]
            for a in data.get("adjustments", [])
        ],
    }
    rows_by_sheet["Расчёты по группам"] = [
        [
            "Уровень защиты",
            "Процесс",
            "Выбор",
            "Планирование",
            "Реализация",
            "Контроль",
            "Совершенствование",
            "Оценка процесса",
            "Качественный уровень",
        ]
    ] + [
        [
            group["level"],
            process["process"],
            process["selection"],
            *process["directions"],
            process["value"],
            process["quality"],
        ]
        for group in data.get("result", {}).get("groups", [])
        for process in group["processes"]
    ]
    # Уровень нужен и в листах исходных оценок: код меры в разных группах совпадает.
    for process_index, name in enumerate(sheet_names[4:12], 1):
        rows_by_sheet[name] = [["Уровень", "Код меры", "Содержание", "Оценка"]] + [
            [
                a["measure"].get("level", ""),
                a["measure"]["code"],
                a["measure"]["text"],
                a.get("score"),
            ]
            for a in answers
            if a["measure"].get("kind") == "selection"
            and a["measure"].get("process") == process_index
        ]
    for direction, name in enumerate(sheet_names[12:16], 1):
        rows_by_sheet[name] = [["Уровень", "Процесс", "Код меры", "Содержание", "Оценка"]] + [
            [
                a["measure"].get("level", ""),
                a["measure"].get("process", ""),
                a["measure"]["code"],
                a["measure"]["text"],
                a.get("score"),
            ]
            for a in answers
            if a["measure"].get("kind") == "management"
            and a["measure"].get("direction") == direction
        ]
    rows_by_sheet["Жизненный цикл АС"] = [["Уровень", "Код меры", "Содержание", "Оценка"]] + [
        [a["measure"].get("level", ""), a["measure"]["code"], a["measure"]["text"], a.get("score")]
        for a in answers
        if a["measure"].get("kind") == "lifecycle"
    ]

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(sheet_names) + 1)
            )
            + "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(
                f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
                for i, name in enumerate(sheet_names, 1)
            )
            + "</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{i}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                'worksheet" '
                f'Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(sheet_names) + 1)
            )
            + "</Relationships>",
        )
        for index, name in enumerate(sheet_names, 1):
            rows = rows_by_sheet.get(name, [["Раздел", "Данные отсутствуют"]])
            xml_rows = "".join(row_xml(row, number) for number, row in enumerate(rows, 1))
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                + xml_rows
                + "</sheetData></worksheet>",
            )
    return output.getvalue()
