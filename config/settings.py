from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "dev-only-change-me"
DEBUG = True
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "rest_framework", "traffic"]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "config.urls"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3",
                         "OPTIONS": {"timeout": 20}}}   # web + ticker processes share this file
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "traffic.views.exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],   # auth = bonus, see README
    "UNAUTHENTICATED_USER": None,
}
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"f": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"c": {"class": "logging.StreamHandler", "formatter": "f"}},
    "loggers": {"traffic": {"handlers": ["c"], "level": "INFO"}},
}
