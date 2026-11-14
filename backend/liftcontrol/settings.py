from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def setting_or_file(env_name: str, file_name: str, default: str = "") -> str:
    """Read Docker secret files without exposing their contents to process environment."""
    secret_path = os.getenv(file_name)
    if secret_path:
        value = Path(secret_path).read_text(encoding="utf-8").strip()
        if not value:
            raise ImproperlyConfigured(f"{file_name} points to an empty secret")
        return value
    return os.getenv(env_name, default)


DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
SECRET_KEY = setting_or_file("DJANGO_SECRET_KEY", "DJANGO_SECRET_KEY_FILE")
if not DEBUG and (not SECRET_KEY or SECRET_KEY in {"change-me", "dev-only-change-me"}):
    raise ImproperlyConfigured("A secure DJANGO_SECRET_KEY or DJANGO_SECRET_KEY_FILE is required")

# Development-only fallback; production startup requires a real key.
if not SECRET_KEY:
    SECRET_KEY = "dev-only-change-me"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "liftcontrol.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "liftcontrol.wsgi.application"
ASGI_APPLICATION = "liftcontrol.asgi.application"

DB_ENGINE = os.getenv("DB_ENGINE", "sqlite")
if DB_ENGINE == "postgresql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "liftcontrol"),
            "USER": os.getenv("DB_USER", "liftapp"),
            "PASSWORD": setting_or_file("DB_PASSWORD", "DB_PASSWORD_FILE"),
            "HOST": os.getenv("DB_HOST", "db"),
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"options": "-c search_path=app,public"},
        }
    }
    if not DATABASES["default"]["PASSWORD"]:
        raise ImproperlyConfigured("PostgreSQL password is required")
elif DB_ENGINE == "sqlite":
    if not DEBUG:
        raise ImproperlyConfigured("SQLite is allowed only with DJANGO_DEBUG=True")
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.getenv("DJANGO_SQLITE_PATH", str(BASE_DIR / "db.sqlite3"))}}
else:
    raise ImproperlyConfigured(f"Unsupported DB_ENGINE: {DB_ENGINE}")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Asia/Yakutsk"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_ROOT = Path(os.getenv("DJANGO_MEDIA_ROOT", str(BASE_DIR / "private_media")))
MEDIA_URL = "/private-media-not-public/"
FILE_UPLOAD_HANDLERS = ["core.uploads.BoundedUploadHandler", "django.core.files.uploadhandler.MemoryFileUploadHandler", "django.core.files.uploadhandler.TemporaryFileUploadHandler"]
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024 * 1024  # Bounded JSON offline photo (10 MB before base64).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# API is private until a TLS reverse proxy is configured.
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
USE_HTTPS = os.getenv("DJANGO_BEHIND_HTTPS_PROXY", "False").lower() == "true"
if USE_HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True

REST_FRAMEWORK = {
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser", "core.uploads.BoundedMultiPartParser", "rest_framework.parsers.FormParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "core.native_authentication.MobileSessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
}


LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

