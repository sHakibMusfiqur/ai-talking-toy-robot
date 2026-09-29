from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and isinstance(
        exc, (NotAuthenticated, AuthenticationFailed)
    ):
        # DRF downgrades both to 403 when no WWW-Authenticate scheme is
        # advertised; the project contract is always 401 for auth failures.
        response.status_code = status.HTTP_401_UNAUTHORIZED
    return response
