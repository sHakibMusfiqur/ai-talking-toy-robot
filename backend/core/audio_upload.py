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


def content_length_error(raw):
   
    if raw is None or not str(raw).strip():
        return "Missing Content-Length header."
    value = str(raw).strip()
    if "," in value:
        return "Duplicate or conflicting Content-Length header."
    if value.startswith("-"):
        return "Negative Content-Length header."
    if not value.isdigit():
        return "Malformed Content-Length header."
    if int(value) > settings.AUDIO_UPLOAD_MAX_BODY_BYTES:
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
    
    if scope.get("type") != "http":
        return None
    path = scope.get("path", "")
    if isinstance(path, bytes):
        path = path.decode("latin-1")
    values = [
        value.decode("latin-1")
        for key, value in scope.get("headers", [])
        if key.lower() == b"content-length"
    ]
    if len(values) > 1:
        return "Duplicate or conflicting Content-Length header."
    return audio_upload_post_error(
        scope.get("method", ""), path, values[0] if values else None
    )


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
