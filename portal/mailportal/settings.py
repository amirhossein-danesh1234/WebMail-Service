"""Invite-only portal. Stalwart remains the sole source of mailbox data."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ['PORTAL_SECRET_KEY']
MAILBOX_KEY = os.environ['PORTAL_MAILBOX_KEY']
DEBUG = False
ADMIN_HOST = 'mail-admin.amirhossein-danesh.ir'
MAIL_HOSTS = ['mail.amirhossein-danesh.ir', 'mail.kappaut.ir', 'mail.unilrn.ir']
ALLOWED_HOSTS = MAIL_HOSTS + [ADMIN_HOST, 'localhost', 'testserver']
CSRF_TRUSTED_ORIGINS = ['https://' + h for h in MAIL_HOSTS + [ADMIN_HOST]]
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth',
                  'django.contrib.contenttypes', 'django.contrib.sessions',
                  'django.contrib.messages', 'django.contrib.staticfiles', 'mailboxes']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware',
              'django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware',
              'django.contrib.messages.middleware.MessageMiddleware',
              'django.middleware.clickjacking.XFrameOptionsMiddleware',
              'mailboxes.middleware.BoundariesMiddleware']
ROOT_URLCONF = 'mailportal.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
              'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': [
                  'django.template.context_processors.request',
                  'django.contrib.auth.context_processors.auth',
                  'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'mailportal.wsgi.application'
DATA_DIR = Path(os.environ.get('PORTAL_DATA_DIR', '/var/lib/webmail-portal'))
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
                         'NAME': DATA_DIR / 'portal.sqlite3',
                         'OPTIONS': {'timeout': 20}}}
LANGUAGE_CODE = 'fa'
TIME_ZONE = 'Asia/Tehran'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/portal-static/'
STATIC_ROOT = DATA_DIR / 'static'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
PASSWORD_RESET_TIMEOUT = 86400
SESSION_COOKIE_NAME = '__Host-mailportal'
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 43200
CSRF_COOKIE_NAME = '__Host-mailportal-csrf'
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 3600
# Only the loopback proxy can reach this service; it replaces this header.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 22 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
MAIL_SERVER = 'mx.amirhossein-danesh.ir'
MAIL_DOMAINS = ('amirhossein-danesh.ir', 'kappaut.ir', 'unilrn.ir')
