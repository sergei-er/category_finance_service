from .services import import_catalog, verify_catalog, verify_requirement


def demo_payload():
    rows = []
    for process in range(1, 9):
        subs = range(1, (2 if process == 6 else 4) + 1) if process in {1, 2, 6} else [None]
        for sub in subs:
            for measure in range(1, 3):
                rows.append(
                    {
                        "code": f"SYN-P{process}-S{sub or 0}-{measure}",
                        "text": f"Синтетическое учебное действие {measure} процесса "
                        "{process}, не текст ГОСТ",
                        "kind": "selection",
                        "process": process,
                        "subprocess": sub,
                        "citation": "Учебный набор, не нормативная мера",
                        "levels": {"1": "О", "2": "О", "3": "О"},
                    }
                )
    for direction in range(1, 5):
        rows.append(
            {
                "code": f"SYN-M{direction}",
                "text": f"Учебная управленческая мера направления {direction}",
                "kind": "management",
                "direction": direction,
                "citation": "Учебный набор, не ГОСТ",
                "levels": {"1": "О", "2": "О", "3": "О"},
            }
        )
    for measure in range(1, 5):
        rows.append(
            {
                "code": f"SYN-L{measure}",
                "text": f"Учебное действие жизненного цикла {measure}",
                "kind": "lifecycle",
                "citation": "Учебный набор, не ГОСТ",
                "levels": {"1": "О", "2": "О", "3": "О"},
            }
        )
    return {
        "schema": 1,
        "title": "Синтетический учебный каталог v1 (НЕ ГОСТ)",
        "source": "Только проверка приложения; не нормативный каталог",
        "synthetic": True,
        "requirements": rows,
    }


def create_demo_catalog(actor):
    catalog = import_catalog(actor, demo_payload())
    for req in catalog.requirements.all():
        verify_requirement(
            actor, req.pk, "Проверена структура синтетического примера; не верификация ГОСТ"
        )
    verify_catalog(actor, catalog.pk)
    catalog.refresh_from_db()
    return catalog
