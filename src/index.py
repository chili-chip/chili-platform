import os

from django.core.wsgi import get_wsgi_application
from django_cf import DjangoCF
from workers import WorkerEntrypoint

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

application = get_wsgi_application()

class Default(DjangoCF, WorkerEntrypoint):
    def get_app(self):
        return application
