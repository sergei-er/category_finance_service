"""Чистый расчёт групп контуров; состав входов определяет вызывающий сервис.

Решения/снимки проверяются до вызова. Неподтверждённое Н не оценивается.
Ссылки, интерпретации округления и ограничения — docs/METHODOLOGY.md.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

LEVEL_WEIGHTS = {
    (1,): ("1",),
    (2,): ("1",),
    (3,): ("1",),
    (1, 2): ("0.7", "0.3"),
    (1, 3): ("0.8", "0.2"),
    (2, 3): ("0.6", "0.4"),
    (1, 2, 3): ("0.6", "0.3", "0.1"),
}


class UndefinedResult(ValueError):
    def __init__(self, issues):
        self.issues = list(issues)
        super().__init__("; ".join(self.issues))


@dataclass(frozen=True)
class MeasureInput:
    key: str
    kind: str
    process: int
    subprocess: int
    direction: int
    score: Decimal | None
    excluded: bool = False
    verified: bool = True
    mode: str = "О"


def rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def qualitative(value: Decimal) -> str:
    if not value.is_finite() or not Decimal(0) <= value <= Decimal(1):
        raise ValueError("Оценка процесса вне [0;1]")
    if value == 0:
        return "Нулевой"
    for boundary, label in [
        ("0.50", "Первый"),
        ("0.70", "Второй"),
        ("0.85", "Третий"),
        ("0.90", "Четвёртый"),
        ("1", "Пятый"),
    ]:
        if value <= Decimal(boundary):
            return label
    raise ValueError("Не определён уровень")


def combine_level_process_scores(level_scores: dict[int, Decimal]) -> Decimal:
    """Объединить оценку одного процесса для представленных уровней по формулам 9–12."""
    if not level_scores or any(
        type(level) is not int or level not in {1, 2, 3} for level in level_scores
    ):
        raise UndefinedResult(["Некорректный набор уровней защиты"])
    if any(
        not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value <= 1
        for value in level_scores.values()
    ):
        raise UndefinedResult(["Некорректная оценка уровня защиты"])
    levels = tuple(sorted(level_scores))
    with localcontext() as ctx:
        ctx.prec = 50
        return rounded(
            sum(
                (
                    level_scores[level] * Decimal(weight)
                    for level, weight in zip(levels, LEVEL_WEIGHTS[levels], strict=True)
                ),
                Decimal(0),
            )
        )


def calculate_areas(
    areas: dict[int, list[MeasureInput]],
    expected_levels: list[int],
    processes: list[int],
    lifecycle: bool,
    z: int,
) -> dict:
    """Сначала (1)–(8) по уровню, затем (9)–(12) по процессу, затем (13).

    Межуровневое правило для E_AC не установлено в п. 7.8. При включённом
    жизненном цикле нескольких уровней сохраняем групповые/процессные оценки,
    но не придумываем общий R. См. М-05 в docs/METHODOLOGY.md.
    """
    if (
        not expected_levels
        or any(type(level) is not int or level not in {1, 2, 3} for level in expected_levels)
        or len(set(expected_levels)) != len(expected_levels)
        or set(areas) != set(expected_levels)
        or any(type(level) is not int for level in areas)
    ):
        raise UndefinedResult(["Неполный либо некорректный набор расчётных уровней"])
    if type(z) is not int or z < 0:
        raise UndefinedResult(["Z должен быть целым неотрицательным числом"])
    keys = [row.key for rows in areas.values() for row in rows]
    if len(keys) != len(set(keys)):
        raise UndefinedResult(["Повторяющиеся идентификаторы входов разных областей"])
    groups = []
    for level in sorted(areas):
        try:
            result = calculate(areas[level], processes, lifecycle, 0)
        except UndefinedResult as exc:
            raise UndefinedResult([f"Уровень {level}: {issue}" for issue in exc.issues]) from exc
        groups.append(
            {
                "level": level,
                "processes": result["processes"],
                "lifecycle": result["lifecycle"],
                "trace": [step for step in result["trace"] if step["formula"] != 13],
            }
        )
    trace = [dict(step, level=group["level"]) for group in groups for step in group["trace"]]
    scores = []
    levels = tuple(sorted(areas))
    formulas = {(1, 2, 3): 9, (1, 2): 10, (1, 3): 11, (2, 3): 12}
    for index, process in enumerate(processes):
        values = {g["level"]: Decimal(g["processes"][index]["value"]) for g in groups}
        value = combine_level_process_scores(values)
        if len(groups) == 1:
            scores.append(groups[0]["processes"][index])
        else:
            # Выбор и направления существуют только внутри группы уровня.
            scores.append(
                {
                    "process": process,
                    "selection": None,
                    "directions": [None] * 4,
                    "value": str(value),
                    "quality": qualitative(value),
                }
            )
            trace.append(
                {
                    "formula": formulas[levels],
                    "context": f"Процесс {process}, все уровни",
                    "inputs": [{"level": level, "value": str(values[level])} for level in levels],
                    "weights": list(LEVEL_WEIGHTS[levels]),
                    "raw": str(
                        sum(
                            (
                                values[level] * Decimal(weight)
                                for level, weight in zip(levels, LEVEL_WEIGHTS[levels], strict=True)
                            ),
                            Decimal(0),
                        )
                    ),
                    "rounded": str(value),
                }
            )
    result = {
        "algorithm": "gost-areas-2",
        "groups": groups,
        "processes": scores,
        "lifecycle_included": lifecycle,
        "lifecycle": groups[0]["lifecycle"] if len(groups) == 1 else None,
        "z": z,
        "result": None,
        "negative": False,
        "rounding": "ROUND_HALF_UP по результатам формул",
        "trace": trace,
        "status": "defined",
        "issues": [],
    }
    if lifecycle and len(groups) > 1:
        result.update(
            status="undefined",
            issues=[
                "Не определён общий результат: требуется подтверждённое правило объединения "
                "оценок жизненного цикла АС разных уровней защиты (М-05). "
                "Оценки групп и процессов рассчитаны отдельно."
            ],
        )
        return result
    with localcontext() as ctx:
        ctx.prec = 50
        numerator = sum((Decimal(row["value"]) for row in scores), Decimal(0))
        if lifecycle:
            numerator += Decimal(result["lifecycle"])
        denominator = len(processes) + int(lifecycle)
        before = numerator / Decimal(denominator)
        penalty = Decimal("0.01") * z
        raw = before - penalty
        final = rounded(raw)
    result.update(result=str(final), negative=final < 0)
    trace.append(
        {
            "formula": 13,
            "numerator": str(numerator),
            "denominator": denominator,
            "before_penalty": str(before),
            "z": z,
            "penalty": str(penalty),
            "raw": str(raw),
            "rounded": str(final),
        }
    )
    return result


def calculate(inputs: list[MeasureInput], processes: list[int], lifecycle: bool, z: int) -> dict:
    if type(z) is not int or z < 0:
        raise UndefinedResult(["Z должен быть целым неотрицательным числом"])
    if (
        not processes
        or len(set(processes)) != len(processes)
        or any(type(p) is not int or p not in range(1, 9) for p in processes)
    ):
        raise UndefinedResult(["Некорректный набор процессов"])
    issues = []
    if len({m.key for m in inputs}) != len(inputs):
        issues.append("Повторяющиеся идентификаторы входов")
    for m in inputs:
        if m.kind not in {"selection", "management", "lifecycle"}:
            issues.append(f"{m.key}: неизвестный вид показателя")
        if not m.verified:
            issues.append(f"{m.key}: запись не верифицирована")
        if m.mode not in {"О", "Т", "Н"}:
            issues.append(f"{m.key}: неизвестное обозначение способа")
        if m.mode == "Н" and not m.excluded:
            issues.append(f"{m.key}: М-01, правило учёта Н не подтверждено")
        if m.excluded:
            if m.score is not None:
                issues.append(f"{m.key}: исключённая мера не имеет числовой оценки")
            continue
        allowed = (
            {Decimal("0"), Decimal("1")}
            if m.kind == "selection"
            else {Decimal("0"), Decimal("0.5"), Decimal("1")}
        )
        if not isinstance(m.score, Decimal) or not m.score.is_finite() or m.score not in allowed:
            issues.append(f"{m.key}: отсутствующая либо недопустимая оценка")
        if m.kind in {"selection", "management"} and m.process not in processes:
            issues.append(f"{m.key}: процесс вне области")
        if m.kind == "management" and m.direction not in range(1, 5):
            issues.append(f"{m.key}: неизвестное направление")
        if m.kind == "lifecycle" and not lifecycle:
            issues.append(f"{m.key}: жизненный цикл вне области")
    if issues:
        raise UndefinedResult(issues)
    trace = []
    active = [m for m in inputs if not m.excluded]

    def mean(rows, formula, context):
        if not rows:
            raise UndefinedResult([f"{context}: нет оцениваемых мер, знаменатель равен нулю"])
        numerator = sum((m.score for m in rows), Decimal(0))
        raw = numerator / Decimal(len(rows))
        value = rounded(raw)
        trace.append(
            {
                "formula": formula,
                "context": context,
                "inputs": [{"key": m.key, "value": str(m.score)} for m in rows],
                "numerator": str(numerator),
                "denominator": len(rows),
                "raw": str(raw),
                "rounded": str(value),
            }
        )
        return value

    scores = []
    with localcontext() as ctx:
        ctx.prec = 50
        for process in processes:
            rows = [m for m in active if m.kind == "selection" and m.process == process]
            if process in {1, 2, 6}:
                count = 2 if process == 6 else 4
                if any(m.subprocess not in range(1, count + 1) for m in rows):
                    raise UndefinedResult([f"Процесс {process}: неверный подпроцесс"])
                subs = [
                    mean(
                        [m for m in rows if m.subprocess == sub],
                        1,
                        f"Процесс {process}, подпроцесс {sub}",
                    )
                    for sub in range(1, count + 1)
                ]
                numerator = sum(subs)
                raw = numerator / Decimal(count)
                selection = rounded(raw)
                trace.append(
                    {
                        "formula": 2,
                        "context": f"Процесс {process}",
                        "inputs": list(map(str, subs)),
                        "numerator": str(numerator),
                        "denominator": count,
                        "raw": str(raw),
                        "rounded": str(selection),
                    }
                )
            else:
                selection = mean(rows, 1, f"Процесс {process}")
            directions = [
                mean(
                    [
                        m
                        for m in active
                        if m.kind == "management"
                        and m.process == process
                        and m.direction == direction
                    ],
                    direction + 2,
                    f"Процесс {process}, направление {direction}",
                )
                for direction in range(1, 5)
            ]
            weighted = sum(
                (
                    w * v
                    for w, v in zip(
                        map(Decimal, ["0.2", "0.4", "0.25", "0.15"]), directions, strict=True
                    )
                ),
                Decimal(0),
            )
            raw = (selection + weighted) / Decimal(2)
            value = rounded(raw)
            trace.append(
                {
                    "formula": 8,
                    "context": f"Процесс {process}",
                    "selection": str(selection),
                    "directions": list(map(str, directions)),
                    "weights": ["0.2", "0.4", "0.25", "0.15"],
                    "weighted": str(weighted),
                    "denominator": 2,
                    "raw": str(raw),
                    "rounded": str(value),
                }
            )
            scores.append(
                {
                    "process": process,
                    "selection": str(selection),
                    "directions": list(map(str, directions)),
                    "value": str(value),
                    "quality": qualitative(value),
                }
            )
        cycle = (
            mean([m for m in active if m.kind == "lifecycle"], 7, "Жизненный цикл")
            if lifecycle
            else None
        )
        numerator = sum((Decimal(s["value"]) for s in scores), Decimal(0)) + (
            cycle if cycle is not None else Decimal(0)
        )
        denominator = len(processes) + int(lifecycle)
        before = numerator / Decimal(denominator)
        penalty = Decimal("0.01") * z
        raw = before - penalty
        result = rounded(raw)
        trace.append(
            {
                "formula": 13,
                "numerator": str(numerator),
                "denominator": denominator,
                "before_penalty": str(before),
                "z": z,
                "penalty": str(penalty),
                "raw": str(raw),
                "rounded": str(result),
            }
        )
    return {
        "algorithm": "gost-prototype-1",
        "processes": scores,
        "lifecycle_included": lifecycle,
        "lifecycle": str(cycle) if cycle is not None else None,
        "z": z,
        "result": str(result),
        "negative": result < 0,
        "rounding": "ROUND_HALF_UP по результатам формул",
        "trace": trace,
    }
