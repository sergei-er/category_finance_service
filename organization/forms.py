from django import forms

from .models import Employee, InformationSystem, OrganizationProfile, ProtectionContour


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = OrganizationProfile
        fields = [
            "name",
            "short_name",
            "inn",
            "legal_address",
            "organization_type",
            "questionnaire_owner",
            "result_approver",
            "description",
        ]
        labels = {
            "questionnaire_owner": "Ответственный за заполнение анкет",
            "result_approver": "Согласующий результаты обследований",
        }


class ContourForm(forms.ModelForm):
    class Meta:
        model = ProtectionContour
        fields = ["code", "name", "level", "description"]


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["last_name", "first_name", "middle_name", "position", "department", "contact"]


class InformationSystemForm(forms.ModelForm):
    class Meta:
        model = InformationSystem
        fields = [
            "name",
            "purpose",
            "developer",
            "responsible_department",
            "responsible_employee",
            "contour",
        ]
        labels = {"responsible_employee": "Ответственный сотрудник", "contour": "Контур защиты"}
