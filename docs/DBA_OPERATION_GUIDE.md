# DBA 操作手册 - SQL 迁移执行流程

## 场景：开发提交 V002__add_user_phone.sql

### 1. 收到 SQL 文件

```sql
-- 开发发来的文件：docs/sql/migrations/V002__add_user_phone.sql

-- =============================================================================
-- V002: Add phone column to users table
-- Author: Zhang San
-- Date: 2026-08-05
-- Description: Add optional phone number field for user contact
-- Impact: Low - nullable column, no default value
-- Estimated time: < 1 second
-- Rollback: DROP INDEX idx_users_phone; ALTER TABLE users DROP COLUMN phone;
-- =============================================================================

-- Forward migration
ALTER TABLE users ADD COLUMN phone VARCHAR(20);
CREATE INDEX idx_users_phone ON users(phone) WHERE phone IS NOT NULL;
```

### 2. DBA 审查清单

```bash
# 检查文件是否存在
ls -lh docs/sql/migrations/V002__add_user_phone.sql

# 查看文件内容
cat docs/sql/migrations/V002__add_user_phone.sql

# 检查语法（使用 pg_hint_plan 或 EXPLAIN）
psql -d ai_platform_test -f docs/sql/migrations/V002__add_user_phone.sql --dry-run
```

**审查要点**：
- [ ] 是否有 `IF NOT EXISTS` 防护？
- [ ] 索引是否合理？（字段选择、WHERE 条件）
- [ ] 是否会锁表？（ALTER TABLE 会锁表）
- [ ] 数据量多大？执行时间预估？
- [ ] 回滚 SQL 是否正确？

### 3. 测试环境验证

```bash
# 连接测试数据库
export DATABASE_URL="postgresql://user:pass@test-db:5432/ai_platform_test"

# 执行迁移
psql $DATABASE_URL -f docs/sql/migrations/V002__add_user_phone.sql

# 验证结果
psql $DATABASE_URL << 'SQL'
-- 检查列是否存在
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'phone';

-- 检查索引
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'users' AND indexname = 'idx_users_phone';

-- 测试查询
EXPLAIN ANALYZE SELECT * FROM users WHERE phone = '13800138000';
SQL

# 记录版本
psql $DATABASE_URL -c "INSERT INTO schema_versions (version, description, checksum) VALUES ('V002', 'Add phone column', '$(sha256sum docs/sql/migrations/V002__add_user_phone.sql | cut -d' ' -f1)');"
```

### 4. 生产环境准备

```bash
# 4.1 预约维护窗口（例如：凌晨 2:00-3:00）
# 通知相关团队：运维、开发、产品

# 4.2 备份数据库
pg_dump -h prod-db -U postgres ai_platform > backup_$(date +%Y%m%d_%H%M%S).sql

# 验证备份
pg_restore --list backup_20260805_020000.sql | head -20
```

### 5. 生产环境执行

```bash
# 5.1 连接生产数据库
export DATABASE_URL="postgresql://dba_user:***@prod-db:5432/ai_platform_prod"

# 5.2 最后一次确认
psql $DATABASE_URL -c "SELECT version FROM schema_versions ORDER BY applied_at DESC LIMIT 1;"
# 应该显示 V001

# 5.3 执行迁移
echo "开始执行 V002..."
time psql $DATABASE_URL -f docs/sql/migrations/V002__add_user_phone.sql

# 预期输出：
# ALTER TABLE
# CREATE INDEX
# 执行时间：0.234 秒

# 5.4 验证成功
psql $DATABASE_URL << 'SQL'
-- 检查列
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'phone';

-- 检查索引
SELECT indexname FROM pg_indexes WHERE indexname = 'idx_users_phone';

-- 检查表大小
SELECT pg_size_pretty(pg_total_relation_size('users'));
SQL

# 5.5 记录版本
psql $DATABASE_URL -c "INSERT INTO schema_versions (version, description, checksum, execution_time_ms) VALUES ('V002', 'Add phone column', '$(sha256sum docs/sql/migrations/V002__add_user_phone.sql | cut -d' ' -f1)', 234);"

# 5.6 确认版本
psql $DATABASE_URL -c "SELECT * FROM schema_versions ORDER BY applied_at DESC;"
```

### 6. 通知开发部署应用

```bash
# 发送邮件/钉钉通知
echo "数据库迁移完成，可以部署应用代码了"
```

### 7. 故障回滚（如果执行失败）

```bash
# 7.1 检查错误日志
# 如果 ALTER TABLE 失败，数据库会自动回滚

# 7.2 如果需要手动回滚
psql $DATABASE_URL << 'SQL'
-- 执行回滚 SQL（从迁移文件末尾注释中提取）
DROP INDEX IF EXISTS idx_users_phone;
ALTER TABLE users DROP COLUMN IF EXISTS phone;

-- 删除版本记录
DELETE FROM schema_versions WHERE version = 'V002';
SQL

# 7.3 验证回滚
psql $DATABASE_URL -c "SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'phone';"
# 应该返回空

# 7.4 如果数据损坏，从备份恢复
pg_restore -h prod-db -U postgres -d ai_platform backup_20260805_020000.sql
```

---

## 常用检查命令

```bash
# 查看当前版本
psql $DATABASE_URL -c "SELECT * FROM schema_versions ORDER BY applied_at DESC;"

# 查看表结构
psql $DATABASE_URL -c "\d users"

# 查看索引
psql $DATABASE_URL -c "SELECT * FROM pg_indexes WHERE tablename = 'users';"

# 查看表大小
psql $DATABASE_URL -c "SELECT pg_size_pretty(pg_total_relation_size('users'));"

# 查看锁
psql $DATABASE_URL -c "SELECT * FROM pg_locks WHERE NOT granted;"

# 查看长事务
psql $DATABASE_URL -c "SELECT * FROM pg_stat_activity WHERE state = 'active' AND query_start < now() - interval '5 minutes';"
```

---

## 风险提示

| 操作 | 风险等级 | 说明 |
|------|---------|------|
| `ADD COLUMN` (nullable) | 🟢 低 | 不锁表，瞬间完成 |
| `ADD COLUMN` (NOT NULL) | 🟡 中 | 需要全表扫描，可能锁表 |
| `CREATE INDEX` | 🟡 中 | 会锁表，大表需要 `CONCURRENTLY` |
| `DROP COLUMN` | 🟡 中 | 会锁表 |
| `ALTER COLUMN TYPE` | 🔴 高 | 会锁表，大表可能需要重写 |
| `DROP TABLE` | 🔴 高 | 数据丢失，需要备份 |

**大表操作建议**：
```sql
-- 创建索引（不锁表）
CREATE INDEX CONCURRENTLY idx_name ON users(email);

-- 添加列（PostgreSQL 11+ 不锁表）
ALTER TABLE users ADD COLUMN phone VARCHAR(20);
```

---

## 检查清单

### 执行前
- [ ] 备份数据库
- [ ] 测试环境验证通过
- [ ] 维护窗口已预约
- [ ] 相关团队已通知
- [ ] 回滚方案已准备

### 执行后
- [ ] 验证列/索引存在
- [ ] 验证查询性能
- [ ] 记录到 schema_versions
- [ ] 通知开发部署
- [ ] 监控错误日志
