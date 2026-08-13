#!/bin/sh
# AVDB-SERVER 启动入口
# 以 root 启动，修复 /app/data 权限后降权到 appuser 执行

# 确保 data 目录存在且属主正确；/ms-playwright 构建期保持 root 属主（避免重建大层），在此运行时 chown
mkdir -p /app/data/images /app/data/backups
chown -R appuser:appuser /app/data /ms-playwright

# 降权执行实际启动命令
exec su appuser -s /bin/sh -c "$*"
