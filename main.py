# -*- coding: utf-8 -*-
"""GitHub 热榜 → 小黑盒图文帖 主流程
用法:
    python main.py --period daily   [--no-image] [--dry-run] [--force]
    python main.py --period weekly  ...
"""
import argparse
import datetime
import json
import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from github_trending import (fetch_trending, enrich_details,
                              fetch_readme_excerpts)
from summarizer import summarize_repos
from image_maker import render_long_image
from heybox_client import HeyboxClient
from heybox_publisher import HeyboxPublisher

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
STATE_PATH = os.path.join(BASE_DIR, "state.json")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def already_posted(period: str) -> bool:
    """当日/当周是否已发布过"""
    state = load_state()
    key = f"last_{period}"
    last = state.get(key, "")
    today = datetime.date.today()
    if period == "daily":
        return last == today.isoformat()
    else:  # weekly: 按ISO周判断
        return last == f"{today.isocalendar().year}-W{today.isocalendar().week}"


def mark_posted(period: str, link: str, repos: list = None):
    state = load_state()
    today = datetime.date.today()
    if period == "daily":
        state[f"last_{period}"] = today.isoformat()
    else:
        state[f"last_{period}"] = (f"{today.isocalendar().year}-"
                                   f"W{today.isocalendar().week}")
    state.setdefault("history", []).append({
        "period": period, "date": today.isoformat(), "url": link,
        "repos": [r["full_name"] for r in (repos or [])]})
    # 最近发过的仓库(用于换新面孔降权),35天后清理
    recent = state.setdefault("recent_repos", {})
    for r in (repos or []):
        recent[r["full_name"]] = today.isoformat()
    cutoff = (today - datetime.timedelta(days=35)).isoformat()
    state["recent_repos"] = {k: v for k, v in recent.items() if v >= cutoff}
    save_state(state)


def recent_repo_names(period: str) -> set:
    """最近已发过的仓库名: 日榜看最近2天,周榜看最近14天(降权换新面孔)"""
    state = load_state()
    recent = state.get("recent_repos", {})
    days = 2 if period == "daily" else 14
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    names = set()
    for name, ds in recent.items():
        try:
            if datetime.date.fromisoformat(ds) >= cutoff:
                names.add(name)
        except ValueError:
            continue
    return names


def build_post_text(title: str, intro: str, repos: list, summaries: list,
                    period: str, outro: str = "") -> str:
    """构造帖子正文文字部分(长图之外的文字)"""
    lines = [intro, ""]
    for r in repos:
        summary = ""
        for it in summaries:
            if it.get("rank") == r["rank"]:
                summary = it.get("summary", "")
                break
        stars = r.get("stars", 0)
        stars_str = f"{stars/1000:.1f}k" if stars >= 1000 else str(stars)
        hot = r.get("period_stars", "")
        hot_str = f"({hot})" if hot else ""
        lines.append(f"TOP{r['rank']} {r['full_name']}  ★{stars_str} {hot_str}")
        lines.append(summary)
        lines.append(r["url"])
        lines.append("")
    outro = (outro or "榜单和数据来自 GitHub Trending,"
              "看上哪个直接搜名字就能玩。").strip()
    lines.append(outro)
    lines.append("")
    lines.append("#GitHub# #开源# #程序员#")
    return "\n".join(lines)


def run(period: str, dry_run: bool, force: bool, no_image: bool = False,
        keep_image: bool = False):
    cfg = load_config()
    top_n = int(cfg.get("top_n", 10))

    print(f"===== GitHub 热榜 → 小黑盒 [{period}] "
          f"{'DRY-RUN' if dry_run else 'LIVE'} =====")
    print(f"[1/6] 抓取 GitHub Trending (top {top_n}, 老项目过滤+新项目优先)...")
    recent = recent_repo_names(period)
    if recent:
        print(f"   最近发过 {len(recent)} 个仓库,本轮降权换新面孔")
    repos = fetch_trending(period, top_n=top_n, recent_names=recent)
    repos = enrich_details(repos)
    for r in repos:
        print(f"   {r['rank']:>2}. {r['full_name']} ★{r['stars']} "
              f"{r.get('period_stars','')}")

    print(f"[2/6] 抓取 README 素材...")
    repos = fetch_readme_excerpts(repos)

    print(f"[3/6] LLM 生成中文简介(生成+去AI味两段式)...")
    data = summarize_repos(repos, period)
    print(f"   标题: {data['title']}")

    print(f"[4/6] 渲染榜单长图...")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = os.path.join(OUTPUT_DIR, f"trending_{period}_{ts}.png")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if no_image:
        image_path = None
        print("   (跳过长图)")
    else:
        render_long_image(data["title"], repos, data["items"],
                          image_path, period)

    print(f"[5/6] 组装帖子内容...")
    text = build_post_text(data["title"], data["intro"], repos,
                           data["items"], period,
                           outro=data.get("outro", ""))
    # 保存本地预览
    preview_path = os.path.join(OUTPUT_DIR, f"post_{period}_{ts}.md")
    with open(preview_path, "w") as f:
        f.write(f"# {data['title']}\n\n{text}\n\n---\n"
                f"图片: {image_path}\n")
    print(f"   预览已保存: {preview_path}")

    print(f"[6/6] 发布到小黑盒(盒友杂谈)...")
    client = HeyboxClient(cfg["cookie"])
    if not client.check_login_ok():
        print("   [错误] Cookie 已失效,请更新 config.json 中的 cookie!")
        return None
    publisher = HeyboxPublisher(
        client, topic_ids=str(cfg.get("topic_ids", "7214")))
    result = publisher.publish(
        title=data["title"],
        content_text=text,
        image_paths=[image_path] if image_path else None,
        dry_run=dry_run)

    if dry_run:
        print("   [DRY-RUN] 未真实发布。payload 摘要:")
        body = result.get("body", {})
        print("   -", json.dumps({k: v for k, v in body.items()
                                 if k != "text"}, ensure_ascii=False)[:300])
        return result

    link = result.get("post_url", "")
    print(f"   ✅ 发布成功: {link}")
    mark_posted(period, link, repos)
    # 清理历史图片(保留最近5张)
    if not keep_image:
        _cleanup_images()
    return result


def _cleanup_images(keep: int = 5):
    files = sorted(
        [f for f in os.listdir(OUTPUT_DIR)
         if f.startswith("trending_") and f.endswith(".png")],
        reverse=True)
    for f in files[keep:]:
        try:
            os.unlink(os.path.join(OUTPUT_DIR, f))
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["daily", "weekly"],
                        default="daily")
    parser.add_argument("--dry-run", action="store_true",
                        help="只构造不发帖")
    parser.add_argument("--force", action="store_true",
                        help="忽略当日已发布标记")
    parser.add_argument("--no-image", action="store_true",
                        help="不生成长图(纯文字帖)")
    parser.add_argument("--keep-image", action="store_true",
                        help="保留生成的图片文件")
    args = parser.parse_args()

    cfg = load_config()
    if args.dry_run:
        dry = True
    else:
        dry = bool(cfg.get("dry_run", True))

    if not args.force and not dry and already_posted(args.period):
        print(f"[跳过] {args.period} 本期已发布过 (state.json 有记录,"
              f"如需重发加 --force)")
        return

    try:
        run(args.period, dry, args.force, args.no_image, args.keep_image)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
