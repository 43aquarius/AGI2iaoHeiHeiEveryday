# -*- coding: utf-8 -*-
"""GitHub 热榜 → 小黑盒图文帖 主流程

帖子版式(2026-10-18 v3, 应用户要求回调):
  intro → 每个项目[编号头+数据行+分段正文+链接] →
  outro → 话题标签 → 末尾附一张完整榜单总览长图
(文字排版的分段/空行/序号保留, 图片从"封面+11张卡片交错"
回退为单张完整长图)
text 块内用 \n\n 空行制造呼吸感(网页版正文 white-space:break-spaces 已验证)。

用法:
    python main.py --period daily   [--no-image] [--dry-run] [--force]
    python main.py --period weekly  ...
"""
import argparse
import datetime
import json
import os
import re
import sys
import traceback
from typing import Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from github_trending import (fetch_trending, enrich_details,
                              fetch_readme_excerpts)
from summarizer import summarize_repos
from heybox_client import HeyboxClient
from heybox_publisher import HeyboxPublisher

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
STATE_PATH = os.path.join(BASE_DIR, "state.json")

RANK_EMOJI = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
              6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"}


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


# ---------------------------------------------------------------------------
# 帖子正文组装(图文交错 segments)
# ---------------------------------------------------------------------------

def _fmt_stars(stars: int) -> str:
    return f"{stars/1000:.1f}k" if stars >= 1000 else str(stars)


def _fmt_hot(period_stars: str, period: str) -> str:
    """'1,102 stars today' → '今日 +1,102'"""
    if not period_stars:
        return ""
    m = re.search(r"([\d,]+)\s*stars?", period_stars)
    if not m:
        return ""
    n = m.group(1).replace(",", "")
    unit = "本周" if "week" in period_stars.lower() or period == "weekly" \
        else "今日"
    return f"{unit} +{n}"


def _is_new_repo(r: dict) -> bool:
    created = r.get("created_at") or ""
    if not created:
        return False
    try:
        dt = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
        return 0 <= (datetime.datetime.now(datetime.timezone.utc) - dt).days <= 100
    except ValueError:
        return False


def _clean_summary(s: str) -> str:
    """正文纯文本清理: 去掉 markdown 记号(小黑盒纯文本渲染会显示原符号)"""
    s = (s or "").replace("**", "").replace("`", "")
    return s.strip()


def _split_paragraphs(text: str, min_side: int = 70) -> List[str]:
    """把长 summary 在句号附近分成两段(手机阅读节奏)"""
    t = (text or "").strip()
    if len(t) < 180:
        return [t] if t else []
    ends = [m.end() for m in re.finditer(r"[。！？；]", t)]
    if not ends:
        return [t]
    mid = len(t) // 2
    best = None
    for e in ends:
        if e >= min_side and (len(t) - e) >= min_side:
            if best is None or abs(e - mid) < abs(best - mid):
                best = e
    if best is None:
        return [t]
    return [t[:best].strip(), t[best:].strip()]


def _summary_by_rank(summaries: List[Dict], rank: int) -> str:
    for it in summaries or []:
        if it.get("rank") == rank:
            return it.get("summary", "")
    return ""


def _project_section(r: dict, summary: str, period: str) -> str:
    """单个项目的文字块(块内 \n 分行, \n\n 空行)"""
    rank = r.get("rank", 0)
    emoji = RANK_EMOJI.get(rank, f"TOP{rank}")
    new_mark = " 🆕" if _is_new_repo(r) else ""
    head = f"{emoji} {r['full_name']}{new_mark}"

    stats = [f"⭐ {_fmt_stars(r.get('stars', 0))} 星"]
    hot = _fmt_hot(r.get("period_stars", ""), period)
    if hot:
        stats.append(f"🔥 {hot}")
    if r.get("language"):
        stats.append(r["language"])
    stats_line = " · ".join(stats)

    parts = [f"{head}\n{stats_line}"]
    for para in _split_paragraphs(_clean_summary(summary)):
        if para:
            parts.append(para)
    parts.append(f"🔗 {r['url']}")
    return "\n\n".join(parts)


def build_post_segments(title: str, intro: str, repos: list,
                        summaries: List[Dict], period: str,
                        outro: str = "",
                        long_path: Optional[str] = None,
                        illustrations: Optional[Dict[int, str]] = None
                        ) -> List[Tuple[str, str]]:
    """构造帖子 segments: 文字流(对应项目后可插小黑手绘配图) + 末尾总览长图
    illustrations: {项目rank: 图片路径}"""
    segs: List[Tuple[str, str]] = []
    if (intro or "").strip():
        segs.append(("text", _clean_summary(intro)))
    for r in repos:
        summary = _summary_by_rank(summaries, r.get("rank"))
        segs.append(("text", _project_section(r, summary, period)))
        rank = int(r.get("rank") or 0)
        if illustrations and rank in illustrations:
            segs.append(("img", illustrations[rank]))
    outro = (outro or "榜单和数据来自 GitHub Trending,"
              "看上哪个直接搜名字就能玩。").strip()
    if outro:
        segs.append(("text", _clean_summary(outro)))
    segs.append(("text", "#GitHub# #开源# #程序员#"))
    if long_path:
        segs.append(("img", long_path))
    return segs


def segments_to_markdown(title: str, segs: List[Tuple[str, str]],
                         base_dir: str) -> str:
    """segments → 本地预览 markdown(图片用相对路径)"""
    lines = [f"# {title}", ""]
    for kind, val in segs:
        if kind == "text":
            lines.append(val)
            lines.append("")
        else:
            rel = os.path.relpath(val, base_dir)
            lines.append(f"![配图]({rel})")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run(period: str, dry_run: bool, force: bool, no_image: bool = False,
        keep_image: bool = False):
    cfg = load_config()
    top_n = int(cfg.get("top_n", 10))

    print(f"===== GitHub 热榜 → 小黑盒 [{period}] "
          f"{'DRY-RUN' if dry_run else 'LIVE'} =====")
    print(f"[1/7] 抓取 GitHub Trending (top {top_n}, 老项目过滤+新项目优先)...")
    recent = recent_repo_names(period)
    if recent:
        print(f"   最近发过 {len(recent)} 个仓库,本轮降权换新面孔")
    repos = fetch_trending(period, top_n=top_n, recent_names=recent)
    repos = enrich_details(repos)
    for r in repos:
        print(f"   {r['rank']:>2}. {r['full_name']} ★{r['stars']} "
              f"{r.get('period_stars','')}")

    print(f"[2/7] 抓取 README 素材...")
    repos = fetch_readme_excerpts(repos)

    print(f"[3/7] LLM 生成中文简介(生成+去AI味两段式)...")
    data = summarize_repos(repos, period)
    print(f"   标题: {data['title']}")

    print(f"[4/7] 渲染榜单总览长图...")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = os.path.join(OUTPUT_DIR, f"trending_{period}_{ts}.png")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if no_image:
        image_path = None
        print("   (跳过图片)")
    else:
        from image_maker import render_long_image
        render_long_image(data["title"], repos, data["items"],
                          image_path, period)

    print(f"[5/7] 生成小黑手绘配图(插在对应项目后)...")
    illustrations: Dict[int, str] = {}
    illus_cfg = (cfg.get("illustrations") or {})
    if no_image or not illus_cfg.get("enabled", True):
        print("   已关闭, 跳过")
    else:
        from illustrator import make_illustrations
        illus_items = [{"num": int(r.get("rank") or 0),
                        "headline": f"{r.get('full_name', '')} "
                                    f"{_fmt_stars(int(r.get('stars') or 0))}",
                        "summary": (_summary_by_rank(data["items"],
                                                     r.get("rank")) or "")}
                       for r in repos]
        illustrations = make_illustrations(
            data["title"], illus_items, OUTPUT_DIR,
            prefix=f"weekly_illus_{period}",
            max_count=int(illus_cfg.get("weekly_max", 2)),
            extra_hint="选最出人意料/最有画面感的项目, 比如量级反差大、"
                       "名字自带隐喻、玩法违反直觉的")

    print(f"[6/7] 组装帖子内容(文字 + 插图 + 末尾总览长图)...")
    segments = build_post_segments(
        data["title"], data["intro"], repos, data["items"], period,
        outro=data.get("outro", ""), long_path=image_path,
        illustrations=illustrations)
    preview_path = os.path.join(OUTPUT_DIR, f"post_{period}_{ts}.md")
    with open(preview_path, "w") as f:
        f.write(segments_to_markdown(data["title"], segments, OUTPUT_DIR))
    print(f"   预览已保存: {preview_path}")

    print(f"[7/7] 发布到小黑盒(盒友杂谈)...")
    client = HeyboxClient(cfg["cookie"])
    if not client.check_login_ok():
        print("   [错误] Cookie 已失效,请更新 config.json 中的 cookie!")
        return None
    publisher = HeyboxPublisher(
        client, topic_ids=str(cfg.get("topic_ids", "7214")))
    result = publisher.publish(
        title=data["title"],
        segments=segments,
        dry_run=dry_run)

    if dry_run:
        print("   [DRY-RUN] 未真实发布。内容块序列:")
        try:
            blocks = json.loads(result["body"]["text"])
            seq = []
            for b in blocks:
                if b["type"] == "text":
                    seq.append(f"文字{len(b['text'])}字")
                else:
                    seq.append(f"图{b['width']}x{b['height']}")
            print("   " + " → ".join(seq))
        except Exception:
            print("   ", json.dumps(result.get("body", {}), ensure_ascii=False)[:300])
        return result

    link = result.get("post_url", "")
    print(f"   ✅ 发布成功: {link}")
    mark_posted(period, link, repos)
    # 清理历史图片(保留最近几套)
    if not keep_image:
        _cleanup_images()
    return result


def _cleanup_images(keep: int = 3):
    """清理旧长图与旧切片目录,各保留最近几套"""
    files = sorted(
        [f for f in os.listdir(OUTPUT_DIR)
         if f.startswith("trending_") and f.endswith(".png")],
        reverse=True)
    for f in files[keep:]:
        try:
            os.unlink(os.path.join(OUTPUT_DIR, f))
        except OSError:
            pass
    slice_dirs = sorted(
        [d for d in os.listdir(OUTPUT_DIR) if d.startswith("slices_")],
        reverse=True)
    for d in slice_dirs[keep:]:
        try:
            for f in os.listdir(os.path.join(OUTPUT_DIR, d)):
                os.unlink(os.path.join(OUTPUT_DIR, d, f))
            os.rmdir(os.path.join(OUTPUT_DIR, d))
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
                        help="不生成图片(纯文字帖)")
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
