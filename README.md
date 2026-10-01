# SmartKBQA — 企业知识库智能问答

基于 Django + DRF + JWT + Celery + Chroma + LangGraph + DeepSeek 的多角色企业知识库问答系统。

## 功能概览

- 认证：注册 / 登录 / JWT / 改密 / 退出
- 角色：总管理员、部门管理员（如研发部/财务部）、普通员工
- 文档：上传、解析分块、向量化、部门可见性、部门上传需总管审批
- 问答：RAG + LangGraph 多步检索、会话历史、SSE 流式输出
- 员工管理：按角色查看/编辑，仅总管可任命管理员
- 安全运维：接口限流、审计日志、健康检查、结构化日志、任务重试与告警 webhook

## 角色说明

| 角色 | 能力 |
|------|------|
| **总管理员** `super_admin` | 全文档、审批、全员员工、任命部门/总管理员、审计日志 |
| **部门管理员** `dept_admin` | 必须绑定部门；上传本部门文档（待审批）；管理本部门普通员工 |
| **普通员工** `user` | 检索全员 + 本部门已入库文档；改密；不可上传/管人 |

## 本地快速启动

### 1. 环境

- Python 3.10+
- MySQL 8（或开发时 `USE_SQLITE=True`）
- Redis（或开发时 `USE_LOCMEM_CACHE=True` + `CELERY_TASK_ALWAYS_EAGER=True`）

```bash
cd smart_kbqa
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env：DJANGO_SECRET_KEY、数据库、DEEPSEEK_API_KEY 等
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

浏览器打开：http://127.0.0.1:8000/login/

### 2. 运行测试

```bash
python manage.py test accounts documents qa common -v 2
```

测试使用内存 SQLite + LocMem 缓存（`test` 命令自动启用）。

### 3. 异步 Worker（可选）

```bash
# .env 中 CELERY_TASK_ALWAYS_EAGER=False
celery -A config worker -l info
```

## Docker Compose 部署

```bash
cp .env.example .env
# 生产务必：DEBUG=False，强密钥，真实 DB 密码，配置 ALLOWED_HOSTS
docker compose up -d --build
```

- 应用入口（Nginx）：http://127.0.0.1:8080/
- 健康检查：`GET /api/health/` 、`GET /api/health/ready/`
- HTTPS：将证书放入 `deploy/certs/`，按 `deploy/nginx.conf` 启用 443，并可设：
  - `SECURE_SSL_REDIRECT=True`
  - `SESSION_COOKIE_SECURE=True`
  - `CSRF_COOKIE_SECURE=True`
  - `SECURE_HSTS_SECONDS=31536000`

### 备份与恢复

```bash
# Linux / macOS / Git Bash
chmod +x scripts/*.sh
./scripts/backup.sh
./scripts/restore.sh backups/YYYYMMDD_HHMMSS

# Windows PowerShell
.\scripts\backup.ps1
```

备份内容：MySQL dump、`media/`、`data/chroma/`。建议配合 cron / 计划任务每日执行。

## 页面路由

| 路径 | 说明 |
|------|------|
| `/login/` `/register/` | 登录注册 |
| `/qa/` | 知识问答（会话历史 + 流式） |
| `/documents/` | 文档列表与审批 |
| `/upload/` | 上传（含进度条） |
| `/staff/` | 员工管理 |
| `/profile/` | 个人信息与改密 |

## 主要 API 清单

### 认证 ` /api/auth/ `

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/login/` | 登录，返回 JWT + user |
| POST | `/register/` | 注册普通用户 |
| POST | `/refresh/` | 刷新 token |
| POST | `/logout/` | 拉黑 refresh |
| GET | `/me/` | 当前用户 |
| POST | `/change-password/` | 修改密码 |
| GET | `/departments/` | 部门列表 |
| GET | `/staff/` | 员工列表（管理员） |
| GET/PATCH | `/staff/{id}/` | 员工详情/更新 |

### 文档 ` /api/documents/ `

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/` | 列表 / 上传 |
| GET/DELETE | `/{id}/` | 详情 / 删除 |
| POST | `/{id}/reprocess/` | 重处理 |
| POST | `/{id}/approve/` | 审批通过 |
| POST | `/{id}/reject/` | 驳回 |

### 问答 ` /api/qa/ `

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/ask/` | 问答；`stream=true` 为 SSE |
| GET | `/sessions/` | 会话列表 |
| GET/DELETE | `/sessions/{id}/` | 会话详情/删除 |

### 运维 ` /api/ `

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health/` | 存活 |
| GET | `/health/ready/` | DB/缓存就绪 |
| GET | `/audit/` | 审计日志（仅总管理员） |

## 限流（默认）

| 范围 | 速率 |
|------|------|
| 匿名 | 60/min |
| 登录用户 | 300/min |
| 登录/注册 | 20/min |
| 问答 | 30/min |
| 上传 | 20/min |

可通过环境变量 `THROTTLE_*` 调整。

## 日志与告警

- 结构化 JSON 日志：`logs/app.jsonl` + 控制台
- 文档处理失败：写失败状态 + 日志；若配置 `ALERT_WEBHOOK_URL` 则 POST 告警
- Celery 任务最多重试 3 次（非 eager 模式）

## 环境变量要点

见 `.env.example`。生产检查清单：

- [ ] `DEBUG=False`
- [ ] 强 `DJANGO_SECRET_KEY`
- [ ] `ALLOWED_HOSTS` 为真实域名
- [ ] MySQL / Redis 强密码
- [ ] `CELERY_TASK_ALWAYS_EAGER=False` 并启动 worker
- [ ] HTTPS 证书与反向代理 `X-Forwarded-Proto`
- [ ] 定期执行 `scripts/backup.sh`

## 项目结构（简）

```
smart_kbqa/
  apps/accounts|documents|qa|common
  config/          # settings, urls, celery, wsgi
  templates/       # 多页面工作台
  deploy/          # nginx + certs
  scripts/         # backup/restore
  docker-compose.yml
```

## 许可证

教学/演示用途，按需自行约定。
