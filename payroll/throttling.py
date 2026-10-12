"""Rate limiting keyed on the real client IP when the request comes through our own web proxy.

The Next.js app calls this API from Vercel, so every visitor would otherwise share one IP and one
rate-limit bucket. The proxy sends its client's IP in X-Client-IP together with a shared secret in
X-Proxy-Secret; the IP is trusted only when the secret matches, so it cannot be forged by outsiders.
"""

import hmac

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle


def trusted_client_ip(request) -> str | None:
    secret = getattr(settings, "PROXY_IP_SECRET", "")
    supplied = request.META.get("HTTP_X_PROXY_SECRET", "")
    if secret and supplied and hmac.compare_digest(secret.encode(), supplied.encode()):
        return request.META.get("HTTP_X_CLIENT_IP", "").strip() or None
    return None


class TrustedProxyMixin:
    def get_ident(self, request):
        return trusted_client_ip(request) or super().get_ident(request)


class ProxyAnonRateThrottle(TrustedProxyMixin, AnonRateThrottle):
    pass


class ProxyUserRateThrottle(TrustedProxyMixin, UserRateThrottle):
    pass


class ProxyScopedRateThrottle(TrustedProxyMixin, ScopedRateThrottle):
    pass
