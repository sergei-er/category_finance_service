"""Начальные значения учебной карточки из существующих синтетических данных."""

from dataclasses import dataclass, field

from django.utils import timezone

from organization.models import OrganizationProfile, ProtectionContour
from standards.demo import demo_payload
from standards.models import CatalogVersion


@dataclass(frozen=True)
class DemoPreset:
    available: bool
    message: str
    organization: OrganizationProfile | None
    catalog: CatalogVersion | None = None
    contour_ids: tuple[int, ...] = ()
    initial: dict = field(default_factory=dict)


def get_demo_preset() -> DemoPreset:
    """Найти учебный набор без создания или изменения профиля организации."""
    organization = OrganizationProfile.objects.filter(pk=1).first()
    if not organization or not organization.synthetic:
        return DemoPreset(
            False,
            "Учебный шаблон доступен только для существующего профиля "
            "синтетической организации. Профиль организации не изменяется.",
            organization,
        )

    catalog = CatalogVersion.objects.filter(
        title=demo_payload()["title"], synthetic=True, verified=True
    ).first()
    if catalog is None:
        return DemoPreset(
            False,
            "Учебный шаблон недоступен: подготовленный каталог SYN версии v1 "
            "не найден или его проверка не завершена.",
            organization,
        )

    contour_ids = tuple(
        ProtectionContour.objects.filter(organization=organization)
        .order_by("name")
        .values_list("pk", flat=True)
    )
    if not contour_ids:
        return DemoPreset(
            False,
            "Учебный шаблон недоступен: у синтетической организации нет контура защиты.",
            organization,
            catalog,
        )

    today = timezone.localdate()
    initial = {
        "title": "Учебное обследование по шаблону SYN v1",
        "catalog": catalog.pk,
        "period_start": today,
        "period_end": today,
        "scope": "Учебная оценка выбранного контура синтетической организации: "
        "процессы 1–8 и жизненный цикл. Используются синтетические меры SYN; "
        "набор не является полным каталогом ГОСТ Р 57580.1.",
        "processes": [str(process) for process in range(1, 9)],
        "lifecycle_included": True,
    }
    if len(contour_ids) == 1:
        initial["contours"] = list(contour_ids)
    return DemoPreset(
        True,
        "Шаблон заполняет карточку учебного обследования. Меры SYN вымышлены "
        "для проверки приложения; это не полный каталог ГОСТ Р 57580.1. "
        "Ответы, свидетельства и решения предстоит оформить участникам обследования.",
        organization,
        catalog,
        contour_ids,
        initial,
    )
