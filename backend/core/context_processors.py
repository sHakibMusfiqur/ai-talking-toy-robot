from django.conf import settings


def asset_version(request):
    
    return {"asset_version": getattr(settings, "ASSET_VERSION", "1")}
