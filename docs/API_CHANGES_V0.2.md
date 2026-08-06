# API 变更文档 — v0.2.0 (Enterprise Features)

> 发布日期：2026-08-05
> 目标：支持前端和企业级需求，完成 P0/P1 任务

---

## 📋 目录

- [1. 认证增强 — JWT Refresh Token](#1-认证增强)
- [2. 审计日志 API](#2-审计日志-api)
- [3. 数据导出接口](#3-数据导出接口)
- [4. SSO/SAML 集成框架](#4-sso集成框架)
- [5. API Key 管理](#5-api-key-管理)
- [6. 指标查询接口](#6-指标查询接口)
- [7. OpenAPI 文档增强](#7-openapi-文档增强)
- [8. 数据库迁移](#8-数据库迁移)

---

## 1. 认证增强

### 变更概览

| 变更项 | 旧值 | 新值 |
|--------|------|------|
| Access Token 有效期 | 1440 min (24h) | 30 min |
| Refresh Token | 无 | 7 天 |
| 登出功能 | 无 | ✅ 撤销 Refresh Token |

### 新端点

#### `POST /api/v1/auth/refresh`

**功能**：使用 Refresh Token 换取新的 Access Token + Refresh Token（Rotation 策略）。

**请求体**：
```json
{
  "refresh_token": "eyJhbGc..."
}
```

**响应**：
```json
{
  "code": 0,
  "data": {
    "token": "eyJhbGc...(新 Access Token)",
    "refresh_token": "eyJhbGc...(新 Refresh Token)",
    "expires_in": 1800
  },
  "message": "ok"
}
```

**错误码**：
| 状态码 | 说明 |
|--------|------|
| 401 | Refresh Token 无效、已过期或已被撤销 |

#### `POST /api/v1/auth/logout`

**功能**：撤销 Refresh Token，使其无法再用于刷新。

**请求体**：
```json
{
  "refresh_token": "eyJhbGc..."
}
```

#### `POST /api/v1/auth/login` (变更)

**响应新增字段**：
```json
{
  "data": {
    "token": "...",
    "refresh_token": "...",     // ← 新增
    "expires_in": 1800,         // ← 新增（秒）
    "user": {...}
  }
}
```

### 前端变更

- `src/contexts/auth.ts`：新增 `refreshToken` 状态、`updateTokens()` 方法
- `src/api/client.ts`：响应拦截器自动在 401 时静默刷新 Token
- `src/pages/Login.tsx`：登录成功后保存 Refresh Token
- Refresh 队列机制：并发请求只触发一次刷新，其他请求等待新 Token

---

## 2. 审计日志 API

### `GET /api/v1/audit-logs/`

**功能**：分页查询审计日志。

**查询参数**：
| 参数 | 类型 | 说明 |
|------|------|------|
| `page` | int | 页码（默认 1） |
| `page_size` | int | 每页条数（默认 20，最大 100） |
| `user_id` | string | 按操作人过滤 |
| `action` | string | 按操作类型（如 `chat.post`, `agent.delete`） |
| `resource_type` | string | 按资源类型（如 `agent`, `conversation`） |
| `resource_id` | string | 按资源 ID 过滤 |
| `start_time` | datetime | 开始时间（ISO 8601） |
| `end_time` | datetime | 结束时间 |
| `response_code_min` | int | 最小响应码（如 400） |
| `response_code_max` | int | 最大响应码（如 499） |

**响应**：
```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "id": 12345,
        "action": "chat.post",
        "resource_type": "chat",
        "response_code": 200,
        "latency_ms": 1234,
        "user_id": "user-uuid",
        "ip_address": "1.2.3.4",
        "trace_id": "trace-uuid",
        "created_at": "2026-08-05T10:30:00+00:00"
      }
    ],
    "total": 1234,
    "page": 1,
    "page_size": 20
  }
}
```

### `GET /api/v1/audit-logs/stats`

**功能**：获取审计日志统计数据。

### `GET /api/v1/audit-logs/actions`

**功能**：返回所有已记录的操作类型及可读标签。

---

## 3. 数据导出接口

### `GET /api/v1/conversations/{id}/messages/export`

**功能**：导出指定会话的所有消息。

**参数**：
| 参数 | 说明 |
|------|------|
| `format` | `csv` 或 `json`（默认 csv） |

**响应**：文件下载（`Content-Disposition: attachment`）

**特性**：
- ✅ 流式输出（不占用大量内存）
- ✅ CSV 带 BOM（Excel 兼容）
- ✅ 大数据量分页读取（每页 500 条）

### `GET /api/v1/costs/export`

**功能**：导出每日成本明细。

**参数**：
| 参数 | 说明 |
|------|------|
| `days` | 最近 N 天（默认 30） |
| `format` | `csv` 或 `json` |
| `app_id` | 按应用过滤 |

### `POST /api/v1/evaluations/{run_id}/export`

**状态**：501 — 评估结果持久化尚未实现，预留接口。

---

## 4. SSO 集成框架

### 支持的身份提供者类型

| 类型 | 标识符 | 状态 |
|------|--------|------|
| OpenID Connect | `oidc` | ✅ 授权 URL 生成完成 |
| OAuth2 | `oauth2` | ✅ 授权 URL 生成完成 |
| 飞书 | `feishu` | ✅ 授权 URL 生成完成 |
| 钉钉 | `dingtalk` | ✅ 授权 URL 生成完成 |
| 企业微信 | `wecom` | ✅ 授权 URL 生成完成 |
| SAML | `saml` | 🟡 预留接口 |

### 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/sso/providers` | 获取 SSO 提供者列表 |
| POST | `/api/v1/sso/providers` | 创建 SSO 提供者 |
| PUT | `/api/v1/sso/providers/{id}` | 更新 SSO 提供者 |
| DELETE | `/api/v1/sso/providers/{id}` | 删除 SSO 提供者 |
| GET | `/api/v1/sso/providers/{id}/authorize` | 发起授权（返回授权 URL） |
| GET | `/api/v1/sso/callback/{name}` | OAuth 回调处理（TODO） |

**安全**：`client_secret` 使用 AES-256-GCM 加密存储，API 响应中不返回。

---

## 5. API Key 管理

### 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/api-keys/` | 列出所有 API Key |
| POST | `/api/v1/api-keys/` | 创建 API Key（返回原始密钥，仅此一次） |
| PUT | `/api/v1/api-keys/{id}` | 更新名称、权限、速率限制 |
| DELETE | `/api/v1/api-keys/{id}` | 删除 API Key |
| POST | `/api/v1/api-keys/{id}/toggle?enabled=true\|false` | 启用/禁用 |
| GET | `/api/v1/api-keys/{id}/stats` | 使用统计（24h/7d/30d） |

### API Key 格式

- 前缀：`aiplat_`
- 长度：约 60 字符
- 存储：SHA-256 哈希（数据库），Redis 缓存 5 分钟
- 启用/禁用：通过 `is_enabled` 字段控制

### 创建示例

**请求**：
```json
POST /api/v1/api-keys/
{
  "app_id": "app-uuid",
  "name": "Production Backend",
  "permissions": ["chat.read", "knowledge.read"],
  "rate_limit": 5000,
  "expires_at": "2027-01-01T00:00:00Z"
}
```

**响应**（原始密钥只返回这一次）：
```json
{
  "data": {
    "id": "key-uuid",
    "key": "aiplat_aBcDeFgHiJkLmNoPqRsTuVwXyZ...",
    "key_prefix": "aiplat_aB",
    "name": "Production Backend",
    ...
  }
}
```

---

## 6. 指标查询接口

### `GET /api/v1/metrics/system`

系统指标：CPU、内存、磁盘、运行时长。

```json
{
  "data": {
    "cpu_usage_percent": 45.2,
    "memory_usage_percent": 68.5,
    "memory_total_bytes": 17179869184,
    "memory_used_bytes": 11777433600,
    "disk_usage_percent": 42.3,
    "uptime_seconds": 86400,
    "timestamp": "2026-08-05T10:30:00+00:00"
  }
}
```

### `GET /api/v1/metrics/api?minutes=60`

API 性能指标：QPS、延迟分位数、错误率。

```json
{
  "data": {
    "qps": 12.5,
    "avg_latency_ms": 234.5,
    "p50_latency_ms": 187.6,
    "p95_latency_ms": 469.0,
    "p99_latency_ms": 820.8,
    "error_rate_percent": 1.2,
    "total_requests": 45000
  }
}
```

### `GET /api/v1/metrics/models?minutes=1440`

各模型使用指标：调用量、成功率、Token 消耗、费用。

```json
{
  "data": {
    "models": [
      {
        "model": "gpt-4o",
        "provider": "openai",
        "total_requests": 1234,
        "success_rate": 99.2,
        "avg_latency_ms": 1234,
        "total_input_tokens": 5000000,
        "total_output_tokens": 2000000,
        "estimated_cost_usd": 32.50
      }
    ]
  }
}
```

---

## 7. OpenAPI 文档增强

- ✅ Swagger UI 可用：`/docs`（开发环境）
- ✅ ReDoc 可用：`/redoc`
- ✅ OpenAPI JSON：`/openapi.json`
- ✅ 所有端点添加中文描述
- ✅ 所有端点添加 `summary` 和 `description`
- ✅ 错误码统一说明
- ✅ Tags 分组：17 个模块标签
- ✅ 生产环境自动禁用文档端点

---

## 8. 数据库迁移

**文件**：`docs/sql/migrations/V002__enterprise_features.sql`

**变更**：
1. 新增 `sso_providers` 表（含唯一索引、触发器）
2. `api_keys` 表新增 `is_enabled` 列
3. `audit_logs` 表新增 5 个查询优化索引

**执行**：
```bash
psql -f docs/sql/migrations/V002__enterprise_features.sql
```

---

## 🧪 测试

**前端**：158 个测试全部通过 ✅
**类型检查**：0 errors ✅
**Lint**：0 errors（12 个预存 warnings） ✅
**后端**：Python AST 语法检查通过 ✅

---

## 📂 变更文件清单

### 后端（`ai-platform-api`）

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/ai_platform/config.py` | 修改 | 新增 `jwt_refresh_expire_days` 配置 |
| `src/ai_platform/main.py` | 修改 | 增强 OpenAPI 元数据、标签、文档描述 |
| `src/ai_platform/api/middleware/auth.py` | 修改 | 新增 `create_refresh_token()`, `revoke_refresh_token()`, `is_refresh_token_revoked()` |
| `src/ai_platform/api/v1/router.py` | 修改 | 注册 audit-logs, sso, api-keys, metrics 路由 |
| `src/ai_platform/api/v1/users.py` | 修改 | 登录返回 refresh_token，新增 /refresh、/logout 端点 |
| `src/ai_platform/api/v1/conversations.py` | 修改 | 新增 /messages/export 端点 |
| `src/ai_platform/api/v1/costs.py` | 修改 | 新增 /export 端点 |
| `src/ai_platform/api/v1/evaluations.py` | 修改 | 新增 /{run_id}/export 端点（501 占位） |
| `src/ai_platform/api/v1/audit_logs.py` | **新增** | 审计日志查询、统计、操作类型列表 |
| `src/ai_platform/api/v1/sso.py` | **新增** | SSO 提供者 CRUD + 授权流程框架 |
| `src/ai_platform/api/v1/api_keys.py` | **新增** | API Key CRUD + 使用统计 |
| `src/ai_platform/api/v1/metrics_api.py` | **新增** | 系统/API/模型指标查询 |
| `src/ai_platform/api/export_utils.py` | **新增** | 流式 CSV/JSON 导出工具 |
| `src/ai_platform/domain/models.py` | 修改 | 新增 `SsoProvider` 模型，`ApiKey.is_enabled` 字段 |
| `docs/sql/migrations/V002__enterprise_features.sql` | **新增** | 数据库迁移 |

### 前端（`ai-platform-web`）

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/types/index.ts` | 修改 | 新增 `RefreshResponse`, `AuditLogEntry`, `SsoProvider`, `ApiKeyItem`, `SystemMetrics`, `ApiMetrics`, `ModelMetrics` 类型 |
| `src/contexts/auth.ts` | 修改 | 新增 refresh token 存储、`updateTokens()`、`getRefreshToken()`、`willExpireSoon()` |
| `src/api/client.ts` | 修改 | 响应拦截器自动静默刷新、刷新队列防并发 |
| `src/pages/Login.tsx` | 修改 | 登录成功后保存 refresh token |
| `src/test/utils.tsx` | 修改 | 测试辅助函数支持 refresh token |
| `src/contexts/__tests__/auth.test.ts` | 修改 | 测试用例适配新 login 签名 |
| `src/pages/__tests__/Login.test.tsx` | 修改 | Mock 数据包含 refresh_token |

---

## 🔜 后续工作（不在本次范围）

- [ ] 评估结果持久化（Evaluation → DB）
- [ ] SSO 回调完整实现（code → token → user info 交换）
- [ ] SAML 2.0 授权流程
- [ ] 前端审计日志页面
- [ ] 前端 SSO 配置页面
- [ ] 前端 API Key 管理页面
- [ ] 前端指标 Dashboard
- [ ] 数据导出按钮接入（Dashboard 导出 stub）
- [ ] WebSocket 实时通知
