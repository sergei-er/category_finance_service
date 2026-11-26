import hashlib

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from accounts.access import require_participant
from assessments.models import Assessment
from assessments.services import WORKING, require_state
from auditlog.services import record

from .models import Evidence
from .validation import inspect_upload


@transaction.atomic
def upload_evidence(actor, assessment_id, upload, description):
    assessment = Assessment.objects.select_for_update(of=("self",)).get(pk=assessment_id)
    require_participant(actor, assessment)
    if actor.pk not in {assessment.employee_id, assessment.reviewer_id}:
        raise PermissionDenied("Свидетельства добавляют только назначенные участники")
    if actor.pk == assessment.employee_id:
        require_state(assessment, WORKING)
    else:
        require_state(assessment, WORKING | {Assessment.State.REVIEW})
    if not description.strip():
        raise ValidationError("Укажите, что подтверждает документ и где это находится")
    name, mime, checksum = inspect_upload(upload)
    item = Evidence(
        assessment=assessment,
        original_name=name,
        content_type=mime,
        size=upload.size,
        sha256=checksum,
        author=actor,
        description=description.strip(),
    )
    item.file.save(name, upload, save=False)
    try:
        item.save()
    except Exception:
        item.file.delete(save=False)
        raise
    record(actor, "evidence.added", item, new={"name": item.original_name, "size": item.size})
    return item


@transaction.atomic
def retire_evidence(actor, evidence_id):
    evidence = Evidence.objects.select_related("assessment").get(pk=evidence_id)
    assessment = Assessment.objects.select_for_update(of=("self",)).get(pk=evidence.assessment_id)
    require_participant(actor, assessment)
    if actor.pk not in {assessment.employee_id, assessment.reviewer_id}:
        raise PermissionDenied("Свидетельства изменяют только назначенные участники")
    require_state(assessment, WORKING)
    if evidence.author_id != actor.pk:
        raise PermissionDenied("Удалять из рабочего списка можно только собственный файл")
    if evidence.answerrevision_set.exists() or evidence.findingrevision_set.exists():
        raise ValidationError("Файл связан с исторической версией; удаление запрещено")
    evidence.retired = True
    evidence.save(update_fields=["retired"])
    record(actor, "evidence.retired", evidence, previous={"retired": False}, new={"retired": True})


def open_evidence(actor, evidence):
    require_participant(actor, evidence.assessment)
    if evidence.retired:
        raise PermissionDenied("Файл удалён из рабочего списка")
    if not evidence.file:
        raise ValidationError("У текстового свидетельства нет файла")
    stream = evidence.file.open("rb")
    checksum = hashlib.sha256()
    for chunk in iter(lambda: stream.read(65536), b""):
        checksum.update(chunk)
    if checksum.hexdigest() != evidence.sha256:
        stream.close()
        raise ValidationError("Контроль целостности не пройден")
    stream.seek(0)
    return stream
