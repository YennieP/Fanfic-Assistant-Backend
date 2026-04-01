from pathlib import Path
from dotenv import load_dotenv
import os
import dj_database_url

# 将.env的内容写入当前进程的环境变量 os.environ
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY') # Django 用于签名 session、CSRF token 等的密钥。
DEBUG = os.getenv('DEBUG', 'False') == 'True' # 将环境变量中的字符串T/F转换为boolean格式的T/F
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') # 从环境变量中获取允许访问的主机名列表，转换为list。如果获取的变量ALLOWED_HOSTS不存在，则默认为localhost

# 项目中所有已激活的app。用于进行模型发现、数据库迁移等操作。
INSTALLED_APPS = [
    'django.contrib.admin', # admin 后台
    'django.contrib.auth', # 内置用户系统
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework', # DRF (Django REST Framework)，用于快速构建RESTful API
    'rest_framework_simplejwt', # JDW，DRF的认证插件
    'corsheaders', # CORS，处理跨域资源共享
    'characters', # 角色卡app
    'users', # 用户app
    'logs',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware', # CORS，必须放在第一个，因为必须在响应被其他中间件修改前添加
    'django.middleware.security.SecurityMiddleware', # 提供一系列安全增强功能
    'whitenoise.middleware.WhiteNoiseMiddleware', # 在生产环境（DEBUG=False）中高效地提供静态文件。它需要紧跟 Security，因为WhiteNoise 需要尽早处理静态文件请求，避免不必要的开销
    'logs.middleware.RequestLoggingMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware', # 启用会话支持。
    'django.middleware.common.CommonMiddleware', # 提供一些通用功能,需要访问请求的路径信息，但不需要会话数据
    'django.middleware.csrf.CsrfViewMiddleware', # 跨站请求伪造（CSRF）保护
    'django.contrib.auth.middleware.AuthenticationMiddleware', # 将用户与请求关联
    'django.contrib.messages.middleware.MessageMiddleware', # 支持一次性消息（如操作成功/失败提示）
    'django.middleware.clickjacking.XFrameOptionsMiddleware', # 防止点击劫持（clickjacking）
]

ROOT_URLCONF = 'core.urls' # URL 路由配置, 指定 Django 项目的根 URL 配置文件

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates', # 指定使用的模板引擎类(负责加载、编译和渲染模板)
        'DIRS': [], # 指定自定义模板目录的列表
        'APP_DIRS': True, # 控制 Django 是否在每个已安装的应用（INSTALLED_APPS 中的每个应用）的 templates 子目录中自动查找模板
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# 数据库：优先读 DATABASE_URL（Railway 注入），fallback 到本地 SQLite
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_RENDERER_CLASSES': (
        'djangorestframework_camel_case.render.CamelCaseJSONRenderer',
        'djangorestframework_camel_case.render.CamelCaseBrowsableAPIRenderer',
    ),
    'DEFAULT_PARSER_CLASSES': (
        'djangorestframework_camel_case.parser.CamelCaseJSONParser',
    ),
}

from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}

CORS_ALLOWED_ORIGINS = os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:5173').split(',')