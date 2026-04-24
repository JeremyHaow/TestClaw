# TestClaw

TestClaw 是一个可直接使用的全链路智能测试平台，包含 FastAPI 后端、Celery Worker、PostgreSQL/Redis，以及 Vue 3 管理后台。

## 当前功能

- 本地账号登录
- 仪表盘概览
- 任务创建与任务详情
- 用例库管理
- API 文档导入与解析
- 模型 Provider 管理
- 环境管理
- 基础 Agent 执行链路与任务状态记录

## 默认登录账号

- 用户名：`admin`
- 密码：`testclaw123`

可在 `.env` 中通过 `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` 修改。

## 本地开发

### 1. 准备环境变量

复制环境变量文件：

```bash
cp .env.example .env
```

填写至少以下关键配置：

- `FERNET_KEY`
- `SECRET_KEY`
- `DATABASE_URL`
- `REDIS_URL`

生成 Fernet Key：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. 安装后端依赖

```bash
uv sync
```

### 3. 安装前端依赖

```bash
cd frontend && npm install
```

### 4. 启动数据库与缓存

```bash
docker compose -f docker/docker-compose.yml up -d db redis
```

### 5. 启动后端 API

```bash
uvicorn app.main:app --reload
```

访问：`http://localhost:8000/docs`

### 6. 启动 Worker

```bash
celery -A app.worker.celery_app worker --loglevel=info
```

### 7. 启动前端

```bash
cd frontend && npm run dev -- --host 0.0.0.0
```

访问：`http://localhost:5173`

## 一键启动全栈

如果你已经准备好 `.env`，可以直接运行：

```bash
docker compose -f docker/docker-compose.yml up -d
```

这会启动：

- `db`
- `redis`
- `api`
- `worker`
- `frontend`

## 目录说明

- `app/`：FastAPI 后端
- `app/agent/`：LangGraph Agent
- `app/tools/`：测试工具集
- `app/api/v1/`：后端接口
- `frontend/`：Vue 3 管理后台
- `docker/`：容器启动配置
- `tests/`：后端测试
