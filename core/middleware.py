from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin


class DomainErrorMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        if isinstance(exception, ValidationError):
            return render(
                request, "workspace/error.html", {"issues": exception.messages}, status=400
            )
        return None

    def process_response(self, request, response):
        if getattr(request, "user", None) and request.user.is_authenticated:
            response["Cache-Control"] = "private, no-store"
        return response
