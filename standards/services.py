from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.access import ADMIN, require_role

from .models import CatalogVersion, ImplementationRequirement, RequirementVersion


@transaction.atomic
def import_catalog(actor, payload):
    require_role(actor, ADMIN)
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != 1
        or not isinstance(payload.get("requirements"), list)
    ):
        raise ValidationError("Нужен JSON schema=1 со списком requirements")
    if (
        not isinstance(payload.get("title"), str)
        or not payload["title"].strip()
        or len(payload["title"]) > 250
        or not isinstance(payload.get("source"), str)
        or not payload["source"].strip()
        or not payload["requirements"]
        or len(payload["requirements"]) > 5000
    ):
        raise ValidationError("Нужны название, источник и от 1 до 5000 записей")
    if CatalogVersion.objects.filter(title=payload["title"]).exists():
        raise ValidationError("Каталог с таким названием уже существует")
    catalog = CatalogVersion.objects.create(
        title=payload["title"][:250],
        source=payload["source"],
        synthetic=payload.get("synthetic") is True,
        verified=False,
    )
    seen = set()
    for row in payload["requirements"]:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(k), str) or not row[k].strip()
            for k in ["code", "text", "citation", "kind"]
        ):
            raise ValidationError("Каждая мера требует code, text, citation и kind")
        if not isinstance(row.get("stage", ""), str):
            raise ValidationError("stage должен быть строкой")
        code = row["code"]
        if code in seen or len(code) > 80 or len(row["citation"]) > 250:
            raise ValidationError("Повторный или слишком длинный код/пункт")
        seen.add(code)
        kind = row["kind"]
        process, sub, direction = row.get("process"), row.get("subprocess"), row.get("direction")
        if kind == "selection":
            if type(process) is not int or process not in range(1, 9):
                raise ValidationError(f"{code}: процесс должен быть 1–8")
            count = 2 if process == 6 else 4
            if process in {1, 2, 6}:
                if type(sub) is not int or sub not in range(1, count + 1):
                    raise ValidationError(f"{code}: неверный подпроцесс")
            elif sub is not None:
                raise ValidationError(f"{code}: подпроцесс для этой структуры не нужен")
            if direction is not None:
                raise ValidationError("Направление задаётся только управленческим мерам")
        elif kind == "management":
            if (
                process is not None
                or sub is not None
                or type(direction) is not int
                or direction not in range(1, 5)
            ):
                raise ValidationError(
                    f"{code}: управление задаётся по направлению 1–4; процесс "
                    f"добавляется контекстом анкеты"
                )
        elif kind == "lifecycle":
            if any(v is not None for v in [process, sub, direction]):
                raise ValidationError(f"{code}: жизненный цикл не имеет процесса/направления")
        else:
            raise ValidationError(f"{code}: неизвестный kind")
        modes = row.get("levels")
        if (
            not isinstance(modes, dict)
            or set(modes) != {"1", "2", "3"}
            or any(not isinstance(m, str) or m not in {"О", "Т", "Н"} for m in modes.values())
        ):
            raise ValidationError(f"{code}: нужны явные levels 1/2/3 с О/Т/Н")
        req = RequirementVersion.objects.create(
            catalog=catalog,
            code=code,
            text=row["text"],
            kind=kind,
            process=process,
            subprocess=sub,
            direction=direction,
            stage=str(row.get("stage", ""))[:150],
            citation=row["citation"],
            verified=False,
        )
        for level, mode in modes.items():
            ImplementationRequirement.objects.create(requirement=req, level=int(level), mode=mode)
    return catalog


@transaction.atomic
def verify_requirement(actor, requirement_id, note):
    require_role(actor, ADMIN)
    req = RequirementVersion.objects.select_related("catalog").get(pk=requirement_id)
    catalog = CatalogVersion.objects.select_for_update().get(pk=req.catalog_id)
    if catalog.assessment_set.exclude(state="draft").exists():
        raise ValidationError("Каталог уже использован: создайте новую редакцию")
    if not note.strip():
        raise ValidationError("Укажите, с какой страницей/таблицей и редакцией сверена запись")
    req.verified = True
    req.verification_note = note.strip()
    req.verified_by = actor
    req.save(update_fields=["verified", "verification_note", "verified_by"])


@transaction.atomic
def verify_catalog(actor, catalog_id):
    require_role(actor, ADMIN)
    catalog = CatalogVersion.objects.select_for_update().get(pk=catalog_id)
    if not catalog.requirements.exists() or catalog.requirements.filter(verified=False).exists():
        raise ValidationError(
            "Сначала вручную сверьте каждую запись, таблицы уровней и состав каталога"
        )
    if catalog.assessment_set.exclude(state="draft").exists():
        raise ValidationError("Использованный каталог неизменяем")
    catalog.verified = True
    catalog.save(update_fields=["verified"])
