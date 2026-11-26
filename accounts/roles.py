from django.contrib.auth.models import Group, Permission

from .access import ADMIN, EMPLOYEE, REVIEWER


def setup_roles():
    role_permissions = {
        ADMIN: {
            "auth.add_user",
            "auth.change_user",
            "auth.view_user",
            "organization.add_organizationprofile",
            "organization.change_organizationprofile",
            "organization.view_organizationprofile",
            "organization.add_protectioncontour",
            "organization.change_protectioncontour",
            "organization.view_protectioncontour",
            "organization.add_employee",
            "organization.change_employee",
            "organization.view_employee",
            "organization.add_informationsystem",
            "organization.change_informationsystem",
            "organization.view_informationsystem",
            "assessments.add_assessment",
            "assessments.change_assessment",
            "assessments.view_assessment",
            "standards.add_catalogversion",
            "standards.change_catalogversion",
            "standards.view_catalogversion",
            "standards.change_requirementversion",
        },
        EMPLOYEE: {
            "assessments.add_answerrevision",
            "assessments.view_assessment",
            "assessments.view_answerrevision",
            "assessments.add_finding",
            "assessments.add_findingrevision",
            "evidence.add_evidence",
            "evidence.view_evidence",
        },
        REVIEWER: {
            "assessments.view_assessment",
            "assessments.add_reviewdecision",
            "assessments.add_finding",
            "assessments.add_findingrevision",
            "assessments.add_findingdecision",
            "evidence.add_evidence",
            "evidence.view_evidence",
            "calculations.add_calculationrun",
            "reports.add_resultsnapshot",
            "reports.add_reportexport",
        },
    }
    permissions = list(Permission.objects.select_related("content_type"))
    for name, wanted in role_permissions.items():
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set(
            p for p in permissions if f"{p.content_type.app_label}.{p.codename}" in wanted
        )
