# Database Schema Management

## 概述

本项目使用**纯 SQL 脚本**管理数据库 schema，不依赖 ORM 迁移工具。所有 schema 变更都通过手动编写和审查 SQL 脚本完成。

## 目录结构

```
docs/sql/
├── migrations/                    # 迁移脚本目录
│   ├── V001__initial_schema.sql   # 初始 schema（22 张表）
│   ├── V002__add_xxx.sql          # 后续变更
│   └── ...
├── schema_versions.sql            # 版本追踪表定义
└── README.md                      # 本文档
```

## 工作流程

### 1. 开发阶段

```bash
# 修改 ORM 模型后，导出新的 schema
make schema-export

# 对比差异，编写增量 SQL
diff -u docs/sql/migrations/V001__initial_schema.sql <(new_schema.sql) > V002__changes.sql

# 本地测试
psql $DATABASE_URL -f docs/sql/migrations/V002__changes.sql
```

### 2. 审查阶段

```bash
# 提交 SQL 给 DBA 审查
git add docs/sql/migrations/V002__*.sql
git commit -m "schema: V002 add xxx column"
git push

# DBA 审查要点：
# - 是否有索引？性能如何？
# - 是否会锁表？影响范围？
# - 数据量预估？执行时间？
# - 回滚方案？
```

### 3. 部署阶段

#### 开发/测试环境

```bash
# 直接执行
make migrate-apply file=V002__add_xxx.sql

# 或手动
psql $DATABASE_URL -f docs/sql/migrations/V002__add_xxx.sql

# 记录版本
psql $DATABASE_URL -c "INSERT INTO schema_versions (version, description) VALUES ('V002', 'Add xxx column');"
```

#### 生产环境

```bash
# 1. 备份数据库
pg_dump -h prod-db -U postgres ai_platform > backup_$(date +%Y%m%d_%H%M%S).sql

# 2. 执行迁移（需要确认）
make migrate-apply-prod file=V002__add_xxx.sql

# 3. 记录版本
psql $PROD_DATABASE_URL -c "INSERT INTO schema_versions (version, description) VALUES ('V002', 'Add xxx column');"

# 4. 验证
psql $PROD_DATABASE_URL -c "SELECT * FROM schema_versions ORDER BY applied_at DESC;"
```

## SQL 脚本命名规范

```
V<版本号>__<描述>.sql

示例：
V001__initial_schema.sql
V002__add_user_phone_column.sql
V003__create_audit_log_index.sql
V004__alter_conversations_add_status.sql
```

**规则**：
- 版本号递增（V001, V002, V003...）
- 描述使用小写 + 下划线
- 一个文件只做一件事
- 必须包含回滚 SQL（注释在文件末尾）

## SQL 脚本模板

```sql
-- =============================================================================
-- V002: Add phone column to users table
-- Author: Zhang San
-- Date: 2026-08-05
-- Description: Add optional phone number field for user contact
-- Impact: Low - nullable column, no default value
-- Estimated time: < 1 second
-- =============================================================================

-- Forward migration
ALTER TABLE users ADD COLUMN phone VARCHAR(20);
CREATE INDEX idx_users_phone ON users(phone) WHERE phone IS NOT NULL;

-- =============================================================================
-- ROLLBACK (run this if something goes wrong)
-- =============================================================================
-- DROP INDEX IF EXISTS idx_users_phone;
-- ALTER TABLE users DROP COLUMN IF EXISTS phone;
```

## 常用命令

```bash
# 查看所有迁移文件
make migrate-list

# 查看已应用的版本
make schema-status

# 导出当前 ORM schema（用于对比）
make schema-export

# 应用迁移（开发/测试）
make migrate-apply file=V002__xxx.sql

# 应用迁移（生产，需要确认）
make migrate-apply-prod file=V002__xxx.sql
```

## 安全检查清单

### 提交前

- [ ] SQL 语法正确（本地测试通过）
- [ ] 包含回滚 SQL（注释形式）
- [ ] 评估了锁表风险
- [ ] 评估了执行时间
- [ ] 考虑了数据量影响

### 生产执行前

- [ ] DBA 已审查
- [ ] 有完整备份
- [ ] 选择了维护窗口
- [ ] 通知了相关团队
- [ ] 准备了回滚方案

## 常见问题

### Q: 为什么不继续用 Alembic？

A: 企业级生产环境通常由 DBA 管控 schema 变更，需要：
- 人工审查 SQL
- 控制执行时机（维护窗口）
- 独立的备份和回滚流程
- 与 ORM 解耦，支持混合使用原生 SQL

### Q: 如何生成增量 SQL？

A: 
1. 修改 ORM 模型
2. 运行 `make schema-export` 导出新 schema
3. 用 diff 工具对比新旧 schema
4. 手动编写增量 SQL

### Q: 迁移失败怎么办？

A:
1. 执行回滚 SQL（在脚本末尾注释中）
2. 从备份恢复（最坏情况）
3. 分析失败原因，修复后重新执行

### Q: 多个迁移文件如何管理？

A:
- 按版本号顺序执行（V001 → V002 → V003...）
- 每个文件独立可执行
- 在 `schema_versions` 表中记录执行状态

## 参考资料

- [PostgreSQL ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html)
- [PostgreSQL CREATE INDEX](https://www.postgresql.org/docs/current/sql-createindex.html)
- [PostgreSQL 锁机制](https://www.postgresql.org/docs/current/explicit-locking.html)
