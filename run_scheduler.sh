#!/bin/bash
# 启动常驻调度器(带自动重启)。停掉: kill $(cat scheduler.pid)
cd "$(dirname "$0")"

# 已在运行则不重复启动
if [ -f scheduler.pid ] && kill -0 "$(cat scheduler.pid)" 2>/dev/null; then
    echo "调度器已在运行 (pid $(cat scheduler.pid))"
    exit 0
fi

nohup bash -c 'while true; do python3 scheduler.py; echo "[$(date)] scheduler 退出,30秒后重启" >> scheduler.log; sleep 30; done' \
    >/dev/null 2>&1 &
echo $! > scheduler.pid
echo "调度器已启动 (watchdog pid $(cat scheduler.pid))，日志: scheduler.log"
