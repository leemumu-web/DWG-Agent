-- DWG-Agent Platform — MySQL 初始化脚本
-- Docker Compose 首次启动时自动执行（/docker-entrypoint-initdb.d/）
-- MySQL 8.4, utf8mb4, 严格模式

-- 强制 utf8mb4
ALTER DATABASE dwg_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 应用用户授权（库已由 MYSQL_DATABASE 环境变量创建）
GRANT ALL PRIVILEGES ON dwg_agent.* TO 'dwg_user'@'%';
FLUSH PRIVILEGES;
