from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class ConversationTurnThrottle(SimpleRateThrottle):
   

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
