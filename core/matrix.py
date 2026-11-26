"""Представление замороженной анкеты; GET-фильтры не меняют область расчёта."""

from collections import Counter
from itertools import groupby
from urllib.parse import urlencode

from django.db.models import Prefetch

from assessments.models import AnswerRevision, ReviewDecision
from evidence.models import Evidence

TABS = (
    ("card", "Карточка"),
    ("scores", "Опросный лист"),
    ("applicability", "Применимость"),
    ("evidence", "Свидетельства"),
    ("findings", "Нарушения"),
    ("agreement", "Согласование"),
    ("history", "История изменений"),
    ("calculation", "Расчёт"),
    ("report", "Отчёт"),
)
KINDS = (
    ("selection", "Раздел 7 · Выбор мер"),
    ("management", "Раздел 8 · Организация и управление"),
    ("lifecycle", "Раздел 9 · Жизненный цикл"),
)
STATUSES = (
    ("empty", "Не заполнено"),
    ("pending", "Ожидает проверки"),
    ("recheck", "Ожидает повторной проверки"),
    ("rework", "На доработке"),
    ("accepted", "Принято"),
    ("excluded", "Исключено по решению"),
)
MODES = {"О": "Организационная", "Т": "Техническая", "Н": "Необязательная"}
DIRECTIONS = {1: "Планирование", 2: "Реализация", 3: "Контроль", 4: "Совершенствование"}
PROCESSES = {
    1: "Управление доступом",
    2: "Защита вычислительных сетей",
    3: "Целостность инфраструктуры",
    4: "Защита от вредоносного кода",
    5: "Предотвращение утечек",
    6: "Управление инцидентами",
    7: "Защита виртуализации",
    8: "Удалённый доступ",
}


def _choice(params, name, choices, default=""):
    value = params.get(name, default)
    return value if value in choices else default


def _row(item):
    revision = next(iter(item.matrix_revisions), None)
    decision = next(iter(revision.matrix_decisions), None) if revision else None
    if decision:
        status = decision.outcome
    elif revision:
        status = "recheck" if revision.number > 1 else "pending"
    else:
        status = "empty"
    snapshot = item.snapshot
    kind = snapshot.get("kind", "")
    process = snapshot.get("process", item.context_process) or 0
    subprocess = snapshot.get("subprocess") or 0
    direction = snapshot.get("direction") or 0
    stage = snapshot.get("stage") or ""
    kind_label = dict(KINDS).get(kind, "Неизвестный раздел")
    process_label = f"Процесс {process}" if process else "Жизненный цикл"
    area_label = item.area.title if item.area_id else "Историческая область не определена"
    group = [area_label, kind_label]
    if kind != "lifecycle":
        group.append(process_label)
    if kind == "selection" and subprocess:
        group.append(f"Подпроцесс {subprocess}")
    elif kind == "management":
        group.append(DIRECTIONS.get(direction, "Направление не указано"))
    elif kind == "lifecycle" and stage:
        group.append(stage)
    excluded = bool(decision and decision.outcome == "excluded")
    if excluded:
        applicability_status = "Исключено"
    elif revision and revision.exclusion_reason:
        if decision:
            applicability_status = "На доработке" if status == "rework" else "Остаётся в области"
        else:
            applicability_status = "Ожидает решения"
    else:
        applicability_status = "В области"
    return {
        "item": item,
        "area": str(item.area_id) if item.area_id else "legacy",
        "area_label": area_label,
        "revision": revision,
        "decision": decision,
        "status": status,
        "status_label": dict(STATUSES).get(status, "Решение требует уточнения"),
        "kind": kind,
        "kind_label": kind_label,
        "process": str(process),
        "process_label": process_label,
        "process_title": PROCESSES.get(process, "Жизненный цикл АС"),
        "block_key": (item.area_id, kind, process),
        "mode": snapshot.get("mode", ""),
        "mode_label": MODES.get(snapshot.get("mode"), "Не указано"),
        "included": not excluded,
        "scope_label": "Исключена по решению" if excluded else "Включена в оценку",
        "applicability_status": applicability_status,
        "score": decision.score if decision and decision.outcome == "accepted" else None,
        "changed": bool(
            decision
            and decision.outcome == "accepted"
            and revision.employee_score != decision.score
        ),
        "employee_score": revision.employee_score if revision else None,
        "needs_review": bool(revision and status in {"pending", "recheck", "rework"}),
        "evidence": revision.matrix_evidence if revision else [],
        "group_label": " · ".join(group),
        "subgroup_label": group[-1],
        "sort_key": (
            item.area.level if item.area_id else 0,
            {"selection": 0, "management": 1, "lifecycle": 2}.get(kind, 3),
            process,
            subprocess,
            direction,
            stage,
            snapshot.get("code", ""),
            item.pk,
        ),
    }


def matrix_context(assessment, params):
    """Вернуть текущие ответы и решения, сводку всей анкеты и выбранные GET-строки.

    Срезы Prefetch загружают последнюю ревизию и её последнее решение для каждой
    строки. Старые решения не переносятся на новую версию ответа. Связанные
    свидетельства загружаются одним запросом независимо от размера анкеты.
    """
    revisions = AnswerRevision.objects.order_by("-number", "-pk").prefetch_related(
        "answerevidence_set",
        Prefetch(
            "decisions",
            queryset=ReviewDecision.objects.order_by("-pk")[:1],
            to_attr="matrix_decisions",
        ),
        Prefetch(
            "evidence",
            queryset=Evidence.objects.order_by("pk"),
            to_attr="matrix_evidence",
        ),
    )
    items = (
        assessment.items.select_related("area")
        .order_by("pk")
        .prefetch_related(Prefetch("revisions", queryset=revisions[:1], to_attr="matrix_revisions"))
    )
    rows = sorted((_row(item) for item in items), key=lambda row: row["sort_key"])
    active_tab = _choice(params, "tab", dict(TABS), "scores")
    areas = list(assessment.calculation_areas.all())
    contours = list(assessment.contour_links.all())
    area_options = [{"value": str(area.pk), "label": area.title} for area in areas]
    contour_options = [
        {"value": str(link.contour_id), "label": link.name_snapshot} for link in contours
    ]
    filters = {
        "q": str(params.get("q", "")).strip()[:200],
        "kind": _choice(params, "kind", dict(KINDS)),
        "process": _choice(params, "process", {str(i) for i in range(1, 9)}),
        "status": _choice(params, "status", dict(STATUSES)),
        "area": _choice(params, "area", {str(area.pk) for area in areas} | {"legacy"}),
        "contour": _choice(params, "contour", {str(link.contour_id) for link in contours}),
        "attention": _choice(params, "attention", {"changed", "remarks", "excluded"}),
    }
    filtered = []
    query = filters["q"].casefold()
    for row in rows:
        snapshot = row["item"].snapshot
        searchable = f"{snapshot.get('code', '')} {snapshot.get('text', '')}".casefold()
        if query and query not in searchable:
            continue
        if filters["attention"] == "changed" and not row["changed"]:
            continue
        if filters["attention"] == "remarks" and row["status"] != "rework":
            continue
        if filters["attention"] == "excluded" and row["status"] != "excluded":
            continue
        if any(
            filters[key] and filters[key] != row[key]
            for key in ("kind", "process", "status", "area")
        ):
            continue
        if filters["contour"] and not any(
            str(link.contour_id) == filters["contour"]
            and row["item"].area_id
            and link.level_snapshot == row["item"].area.level
            for link in contours
        ):
            continue
        filtered.append(row)
    counts = Counter(row["status"] for row in rows)
    unique_codes = {row["item"].snapshot.get("code") for row in rows}
    summary = {key: counts[key] for key, _ in STATUSES}
    summary.update(
        total=len(rows),
        answered=len(rows) - counts["empty"],
        reviewed=counts["accepted"] + counts["excluded"],
    )
    tab_params = {key: value for key, value in filters.items() if value}
    blocks = []
    for key, block_rows in groupby(filtered, key=lambda row: row["block_key"]):
        block_rows = list(block_rows)
        first = block_rows[0]
        answered = sum(row["revision"] is not None for row in block_rows)
        blocks.append(
            {
                "key": "-".join(str(value) for value in key),
                "title": first["process_title"],
                "kind": first["kind_label"],
                "area": first["area_label"],
                "total": len(block_rows),
                "answered": answered,
                "percent": round(100 * answered / len(block_rows)),
                "remarks": sum(row["status"] == "rework" for row in block_rows),
                "groups": [
                    {"label": label, "rows": list(group)}
                    for label, group in groupby(block_rows, key=lambda row: row["subgroup_label"])
                ],
            }
        )
    navigation = []
    for process, title in PROCESSES.items():
        process_rows = [row for row in rows if row["process"] == str(process)]
        answered = sum(row["revision"] is not None for row in process_rows)
        navigation.append(
            {
                "process": str(process),
                "title": title,
                "total": len(process_rows),
                "percent": round(100 * answered / len(process_rows)) if process_rows else 0,
                "url": "?"
                + urlencode(
                    {**tab_params, "tab": "scores", "process": process, "kind": "selection"}
                ),
            }
        )
    stage = {
        "draft": 0,
        "filling": 1,
        "rework": 1,
        "review": 2,
        "agreement": 3,
        "approved": 4,
        "closed": 4,
    }[assessment.state]
    return {
        "workflow_steps": [
            {
                "label": label,
                "state": "done" if index < stage else "current" if index == stage else "future",
            }
            for index, label in enumerate(
                ["Подготовка", "Заполнение анкеты", "Проверка", "Согласование", "Завершение"]
            )
        ],
        "matrix_blocks": blocks,
        "process_navigation": navigation,
        "answered_percent": round(100 * summary["answered"] / len(rows)) if rows else 0,
        "return_query": params.urlencode(),
        "active_tab": active_tab,
        "matrix_tabs": [
            {"key": key, "label": label, "url": "?" + urlencode({**tab_params, "tab": key})}
            for key, label in TABS
        ],
        "matrix_filters": filters,
        "matrix_area_options": area_options,
        "matrix_contour_options": contour_options,
        "matrix_rows": filtered,
        "matrix_groups": [
            {"label": label, "rows": list(group)}
            for label, group in groupby(filtered, key=lambda row: row["group_label"])
        ],
        "matrix_total": len(rows),
        "matrix_unique_total": len(unique_codes),
        "matrix_filtered_count": len(filtered),
        "matrix_counts": summary,
        "matrix_status_options": [{"value": key, "label": label} for key, label in STATUSES],
        "matrix_kind_options": [{"value": key, "label": label} for key, label in KINDS],
        "matrix_process_options": [{"value": str(i), "label": f"Процесс {i}"} for i in range(1, 9)],
        "applicability_rows": rows,
        "applicability_request_count": sum(
            bool(row["revision"] and row["revision"].exclusion_reason) for row in rows
        ),
    }
