from dataclasses import replace
from decimal import Decimal

from django.test import SimpleTestCase

from .engine import (
    MeasureInput,
    UndefinedResult,
    calculate,
    calculate_areas,
    combine_level_process_scores,
    qualitative,
)


def inputs(selection="1", management="0.5", life="0.5"):
    rows = [MeasureInput("S", "selection", 3, 0, 0, Decimal(selection))]
    rows += [MeasureInput(f"M{i}", "management", 3, 0, i, Decimal(management)) for i in range(1, 5)]
    if life is not None:
        rows.append(MeasureInput("L", "lifecycle", 0, 0, 0, Decimal(life)))
    return rows


class EngineTests(SimpleTestCase):
    def test_area_process_weights_then_single_penalty(self):
        # E1=1; E2=0.5; E3=0.25. Коэффициенты применяются к E_i, не к R.
        data = {
            1: inputs("1", "1", None),
            2: inputs("1", "0", None),
            3: inputs("0", "0.5", None),
        }
        data = {
            level: [replace(m, key=f"{level}-{m.key}") for m in rows]
            for level, rows in data.items()
        }
        for levels, expected in [
            ([1], "0.99"),
            ([2], "0.49"),
            ([3], "0.24"),
            ([1, 2], "0.84"),
            ([1, 3], "0.84"),
            ([2, 3], "0.39"),
            ([1, 2, 3], "0.77"),
        ]:
            with self.subTest(levels=levels):
                result = calculate_areas(
                    {level: data[level] for level in levels}, levels, [3], False, 1
                )
                self.assertEqual(result["result"], expected)
                self.assertEqual(len([s for s in result["trace"] if s["formula"] == 13]), 1)
                self.assertEqual(result["trace"][-1]["penalty"], "0.01")

    def test_missing_level_cannot_select_a_different_weight_formula(self):
        with self.assertRaisesMessage(UndefinedResult, "Неполный"):
            calculate_areas({1: inputs(life=None)}, [1, 2], [3], False, 0)

    def test_multilevel_cycle_keeps_group_results_but_blocks_final(self):
        result = calculate_areas(
            {
                1: inputs(),
                3: [replace(m, key=f"3-{m.key}") for m in inputs()],
            },
            [1, 3],
            [3],
            True,
            0,
        )
        self.assertEqual(result["status"], "undefined")
        self.assertIsNone(result["result"])
        self.assertEqual(result["processes"][0]["value"], "0.75")
        self.assertEqual([g["lifecycle"] for g in result["groups"]], ["0.50", "0.50"])
        self.assertFalse(any(s["formula"] == 13 for s in result["trace"]))

    def test_area_missing_direction_and_invalid_inputs_do_not_produce_result(self):
        for rows in [[], inputs(life=None)[:-1], [replace(inputs()[0], score=None)]]:
            with self.subTest(rows=rows), self.assertRaises(UndefinedResult):
                calculate_areas({2: rows}, [2], [3], False, 0)
        for scores in [{True: Decimal(1)}, {1: Decimal("1.1")}, {1: Decimal("NaN")}]:
            with self.subTest(scores=scores), self.assertRaises(UndefinedResult):
                combine_level_process_scores(scores)

    def test_level_combinations_formulas_9_to_12(self):
        cases = [
            ({1: "1", 2: "0.5", 3: "0"}, "0.75"),
            ({1: "1", 2: "0.5"}, "0.85"),
            ({1: "1", 3: "0.5"}, "0.90"),
            ({2: "1", 3: "0.5"}, "0.80"),
            ({3: "0.5"}, "0.50"),
        ]
        for values, expected in cases:
            with self.subTest(levels=values):
                actual = combine_level_process_scores(
                    {level: Decimal(value) for level, value in values.items()}
                )
                self.assertEqual(actual, Decimal(expected))

    def test_manual_weighted_and_penalty(self):
        result = calculate(inputs(), [3], True, 1)
        self.assertEqual(result["processes"][0]["value"], "0.75")
        self.assertEqual(result["result"], "0.62")
        self.assertEqual(result["trace"][-1]["denominator"], 2)

    def test_zero_cycle_differs_from_exclusion(self):
        self.assertEqual(calculate(inputs(life="0"), [3], True, 0)["result"], "0.38")
        self.assertEqual(calculate(inputs(life=None), [3], False, 0)["result"], "0.75")

    def test_negative_is_preserved(self):
        self.assertEqual(calculate(inputs("0", "0", "0"), [3], True, 2)["result"], "-0.02")

    def test_invalid_scales(self):
        for value in [Decimal("0.5"), Decimal("NaN"), Decimal("-1"), None, 1]:
            with self.subTest(value=value), self.assertRaises(UndefinedResult):
                calculate([replace(inputs()[0], score=value), *inputs()[1:]], [3], True, 0)

    def test_unknown_optional_rule_blocks(self):
        with self.assertRaisesMessage(UndefinedResult, "М-01"):
            calculate([replace(inputs()[0], mode="Н"), *inputs()[1:]], [3], True, 0)

    def test_zero_denominator(self):
        with self.assertRaisesMessage(UndefinedResult, "знаменатель равен нулю"):
            calculate(
                [replace(inputs()[0], score=None, excluded=True), *inputs()[1:]], [3], True, 0
            )

    def test_missing_direction(self):
        with self.assertRaises(UndefinedResult):
            calculate(inputs()[:-2] + inputs()[-1:], [3], True, 0)

    def test_boundaries(self):
        for value, label in [
            ("0.50", "Первый"),
            ("0.70", "Второй"),
            ("0.90", "Четвёртый"),
            ("0.91", "Пятый"),
        ]:
            self.assertEqual(qualitative(Decimal(value)), label)

    def test_subprocess_rounding(self):
        rows = [replace(m, process=1) for m in inputs(life=None)[1:]]
        for sub in range(1, 5):
            rows += [
                MeasureInput(f"S{sub}-{n}", "selection", 1, sub, 0, Decimal(n < 2))
                for n in range(3)
            ]
        result = calculate(rows, [1], False, 0)
        self.assertEqual(result["processes"][0]["selection"], "0.67")
        self.assertEqual(result["processes"][0]["value"], "0.59")

    def test_independent_documented_example(self):
        rows = []
        for sub, values in enumerate([[1, 0], [1, 1, 1], [1, 1, 0], [0, 0]], 1):
            for n, value in enumerate(values):
                rows.append(MeasureInput(f"S{sub}-{n}", "selection", 1, sub, 0, Decimal(value)))
        for direction, values in enumerate(
            [["1", "0.5"], ["1", "0.5", "0"], ["1", "1"], ["0.5", "0.5"]], 1
        ):
            for n, value in enumerate(values):
                rows.append(
                    MeasureInput(f"M{direction}-{n}", "management", 1, 0, direction, Decimal(value))
                )
        rows += [
            MeasureInput(f"L{n}", "lifecycle", 0, 0, 0, Decimal(v))
            for n, v in enumerate(["1", "0.5"])
        ]
        result = calculate(rows, [1], True, 1)
        self.assertEqual(result["processes"][0]["value"], "0.61")
        self.assertEqual(result["result"], "0.67")
