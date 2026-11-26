from django.core.exceptions import PermissionDenied
from django.db.models import Q

ADMIN = "Администратор"
EMPLOYEE = "Специалист по ИБ"
REVIEWER = "Внутренний проверяющий"


def has_role(user, role):
    if not user.is_authenticated or not user.is_active:
        return False
    if role == ADMIN and user.is_superuser:
        return True
    marker = {
        ADMIN: "auth.add_user",
        EMPLOYEE: "assessments.add_answerrevision",
        REVIEWER: "assessments.add_reviewdecision",
    }[role]
    return user.groups.filter(name=role).exists() and user.has_perm(marker)


def require_role(user, role):
    if not has_role(user, role):
        raise PermissionDenied("Недостаточно полномочий")


def participant(user, assessment):
    return has_role(user, ADMIN) or (
        assessment.employee_id == user.pk and has_role(user, EMPLOYEE)
    ) or (
        assessment.reviewer_id == user.pk and has_role(user, REVIEWER)
    )


def require_participant(user, assessment):
    if not participant(user, assessment):
        raise PermissionDenied("Обследование не назначено пользователю")


def require_reviewer(user, assessment):
    require_role(user, REVIEWER)
    if assessment.reviewer_id != user.pk:
        raise PermissionDenied("Назначен другой проверяющий")


def assigned_filter(user):
    if has_role(user, ADMIN):
        return Q()
    query = Q(pk__in=[])
    if has_role(user, EMPLOYEE):
        query |= Q(employee=user)
    if has_role(user, REVIEWER):
        query |= Q(reviewer=user)
    return query
