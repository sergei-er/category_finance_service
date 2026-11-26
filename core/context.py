from accounts.access import ADMIN, has_role


def navigation(request):
    return {"nav_admin": has_role(request.user, ADMIN)}
