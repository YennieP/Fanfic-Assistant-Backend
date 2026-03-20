# Fanfic Assistant Backend — Django REST API

> 中文同人文长文写作辅助工具的后端服务，提供角色卡管理、AU Mod 管理、用户认证等 API 接口。

---

## 项目背景

本项目是 Yanxi Pan（Northeastern University CS 硕士在读，2027 届）的简历项目后端部分，方向为 Applied ML Engineer，聚焦生成式 AI 与内容 AI。

前端仓库：[Fanfic-Assistant](https://github.com/YennieP/Fanfic-Assistant)

**🌐 后端 API 地址：[https://web-production-29e7.up.railway.app](https://web-production-29e7.up.railway.app)**


---

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端框架 | Django 5 + Django REST Framework |
| 认证 | JWT（djangorestframework-simplejwt）|
| 数据库 | SQLite（开发）/ PostgreSQL（生产）|
| 缓存 | Redis（待接入）|
| 异步队列 | Bull + Redis（待接入）|
| 跨域 | django-cors-headers |
| 命名转换 | djangorestframework-camel-case |
| 部署 | Railway（计划中）|

---

## 项目结构

```
fanfic-assistant-backend/
  core/
    settings.py       # 项目配置
    urls.py           # 根路由
    wsgi.py
  characters/
    models.py         # BaseCard、AUMod 数据模型
    serializers.py    # 序列化器（列表版 + 详情版）
    views.py          # ViewSet
    urls.py           # 嵌套路由
    admin.py          # Django Admin 注册
    migrations/
  users/
    serializers.py    # 注册序列化器
    views.py          # 注册、获取当前用户
    urls.py
  .env                # 环境变量（不提交到 Git）
  requirements.txt
  manage.py
```

---

## API 接口文档

### 认证

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/auth/register/` | 注册新用户 | 公开 |
| POST | `/api/token/` | 登录，获取 JWT token | 公开 |
| POST | `/api/token/refresh/` | 刷新 access token | 公开 |
| GET | `/api/auth/me/` | 获取当前用户信息 | 需登录 |

**注册请求体：**
```json
{
  "username": "yanxi",
  "email": "yanxi@example.com",
  "password": "yourpassword",
  "password_confirm": "yourpassword"
}
```

**登录响应：**
```json
{
  "access": "eyJ...",
  "refresh": "eyJ..."
}
```

所有需要登录的接口请求头携带：
```
Authorization: Bearer <access_token>
```

---

### 角色卡

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/characters/` | 获取当前用户所有角色卡（轻量列表）|
| POST | `/api/characters/` | 创建角色卡 |
| GET | `/api/characters/:id/` | 获取角色卡详情（完整数据）|
| PATCH | `/api/characters/:id/` | 更新角色卡 |
| DELETE | `/api/characters/:id/` | 删除角色卡 |

**列表视图字段（轻量）：**
```json
{
  "id": "uuid",
  "name": "林宇",
  "fandom": "某某组合",
  "mbti": "INTJ",
  "quickLabels": [],
  "auMods": [],
  "createdAt": "...",
  "updatedAt": "..."
}
```

**详情视图字段（完整）：** 包含所有字段，见数据模型章节。

---

### AU Mod

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/characters/:id/mods/` | 获取某角色的所有 AU Mod |
| POST | `/api/characters/:id/mods/` | 创建 AU Mod |
| GET | `/api/characters/:id/mods/:modId/` | 获取 AU Mod 详情 |
| PATCH | `/api/characters/:id/mods/:modId/` | 更新 AU Mod |
| DELETE | `/api/characters/:id/mods/:modId/` | 删除 AU Mod |

---

## 数据模型

### BaseCard

```python
class BaseCard(models.Model):
    owner                    # 关联用户
    name                     # 角色名
    fandom                   # 来源作品
    card_author              # 卡片作者
    version                  # 版本
    author_nicknames         # 作者昵称（JSONField）
    mbti                     # MBTI 类型
    mbti_notes               # MBTI 具体体现
    core_values              # 核心价值观（JSONField）
    core_fears               # 核心恐惧（JSONField）
    key_experiences          # 重要经历（JSONField）
    quick_labels             # 性格标签（JSONField）
    behavioral_patterns      # 行为模式（JSONField）
    forbidden_behaviors      # 人设红线（JSONField）
    default_state            # 日常情绪基调
    emotional_triggers       # 情绪触发点（JSONField）
    emotion_expression_style # 情绪表达方式
    recovery_pattern         # 情绪恢复方式
    conditions               # 疾病（JSONField）
    physical_traits          # 特殊体征（JSONField）
    speech_style_custom_tags # 台词风格自定义标签（JSONField）
```

### AUMod

```python
class AUMod(models.Model):
    character                # 关联角色卡（ForeignKey）
    au_name                  # AU 名称
    setting                  # 世界观/背景设定
    role_title               # 职业/身份名称
    role_age                 # 年龄
    role_current_situation   # 当前人生处境
    quick_labels             # AU 专属性格标签（JSONField）
    forbidden_behaviors      # AU 专属人设红线（JSONField）
```

---

## 命名转换规则

后端存储使用 `snake_case`，API 输出自动转换为 `camelCase`（由 `djangorestframework-camel-case` 处理）：

```
后端：quick_labels → API 输出：quickLabels
后端：au_name     → API 输出：auName
后端：role_title  → API 输出：roleTitle
```

前端 service 层负责将 `roleTitle/roleAge/roleCurrentSituation` 映射回嵌套的 `role` 对象。

---

## 本地开发

### 环境要求

- Python 3.12+
- pip

### 安装步骤

```bash
git clone https://github.com/YennieP/Fanfic-Assistant-Backend.git
cd fanfic-assistant-backend

python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # macOS/Linux

pip install -r requirements.txt

# 创建 .env 文件（参考下方说明）
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

服务器运行在 `http://localhost:8000`

### 环境变量（.env）

```
SECRET_KEY=django-insecure-xxx
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=sqlite:///db.sqlite3
CORS_ALLOWED_ORIGINS=http://localhost:5173
```

---

## 待开发功能

- [ ] 关系实体 API（Relationship Entity）
- [ ] 示例库 API（台词示例片段）
- [ ] Redis 缓存接入
- [ ] PostgreSQL 迁移（Railway 部署）
- [ ] 写作生成 API（RAG pipeline）
- [ ] Bull + Redis 异步队列

---

## 部署（Railway）

待完成，部署后更新此处生产环境地址。

---

## 作者

**Yanxi Pan**
CS Master's Student @ Northeastern University (Silicon Valley Campus, Class of 2027)
目标方向：Applied ML Engineer — Generative AI / Content AI