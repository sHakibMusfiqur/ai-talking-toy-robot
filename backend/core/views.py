from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@require_GET
def root(request):
    if request.user.is_authenticated:
        return redirect("health")
    return redirect("login")
