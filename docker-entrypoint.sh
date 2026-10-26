#!/bin/sh
# 容器入口：先把数据目录交还给 app 用户，再降权运行。
# 这样宿主机 ./data 无论属于谁（Linux 上常见问题），容器内都能读写。
set -e

DATA_DIR="${WHUT_DATA_DIR:-/app/data}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R app:app "$DATA_DIR" 2>/dev/null || true
  if [ -f /app/config.json ]; then
    chown app:app /app/config.json 2>/dev/null || true
  fi
  # setpriv 只换 uid/gid，不会改 HOME；不设置的话 ~ 会展开成 /root
  export HOME=/home/app
  exec setpriv --reuid=10001 --regid=10001 --clear-groups "$@"
fi

exec "$@"
