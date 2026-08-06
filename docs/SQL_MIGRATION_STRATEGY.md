# SQL 脚本化管理方案

## 架构变更

```
Before (Alembic):                    After (SQL Scripts):
┌─────────────────────┐              ┌─────────────────────┐
│  ORM Models         │              │  ORM Models         │
│  ↓                  │              │  ↓                  │
│  Alembic            │              │  SQL Scripts        │
│  ↓                  │              │  (手动维护)          │
│  Auto Migration     │              │  ↓                  │
│  ↓                  │              │  DBA 审查            │
│  Database           │              │  ↓                  │
└─────────────────────┘              │  手动执行            │
                                     │  ↓                  │
                                     │  Database           │
                                     └─────────────────────┘
```

## 文件结构

```
docs/sql/
├── README.md                        # 使用指南
├── migrations/                      # 增量迁移脚本
│   └── V001__initial_schema.sql     # 初始 schema (22 张表)
├── schema_versions.sql              # 版本追踪表定义
└── current_orm_schema.sql           # 当前 ORM 导出（参考用）

scripts/
└── export_schema.py                 # ORM → SQL 导出工具
```

## 工作流程

### 开发阶段

```bash
# 1. 修改 ORM 模型
vim src/ai_platform/domain/models.py

# 2. 导出新 schema
make schema-export

# 3. 对比差异，编写增量 SQL
diff -u docs/sql/current_orm_schema.sql <(previous_version.sql)

# 4. 创建迁移脚本
vim docs/sql/migrations/V002__add_xxx.sql

# 5. 本地测试
psql $DATABASE_URL -f docs/sql/migrations/V002__add_xxx.sql
```

### 生产部署

```bash
# 1. 备份数据库
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql

# 2. DBA 审查 SQL
cat docs/sql/migrations/V002__add_xxx.sql

# 3. 执行迁移（需要确认）
make migrate-apply-prod file=V002__add_xxx.sql

# 4. 记录版本
psql $DATABASE_URL -c \
  "INSERT INTO schema_versions (version, description) VALUES ('V002', 'Add xxx column');"

# 5. 部署应用代码
docker build -t ai-platform:latest .
docker push ai-platform:latest
kubectl rollout restart deployment/ai-platform
```

## 迁移脚本模板

```sql
-- =============================================================================
-- V002: Add phone column to users table
-- Author: Zhang San
-- Date: 2026-08-05
-- Description: Add optional phone number field
-- Impact: Low - nullable column
-- Rollback: DROP INDEX idx_users_phone; ALTER TABLE users DROP COLUMN phone;
-- =============================================================================

-- Forward migration
ALTER TABLE users ADD COLUMN phone VARCHAR(20);
CREATE INDEX idx_users_phone ON users(phone) WHERE phone IS NOT NULL;

-- 记录版本（执行后手动运行）
-- INSERT INTO schema_versions (version, description, checksum)
-- VALUES ('V002', 'Add phone column', 'sha256:...');
```

## 关键差异

| 特性 | Alembic (旧) | SQL Scripts (新) |
|------|-------------|------------------|
| 迁移方式 | 自动执行 | 手动执行 |
| 审查流程 | 无 | DBA 审查 |
| 回滚能力 | 自动 downgrade | 手动 SQL |
| 版本追踪 | alembic_version 表 | schema_versions 表 |
| 生产安全 | 低风险（自动） | 高风险（人工） |
| 适用场景 | 小团队快速迭代 | 企业级 DBA 管控 |

## 安全检查

### 提交前

- [ ] SQL 语法正确
- [ ] 包含回滚 SQL
- [ ] 评估锁表风险
- [ ] 测试环境验证

### 生产执行前

- [ ] DBA 已审查
- [ ] 有完整备份
- [ ] 维护窗口已预约
- [ ] 相关团队已通知

## 常用命令

```bash
# 导出当前 ORM schema
make schema-export

# 查看所有迁移文件
make migrate-list

# 查看已应用的版本
make schema-status

# 应用迁移（开发/测试）
make migrate-apply file=V002__xxx.sql

# 应用迁移（生产）
make migrate-apply-prod file=V002__xxx.sql
```

## 为什么移除 Alembic？

1. **企业管控需求**：DBA 需要审查所有 schema 变更
2. **生产安全**：避免应用启动时自动执行未审查的 SQL
3. **灵活性**：支持复杂迁移（数据迁移、分区表等）
4. **审计追踪**：SQL 脚本更易读、易审查
5. **与 ORM 解耦**：ORM 可以变更，SQL 保持稳定

## 迁移到 SQL 脚本化

已完成：
- ✅ 移除 Alembic 依赖
- ✅ 导出初始 schema (V001)
- ✅ 创建 schema_versions 表定义
- ✅ 更新 entrypoint.sh（移除自动迁移）
- ✅ 更新 Makefile（添加 SQL 管理命令）
- ✅ 创建 export_schema.py 工具

下一步：
- [ ] 生产数据库执行 V001__initial_schema.sql
- [ ] 执行 schema_versions.sql 创建追踪表
- [ ] 记录 V001 到 schema_versions

## 参考资料

- [docs/sql/README.md](sql/README.md) - 详细使用指南
- [PostgreSQL DDL](https://www.postgresql.org/docs/current/ddl.html)
- [SQL 最佳实践](https://www.sqlstyle.guide/)
