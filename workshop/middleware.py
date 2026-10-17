from django.contrib.auth import logout
class SessionGuard:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.user.is_authenticated and (not request.user.is_active or request.session.get("epoch") != request.user.session_epoch):
            logout(request)
        return self.get_response(request)
