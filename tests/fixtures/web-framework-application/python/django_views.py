from django.http import JsonResponse
from django.views import View


def health(request):
    return JsonResponse({"status": "ok"})


class UserListView(View):
    def get(self, request):
        return JsonResponse({"users": []})
