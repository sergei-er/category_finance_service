from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class OrganizationProfile(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    name = models.CharField("Наименование", max_length=250)
    description = models.TextField("Описание", blank=True)
    short_name = models.CharField("Сокращённое наименование", max_length=120, blank=True)
    inn = models.CharField("ИНН", max_length=12, blank=True)
    legal_address = models.CharField("Юридический адрес", max_length=300, blank=True)
    organization_type = models.CharField("Тип финансовой организации", max_length=150, blank=True)
    questionnaire_owner = models.ForeignKey(
        "Employee", null=True, blank=True, on_delete=models.PROTECT,
        related_name="questionnaire_organizations",
    )
    result_approver = models.ForeignKey(
        "Employee", null=True, blank=True, on_delete=models.PROTECT,
        related_name="approval_organizations",
    )
    synthetic = models.BooleanField("Синтетические данные", default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="single_organization")]

    def save(self, *args, **kwargs):
        if self.pk != 1:
            raise ValidationError("В приложении только один профиль организации")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ProtectionContour(models.Model):
    organization = models.ForeignKey(OrganizationProfile, on_delete=models.PROTECT)
    name = models.CharField("Название контура", max_length=200, unique=True)
    level = models.PositiveSmallIntegerField(
        "Уровень защиты", validators=[MinValueValidator(1), MaxValueValidator(3)]
    )
    description = models.TextField("Область контура", blank=True)
    code = models.CharField("Идентификатор", max_length=40, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(level__gte=1, level__lte=3), name="contour_level_range"
            )
        ]

    def __str__(self):
        return f"{self.name} (уровень {self.level})"


class Employee(models.Model):
    organization = models.ForeignKey(
        OrganizationProfile, on_delete=models.PROTECT, related_name="employees"
    )
    last_name = models.CharField("Фамилия", max_length=100)
    first_name = models.CharField("Имя", max_length=100)
    middle_name = models.CharField("Отчество", max_length=100, blank=True)
    position = models.CharField("Должность", max_length=180)
    department = models.CharField("Подразделение", max_length=180, blank=True)
    contact = models.CharField("Контактные данные", max_length=250, blank=True)
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["last_name", "first_name", "middle_name", "pk"]

    def __str__(self):
        return " ".join(filter(None, [self.last_name, self.first_name, self.middle_name]))


class InformationSystem(models.Model):
    organization = models.ForeignKey(
        OrganizationProfile, on_delete=models.PROTECT, related_name="information_systems"
    )
    name = models.CharField("Наименование", max_length=200)
    purpose = models.TextField("Назначение", blank=True)
    developer = models.CharField("Разработчик", max_length=200, blank=True)
    responsible_department = models.CharField(
        "Ответственное подразделение", max_length=180, blank=True
    )
    responsible_employee = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.PROTECT, related_name="systems"
    )
    contour = models.ForeignKey(
        ProtectionContour, on_delete=models.PROTECT, related_name="information_systems"
    )
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="system_name_org_unique")
        ]

    def clean(self):
        if self.contour_id and self.organization_id != self.contour.organization_id:
            raise ValidationError("Информационная система и контур относятся к разным организациям")
        if self.responsible_employee_id and self.organization_id != self.responsible_employee.organization_id:
            raise ValidationError("Ответственный сотрудник относится к другой организации")

    def __str__(self):
        return self.name
