import hashlib
import json
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.access import (
    ADMIN,
    EMPLOYEE,
    REVIEWER,
    has_role,
    require_participant,
    require_reviewer,
    require_role,
)
from auditlog.services import record
from calculations.engine import MeasureInput, UndefinedResult, calculate_areas
from calculations.models import CalculationRun
from reports.models import ResultSnapshot
from standards.models import ImplementationRequirement

from .models import (
    AnswerEvidence,
    AnswerRevision,
    Assessment,
    AssessmentContour,
    AssessmentItem,
    CalculationArea,
    Finding,
    FindingDecision,
    FindingRevision,
    ReviewDecision,
    ScoreAdjustment,
    Submission,
)

WORKING = {Assessment.State.FILLING, Assessment.State.REWORK}
FROZEN = {Assessment.State.APPROVED, Assessment.State.CLOSED}


def digest(data):
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def require_state(assessment, states):
    if assessment.state not in states:
        raise ValidationError("Операция недопустима в текущем состоянии обследования")


def evidence_queryset(actor, assessment):
    require_participant(actor, assessment)
    return assessment.evidence_files.filter(retired=False)


def check_evidence(assessment, files):
    if any(e.assessment_id != assessment.pk or e.retired for e in files):
        raise ValidationError(
            "Свидетельства должны принадлежать этому обследованию и быть действующими"
        )


@transaction.atomic
def create_assessment(actor, **data):
    require_role(actor, ADMIN)
    contours = list(data.pop("contours", ()))
    systems = list(data.pop("systems", ()))
    if not contours and data.get("contour"):
        contours = [data.pop("contour")]
    if not contours:
        raise ValidationError("Выберите хотя бы один контур")
    contour = contours[0]
    if not data["processes"] or data["period_start"] > data["period_end"]:
        raise ValidationError("Проверьте процессы и период")
    previous = data.get("previous")
    if previous and (
        not set(previous.contours.values_list("pk", flat=True)).intersection(c.pk for c in contours)
        or not participant_for_previous(actor, previous)
    ):
        raise ValidationError(
            "Предыдущее обследование должно быть доступно и относиться к тому же контуру"
        )
    employee = data.get("employee")
    reviewer = data.get("reviewer")
    if not employee or not reviewer or employee.pk == reviewer.pk:
        raise ValidationError("Назначьте разных специалиста по ИБ и проверяющего")
    if not has_role(employee, EMPLOYEE) or not has_role(reviewer, REVIEWER):
        raise ValidationError("Назначенные пользователи не имеют требуемых ролей")
    if any(system.contour_id not in {c.pk for c in contours} for system in systems):
        raise ValidationError("Все системы должны относиться к выбранным контурам")
    assessment = Assessment.objects.create(
        created_by=actor, contour=contour, level=contour.level, **data
    )
    AssessmentContour.objects.bulk_create(
        [
            AssessmentContour(
                assessment=assessment, contour=c, level_snapshot=c.level, name_snapshot=c.name
            )
            for c in contours
        ]
    )
    assessment.systems.set(systems)
    for level in sorted({c.level for c in contours}):
        area = CalculationArea.objects.create(
            assessment=assessment, level=level, title=f"Контуры уровня {level}"
        )
        area.contours.set([c for c in contours if c.level == level])
    record(actor, "assessment.created", assessment, new={"state": assessment.state})
    return assessment


def participant_for_previous(actor, assessment):
    return has_role(actor, ADMIN) and assessment.state in FROZEN


@transaction.atomic
def assign_participants(actor, assessment_id, employee, reviewer):
    require_role(actor, ADMIN)
    assessment = Assessment.objects.select_for_update(of=("self",)).get(pk=assessment_id)
    require_state(assessment, {Assessment.State.DRAFT})
    if (
        employee.pk == reviewer.pk
        or not has_role(employee, EMPLOYEE)
        or not has_role(reviewer, REVIEWER)
    ):
        raise ValidationError(
            "Нужны разные активные сотрудник и проверяющий с соответствующими ролями"
        )
    assessment.employee = employee
    assessment.reviewer = reviewer
    assessment.save(update_fields=["employee", "reviewer"])
    record(
        actor,
        "assessment.assigned",
        assessment,
        new={"employee": employee.pk, "reviewer": reviewer.pk},
    )
    return assessment


@transaction.atomic
def begin_assessment(actor, assessment_id):
    return _begin_assessment(actor, assessment_id)


@transaction.atomic
def replace_synthetic_questionnaire(actor, assessment_id, catalog):
    """Новая анкета из источника: старые ответы не переносятся на другие меры."""
    require_role(actor, ADMIN)
    source = Assessment.objects.select_for_update().get(pk=assessment_id)
    if not source.catalog.synthetic:
        raise ValidationError("Переход предназначен только для старой синтетической анкеты")
    catalog = type(catalog).objects.select_for_update().get(pk=catalog.pk)
    if catalog.synthetic or not catalog.requirements.exists():
        raise ValidationError("Выберите непустой нормативный каталог, не SYN")
    if catalog.requirements.filter(code__startswith="SYN-").exists():
        raise ValidationError("В выбранном каталоге остались синтетические меры")
    assessment = create_assessment(
        actor,
        title=f"{source.title[:220]} — ГОСТ",
        contours=list(source.contours.all()),
        systems=list(source.systems.all()),
        employee=source.employee,
        reviewer=source.reviewer,
        catalog=catalog,
        period_start=source.period_start,
        period_end=source.period_end,
        processes=source.processes,
        scope=source.scope,
        lifecycle_included=source.lifecycle_included,
        lifecycle_reason=source.lifecycle_reason,
    )
    # Предварительное заполнение допускается явно, но статус сверки не меняется.
    # collect_inputs и расчётное ядро отклоняют неверифицированные каталог/снимки.
    _begin_assessment(actor, assessment.pk, allow_unverified=True)
    record(
        actor,
        "assessment.catalog_transition",
        assessment,
        new={
            "source_assessment": source.pk,
            "catalog": catalog.pk,
            "catalog_verified": catalog.verified,
        },
    )
    assessment.refresh_from_db()
    return assessment


def _begin_assessment(actor, assessment_id, *, allow_unverified=False):
    assessment = (
        Assessment.objects.select_for_update(of=("self",))
        .select_related("catalog", "contour__organization", "employee", "reviewer")
        .get(pk=assessment_id)
    )
    require_role(actor, ADMIN)
    require_state(assessment, {Assessment.State.DRAFT})
    if (
        not assessment.employee_id
        or not has_role(assessment.employee, EMPLOYEE)
        or not assessment.reviewer_id
    ):
        raise ValidationError("Не назначен отдельный активный сотрудник")
    if not assessment.catalog.verified and not allow_unverified:
        raise ValidationError("Каталог не прошёл верификацию")
    # Блокировка строки каталога сериализует старт с импортом/верификацией.
    type(assessment.catalog).objects.select_for_update().get(pk=assessment.catalog_id)
    areas = {area.level: area for area in assessment.calculation_areas.all()}
    levels = set(assessment.contour_links.values_list("level_snapshot", flat=True))
    if not areas or set(areas) != levels:
        raise ValidationError("Расчётные области не соответствуют зафиксированным уровням контуров")
    for level, area in areas.items():
        contour_ids = set(
            assessment.contour_links.filter(level_snapshot=level).values_list(
                "contour_id", flat=True
            )
        )
        if set(area.contours.values_list("pk", flat=True)) != contour_ids:
            raise ValidationError("Состав расчётной области не соответствует контурам обследования")
    implementations = list(
        ImplementationRequirement.objects.filter(
            requirement__catalog=assessment.catalog, level__in=levels
        )
        .select_related("requirement")
        .order_by("requirement_id", "level")
    )
    # Одна строка на меру/процесс/уровень, независимо от числа контуров и систем.
    items = []
    for implementation in implementations:
        req = implementation.requirement
        if req.kind == "selection" and req.process not in assessment.processes:
            continue
        if req.kind == "lifecycle" and not assessment.lifecycle_included:
            continue
        contexts = assessment.processes if req.kind == "management" else [req.process or 0]
        for process in contexts:
            snapshot = {
                "code": req.code,
                "text": req.text,
                "kind": req.kind,
                "process": process,
                "subprocess": req.subprocess or 0,
                "direction": req.direction or 0,
                "stage": req.stage,
                "citation": req.citation,
                "verified": req.verified,
                "mode": implementation.mode,
                "level": implementation.level,
                "area": areas[implementation.level].pk,
            }
            items.append(
                AssessmentItem(
                    assessment=assessment,
                    area=areas[implementation.level],
                    requirement=req,
                    context_process=process,
                    snapshot=snapshot,
                )
            )
    if not items or (not allow_unverified and any(not item.snapshot["verified"] for item in items)):
        raise ValidationError("В области нет мер либо есть неверифицированные записи")
    AssessmentItem.objects.bulk_create(items)
    assessment.organization_snapshot = {
        "name": assessment.contour.organization.name,
        "description": assessment.contour.organization.description,
        "synthetic": assessment.contour.organization.synthetic,
    }
    assessment.contour_snapshot = {
        "contours": list(
            assessment.contour_links.values("contour_id", "name_snapshot", "level_snapshot")
        ),
        "systems": list(assessment.systems.values("id", "name", "contour_id")),
        "areas": [
            {
                "id": area.pk,
                "level": level,
                "title": area.title,
                "contours": sorted(area.contours.values_list("pk", flat=True)),
            }
            for level, area in sorted(areas.items())
        ],
        "item_ids": [item.pk for item in items],
    }
    assessment.state = Assessment.State.FILLING
    assessment.save(update_fields=["state", "organization_snapshot", "contour_snapshot"])
    record(
        actor,
        "assessment.started",
        assessment,
        previous={"state": Assessment.State.DRAFT},
        new={"state": assessment.state},
    )
    return assessment


@transaction.atomic
def save_answer(
    actor,
    item_id,
    *,
    text,
    score=None,
    sources="",
    exclusion_reason="",
    compensation="",
    files=(),
    reference="",
):
    item = AssessmentItem.objects.select_related("assessment").get(pk=item_id)
    assessment = Assessment.objects.select_for_update(of=("self",)).get(pk=item.assessment_id)
    require_participant(actor, assessment)
    require_state(assessment, WORKING)
    if actor.pk != assessment.employee_id:
        raise PermissionDenied("Ответ заполняет назначенный сотрудник")
    if not text.strip():
        raise ValidationError("Укажите пояснение реализации")
    allowed = (
        {Decimal("0"), Decimal("1")}
        if item.snapshot.get("kind") == "selection"
        else {Decimal("0"), Decimal("0.5"), Decimal("1")}
    )
    if not isinstance(score, Decimal) or not score.is_finite() or score not in allowed:
        raise ValidationError("Выберите допустимую исходную оценку")
    files = list(files)
    check_evidence(assessment, files)
    current = item.current_revision
    revision = AnswerRevision.objects.create(
        item=item,
        number=(current.number + 1 if current else 1),
        author=actor,
        text=text.strip(),
        employee_score=score,
        sources=sources.strip(),
        exclusion_reason=exclusion_reason.strip(),
        compensation=compensation.strip(),
    )
    AnswerEvidence.objects.bulk_create(
        [AnswerEvidence(revision=revision, evidence=e, reference=reference[:250]) for e in files]
    )
    record(actor, "answer.revised", revision, new={"number": revision.number, "score": str(score)})
    return revision


@transaction.atomic
def submit_assessment(actor, assessment_id):
    assessment = Assessment.objects.select_for_update(of=("self",)).get(pk=assessment_id)
    require_participant(actor, assessment)
    require_state(assessment, WORKING)
    if assessment.employee_id != actor.pk:
        raise PermissionDenied
    revisions = []
    issues = []
    for item in assessment.items.all():
        revision = item.current_revision
        if (
            revision is None
            or not revision.text.strip()
            or (not revision.sources.strip() and not revision.evidence.exists())
        ):
            issues.append(
                f"{item.snapshot['code']}, процесс {item.context_process}: нет "
                f"ответа и/или источников свидетельств"
            )
        else:
            revisions.append(revision.pk)
    if issues or not revisions:
        raise ValidationError(issues or ["Анкета пуста"])
    submission = Submission.objects.create(
        assessment=assessment, author=actor, revisions=revisions, digest=digest(revisions)
    )
    assessment.state = Assessment.State.REVIEW
    assessment.save(update_fields=["state"])
    record(
        actor,
        "assessment.submitted",
        assessment,
        previous={"state": "working"},
        new={"state": assessment.state},
    )
    return submission


@transaction.atomic
def review_answer(actor, revision_id, *, outcome, score, reason):
    revision = AnswerRevision.objects.select_related("item__assessment").get(pk=revision_id)
    assessment = Assessment.objects.select_for_update(of=("self",)).get(
        pk=revision.item.assessment_id
    )
    require_reviewer(actor, assessment)
    require_state(assessment, {Assessment.State.REVIEW, Assessment.State.AGREEMENT})
    if revision.item.current_revision.pk != revision.pk:
        raise ValidationError("Нельзя проверить устаревшую версию")
    submission = assessment.submissions.order_by("-pk").first()
    if not submission or revision.pk not in submission.revisions:
        raise ValidationError("Версия не входит в последнюю отправку анкеты")
    if revision.author_id == actor.pk or revision.evidence.filter(author=actor).exists():
        raise PermissionDenied(
            "Нельзя утверждать собственные ответы и предоставленные к ним материалы"
        )
    if outcome not in {"accepted", "rework", "excluded"} or not reason.strip():
        raise ValidationError("Нужны решение и мотивировка")
    if outcome == "accepted":
        if revision.employee_score is None:
            raise ValidationError("Исходная оценка отсутствует: верните ответ на доработку")
        allowed = (
            {Decimal(0), Decimal(1)}
            if revision.item.snapshot["kind"] == "selection"
            else {Decimal(0), Decimal("0.5"), Decimal(1)}
        )
        if not isinstance(score, Decimal) or not score.is_finite() or score not in allowed:
            raise ValidationError("Недопустимая оценка для этого показателя")
    else:
        if score is not None:
            raise ValidationError("Для доработки/неоцениваемости числовая оценка не задаётся")
    if outcome == "excluded" and not revision.exclusion_reason.strip():
        raise ValidationError("Нет предложения об исключении и обоснования области по п. 6.11")
    decision = ReviewDecision.objects.create(
        revision=revision, reviewer=actor, outcome=outcome, score=score, reason=reason.strip()
    )
    ScoreAdjustment.objects.filter(
        revision=revision,
        status__in=[ScoreAdjustment.Status.PENDING, ScoreAdjustment.Status.REJECTED],
    ).update(status=ScoreAdjustment.Status.SUPERSEDED)
    if outcome == "accepted" and score != revision.employee_score:
        ScoreAdjustment.objects.create(
            revision=revision,
            proposed_by=actor,
            original_score=revision.employee_score,
            proposed_score=score,
            reason=reason.strip(),
        )
    record(
        actor,
        "answer.reviewed",
        decision,
        new={"outcome": outcome, "score": str(score) if score is not None else None},
    )
    return decision


@transaction.atomic
def decide_adjustment(actor, adjustment_id, *, decision, comment=""):
    adjustment = (
        ScoreAdjustment.objects.select_related("revision__item__assessment")
        .select_for_update(of=("self",))
        .get(pk=adjustment_id)
    )
    assessment = Assessment.objects.select_for_update(of=("self",)).get(
        pk=adjustment.revision.item.assessment_id
    )
    require_participant(actor, assessment)
    require_state(assessment, {Assessment.State.REVIEW, Assessment.State.AGREEMENT})
    if adjustment.revision.item.current_revision.pk != adjustment.revision_id:
        raise ValidationError("Нельзя согласовать корректировку устаревшей версии ответа")
    if actor.pk != assessment.employee_id:
        raise PermissionDenied("Корректировку согласует назначенный специалист")
    if adjustment.status != ScoreAdjustment.Status.PENDING:
        raise ValidationError("Корректировка уже рассмотрена")
    if decision not in {ScoreAdjustment.Status.ACCEPTED, ScoreAdjustment.Status.REJECTED}:
        raise ValidationError("Неизвестное решение")
    if decision == ScoreAdjustment.Status.REJECTED and not comment.strip():
        raise ValidationError("При отклонении укажите обоснование")
    adjustment.status = decision
    adjustment.decided_by = actor
    adjustment.decision_comment = comment.strip()
    adjustment.decided_at = timezone.now()
    adjustment.save(update_fields=["status", "decided_by", "decision_comment", "decided_at"])
    assessment.state = Assessment.State.AGREEMENT
    assessment.save(update_fields=["state"])
    record(actor, "adjustment.decided", adjustment, new={"status": decision})
    return adjustment


@transaction.atomic
def return_for_rework(actor, assessment_id, reason):
    assessment = Assessment.objects.select_for_update(of=("self",)).get(pk=assessment_id)
    require_reviewer(actor, assessment)
    require_state(assessment, {Assessment.State.REVIEW})
    if not reason.strip():
        raise ValidationError("Укажите причины возврата")
    assessment.state = Assessment.State.REWORK
    assessment.save(update_fields=["state"])
    record(
        actor,
        "assessment.rework",
        assessment,
        previous={"state": Assessment.State.REVIEW},
        new={"state": assessment.state},
    )


@transaction.atomic
def save_finding(actor, assessment_id, *, finding_id=None, evidence=(), **data):
    assessment = Assessment.objects.select_for_update(of=("self",)).get(pk=assessment_id)
    require_participant(actor, assessment)
    if actor.pk not in {assessment.employee_id, assessment.reviewer_id}:
        raise PermissionDenied("Нарушения регистрируют только назначенные участники")
    require_state(assessment, WORKING | {Assessment.State.REVIEW})
    if actor.pk == assessment.employee_id and assessment.state not in WORKING:
        raise PermissionDenied("Для уточнения сотрудником нужен возврат на доработку")
    if (
        not data.get("description", "").strip()
        or not data.get("basis", "").strip()
        or data.get("source") not in {"organization", "reviewer"}
    ):
        raise ValidationError("Опишите обстоятельства, источник обнаружения и основания")
    files = list(evidence)
    check_evidence(assessment, files)
    if finding_id:
        finding = Finding.objects.get(pk=finding_id, assessment=assessment)
        if actor.pk == assessment.employee_id and finding.created_by_id != actor.pk:
            raise PermissionDenied("Сотрудник дополняет предоставленные им сведения")
    else:
        finding = Finding.objects.create(assessment=assessment, created_by=actor)
    current = finding.current_revision
    revision = FindingRevision.objects.create(
        finding=finding, number=current.number + 1 if current else 1, author=actor, **data
    )
    revision.evidence.set(files)
    return revision


@transaction.atomic
def review_finding(actor, revision_id, *, include, reason, duplicate_of=None):
    revision = FindingRevision.objects.select_related("finding__assessment").get(pk=revision_id)
    assessment = Assessment.objects.select_for_update(of=("self",)).get(
        pk=revision.finding.assessment_id
    )
    require_reviewer(actor, assessment)
    require_state(assessment, {Assessment.State.REVIEW})
    if revision.finding.current_revision.pk != revision.pk or not reason.strip():
        raise ValidationError("Нужны актуальная версия и мотивировка")
    if duplicate_of:
        if duplicate_of.pk == revision.finding_id or duplicate_of.assessment_id != assessment.pk:
            raise ValidationError("Дубликат должен ссылаться на другой факт этого обследования")
        primary = duplicate_of.current_revision
        decision = primary.decision if primary else None
        if not decision or decision.duplicate_of_id or include:
            raise ValidationError(
                "Нужна основная рассмотренная карточка, без цепочки дубликатов и "
                "без повторного учёта"
            )
        # Нельзя сделать основной факт дубликатом, если от него уже зависят решения.
        if FindingDecision.objects.filter(duplicate_of=revision.finding).exists():
            raise ValidationError("У этой карточки уже есть дубликаты; цепочки запрещены")
    if include and revision.discovered_on > assessment.period_end:
        raise ValidationError("Факт обнаружен после окончания периода обследования")
    if include and (
        not revision.potential_harm
        or (
            revision.source == "organization"
            and revision.investigation_completed
            and revision.response_documented
        )
    ):
        raise ValidationError("Условия учёта по п. 7.10 не подтверждены либо действует исключение")
    decision = FindingDecision.objects.create(
        revision=revision,
        reviewer=actor,
        include=include,
        duplicate_of=duplicate_of,
        reason=reason.strip(),
    )
    return decision


def collect_inputs(assessment):
    issues = []
    areas = {area.pk: area for area in assessment.calculation_areas.all()}
    expected_levels = sorted(set(assessment.contour_links.values_list("level_snapshot", flat=True)))
    if not areas or {area.level for area in areas.values()} != set(expected_levels):
        issues.append("Расчётные области не соответствуют зафиксированным уровням")
    measures = {level: [] for level in expected_levels}
    manifest = assessment.contour_snapshot.get("item_ids")
    if manifest is not None and set(manifest) != set(assessment.items.values_list("pk", flat=True)):
        issues.append("Состав анкеты отличается от зафиксированного набора мер")
    frozen_areas = assessment.contour_snapshot.get("areas")
    if frozen_areas is not None and {row["id"]: row["level"] for row in frozen_areas} != {
        pk: area.level for pk, area in areas.items()
    }:
        issues.append("Расчётные области отличаются от зафиксированных при запуске")
    answers = []
    current_ids = []
    if not assessment.items.exists():
        issues.append("Нет зафиксированной анкеты")
    for item in assessment.items.select_related("requirement__catalog"):
        area = areas.get(item.area_id)
        if area is None or item.snapshot.get("level") != area.level:
            issues.append(
                f"{item.snapshot.get('code', item.pk)}: расчётная область не определена "
                "или не соответствует уровню меры. Для старой объединённой анкеты "
                "создайте новое обследование; ответы автоматически не переносятся."
            )
            continue
        revision = item.current_revision
        decision = revision.decision if revision else None
        if not revision or not decision or decision.outcome == "rework":
            issues.append(
                f"{item.snapshot['code']} / процесс {item.context_process}: нет "
                f"принятого решения по текущей версии"
            )
            continue
        if (
            decision.reviewer_id != assessment.reviewer_id
            or decision.reviewer_id == revision.author_id
        ):
            issues.append(f"{item.snapshot['code']}: некорректный проверяющий")
        current_ids.append(revision.pk)
        s = item.snapshot
        measures.setdefault(area.level, []).append(
            MeasureInput(
                str(item.pk),
                s["kind"],
                s["process"],
                s["subprocess"],
                s["direction"],
                decision.score,
                decision.outcome == "excluded",
                s["verified"],
                s["mode"],
            )
        )
        links = [
            {
                "id": e.pk,
                "name": e.original_name,
                "sha256": e.sha256,
                "size": e.size,
                "description": e.description,
                "author": e.author_id,
                "created_at": e.created_at.isoformat(),
                "reference": revision.answerevidence_set.get(evidence=e).reference,
            }
            for e in revision.evidence.all()
        ]
        answers.append(
            {
                "item": item.pk,
                "area": area.pk,
                "level": area.level,
                "measure": s,
                "revision": revision.pk,
                "number": revision.number,
                "author": revision.author_id,
                "created_at": revision.created_at.isoformat(),
                "reviewer": decision.reviewer_id,
                "decided_at": decision.created_at.isoformat(),
                "text": revision.text,
                "sources": revision.sources,
                "exclusion": revision.exclusion_reason,
                "compensation": revision.compensation,
                "decision": decision.pk,
                "outcome": decision.outcome,
                "score": str(decision.score) if decision.score is not None else None,
                "reason": decision.reason,
                "evidence": links,
            }
        )
    latest = assessment.submissions.order_by("-pk").first()
    if not latest or set(latest.revisions) != set(current_ids):
        issues.append("Последняя отправка не совпадает с набором рассмотренных версий")
    facts = []
    z = 0
    for finding in assessment.findings.all():
        rev = finding.current_revision
        decision = rev.decision if rev else None
        if not decision:
            issues.append(f"Факт {finding.pk}: рассмотрение не завершено")
            continue
        if decision.duplicate_of_id:
            primary = decision.duplicate_of.current_revision
            pd = primary.decision if primary else None
            if not pd or pd.duplicate_of_id:
                issues.append(f"Факт {finding.pk}: основная карточка дубликата не рассмотрена")
        elif decision.include:
            z += 1
        facts.append(
            {
                "id": finding.pk,
                "revision": rev.pk,
                "author": rev.author_id,
                "created_at": rev.created_at.isoformat(),
                "reviewer": decision.reviewer_id,
                "decided_at": decision.created_at.isoformat(),
                "description": rev.description,
                "source": rev.source,
                "discovered_on": str(rev.discovered_on),
                "potential_harm": rev.potential_harm,
                "investigation_completed": rev.investigation_completed,
                "response_documented": rev.response_documented,
                "basis": rev.basis,
                "decision": decision.pk,
                "reason": decision.reason,
                "include": decision.include,
                "duplicate_of": decision.duplicate_of_id,
                "evidence": [
                    {"id": e.pk, "name": e.original_name, "sha256": e.sha256}
                    for e in rev.evidence.all()
                ],
            }
        )
    if issues:
        raise UndefinedResult(issues)
    inputs = {
        "assessment": assessment.pk,
        "catalog": assessment.catalog_id,
        "catalog_title": assessment.catalog.title,
        "catalog_verified": assessment.catalog.verified,
        "synthetic": assessment.catalog.synthetic
        or assessment.organization_snapshot.get("synthetic", False),
        "level": assessment.level,
        "levels": expected_levels,
        "areas": assessment.contour_snapshot.get("areas")
        or [
            {
                "id": area.pk,
                "level": area.level,
                "title": area.title,
                "contours": sorted(area.contours.values_list("pk", flat=True)),
            }
            for area in areas.values()
        ],
        "processes": assessment.processes,
        "lifecycle_included": assessment.lifecycle_included,
        "lifecycle_reason": assessment.lifecycle_reason,
        "answers": answers,
        "findings": facts,
        "z": z,
    }
    if not assessment.catalog.verified:
        raise UndefinedResult(["Каталог не верифицирован"])
    return inputs, measures


@transaction.atomic
def run_calculation(actor, assessment_id):
    assessment = (
        Assessment.objects.select_for_update(of=("self",))
        .select_related("catalog")
        .get(pk=assessment_id)
    )
    require_reviewer(actor, assessment)
    require_state(assessment, {Assessment.State.REVIEW, Assessment.State.AGREEMENT})
    unresolved = ScoreAdjustment.objects.filter(
        revision__item__assessment=assessment,
        status__in=[ScoreAdjustment.Status.PENDING, ScoreAdjustment.Status.REJECTED],
    )
    if unresolved.exists():
        raise UndefinedResult(["Есть несогласованные корректировки оценок"])
    inputs, measures = collect_inputs(assessment)
    result = calculate_areas(
        measures, inputs["levels"], assessment.processes, assessment.lifecycle_included, inputs["z"]
    )
    run = CalculationRun.objects.create(
        assessment=assessment,
        actor=actor,
        inputs=inputs,
        result=result,
        algorithm_version=result["algorithm"],
        digest=digest({"inputs": inputs, "result": result}),
    )
    record(actor, "calculation.created", run, new={"algorithm": run.algorithm_version})
    return run


@transaction.atomic
def approve_assessment(actor, assessment_id):
    assessment = (
        Assessment.objects.select_for_update(of=("self",))
        .select_related("catalog")
        .get(pk=assessment_id)
    )
    require_reviewer(actor, assessment)
    require_state(assessment, {Assessment.State.REVIEW, Assessment.State.AGREEMENT})
    if ScoreAdjustment.objects.filter(
        revision__item__assessment=assessment,
        status__in=[ScoreAdjustment.Status.PENDING, ScoreAdjustment.Status.REJECTED],
    ).exists():
        raise ValidationError("Нельзя утвердить обследование с несогласованными корректировками")
    # Повторный расчёт под той же блокировкой: устаревший результат не утверждается.
    run = run_calculation(actor, assessment.pk)
    if run.result.get("status") == "undefined":
        raise UndefinedResult(run.result["issues"])
    data = {
        "label": "Внутренняя оценка / проект отчёта",
        "synthetic": run.inputs["synthetic"],
        "title": assessment.title,
        "organization": assessment.organization_snapshot,
        "contour": assessment.contour_snapshot,
        "contours": list(
            assessment.contour_links.values("contour_id", "name_snapshot", "level_snapshot")
        ),
        "systems": assessment.contour_snapshot.get("systems", []),
        "scope": assessment.scope,
        "period": [str(assessment.period_start), str(assessment.period_end)],
        "reviewer": {"id": actor.pk, "name": actor.get_full_name() or actor.username},
        "approved_at": timezone.now().isoformat(),
        "inputs": run.inputs,
        "result": run.result,
        "calculation_digest": run.digest,
        "recommendations": [
            {
                "title": row.title,
                "description": row.description,
                "status": row.status,
                "due_date": row.due_date.isoformat() if row.due_date else None,
            }
            for row in assessment.recommendations.all()
        ],
        "adjustments": [
            {
                "revision_id": adjustment.revision_id,
                "original_score": str(adjustment.original_score),
                "proposed_score": str(adjustment.proposed_score),
                "reason": adjustment.reason,
                "status": adjustment.status,
            }
            for adjustment in ScoreAdjustment.objects.filter(revision__item__assessment=assessment)
        ],
    }
    snapshot = ResultSnapshot.objects.create(
        assessment=assessment, calculation=run, data=data, digest=digest(data), approved_by=actor
    )
    assessment.state = Assessment.State.APPROVED
    assessment.save(update_fields=["state"])
    record(
        actor,
        "assessment.approved",
        assessment,
        previous={"state": "review"},
        new={"state": assessment.state},
    )
    return snapshot


@transaction.atomic
def close_assessment(actor, assessment_id):
    assessment = Assessment.objects.select_for_update(of=("self",)).get(pk=assessment_id)
    require_reviewer(actor, assessment)
    require_state(assessment, {Assessment.State.APPROVED})
    if not hasattr(assessment, "result_snapshot"):
        raise ValidationError("Нет зафиксированного результата")
    assessment.state = Assessment.State.CLOSED
    assessment.save(update_fields=["state"])
    record(
        actor,
        "assessment.closed",
        assessment,
        previous={"state": Assessment.State.APPROVED},
        new={"state": assessment.state},
    )
