from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.access import EMPLOYEE, REVIEWER
from organization.models import InformationSystem, ProtectionContour
from standards.models import CatalogVersion

from .models import Assessment, FindingRevision
from .presets import get_demo_preset


class AssessmentForm(forms.ModelForm):
    contours = forms.ModelMultipleChoiceField(
        label="Контуры безопасности",
        queryset=ProtectionContour.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    systems = forms.ModelMultipleChoiceField(
        label="Информационные системы",
        queryset=InformationSystem.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    employee = forms.ModelChoiceField(
        label="Ответственный специалист по ИБ", queryset=get_user_model().objects.none()
    )
    reviewer = forms.ModelChoiceField(
        label="Внутренний проверяющий", queryset=get_user_model().objects.none()
    )
    processes = forms.MultipleChoiceField(
        label="Процессы",
        choices=[(str(i), f"Процесс {i}") for i in range(1, 9)],
        widget=forms.CheckboxSelectMultiple,
        initial=[str(i) for i in range(1, 9)],
    )
    lifecycle_included = forms.BooleanField(
        label="Оценивать жизненный цикл", required=False, initial=True
    )
    lifecycle_reason = forms.CharField(
        label="Основание неоцениваемости жизненного цикла (п. 6.8)",
        required=False,
        widget=forms.Textarea,
    )

    class Meta:
        model = Assessment
        fields = [
            "title",
            "purpose",
            "contours",
            "systems",
            "employee",
            "reviewer",
            "catalog",
            "period_start",
            "period_end",
            "scope",
            "processes",
            "lifecycle_included",
            "lifecycle_reason",
        ]
        widgets = {
            "period_start": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "period_end": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }

    def __init__(self, *args, mode="manual", **kwargs):
        self.mode = mode
        self.preset = get_demo_preset()
        self.organization = self.preset.organization
        self.demo_available = self.preset.available
        self.mode_available = mode == "manual" or (mode == "demo" and self.demo_available)
        self.mode_message = (
            self.preset.message
            if mode == "demo"
            else "Заполните карточку и выберите область оценки."
        )
        initial = {
            "period_start": timezone.localdate(),
            "period_end": timezone.localdate(),
            "processes": [str(i) for i in range(1, 9)],
        }
        if mode == "demo" and self.demo_available:
            initial.update(self.preset.initial)
        initial.update(kwargs.pop("initial", {}) or {})
        if mode == "demo" and self.demo_available:
            initial["catalog"] = self.preset.catalog.pk
        kwargs["initial"] = initial
        super().__init__(*args, **kwargs)
        self.fields["contours"].queryset = ProtectionContour.objects.order_by("level", "name")
        self.fields["systems"].queryset = InformationSystem.objects.filter(archived=False).order_by(
            "name"
        )
        self.fields["employee"].queryset = (
            get_user_model().objects.filter(is_active=True, groups__name=EMPLOYEE).distinct()
        )
        self.fields["reviewer"].queryset = (
            get_user_model().objects.filter(is_active=True, groups__name=REVIEWER).distinct()
        )
        self.fields["catalog"].queryset = CatalogVersion.objects.filter(verified=True)
        if mode == "manual":
            self.fields["catalog"].queryset = self.fields["catalog"].queryset.filter(
                synthetic=False
            )
        self.fields["catalog"].label = "Проверенная версия каталога"
        self.fields[
            "contours"
        ].help_text = "Выберите один или несколько контуров, входящих в область обследования."
        self.fields[
            "catalog"
        ].help_text = (
            "Выберите редакцию каталога. Его состав фиксируется при начале заполнения анкеты."
        )
        self.fields["scope"].help_text = (
            "Опишите согласованную область оценки и основания её ограничения, "
            "если включены не все процессы."
        )
        self.fields["processes"].help_text = (
            "Выберите процессы, которые входят в область обследования. Для каждого выбранного "
            "процесса система сформирует соответствующие меры разделов 7 и 8. Отдельное "
            "исключение конкретной меры оформляется проверяющим с обоснованием."
        )
        self.fields["lifecycle_reason"].label = "Основание, почему жизненный цикл не оценивается"
        self.fields["lifecycle_reason"].help_text = (
            "Заполняется только если снята отметка «Оценивать жизненный цикл». "
            "Укажите документированную причину ограничения области обследования; "
            "это не исключает отдельные меры автоматически."
        )
        if mode == "demo":
            catalog_ids = [self.preset.catalog.pk] if self.demo_available else []
            self.fields["catalog"].queryset = self.fields["catalog"].queryset.filter(
                pk__in=catalog_ids, synthetic=True
            )
            self.fields["contours"].queryset = self.fields["contours"].queryset.filter(
                pk__in=self.preset.contour_ids, organization__synthetic=True
            )

    def _selected_object(self, field_name):
        value = self[field_name].value()
        if value is None or not str(value).isdigit():
            return None
        return self.fields[field_name].queryset.filter(pk=value).first()

    @property
    def selected_contour(self):
        selected = self["contours"].value() or []
        return self.fields["contours"].queryset.filter(pk__in=selected).first()

    @property
    def selected_catalog(self):
        return self._selected_object("catalog")

    def clean_processes(self):
        return sorted(int(p) for p in self.cleaned_data["processes"])

    def clean(self):
        data = super().clean()
        if self.mode not in {"manual", "demo"}:
            raise forms.ValidationError("Неизвестный способ создания обследования")
        if self.data.get("mode", self.mode) != self.mode:
            raise forms.ValidationError("Способ создания обследования изменился; обновите карточку")
        if not self.mode_available:
            raise forms.ValidationError(self.mode_message)
        if (
            data.get("period_start")
            and data.get("period_end")
            and data["period_start"] > data["period_end"]
        ):
            raise forms.ValidationError("Начало периода позже окончания")
        if not data.get("lifecycle_included") and not data.get("lifecycle_reason", "").strip():
            self.add_error(
                "lifecycle_reason", "Укажите основание; пустые данные не означают неоцениваемость"
            )
        contours = data.get("contours")
        systems = data.get("systems")
        if data.get("employee") and data.get("employee") == data.get("reviewer"):
            raise forms.ValidationError(
                "Сотрудник и проверяющий должны быть разными пользователями"
            )
        if contours and systems:
            contour_ids = {contour.pk for contour in contours}
            invalid = [system.name for system in systems if system.contour_id not in contour_ids]
            if invalid:
                self.add_error("systems", "Системы вне выбранных контуров: " + ", ".join(invalid))
        return data


class AssignmentForm(forms.Form):
    employee = forms.ModelChoiceField(label="Сотрудник", queryset=get_user_model().objects.none())
    reviewer = forms.ModelChoiceField(label="Проверяющий", queryset=get_user_model().objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = get_user_model().objects.filter(
            is_active=True, groups__name=EMPLOYEE
        )
        self.fields["reviewer"].queryset = get_user_model().objects.filter(
            is_active=True, groups__name=REVIEWER
        )

    def clean(self):
        data = super().clean()
        if data.get("employee") == data.get("reviewer") and data.get("employee"):
            raise forms.ValidationError(
                "Сотрудник и проверяющий должны быть разными пользователями"
            )
        return data


class AnswerForm(forms.Form):
    score = forms.TypedChoiceField(
        label="Исходная оценка",
        choices=[("", "Не оценено"), ("0", "0"), ("0.5", "0,5"), ("1", "1")],
        coerce=Decimal,
        empty_value=None,
    )
    text = forms.CharField(label="Описание реализации", widget=forms.Textarea)
    sources = forms.CharField(
        label="Источники: документ, опрос, наблюдение и дата", widget=forms.Textarea, required=False
    )
    exclusion_reason = forms.CharField(
        label="Предложение об исключении: основание по п. 6.11",
        required=False,
        widget=forms.Textarea,
    )
    compensation = forms.CharField(
        label="Комплект компенсации и обоснование", required=False, widget=forms.Textarea
    )
    files = forms.ModelMultipleChoiceField(
        label="Свидетельства", queryset=None, required=False, widget=forms.CheckboxSelectMultiple
    )
    reference = forms.CharField(
        label="Разделы/страницы выбранных материалов", required=False, max_length=250
    )

    def __init__(self, *args, assessment, item=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["files"].queryset = assessment.evidence_files.filter(retired=False)
        if item and item.snapshot.get("kind") == "selection":
            self.fields["score"].choices = [
                ("", "Не оценено"),
                ("0", "0 — мера не выбрана"),
                ("1", "1 — мера выбрана"),
            ]

    def clean(self):
        data = super().clean()
        if not data.get("sources", "").strip() and not data.get("files"):
            raise forms.ValidationError("Укажите источник свидетельств или выберите файл")
        return data


class ReviewForm(forms.Form):
    outcome = forms.ChoiceField(
        label="Решение",
        choices=[
            ("accepted", "Принято"),
            ("rework", "На доработку"),
            ("excluded", "Не оценивается по обоснованию"),
        ],
    )
    score = forms.TypedChoiceField(
        label="Оценка (пусто для доработки/исключения)",
        choices=[("", "—"), ("0", "0"), ("0.5", "0,5"), ("1", "1")],
        required=False,
        coerce=Decimal,
        empty_value=None,
    )
    reason = forms.CharField(label="Мотивировка решения, факты и источник", widget=forms.Textarea)


class AdjustmentDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        label="Решение", choices=[("accepted", "Согласовать"), ("rejected", "Отклонить")]
    )
    comment = forms.CharField(label="Комментарий", required=False, widget=forms.Textarea)

    def clean(self):
        data = super().clean()
        if data.get("decision") == "rejected" and not data.get("comment", "").strip():
            self.add_error("comment", "При отклонении укажите обоснование")
        return data


class ReasonForm(forms.Form):
    reason = forms.CharField(label="Причина возврата анкеты", widget=forms.Textarea)


class FindingForm(forms.ModelForm):
    class Meta:
        model = FindingRevision
        fields = [
            "description",
            "source",
            "discovered_on",
            "investigation_completed",
            "response_documented",
            "potential_harm",
            "basis",
            "evidence",
        ]
        labels = {
            "description": "Факт и объект нарушения",
            "source": "Кем обнаружено (не автор записи)",
            "discovered_on": "Дата обнаружения",
            "investigation_completed": "Разбирательство завершено до окончания оценки",
            "response_documented": "Реагирование принято/запланировано документально",
            "potential_harm": "Возможен инцидент с ущербом",
            "basis": "Основания и реквизиты подтверждающих документов",
            "evidence": "Свидетельства",
        }
        widgets = {"discovered_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, assessment, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["evidence"].queryset = assessment.evidence_files.filter(retired=False)


class FindingReviewForm(forms.Form):
    include = forms.BooleanField(label="Учитывать самостоятельный факт в Z", required=False)
    duplicate_of = forms.ModelChoiceField(
        label="Дубликат основной карточки", queryset=None, required=False
    )
    reason = forms.CharField(
        label="Основание учёта/неучёта и самостоятельности факта", widget=forms.Textarea
    )

    def __init__(self, *args, finding, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["duplicate_of"].queryset = finding.assessment.findings.exclude(pk=finding.pk)
