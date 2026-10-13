from io import BytesIO

from django.conf import settings
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.uploadhandler import FileUploadHandler, StopFutureHandlers
from django.http import JsonResponse
from django.http.multipartparser import MultiPartParserError

AUDIO_UPLOAD_PATHS = frozenset(
    {
        "/api/v1/conversations/turn/audio",
        "/api/v1/conversations/turn/audio/",
    }
)

# Header values longer than this are rejected before any integer
# conversion. No legitimate body size needs more than a handful of
# digits (the approved bound is 7), and CPython 3.11 refuses int()
# beyond 4300 digits with a ValueError - a conversion failure must
# never surface as HTTP 500, so huge digit strings never reach int().
CONTENT_LENGTH_MAX_CHARS = 64


def content_length_error(raw):
    """Decide one Content-Length value. ``None`` = acceptable.

    Strict ASCII-digit parsing: only ``0-9`` counts. Unicode digits
    (fullwidth ``\\uFF11``, Arabic-Indic ``\\u0661``, superscript
    ``\\u00B2``), signs, decimal points, inner whitespace, or
    byte-string representations are malformed. Missing, negative,
    duplicate/conflicting, oversized, and excessively long values are
    distinct deterministic rejections - every path returns a message;
    none can raise.
    """
    if raw is None or not str(raw).strip():
        return "Missing Content-Length header."
    value = str(raw).strip()
    if "," in value:
        return "Duplicate or conflicting Content-Length header."
    if value.startswith("-"):
        return "Negative Content-Length header."
    if not (value.isascii() and value.isdigit()):
        return "Malformed Content-Length header."
    if len(value) > CONTENT_LENGTH_MAX_CHARS:
        return "Malformed Content-Length header."
    try:
        number = int(value)
    except ValueError:
        return "Malformed Content-Length header."
    if number > settings.AUDIO_UPLOAD_MAX_BODY_BYTES:
        return "Content-Length exceeds the allowed upload size."
    return None


def audio_upload_post_error(method, path, raw_content_length):
    """Gate result for one request; ``None`` = let it through."""
    if method != "POST" or path not in AUDIO_UPLOAD_PATHS:
        return None
    return content_length_error(raw_content_length)


class AudioUploadBodyGateMiddleware:
    

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        detail = audio_upload_post_error(
            request.method, request.path, request.META.get("CONTENT_LENGTH")
        )
        if detail is not None:
            return JsonResponse({"detail": detail}, status=400)
        return self.get_response(request)


def scope_post_error(scope):
    """Same decision for an ASGI scope as WSGI sees for one request.

    Header names/values are tolerated as bytes (the ASGI spec) or str;
    the actual value decision is delegated to ``content_length_error``
    so both gate surfaces give byte-identical messages and can never
    raise.
    """
    if scope.get("type") != "http":
        return None
    path = scope.get("path") or ""
    if isinstance(path, bytes):
        path = path.decode("latin-1")
    method = scope.get("method") or ""
    if isinstance(method, bytes):
        method = method.decode("latin-1")
    values = []
    for key, value in scope.get("headers") or []:
        if isinstance(key, bytes):
            key = key.decode("latin-1")
        if str(key).lower() == "content-length":
            if isinstance(value, bytes):
                value = value.decode("latin-1")
            values.append(str(value))
    if len(values) > 1:
        return "Duplicate or conflicting Content-Length header."
    return audio_upload_post_error(method, path, values[0] if values else None)


class BoundedMemoryAudioUploadHandler(FileUploadHandler):
    

    def __init__(self, request=None, max_bytes=None):
        super().__init__(request)
        self.max_bytes = (
            settings.AUDIO_UPLOAD_MAX_FILE_BYTES if max_bytes is None else max_bytes
        )
        self.buffer = None
        self.received = 0

    def new_file(self, *args, **kwargs):
        if self.buffer is not None:
            raise MultiPartParserError("Exactly one 'audio' file part is required.")
        super().new_file(*args, **kwargs)
        if self.field_name != "audio":
            raise MultiPartParserError("Unexpected file part.")
        self.buffer = BytesIO()
        self.received = 0
        raise StopFutureHandlers()

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > self.max_bytes:
            raise MultiPartParserError("Audio file exceeds the maximum allowed size.")
        self.buffer.write(raw_data)
        return None

    def file_complete(self, file_size):
        if self.buffer is None:
            return None
        self.buffer.seek(0)
        return InMemoryUploadedFile(
            file=self.buffer,
            field_name=self.field_name,
            name=self.file_name,
            content_type=self.content_type,
            size=file_size,
            charset=self.charset,
            content_type_extra=self.content_type_extra,
        )

    def upload_interrupted(self):
        if self.buffer is not None:
            self.buffer.close()
            self.buffer = None
