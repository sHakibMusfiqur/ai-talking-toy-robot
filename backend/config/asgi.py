import json
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

_django_asgi_app = get_asgi_application()

from core.audio_upload import scope_post_error  # noqa: E402  (needs app setup)


async def application(scope, receive, send):
    
    detail = scope_post_error(scope)
    if detail is not None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
        return
    await _django_asgi_app(scope, receive, send)
