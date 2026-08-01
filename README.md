# AI Platform API

企业级 AI 中台后端服务 — 基于 FastAPI 构建，为 10-50 个内部业务系统提供标准化 AI 能力。

## 功能模块

| 模块 | 端点 | 说明 |
|------|------|------|
| 对话服务 | `/api/v1/chat/completions` | 多轮对话，支持 SSE 流式输出 |
| 知识问答 | `/api/v1/knowledge-bases/{id}/query` | RAG 检索增强生成 |
| Agent | `/api/v1/agents/{id}/run` | ReAct 智能体，工具调用 |
| 工作流 | `/api/v1/workflows/{id}/execute` | DAG 编排，状态持久化 |
| 提示词 | `/api/v1/prompts/{id}/render` | 模板管理，版本控制 |
| 成本管理 | `/api/v1/costs/` | 用量归因，预算告警 |

## 技术栈

- **Web 框架**: FastAPI + Uvicorn
- **数据库**: PostgreSQL (Neon.tech)
- **缓存**: Redis (Upstash)
- **向量库**: Milvus (Zilliz Cloud)
- **搜索引擎**: OpenSearch (Bonsai)
- **对象存储**: Backblaze B2

## 本地开发

```bash
# 安装依赖
uv pip install -e ".[dev]"

# 复制配置
cp config/ai-platform-db.yaml.example config/ai-platform-db.yaml
# 编辑填入真实连接信息

# 启动服务
uvicorn ai_platform.main:app --reload --port 8000
```

## 部署

### Railway

1. Fork 此仓库
2. 在 Railway 创建新项目 → Deploy from GitHub
3. 添加环境变量（参考 `.env.example`）
4. 自动构建部署

### Docker

```bash
docker build -t ai-platform-api .
docker run -p 8000:8000 --env-file .env ai-platform-api
```

## 项目结构

```
├── api/              # 接入层（路由 + 中间件）
├── services/         # 应用服务层（业务编排）
├── core/             # 核心引擎层（纯技术实现）
├── domain/           # 领域层（ORM + 事件）
├── infra/            # 基础设施层（DB/Cache/Storage）
├── observability/    # 链路追踪 + 指标 + 日志
└── config/           # 配置模板
```
