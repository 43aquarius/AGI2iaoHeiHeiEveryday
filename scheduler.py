# -*- coding: utf-8 -*-
"""常驻定时调度器(容器环境无 cron,用本进程替代)

时间表(北京时间):
  - 每日 09:00 → 日榜 (UTC 01:00)
  - 每周一 10:00 → 周榜 (UTC 周一 02:00)

用法:
  nohup python3 scheduler.py >/dev/null 2>&1 &   # 或用 run_scheduler.sh
日志: scheduler.log (同目录)
"""
import datetime
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "scheduler.log")
MAX_LOG_BYTES = 5 * 1024 * 1024


def log(msg: str):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > MAX_LOG_BYTES:
            with open(LOG, "w") as f:  # 轮转
                f.write("")
        with open(LOG, "a") as f:
            f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def next_run(period: str) -> datetime.datetime:
    """下一次运行时刻(UTC)。北京 09:00 = UTC 01:00;北京周一 10:00 = UTC 周一 02:00"""
    now = datetime.datetime.now(datetime.timezone.utc)
    if period == "daily":
        t = now.replace(hour=1, minute=0, second=0, microsecond=0)
        if t <= now:
            t += datetime.timedelta(days=1)
    else:  # weekly: 每周一北京时间 10:00
        t = now.replace(hour=2, minute=0, second=0, microsecond=0)
        days_ahead = (0 - t.weekday()) % 7  # Monday == 0
        t += datetime.timedelta(days=days_ahead)
        if t <= now:
            t += datetime.timedelta(days=7)
    return t


def run_task(period: str):
    log(f"===== 开始 {period} 任务 =====")
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(BASE, "main.py"),
             "--period", period],
            capture_output=True, text=True, timeout=3600, cwd=BASE)
        tail_out = (r.stdout or "")[-2500:]
        tail_err = (r.stderr or "")[-800:]
        log(f"{period} 退出码 {r.returncode}")
        for line in tail_out.strip().splitlines()[-12:]:
            log(f"  | {line}")
        if tail_err.strip():
            log(f"  stderr: {tail_err.strip()[-500:]}")
    except Exception as e:
        log(f"{period} 任务异常: {e}")


def main():
    log("调度器启动:每日北京 09:00 日榜 / 每周一北京 10:00 周榜 "
        "(容器内常驻进程,掉线由 run_scheduler.sh 拉起)")
    while True:
        tasks = [(next_run("daily"), "daily"), (next_run("weekly"), "weekly")]
        tasks.sort()
        target, period = tasks[0]
        wait = (target - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        log(f"下一次: {period} @ {target.astimezone().strftime('%Y-%m-%d %H:%M')} "
            f"(约 {wait / 3600:.1f} 小时后)")
        time.sleep(max(wait, 5))
        run_task(period)
        time.sleep(90)  # 跳过当前分钟,避免边界重复触发


if __name__ == "__main__":
    main()
