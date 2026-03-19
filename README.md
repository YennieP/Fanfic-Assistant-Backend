# Fanfic Assistant Backend — Django REST API

> 中文同人文长文写作辅助工具的后端服务，提供角色卡管理、用户认证等 API 接口。

---

## 项目背景

本项目是 Yanxi Pan（Northeastern University CS 硕士在读，2027 届）的简历项目后端部分，方向为 Applied ML Engineer，聚焦生成式 AI 与内容 AI。

前端仓库：[Fanfic-Assistant](https://github.com/YennieP/Fanfic-Assistant)

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
| 部署 | Railway |

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
    serializers.py    # 序列化器
    views.py          # ViewSet
    urls.py           # 路由（嵌套路由）
    migrations/       # 数据库迁移文件
  users/
    serializers.py    # 注册序列化器
    views.py          # 注册、获取当前用户
    urls.py           # 路由
  .env                # 环境变量（不提交到 Git）
  requirements.txt    # 依赖列表
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

**登录请求体：**
```json
{
  "username": "yanxi",
  "password": "yourpassword"
}
```

**登录响应：**
```json
{
  "access": "eyJ...",
  "refresh": "eyJ..."
}
```

所有需要登录的接口，请求头需携带：
```
Authorization: Bearer <access_token>
```

---

### 角色卡

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/characters/` | 获取当前用户的所有角色卡（列表视图）|
| POST | `/api/characters/` | 创建新角色卡 |
| GET | `/api/characters/:id/` | 获取角色卡详情 |
| PUT | `/api/characters/:id/` | 完整更新角色卡 |
| PATCH | `/api/characters/:id/` | 部分更新角色卡 |
| DELETE | `/api/characters/:id/` | 删除角色卡 |

**角色卡数据结构：**
```json
{
  "id": "uuid",
  "name": "林宇",
  "fandom": "某某组合",
  "card_author": "Yanxi Pan",
  "version": "v1.0",
  "author_nicknames": ["我家宇宇", "小林"],
  "mbti": "INTJ",
  "mbti_notes": "他的 J 体现在对承诺的执念",
  "core_values": [{"id": "cv_01", "content": "团队稳定高于个人情绪"}],
  "core_fears": [{"id": "cf_01", "content": "被最亲近的人当众否定"}],
  "key_experiences": [],
  "quick_labels": [{"id": "ql_01", "content": "嘴硬心软"}],
  "behavioral_patterns": [],
  "forbidden_behaviors": [],
  "default_state": "表面平静、略带疏离",
  "emotional_triggers": [],
  "emotion_expression_style": "情绪内敛，肢体语言先于语言",
  "recovery_pattern": "需要独处时间",
  "conditions": [],
  "physical_traits": [],
  "speech_style_custom_tags": {},
  "au_mods": [],
  "created_at": "2025-03-01T00:00:00Z",
  "updated_at": "2025-03-01T00:00:00Z"
}
```

---

### AU Mod

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/characters/:id/mods/` | 获取某角色的所有 AU Mod |
| POST | `/api/characters/:id/mods/` | 创建新 AU Mod |
| GET | `/api/characters/:id/mods/:modId/` | 获取 AU Mod 详情 |
| PUT | `/api/characters/:id/mods/:modId/` | 完整更新 AU Mod |
| PATCH | `/api/characters/:id/mods/:modId/` | 部分更新 AU Mod |
| DELETE | `/api/characters/:id/mods/:modId/` | 删除 AU Mod |

---

## 数据模型

### BaseCard

```python
class BaseCard(models.Model):
    owner           # 关联用户（ForeignKey）
    name            # 角色名
    fandom          # 来源作品
    author_nicknames    # 作者昵称列表（JSONField）
    mbti            # MBTI 类型
    mbti_notes      # MBTI 在该角色身上的具体体现
    core_values     # 核心价值观（JSONField）
    core_fears      # 核心恐惧（JSONField）
    key_experiences # 重要经历（JSONField）
    quick_labels    # 性格标签（JSONField）
    behavioral_patterns  # 行为模式（JSONField）
    forbidden_behaviors  # 人设红线（JSONField）
    default_state   # 日常情绪基调
    emotional_triggers   # 情绪触发点（JSONField）
    emotion_expression_style  # 情绪表达方式
    recovery_pattern     # 情绪恢复方式
    conditions      # 疾病（JSONField）
    physical_traits # 特殊体征（JSONField）
    speech_style_custom_tags  # 台词风格自定义标签（JSONField）
```

### AUMod

```python
class AUMod(models.Model):
    character       # 关联角色卡（ForeignKey）
    au_name         # AU 名称
    setting         # 世界观/背景设定
    role_title      # 职业/身份名称
    role_age        # 年龄
    role_current_situation  # 当前人生处境
    quick_labels    # AU 专属性格标签（JSONField）
    forbidden_behaviors  # AU 专属人设红线（JSONField）
```

---

## 本地开发

### 环境要求

- Python 3.12+
- pip

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/YennieP/Fanfic-Assistant-Backend.git
cd fanfic-assistant-backend

# 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux

# 安装依赖
pip install -r requirements.txt

# 创建 .env 文件
cp .env.example .env  # 按需修改

# 数据库迁移
python manage.py migrate

# 创建超级用户
python manage.py createsuperuser

# 启动服务器
python manage.py runserver
```

服务器运行在 `http://localhost:8000`

### 环境变量说明（.env）

```
SECRET_KEY=django-insecure-xxx        # Django 密钥
DEBUG=True                            # 开发模式
ALLOWED_HOSTS=localhost,127.0.0.1     # 允许的主机
DATABASE_URL=sqlite:///db.sqlite3     # 数据库地址
CORS_ALLOWED_ORIGINS=http://localhost:5173  # 允许跨域的前端地址
```

---

## 待开发功能

- [ ] 关系实体 API（Relationship Entity）
- [ ] 示例库 API（台词示例片段 + 向量检索）
- [ ] Redis 缓存接入
- [ ] PostgreSQL 迁移（Railway 部署）
- [ ] 写作生成 API（RAG pipeline）
- [ ] Bull + Redis 异步队列（长文生成）

---

## 部署（Railway）

待完成，部署后更新此处的生产环境地址。

```
后端 API：https://your-railway-app.railway.app
```

---

## 作者

**Yanxi Pan**
CS Master's Student @ Northeastern University (Silicon Valley Campus, Class of 2027)
目标方向：Applied ML Engineer — Generative AI / Content AI