from django.conf import settings
from django.http import HttpResponseNotFound


class BoundariesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0]
        if request.path.startswith(('/manage/', '/staff-gate/')) and host != settings.ADMIN_HOST:
            return HttpResponseNotFound()
        response = self.get_response(request)
        response['Cache-Control'] = 'private, no-store'
        response['Content-Security-Policy'] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; "
            "base-uri 'self'; form-action 'self'")
        return response
