from django import forms

from .models import Employee, InformationSystem, OrganizationProfile, ProtectionContour


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = OrganizationProfile
        fields = [
            "name", "short_name", "inn", "legal_address", "organization_type",
            "questionnaire_owner", "result_approver", "description", "synthetic",
        ]


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
            "name", "purpose", "developer", "responsible_department",
            "responsible_employee", "contour",
        ]
