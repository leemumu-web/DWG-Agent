-- DWG-Agent Platform — MySQL 初始化脚本
-- Docker Compose 首次启动时自动执行（/docker-entrypoint-initdb.d/）
-- MySQL 8.4, utf8mb4, 严格模式

-- 强制 utf8mb4
ALTER DATABASE dwg_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Excel Final 只读五金手册库。数据由随后执行的
-- 02-hardware-handbook.sql 导入；先建库以便授予最小权限。
CREATE DATABASE IF NOT EXISTS hardware_handbook
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 应用用户授权（库已由 MYSQL_DATABASE 环境变量创建）
GRANT ALL PRIVILEGES ON dwg_agent.* TO 'dwg_user'@'%';
GRANT SELECT ON hardware_handbook.* TO 'dwg_user'@'%';
FLUSH PRIVILEGES;
