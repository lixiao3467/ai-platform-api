# SQL 审核平台选型

## 为什么需要平台？

```
手动流程（当前）:
开发 → 发邮件/钉钉 → DBA → psql 手动执行 → 记录 Excel

平台流程（推荐）:
开发 → 提交到平台 → DBA 在线审查 → 一键执行 → 自动记录 → 自动通知
```

## 主流平台对比

### 1. Bytebase（推荐 ⭐⭐⭐⭐⭐）

**官网**：https://www.bytebase.com

**特点**：
- 🌟 开源免费（社区版）
- 🎯 专为 SQL 审核设计
- 🔍 自动 SQL 语法检查
- 👥 多环境管理（dev/staging/prod）
- 📝 完整的审批工作流
- 🔐 RBAC 权限控制
- 📊 变更历史记录
- 🔄 支持回滚
- 🐘 支持 PostgreSQL / MySQL / 等等

**工作流程**：
```
1. 开发登录 Bytebase Web UI
2. 选择目标数据库（production）
3. 粘贴 SQL 或上传文件
4. 填写变更说明
5. 提交审批
6. DBA 收到通知，在线审查
7. DBA 点击"批准"或"拒绝"
8. 批准后，选择维护窗口执行
9. 自动执行 + 自动记录
10. 通知开发部署代码
```

**部署**：
```bash
# Docker 一键部署
docker run -d \
  --name bytebase \
  -p 5678:5678 \
  -v bytebase_data:/var/lib/bytebase/data \
  bytebase/bytebase

# 访问 http://localhost:5678
```

**优势**：
- ✅ 开发友好（Web UI，不用记命令）
- ✅ DBA 友好（在线审查，不用下载文件）
- ✅ 自动化（审批、执行、记录全流程）
- ✅ 安全（权限控制、审计日志）

---

### 2. Yearning（国产推荐 ⭐⭐⭐⭐）

**官网**：https://yearning.io

**特点**：
- 🇨🇳 国产开源
- 🎯 SQL 审核 + 执行
- 📊 支持 MySQL / PostgreSQL
- 👥 多租户
- 🔍 SQL 语法检查
- 📝 工单审批流
- 📈 性能分析

**部署**：
```bash
docker run -d \
  -p 8000:8000 \
  -e HOST=127.0.0.1 \
  -e MYSQL_USER=root \
  -e MYSQL_PASSWORD=*** \
  -e MYSQL_ADDR=mysql:3306 \
  -e MYSQL_DB=yearning \
  registry.cn-hangzhou.aliyuncs.com/cookie/yearning:latest
```

---

### 3. Archery（国产 ⭐⭐⭐⭐）

**官网**：https://archerydms.com

**特点**：
- 🇨🇳 国产开源
- 🎯 集成了多种数据库管理功能
- 📊 SQL 审核 + 慢查询分析
- 🔐 权限管理
- 📝 工单系统

---

### 4. Flyway Enterprise（商业 ⭐⭐⭐）

**官网**：https://flywaydb.org

**特点**：
- 💰 商业版有 Web UI
- 🎯 Java 生态
- 📝 版本控制
- 🔄 自动迁移

**缺点**：
- ❌ 社区版没有 Web UI
- ❌ 企业版价格贵

---

### 5. Liquibase Ops（商业 ⭐⭐⭐）

**官网**：https://www.liquibase.com

**特点**：
- 💰 商业产品
- 🎯 企业级
- 📊 完整的变更管理

**缺点**：
- ❌ 价格贵
- ❌ 重

---

## 推荐方案：Bytebase

### 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Bytebase Platform                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────┐                                                      │
│  │ 开发      │ ──→ 提交 SQL + 说明                                  │
│  └──────────┘                                                      │
│       ↓                                                              │
│  ┌──────────┐                                                      │
│  │ 审批流    │ ──→ DBA 审查（通过/拒绝）                             │
│  └──────────┘                                                      │
│       ↓                                                              │
│  ┌──────────┐                                                      │
│  │ 执行引擎  │ ──→ 选择维护窗口 → 执行 → 验证 → 记录                 │
│  └──────────┘                                                      │
│       ↓                                                              │
│  ┌──────────┐                                                      │
│  │ 通知      │ ──→ 钉钉/邮件/飞书 → 通知开发部署                     │
│  └──────────┘                                                      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
              ↓
    ┌─────────────────┐
    │   Databases     │
    ├─────────────────┤
    │ dev_db          │
    │ staging_db      │
    │ prod_db         │
    └─────────────────┘
```

### 集成到我们的项目

```bash
# 1. 部署 Bytebase
docker-compose -f docker-compose.bytebase.yml up -d

# 2. 配置数据库连接
# 在 Bytebase UI 中添加：
# - dev_db
# - staging_db
# - prod_db

# 3. 开发使用流程
# - 登录 http://bytebase.company.com
# - 选择 prod_db
# - 粘贴 SQL
# - 提交审批

# 4. DBA 使用流程
# - 收到审批通知
# - 在线审查 SQL
# - 点击"批准"
# - 选择维护窗口执行
```

### 自定义脚本（可选）

如果不想引入平台，也可以自己写一个简单的：

```python
# scripts/sql_review_platform.py
# 简单的 Flask Web UI，让 DBA 在线审查和执行 SQL

from flask import Flask, render_template, request
import subprocess

app = Flask(__name__)

@app.route('/submit', methods=['POST'])
def submit_sql():
    sql = request.form['sql']
    description = request.form['description']
    # 保存到数据库，等待 DBA 审查
    return "提交成功，等待 DBA 审查"

@app.route('/review/<id>')
def review_sql(id):
    # DBA 审查页面
    return render_template('review.html', sql=sql)

@app.route('/approve/<id>', methods=['POST'])
def approve_sql(id):
    # DBA 批准后执行
    subprocess.run(['psql', db_url, '-f', f'migrations/{id}.sql'])
    return "执行成功"
```

---

## 对比总结

| 方案 | 成本 | 易用性 | 功能 | 推荐度 |
|------|------|--------|------|--------|
| **Bytebase** | 免费 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Yearning | 免费 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| Archery | 免费 | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| Flyway Enterprise | 贵 | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| Liquibase Ops | 贵 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| 自建平台 | 人力成本 | ⭐⭐ | ⭐⭐ | ⭐⭐ |
| 手动 psql | 免费 | ⭐ | ⭐ | ⭐ |

---

## 实施建议

### 小团队（< 20 人）
- 当前手动流程够用
- 或者用 Yearning（轻量）

### 中型团队（20-100 人）
- **强烈推荐 Bytebase**
- 或者 Yearning / Archery

### 大型团队（> 100 人）
- Bytebase Enterprise
- 或者自建平台

---

## 下一步

1. **部署 Bytebase**（推荐）
   ```bash
   docker run -d -p 5678:5678 bytebase/bytebase
   ```

2. **配置数据库连接**
   - dev / staging / prod

3. **制定使用规范**
   - 开发必须通过 Bytebase 提交 SQL
   - DBA 必须在 Bytebase 审查和执行

4. **集成通知**
   - 钉钉/飞书/邮件通知

需要我帮你部署 Bytebase 吗？
