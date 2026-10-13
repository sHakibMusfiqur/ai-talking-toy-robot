from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class ConversationTurnThrottle(SimpleRateThrottle):
    """Fixed-window cost/abuse limit on POST /api/v1/conversations/turn/.

    Bucketed per principal: device callers by robot (`robot:<pk>`), session
    callers by user (`user:<pk>`). Anonymous requests return no cache key and
    are never throttled here - authentication still rejects them (401) long
    before any AI-service call. An empty `CONVERSATION_TURN_RATE_LIMIT`
    disables the limiter entirely.

    Storage is the default in-memory cache: process-exact for the current
    single-process deployment, and equally limited by the multi-worker caveat
    already documented for ROBOT_COMMAND_COOLDOWN_SECONDS (a multi-worker
    deployment would need a shared cache; none is adopted yet).
    """

    scope = "conversation-turn"

    def __init__(self):
        self.rate = settings.CONVERSATION_TURN_RATE_LIMIT or None
        if self.rate is None:
            # Disabled by configuration: allow everything, no cache traffic.
            self.num_requests = None
            self.duration = None
            return
        self.num_requests, self.duration = self.parse_rate(self.rate)

    def get_cache_key(self, request, view):
        auth = request.auth
        if isinstance(auth, dict) and "robot" in auth:
            ident = f"robot:{auth['robot'].pk}"
        elif request.user is not None and request.user.is_authenticated:
            ident = f"user:{request.user.pk}"
        else:
            return None
        return self.cache_format % {"scope": self.scope, "ident": ident}
