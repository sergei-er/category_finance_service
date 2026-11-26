import json
from collections import Counter

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from accounts.access import (
    ADMIN,
    REVIEWER,
    assigned_filter,
    has_role,
    require_participant,
    require_reviewer,
    require_role,
)
from accounts.forms import CreateUserForm, RolesForm
from assessments import services
from assessments.forms import (
    AdjustmentDecisionForm,
    AnswerForm,
    AssessmentForm,
    AssignmentForm,
    FindingForm,
    FindingReviewForm,
    ReasonForm,
    ReviewForm,
)
from assessments.models import AnswerRevision, Assessment, AssessmentItem, Finding, ScoreAdjustment
from auditlog.models import AuditEvent
from calculations.engine import UndefinedResult
from calculations.models import CalculationRun
from evidence.models import Evidence
from evidence.services import open_evidence, retire_evidence, upload_evidence
from organization.forms import ContourForm, EmployeeForm, InformationSystemForm, OrganizationForm
from organization.models import Employee, InformationSystem, OrganizationProfile, ProtectionContour
from reports.models import ReportExport, ResultSnapshot
from standards.models import CatalogVersion, RequirementVersion
from standards.services import import_catalog, verify_catalog, verify_requirement


class UploadForm(forms.Form):
    file = forms.FileField(label="Свидетельство (до 10 МиБ): PDF, TXT, DOCX, XLSX")
    description = forms.CharField(
        label="Подтверждаемые факты, разделы/страницы", widget=forms.Textarea
    )


class CatalogImportForm(forms.Form):
    file = forms.FileField(label="Каталог JSON schema=1, до 2 МиБ")


class CatalogTransitionForm(forms.Form):
    catalog = forms.ModelChoiceField(
        label="Каталог нормативных мер",
        queryset=CatalogVersion.objects.filter(synthetic=False).order_by("-pk"),
    )
    confirm = forms.BooleanField(
        label="Создать отдельную анкету без переноса ответов SYN. "
        "Непроверенный каталог не позволяет утвердить нормативный результат."
    )


@login_required
def catalog_transition(request, pk):
    require_role(request.user, ADMIN)
    assessment = get_object_or_404(Assessment, pk=pk, catalog__synthetic=True)
    form = CatalogTransitionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            created = services.replace_synthetic_questionnaire(
                request.user, assessment.pk, form.cleaned_data["catalog"]
            )
            messages.success(request, "Новая анкета создана. Старое обследование сохранено.")
            return redirect("assessment", pk=created.pk)
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(
        request,
        form,
        "Новая анкета по нормативному каталогу",
        assessment=assessment,
        context_text="Переносятся участники и область обследования. Коды, тексты и О/Т/Н "
        "берутся из выбранной редакции. Ответы, файлы и решения старой анкеты не изменяются. "
        "Если каталог ещё не сверен, анкета доступна для предварительного заполнения; "
        "после сверки потребуется новая анкета по подтверждённой редакции.",
    )


class NoteForm(forms.Form):
    note = forms.CharField(
        label="Результат ручной сверки: редакция, страница, таблица, уровни", widget=forms.Textarea
    )


def form_page(request, form, title, back="workspace", **extra):
    return render(
        request, "workspace/form.html", {"form": form, "title": title, "back": back, **extra}
    )


def add_error(form, exc):
    if isinstance(exc, UndefinedResult):
        for issue in exc.issues:
            form.add_error(None, issue)
    else:
        form.add_error(None, exc)


def assessment_for(request, pk):
    assessment = get_object_or_404(Assessment, pk=pk)
    require_participant(request.user, assessment)
    return assessment


@login_required
@require_safe
def dashboard(request):
    rows = Assessment.objects.filter(assigned_filter(request.user)).select_related(
        "contour", "catalog", "employee", "reviewer"
    )
    reports_only = request.GET.get("view") == "reports"
    if reports_only:
        rows = rows.filter(result_snapshot__isnull=False).select_related("result_snapshot")
    query = request.GET.get("q", "").strip()[:200]
    state = request.GET.get("state", "")
    if query:
        rows = rows.filter(title__icontains=query)
    if state in Assessment.State.values:
        rows = rows.filter(state=state)
    rows = list(rows.prefetch_related("contour_links"))
    states = Counter(row.state for row in rows)
    return render(
        request,
        "workspace/dashboard.html",
        {
            "assessments": rows,
            "reports_only": reports_only,
            "query": query,
            "selected_state": state,
            "state_options": Assessment.State.choices,
            "assessment_counts": {
                "total": len(rows),
                "working": states["draft"] + states["filling"] + states["rework"],
                "review": states["review"] + states["agreement"],
                "frozen": states["approved"] + states["closed"],
            },
            "is_admin": has_role(request.user, ADMIN),
            "is_reviewer": has_role(request.user, REVIEWER),
        },
    )


@login_required
@require_safe
def organization_card(request):
    profile = get_object_or_404(OrganizationProfile, pk=1)
    tab = request.GET.get("tab", "general")
    if tab not in {"general", "employees", "systems", "assessments"}:
        tab = "general"
    employees = profile.employees.all()
    query = request.GET.get("q", "").strip()[:100]
    if query:
        employees = employees.filter(
            Q(last_name__icontains=query)
            | Q(first_name__icontains=query)
            | Q(position__icontains=query)
            | Q(department__icontains=query)
        )
    return render(
        request,
        "workspace/organization.html",
        {
            "profile": profile,
            "active_tab": tab,
            "employees": employees,
            "contours": ProtectionContour.objects.filter(organization=profile).prefetch_related(
                "information_systems"
            ),
            "systems": profile.information_systems.select_related(
                "contour", "responsible_employee"
            ),
            "assessments": Assessment.objects.filter(
                assigned_filter(request.user), contour__organization=profile
            ).distinct(),
            "is_admin": has_role(request.user, ADMIN),
            "query": query,
        },
    )


@login_required
def employee_edit(request, pk=None):
    require_role(request.user, ADMIN)
    profile = get_object_or_404(OrganizationProfile, pk=1)
    employee = get_object_or_404(Employee, pk=pk, organization=profile) if pk else None
    form = EmployeeForm(request.POST or None, instance=employee)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.organization = profile
        item.save()
        return redirect("organization-card")
    return form_page(request, form, "Сотрудник", back="organization-card")


@login_required
@require_POST
def employee_archive(request, pk):
    require_role(request.user, ADMIN)
    employee = get_object_or_404(Employee, pk=pk, organization_id=1)
    employee.archived = True
    employee.save(update_fields=["archived"])
    return redirect("organization-card")


@login_required
@require_POST
def system_archive(request, pk):
    require_role(request.user, ADMIN)
    system = get_object_or_404(InformationSystem, pk=pk, organization_id=1)
    system.archived = True
    system.save(update_fields=["archived"])
    return redirect("organization-card")


@login_required
def system_edit(request, pk=None):
    require_role(request.user, ADMIN)
    profile = get_object_or_404(OrganizationProfile, pk=1)
    system = get_object_or_404(InformationSystem, pk=pk, organization=profile) if pk else None
    form = InformationSystemForm(request.POST or None, instance=system)
    form.fields["contour"].queryset = ProtectionContour.objects.filter(organization=profile)
    form.fields["responsible_employee"].queryset = Employee.objects.filter(
        organization=profile, archived=False
    )
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.organization = profile
        item.full_clean()
        item.save()
        return redirect("organization-card")
    return form_page(request, form, "Информационная система", back="organization-card")


@login_required
def create(request):
    require_role(request.user, ADMIN)
    previous = None
    initial = {}
    if request.GET.get("previous"):
        from django.http import Http404

        try:
            previous_id = int(request.GET["previous"])
        except (ValueError, TypeError):
            raise Http404("Некорректное предыдущее обследование") from None
        previous = get_object_or_404(Assessment, pk=previous_id)
        require_role(request.user, ADMIN)
        initial["contours"] = list(previous.contours.values_list("pk", flat=True)) or [
            previous.contour_id
        ]
    mode = request.POST.get("mode", request.GET.get("mode", "manual"))
    form = AssessmentForm(request.POST or None, mode=mode, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_assessment(request.user, previous=previous, **form.cleaned_data)
            messages.success(request, "Обследование создано и назначено участникам.")
            return redirect("workspace")
        except ValidationError as exc:
            add_error(form, exc)
    return render(
        request,
        "workspace/assessment_create.html",
        {"form": form, "previous": previous},
    )


@login_required
@require_safe
def detail(request, pk):
    assessment = assessment_for(request, pk)
    from .matrix import matrix_context

    matrix = matrix_context(assessment, request.GET)
    snapshot = ResultSnapshot.objects.filter(assessment=assessment).first()
    if snapshot and services.digest(snapshot.data) != snapshot.digest:
        return HttpResponse("Нарушена целостность снимка", status=409)
    run = assessment.calculations.order_by("-pk").first()
    return render(
        request,
        "workspace/detail.html",
        {
            "assessment": assessment,
            **matrix,
            "files": assessment.evidence_files.filter(retired=False),
            "findings": assessment.findings.all(),
            "snapshot": snapshot,
            "run": run,
            "is_reviewer": assessment.reviewer_id == request.user.pk,
            "is_employee": assessment.employee_id == request.user.pk,
            "is_admin": has_role(request.user, ADMIN),
            "adjustments": ScoreAdjustment.objects.filter(
                revision__item__assessment=assessment
            ).select_related("revision__item", "proposed_by", "decided_by"),
            "events": AuditEvent.objects.filter(
                Q(entity="assessments.assessment", entity_id=str(assessment.pk))
                | Q(
                    entity="assessments.answerrevision",
                    entity_id__in=[
                        str(value)
                        for value in AnswerRevision.objects.filter(
                            item__assessment=assessment
                        ).values_list("pk", flat=True)
                    ],
                )
            ).select_related("actor")[:200],
            "working": assessment.state in services.WORKING,
        },
    )


@login_required
def answer(request, pk):
    item = get_object_or_404(AssessmentItem.objects.select_related("assessment"), pk=pk)
    assessment = item.assessment
    require_participant(request.user, assessment)
    if request.user.pk != assessment.employee_id:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
    services.require_state(assessment, services.WORKING)
    rev = item.current_revision
    initial = (
        {
            "text": rev.text,
            "score": rev.employee_score,
            "sources": rev.sources,
            "exclusion_reason": rev.exclusion_reason,
            "compensation": rev.compensation,
            "files": list(rev.evidence.values_list("pk", flat=True)),
        }
        if rev
        else {}
    )
    form = AnswerForm(request.POST or None, assessment=assessment, item=item, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.save_answer(request.user, item.pk, **form.cleaned_data)
            url = reverse("assessment", args=[assessment.pk])
            query = QueryDict(request.POST.get("return_query", "")).urlencode()
            return redirect(f"{url}?{query}#measure-{item.pk}")
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(
        request,
        form,
        f"{item.snapshot['code']} — процесс {item.context_process}, "
        f"уровень {item.snapshot.get('level', '—')}",
        context_text=item.snapshot["text"],
        assessment=assessment,
    )


@login_required
def adjustment_decide(request, pk):
    adjustment = get_object_or_404(
        ScoreAdjustment.objects.select_related("revision__item__assessment"), pk=pk
    )
    assessment = adjustment.revision.item.assessment
    require_participant(request.user, assessment)
    if request.user.pk != assessment.employee_id:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
    form = AdjustmentDecisionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.decide_adjustment(request.user, adjustment.pk, **form.cleaned_data)
            return redirect("assessment", pk=assessment.pk)
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(
        request,
        form,
        f"Согласование корректировки {adjustment.revision.item.snapshot['code']} "
        f"— уровень {adjustment.revision.item.snapshot.get('level', '—')}",
        assessment=assessment,
    )


@login_required
@require_safe
def answer_history(request, pk):
    item = get_object_or_404(AssessmentItem.objects.select_related("assessment"), pk=pk)
    require_participant(request.user, item.assessment)
    return render(
        request,
        "workspace/history.html",
        {
            "item": item,
            "revisions": item.revisions.order_by("-number").prefetch_related(
                "evidence", "decisions"
            ),
        },
    )


@login_required
def review(request, pk):
    revision = get_object_or_404(AnswerRevision.objects.select_related("item__assessment"), pk=pk)
    require_reviewer(request.user, revision.item.assessment)
    form = ReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.review_answer(request.user, revision.pk, **form.cleaned_data)
            return redirect("assessment", pk=revision.item.assessment_id)
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(
        request,
        form,
        f"Решение: {revision.item.snapshot['code']}, версия {revision.number}, "
        f"уровень {revision.item.snapshot.get('level', '—')}",
        context_text=revision.text,
        revision=revision,
        assessment=revision.item.assessment,
    )


@login_required
@require_POST
def action(request, pk, operation):
    assessment = assessment_for(request, pk)
    try:
        if operation == "begin":
            services.begin_assessment(request.user, pk)
        elif operation == "submit":
            services.submit_assessment(request.user, pk)
        elif operation == "calculate":
            run = services.run_calculation(request.user, pk)
            return redirect("calculation", pk=run.pk)
        elif operation == "approve":
            services.approve_assessment(request.user, pk)
        elif operation == "close":
            services.close_assessment(request.user, pk)
        else:
            return HttpResponse(status=404)
    except (ValidationError, UndefinedResult) as exc:
        return render(
            request,
            "workspace/error.html",
            {
                "assessment": assessment,
                "issues": exc.issues if isinstance(exc, UndefinedResult) else exc.messages,
            },
            status=400,
        )
    return redirect("assessment", pk=pk)


@login_required
def rework(request, pk):
    assessment = assessment_for(request, pk)
    require_reviewer(request.user, assessment)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.return_for_rework(request.user, pk, form.cleaned_data["reason"])
            return redirect("assessment", pk=pk)
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(request, form, "Вернуть всю анкету на доработку", assessment=assessment)


@login_required
def upload(request, pk):
    assessment = assessment_for(request, pk)
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            upload_evidence(
                request.user, pk, form.cleaned_data["file"], form.cleaned_data["description"]
            )
            return redirect("assessment", pk=pk)
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(request, form, "Добавить приватное свидетельство", assessment=assessment)


@login_required
@require_safe
def download(request, pk):
    evidence = get_object_or_404(Evidence.objects.select_related("assessment"), pk=pk)
    try:
        stream = open_evidence(request.user, evidence)
    except (ValidationError, OSError):
        messages.error(request, "Файл недоступен или не прошёл контроль целостности")
        return redirect("assessment", pk=evidence.assessment_id)
    response = FileResponse(
        stream,
        as_attachment=True,
        filename=evidence.original_name,
        content_type="application/octet-stream",
    )
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def retire(request, pk):
    evidence = get_object_or_404(Evidence, pk=pk)
    try:
        retire_evidence(request.user, pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("assessment", pk=evidence.assessment_id)


@login_required
def finding_edit(request, pk, finding_id=None):
    assessment = assessment_for(request, pk)
    finding = (
        get_object_or_404(Finding, pk=finding_id, assessment=assessment) if finding_id else None
    )
    current = finding.current_revision if finding else None
    form = FindingForm(
        request.POST or None,
        assessment=assessment,
        instance=current if request.method != "POST" else None,
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.save_finding(request.user, pk, finding_id=finding_id, **form.cleaned_data)
            return redirect("assessment", pk=pk)
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(request, form, "Новая версия факта нарушения", assessment=assessment)


@login_required
def finding_review(request, pk):
    finding = get_object_or_404(Finding.objects.select_related("assessment"), pk=pk)
    require_reviewer(request.user, finding.assessment)
    revision = finding.current_revision
    form = FindingReviewForm(request.POST or None, finding=finding)
    if request.method == "POST" and form.is_valid():
        try:
            services.review_finding(request.user, revision.pk, **form.cleaned_data)
            return redirect("assessment", pk=finding.assessment_id)
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(
        request,
        form,
        f"Рассмотрение факта №{finding.pk}",
        context_text=revision.description,
        assessment=finding.assessment,
    )


@login_required
@require_safe
def calculation(request, pk):
    run = get_object_or_404(CalculationRun.objects.select_related("assessment"), pk=pk)
    require_participant(request.user, run.assessment)
    return render(
        request,
        "workspace/calculation.html",
        {
            "run": run,
            "result": run.result,
            "inputs_json": json.dumps(run.inputs, ensure_ascii=False, indent=2),
        },
    )


@login_required
@require_safe
def snapshot(request, pk):
    snapshot = get_object_or_404(
        ResultSnapshot.objects.select_related("assessment"), assessment_id=pk
    )
    require_participant(request.user, snapshot.assessment)
    if services.digest(snapshot.data) != snapshot.digest:
        return HttpResponse("Нарушена целостность снимка", status=409)
    return render(request, "workspace/snapshot.html", {"snapshot": snapshot, "data": snapshot.data})


@login_required
@require_POST
def export(request, pk, format):
    snapshot = get_object_or_404(
        ResultSnapshot.objects.select_related("assessment"), assessment_id=pk
    )
    require_participant(request.user, snapshot.assessment)
    if request.user.pk not in {
        snapshot.assessment.employee_id,
        snapshot.assessment.reviewer_id,
    }:
        raise PermissionDenied("Отчёт доступен только назначенным участникам")
    if services.digest(snapshot.data) != snapshot.digest:
        return HttpResponse("Нарушена целостность снимка", status=409)
    if format == "json":
        body = json.dumps(snapshot.data, ensure_ascii=False, sort_keys=True, indent=2).encode()
        content_type = "application/json"
    elif format == "docx":
        from reports.export import build_docx

        body = build_docx(snapshot.data)
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif format == "xlsx":
        from reports.export import build_xlsx

        body = build_xlsx(snapshot.data)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        return HttpResponse(status=404)
    import hashlib

    ReportExport.objects.create(
        snapshot=snapshot,
        actor=request.user,
        format=format,
        sha256=hashlib.sha256(body).hexdigest(),
    )
    response = HttpResponse(body, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="internal-assessment-{pk}.{format}"'
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_safe
def compare(request, pk):
    assessment = assessment_for(request, pk)
    access = Q(pk__in=[])
    from accounts.access import EMPLOYEE

    if has_role(request.user, EMPLOYEE):
        access |= Q(assessment__employee=request.user)
    if has_role(request.user, REVIEWER):
        access |= Q(assessment__reviewer=request.user)
    rows = (
        ResultSnapshot.objects.filter(assessment__contour=assessment.contour)
        .filter(access)
        .select_related("assessment")
        .order_by("created_at")
    )
    return render(request, "workspace/compare.html", {"assessment": assessment, "rows": rows})


@login_required
@require_safe
def settings_home(request):
    require_role(request.user, ADMIN)
    return render(
        request,
        "workspace/settings.html",
        {
            "organization": OrganizationProfile.objects.first(),
            "contours": ProtectionContour.objects.all(),
            "users": get_user_model().objects.prefetch_related("groups").order_by("username"),
            "drafts": Assessment.objects.filter(state="draft").select_related(
                "contour", "employee", "reviewer"
            ),
            "catalogs": CatalogVersion.objects.filter(synthetic=False),
        },
    )


@login_required
def organization_edit(request):
    require_role(request.user, ADMIN)
    profile = OrganizationProfile.objects.first()
    form = OrganizationForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        return redirect("settings-home")
    return form_page(request, form, "Профиль организации", back="organization-card")


@login_required
def contour_edit(request):
    require_role(request.user, ADMIN)
    profile = OrganizationProfile.objects.first()
    if profile is None:
        messages.error(request, "Сначала заполните профиль организации")
        return redirect("settings-home")
    form = ContourForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.organization = profile
            obj.save()
        return redirect("settings-home")
    return form_page(request, form, "Добавить контур", back="settings-home")


@login_required
def assign(request, pk):
    require_role(request.user, ADMIN)
    assessment = get_object_or_404(Assessment, pk=pk)
    form = AssignmentForm(
        request.POST or None,
        initial={"employee": assessment.employee_id, "reviewer": assessment.reviewer_id},
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.assign_participants(request.user, pk, **form.cleaned_data)
            return redirect("settings-home")
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(request, form, f"Назначения обследования №{pk}", back="settings-home")


@login_required
def user_create(request):
    require_role(request.user, ADMIN)
    form = CreateUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            user.groups.set(Group.objects.filter(name__in=form.cleaned_data["roles"]))
        return redirect("settings-home")
    return form_page(request, form, "Создать пользователя", back="settings-home")


@login_required
def user_roles(request):
    require_role(request.user, ADMIN)
    form = RolesForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.cleaned_data["user"]
        if user.pk == request.user.pk:
            form.add_error(None, "Собственные роли и активность изменяет другой администратор")
        elif user.is_superuser:
            form.add_error(None, "Техническую суперучётную запись здесь изменять нельзя")
        else:
            with transaction.atomic():
                user.groups.set(Group.objects.filter(name__in=form.cleaned_data["roles"]))
                user.is_active = form.cleaned_data["is_active"]
                user.save(update_fields=["is_active"])
            return redirect("settings-home")
    return form_page(request, form, "Назначить роли / отключить доступ", back="settings-home")


@login_required
@require_POST
def user_deactivate(request, pk):
    require_role(request.user, ADMIN)
    user = get_object_or_404(get_user_model(), pk=pk)
    if user.pk == request.user.pk or user.is_superuser:
        messages.error(request, "Нельзя отключить текущего или технического администратора")
    else:
        user.is_active = False
        user.save(update_fields=["is_active"])
        messages.success(request, "Доступ пользователя отключён; история обследований сохранена.")
    return redirect("settings-home")


@login_required
def catalog_import(request):
    require_role(request.user, ADMIN)
    form = CatalogImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            upload = form.cleaned_data["file"]
            if upload.size > 2 * 1024 * 1024:
                raise ValidationError("Каталог больше 2 МиБ")
            payload = json.loads(upload.read().decode("utf-8"))
            catalog = import_catalog(request.user, payload)
            return redirect("catalog", pk=catalog.pk)
        except (ValueError, UnicodeError, ValidationError) as exc:
            form.add_error(None, str(exc))
    return form_page(request, form, "Импорт новой версии каталога", back="settings-home")


@login_required
@require_safe
def catalog_detail(request, pk):
    require_role(request.user, ADMIN)
    catalog = get_object_or_404(CatalogVersion, pk=pk)
    if catalog.synthetic:
        messages.info(
            request,
            "Учебный каталог скрыт из рабочего справочника. История обследований сохранена.",
        )
        return redirect("settings-home")
    query = request.GET.get("q", "").strip()[:200]
    requirements = catalog.requirements.prefetch_related("implementations")
    if query:
        requirements = requirements.filter(Q(code__icontains=query) | Q(text__icontains=query))
    page = Paginator(requirements, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "workspace/catalog.html",
        {
            "catalog": catalog,
            "requirements": page,
            "page_obj": page,
            "query": query,
        },
    )


@login_required
def requirement_verify(request, pk):
    require_role(request.user, ADMIN)
    req = get_object_or_404(RequirementVersion, pk=pk)
    form = NoteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            verify_requirement(request.user, pk, form.cleaned_data["note"])
            return redirect("catalog", pk=req.catalog_id)
        except ValidationError as exc:
            add_error(form, exc)
    return form_page(
        request,
        form,
        f"Ручная сверка {req.code}",
        context_text=f"{req.text}\nИсточник: {req.citation}",
    )


@login_required
@require_POST
def catalog_verify(request, pk):
    require_role(request.user, ADMIN)
    try:
        verify_catalog(request.user, pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("catalog", pk=pk)


@login_required
@require_safe
def methodology(request):
    return redirect("workspace")
