# -*- coding: utf-8 -*-
"""榜单图片生成器: HTML+CSS → Playwright 截图
设计: GitHub Dark 风格 + 小黑盒橙, 1080px 宽

两种产物:
  1. 完整长图(本地备份/可配置附在帖尾)
  2. 切片系列(发帖用): 封面 banner + 每个项目一张独立卡片图,
     图文交错插入帖子正文, 改善纯文字帖的观感
"""
import datetime
import html as html_module
import json
import os
import re
from typing import Dict, List, Optional

LANG_COLORS = {
    "Python": "#3572A5", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "Rust": "#dea584", "C++": "#f34b7d", "C": "#555555", "C#": "#178600",
    "Go": "#00ADD8", "Java": "#b07219", "Ruby": "#701516", "PHP": "#4F5D95",
    "Shell": "#89e051", "HTML": "#e34c26", "CSS": "#563d7c",
    "Kotlin": "#A97BFF", "Swift": "#F05138", "Dart": "#00B4AB",
    "Vue": "#41b883", "SCSS": "#c6538c", "Lua": "#000080", "Zig": "#ec915c",
    "Jupyter Notebook": "#DA5B0B", "MDX": "#fcb32c",
}
RANK_STYLES = {
    1: ("#FFD700", "TOP 1"), 2: ("#C0C0C0", "TOP 2"), 3: ("#CD7F32", "TOP 3"),
}

GH_MARK_SVG = (
    '%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22%3E'
    '%3Cpath fill-rule=%22evenodd%22 d=%22M8 0C3.58 0 0 3.58 0 8c0 3.54 '
    '2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37'
    '-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.'
    '63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07'
    '-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08'
    '-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04'
    ' 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07'
    '-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 '
    '.21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z%22/%3E%3C/svg%3E'
)


def _esc(s: str) -> str:
    return html_module.escape(str(s or ""))


def _fmt_stars(stars: int) -> str:
    return f"{stars/1000:.1f}k" if stars >= 1000 else str(stars)


def _age_days(created_at: str) -> Optional[int]:
    if not created_at:
        return None
    try:
        dt = datetime.datetime.fromisoformat(
            created_at.replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - dt).days
    except ValueError:
        return None


def card_tagline(summary: str, repo_desc: str = "") -> str:
    """从 summary 确定性提取卡片标语(兜底用):
    优先第一句(6~22字), 太长截到第一个逗号, 再不行硬截断"""
    s = (summary or "").strip()
    m = re.match(r"(.+?[。！？?!])", s)
    first = m.group(1).rstrip("。！？?! ") if m else s
    if 6 <= len(first) <= 22:
        return first
    for cut in ("，", "、", "；", ","):
        i = first.find(cut)
        if 6 <= i <= 20:
            return first[:i]
    if len(first) > 20:
        return first[:19] + "…"
    return (first or (repo_desc or "").strip()[:22]) or "近期热度上升的新项目"


def _item_by_rank(summaries: List[Dict], rank: int) -> Dict:
    for it in summaries or []:
        if it.get("rank") == rank:
            return it
    return {}


def build_html(title: str, repos: List[Dict], summaries: List[Dict],
               period: str = "daily") -> str:
    """生成榜单 HTML(长图 + 切片共用一页, 切片带 id 供元素截图)"""
    today = datetime.datetime.now()
    date_str = today.strftime("%Y/%m/%d")
    period_cn = "日榜" if period == "daily" else "周榜"
    # 页脚栏目名: 只在 GitHub 榜单帖自己的图里出现对应字眼(用户要求)
    period_label = "每日 GitHub 日榜" if period == "daily" else "每周一 GitHub 周榜"
    weekday = "一二三四五六日"[today.weekday()]

    cards = []
    for r in repos:
        rank = r["rank"]
        it = _item_by_rank(summaries, rank)
        tagline = (it.get("tagline") or "").strip() or \
            card_tagline(it.get("summary", ""), r.get("description") or "")
        color = LANG_COLORS.get(r.get("language") or "", "#8b949e")
        if rank in RANK_STYLES:
            badge_color, badge_text = RANK_STYLES[rank]
            badge = (f'<div class="rank-badge" style="background:{badge_color};'
                     f'color:#0d1117">{badge_text}</div>')
        else:
            badge = f'<div class="rank-badge rank-n">{rank}</div>'
        stars_str = _fmt_stars(r.get("stars", 0))
        forks_str = _fmt_stars(r.get("forks", 0))
        hot = _esc(r.get("period_stars") or "")
        desc = re.sub(r"\s+", " ", (r.get("description") or "")).strip()
        if len(desc) > 64:
            desc = desc[:62] + "…"
        desc_html = (f'<div class="desc">{_esc(desc)}</div>' if desc
                     else "")
        new_pill = ""
        age = _age_days(r.get("created_at") or "")
        if age is not None and 0 <= age <= 100:
            new_pill = '<span class="new-pill">NEW</span>'
        link_short = "github.com/" + _esc(r["full_name"])
        cards.append(f"""
      <div class="pslice" id="slice-{rank}">
      <div class="card">
        <div class="card-head">
          {badge}
          <div class="repo-name">{_esc(r['full_name'])}</div>
          {new_pill}
        </div>
        <div class="meta">
          <span class="dot" style="background:{color}"></span>
          <span class="lang">{_esc(r.get('language') or 'Open Source')}</span>
          <span class="sep">·</span>
          <span class="stars">★ {stars_str}</span>
          <span class="sep">·</span>
          <span class="forks">Fork {forks_str}</span>
          {f'<span class="sep">·</span><span class="hot">🔥 {_esc(hot)}</span>' if hot else ''}
        </div>
        {desc_html}
        <div class="tagline">{_esc(tagline)}</div>
        <div class="card-link">{link_short}</div>
      </div>
      </div>""")

    cards_html = "\n".join(cards)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1080px;
    background: #0d1117;
    font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: #e6edf3;
  }}
  .container {{ padding: 0; }}
  /* 切片包裹: 长图里承担间距, 元素截图时让每张卡片自带深色留白边 */
  .pslice {{ padding: 0 40px 18px 40px; }}
  .pslice.banner-slice {{ padding: 40px 40px 26px 40px; }}
  .pslice.footer-slice {{ padding: 22px 40px 36px 40px; }}
  .header {{
    background: linear-gradient(135deg, #161b22 0%, #1c2431 60%, #233042 100%);
    border: 1px solid #30363d;
    border-radius: 24px;
    padding: 44px 48px 38px;
    position: relative;
    overflow: hidden;
  }}
  .header::before {{
    content: "";
    position: absolute; right: -60px; top: -60px;
    width: 280px; height: 280px; border-radius: 50%;
    background: radial-gradient(circle, rgba(255,125,41,0.18) 0%, transparent 70%);
  }}
  .gh-mark {{
    display: inline-block; width: 64px; height: 64px;
    background: #e6edf3; border-radius: 50%;
    mask: url("data:image/svg+xml,{GH_MARK_SVG}") no-repeat center / contain;
    -webkit-mask: url("data:image/svg+xml,{GH_MARK_SVG}") no-repeat center / contain;
    margin-bottom: 20px;
  }}
  .brand {{ font-size: 26px; color: #ff7d29; font-weight: 700;
    letter-spacing: 2px; margin-bottom: 8px; }}
  .title {{ font-size: 44px; font-weight: 800; line-height: 1.3;
    color: #f0f6fc; }}
  .subtitle {{ margin-top: 16px; font-size: 24px; color: #8b949e;
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }}
  .pill {{
    background: rgba(255,125,41,0.15); color: #ff7d29;
    border: 1px solid rgba(255,125,41,0.4); border-radius: 999px;
    padding: 4px 18px; font-size: 22px; font-weight: 600;
  }}
  .card {{
    background: #161b22; border: 1px solid #30363d;
    border-radius: 18px; padding: 30px 32px 26px;
  }}
  .card-head {{ display: flex; align-items: center; gap: 18px;
    margin-bottom: 14px; }}
  .rank-badge {{
    min-width: 74px; height: 40px; border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; font-weight: 800; flex-shrink: 0;
  }}
  .rank-n {{ background: #21262d; color: #8b949e; border: 1px solid #30363d; }}
  .new-pill {{
    background: rgba(46,160,67,0.16); color: #3fb950;
    border: 1px solid rgba(46,160,67,0.45); border-radius: 999px;
    padding: 2px 14px; font-size: 19px; font-weight: 700;
    letter-spacing: 1px; flex-shrink: 0; margin-left: auto;
  }}
  .repo-name {{ font-size: 28px; font-weight: 700; color: #58a6ff;
    word-break: break-all; }}
  .meta {{ display: flex; align-items: center; gap: 10px; font-size: 21px;
    color: #8b949e; margin-bottom: 16px; flex-wrap: wrap; }}
  .dot {{ width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0; }}
  .stars {{ color: #e3b341; font-weight: 600; }}
  .hot {{ color: #ff7b72; font-weight: 600; }}
  .sep {{ color: #484f58; }}
  .tagline {{ font-size: 27px; line-height: 1.5; color: #e6edf3;
    font-weight: 600; margin-bottom: 14px; }}
  .desc {{ font-size: 21px; line-height: 1.5; color: #8b949e;
    margin-bottom: 14px; }}
  .forks {{ color: #8b949e; font-weight: 600; }}
  .card-link {{ font-size: 20px; color: #6e7681; letter-spacing: 0.5px;
    border-top: 1px solid #21262d; padding-top: 14px; }}
  .footer {{
    text-align: center; font-size: 20px; color: #484f58;
    line-height: 1.7;
  }}
  .footer .accent {{ color: #ff7d29; font-weight: 600; }}
</style></head>
<body>
  <div class="container">
    <div class="pslice banner-slice" id="slice-banner">
    <div class="header">
      <div class="gh-mark"></div>
      <div class="brand">GITHUB TRENDING</div>
      <div class="title">{_esc(title)}</div>
      <div class="subtitle">
        <span class="pill">{period_cn} TOP {len(repos)}</span>
        <span>{date_str} 周{weekday}</span>
        <span>· 老牌项目已过滤,只聊新面孔</span>
      </div>
    </div>
    </div>
    {cards_html}
    <div class="pslice footer-slice" id="slice-footer">
    <div class="footer">
      {period_label} · 榜单由 <span class="accent">小黑盒盒友</span> 为你解读<br>
      已过滤 claude-code 等老牌项目 · 完整项目链接见帖子正文
    </div>
    </div>
  </div>
</body></html>"""


def render_post_images(title: str, repos: List[Dict], summaries: List[Dict],
                       long_path: str, period: str = "daily",
                       width: int = 1080) -> Dict[str, object]:
    """一次渲染, 产出:
      - 完整长图 long_path (本地备份)
      - 封面 banner + 每项目卡片图 (与 long_path 同目录 slices/)
    返回 {"long": path, "banner": path, "cards": [path,...]}
    """
    from playwright.sync_api import sync_playwright

    out_dir = os.path.dirname(long_path)
    stem = os.path.splitext(os.path.basename(long_path))[0]  # trending_daily_ts
    slice_dir = os.path.join(out_dir, f"slices_{stem}")
    os.makedirs(slice_dir, exist_ok=True)

    html = build_html(title, repos, summaries, period)
    tmp_html = long_path + ".html"
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)

    banner_path = os.path.join(slice_dir, "banner.png")
    card_paths = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 800},
                                    device_scale_factor=2)
            page.goto("file://" + os.path.abspath(tmp_html))
            page.wait_for_timeout(500)
            # 1) 完整长图(备份)
            page.screenshot(path=long_path, full_page=True)
            # 2) 封面切片
            page.locator("#slice-banner").screenshot(path=banner_path)
            # 3) 每项目卡片切片
            for r in repos:
                cp = os.path.join(slice_dir, f"card_{r['rank']:02d}.png")
                page.locator(f"#slice-{r['rank']}").screenshot(path=cp)
                card_paths.append(cp)
            browser.close()
    finally:
        try:
            os.unlink(tmp_html)
        except OSError:
            pass

    from PIL import Image

    def _size(p):
        with Image.open(p) as im:
            return im.size

    print(f"[image] 长图(备份): {long_path} {_size(long_path)}")
    print(f"[image] 封面: {banner_path} {_size(banner_path)}")
    print(f"[image] 项目卡片: {len(card_paths)} 张 "
          f"(如 {card_paths[0]} {_size(card_paths[0])})" if card_paths
          else "[image] 无项目卡片")
    return {"long": long_path, "banner": banner_path, "cards": card_paths}


def render_long_image(title: str, repos: List[Dict], summaries: List[Dict],
                      out_path: str, period: str = "daily",
                      width: int = 1080) -> str:
    """兼容旧接口: 只渲染完整长图"""
    from playwright.sync_api import sync_playwright

    html = build_html(title, repos, summaries, period)
    tmp_html = out_path + ".html"
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 800},
                                    device_scale_factor=2)
            page.goto("file://" + os.path.abspath(tmp_html))
            page.wait_for_timeout(500)
            page.screenshot(path=out_path, full_page=True)
            browser.close()
    finally:
        try:
            os.unlink(tmp_html)
        except OSError:
            pass
    print(f"[image] 长图已生成: {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# 单项目安利帖海报(project_post.py 用)
# ---------------------------------------------------------------------------

def _license_cn(lic: str) -> str:
    return {"MIT": "MIT", "Apache-2.0": "Apache-2.0",
            "GPL-3.0": "GPL-3.0", "NOASSERTION": "非标准协议"}.get(
        (lic or "").strip().upper(), (lic or "").strip())


def _age_cn(created_at: str) -> str:
    days = _age_days(created_at)
    if days is None:
        return ""
    if days <= 60:
        return f"{days}天"
    if days < 730:
        return f"{round(days / 30.4)}个月"
    return f"{round(days / 365)}年"


def build_project_html(repo: Dict, content: Dict) -> str:
    """单项目安利帖海报 HTML: header(项目名+数据) + 标语 + 亮点列表 + 链接"""
    lic = _license_cn(repo.get("license") or "")
    age = _age_cn(repo.get("created_at") or "")
    lang = repo.get("language") or "Open Source"
    color = LANG_COLORS.get(lang, "#8b949e")
    stars_str = _fmt_stars(repo.get("stars", 0))

    pills = [f"★ {stars_str}", lang]
    if lic:
        pills.append(lic)
    if age:
        pills.append(age.replace("创建于", ""))
    pills_html = "".join(f'<span class="pill">{_esc(p)}</span>' for p in pills)

    topics = [t for t in (repo.get("topics") or []) if t][:4]
    topics_html = ""
    if topics:
        topics_html = '<div class="topics">' + " ".join(
            f'<span class="topic">#{_esc(t)}</span>' for t in topics) + "</div>"

    points = [p for p in (content.get("points") or []) if str(p).strip()][:5]
    points_html = ""
    if points:
        lis = "".join(
            f'<div class="pt"><span class="pt-num">{i}</span>'
            f'<span class="pt-text">{_esc(str(p))}</span></div>'
            for i, p in enumerate(points, 1))
        points_html = f'<div class="card points-card"><div class="sec-title">核心亮点</div>{lis}</div>'

    link_short = "github.com/" + _esc(repo["full_name"])
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1080px; background: #0d1117;
    font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: #e6edf3;
  }}
  .wrap {{ padding: 40px; }}
  .header {{
    background: linear-gradient(135deg, #161b22 0%, #1c2431 60%, #233042 100%);
    border: 1px solid #30363d; border-radius: 24px;
    padding: 44px 48px 38px; position: relative; overflow: hidden;
  }}
  .header::before {{
    content: ""; position: absolute; right: -60px; top: -60px;
    width: 280px; height: 280px; border-radius: 50%;
    background: radial-gradient(circle, rgba(255,125,41,0.18) 0%, transparent 70%);
  }}
  .gh-mark {{
    display: inline-block; width: 56px; height: 56px;
    background: #e6edf3; border-radius: 50%;
    mask: url("data:image/svg+xml,{GH_MARK_SVG}") no-repeat center / contain;
    -webkit-mask: url("data:image/svg+xml,{GH_MARK_SVG}") no-repeat center / contain;
    margin-bottom: 16px;
  }}
  .brand {{ font-size: 24px; color: #ff7d29; font-weight: 700;
    letter-spacing: 2px; margin-bottom: 10px; }}
  .repo-name {{ font-size: 46px; font-weight: 800; line-height: 1.25;
    color: #f0f6fc; word-break: break-all; }}
  .meta {{ margin-top: 20px; display: flex; align-items: center; gap: 12px;
    flex-wrap: wrap; }}
  .pill {{ background: rgba(255,125,41,0.15); color: #ff7d29;
    border: 1px solid rgba(255,125,41,0.4); border-radius: 999px;
    padding: 4px 18px; font-size: 21px; font-weight: 600; }}
  .tagline-card {{
    margin-top: 18px; background: #161b22; border: 1px solid #30363d;
    border-left: 6px solid #ff7d29; border-radius: 14px;
    padding: 28px 32px;
  }}
  .tagline {{ font-size: 32px; line-height: 1.5; font-weight: 700;
    color: #ffcf9e; }}
  .card {{ margin-top: 18px; background: #161b22; border: 1px solid #30363d;
    border-radius: 14px; padding: 28px 32px; }}
  .sec-title {{ font-size: 22px; color: #8b949e; font-weight: 700;
    letter-spacing: 3px; margin-bottom: 18px; }}
  .pt {{ display: flex; align-items: flex-start; gap: 18px;
    margin-bottom: 16px; }}
  .pt:last-child {{ margin-bottom: 0; }}
  .pt-num {{
    width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0;
    background: rgba(255,125,41,0.16); color: #ff7d29;
    border: 1px solid rgba(255,125,41,0.45); font-size: 21px;
    font-weight: 800; display: flex; align-items: center;
    justify-content: center; margin-top: 2px;
  }}
  .pt-text {{ font-size: 26px; line-height: 1.55; color: #e6edf3; }}
  .topics {{ margin-top: 18px; display: flex; flex-wrap: wrap; gap: 10px; }}
  .topic {{ background: #21262d; color: #58a6ff; border-radius: 8px;
    padding: 4px 14px; font-size: 20px; }}
  .card-link {{ font-size: 21px; color: #6e7681; letter-spacing: 0.5px; }}
  .footer {{ margin-top: 18px; text-align: center; font-size: 20px;
    color: #484f58; line-height: 1.7; }}
  .footer .accent {{ color: #ff7d29; font-weight: 600; }}
</style></head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="gh-mark"></div>
      <div class="brand">GITHUB 开源安利</div>
      <div class="repo-name">{_esc(repo['full_name'])}</div>
      <div class="meta">{pills_html}</div>
    </div>
    <div class="tagline-card">
      <div class="tagline">{_esc(content.get('tagline') or '')}</div>
    </div>
    {topics_html}
    {points_html}
    <div class="card">
      <div class="sec-title">项目地址</div>
      <div class="card-link">{link_short}</div>
    </div>
    <div class="footer">
      GITHUB 开源安利 · 由 <span class="accent">小黑盒盒友</span> 为你解读<br>
      完整介绍见帖子正文 · 内容由 AI 辅助整理
    </div>
  </div>
</body></html>"""


def render_project_poster(repo: Dict, content: Dict, out_path: str,
                          width: int = 1080) -> str:
    """渲染单项目安利海报(1080px 宽, 2x 截图)"""
    from playwright.sync_api import sync_playwright

    html = build_project_html(repo, content)
    tmp_html = out_path + ".html"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 800},
                                    device_scale_factor=2)
            page.goto("file://" + os.path.abspath(tmp_html))
            page.wait_for_timeout(500)
            page.screenshot(path=out_path, full_page=True)
            browser.close()
    finally:
        try:
            os.unlink(tmp_html)
        except OSError:
            pass
    from PIL import Image
    with Image.open(out_path) as im:
        print(f"[image] 海报已生成: {out_path} {im.size}")
    return out_path


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/z/my-project/github-hot-heybox")
    from github_trending import fetch_trending
    from summarizer import summarize_repos

    repos = fetch_trending("daily", top_n=10)
    data = summarize_repos(repos, "daily")
    out = render_post_images(
        data["title"], repos, data["items"],
        "/home/z/my-project/github-hot-heybox/output/test_images.png",
        "daily")
    print("done:", out)


# ---------------------------------------------------------------------------
# AGI 日报长图(agi_news.py 用) — 四套主题按期号轮换 + 富信息卡片
# ---------------------------------------------------------------------------

# 主题色板: 每天轮换, 保持栏目 logo/版式不变但整体气质有变化
AGI_THEMES = {
    "ember": {   # 炉火橙(GitHub Dark 风)
        "bg": "#0d1117", "card_bg": "#161b22", "card_border": "#30363d",
        "hd_grad": "linear-gradient(135deg, #161b22 0%, #1c2431 55%, #26304a 100%)",
        "accent": "#ff7d29", "accent_rgb": "255,125,41",
        "title": "#f0f6fc", "text": "#e6edf3", "dim": "#8b949e",
        "glow2": "rgba(63,114,175,0.16)", "chip_bg": "#0d1117",
        "focus_badge": "#FFD700",
    },
    "ocean": {   # 深海青
        "bg": "#0a1424", "card_bg": "#101d33", "card_border": "#233b5c",
        "hd_grad": "linear-gradient(135deg, #0e1c31 0%, #12294a 55%, #16355f 100%)",
        "accent": "#38bdf8", "accent_rgb": "56,189,248",
        "title": "#f0f7ff", "text": "#dbe9f8", "dim": "#7e93b0",
        "glow2": "rgba(14,165,164,0.14)", "chip_bg": "#0a1424",
        "focus_badge": "#7dd3fc",
    },
    "violet": {  # 星云紫
        "bg": "#120f1d", "card_bg": "#1a1528", "card_border": "#342a4d",
        "hd_grad": "linear-gradient(135deg, #181227 0%, #241a3d 55%, #2f2350 100%)",
        "accent": "#b197fc", "accent_rgb": "177,151,252",
        "title": "#f5f2ff", "text": "#e6dff7", "dim": "#9a8bb5",
        "glow2": "rgba(244,114,182,0.13)", "chip_bg": "#120f1d",
        "focus_badge": "#e9d5ff",
    },
    "matrix": {  # 终端绿
        "bg": "#0a110d", "card_bg": "#0f1a13", "card_border": "#23402c",
        "hd_grad": "linear-gradient(135deg, #0c1710 0%, #10241a 55%, #14361f 100%)",
        "accent": "#4ade80", "accent_rgb": "74,222,128",
        "title": "#eefdf3", "text": "#d9f2e0", "dim": "#7fa88c",
        "glow2": "rgba(52,211,153,0.12)", "chip_bg": "#0a110d",
        "focus_badge": "#bbf7d0",
    },
}
AGI_THEME_ORDER = ["ember", "ocean", "violet", "matrix"]

CAT_ICONS = {
    "大模型": "🧠", "芯片硬件": "💾", "产品发布": "🚀", "开源项目": "📦",
    "研究突破": "🔬", "行业动态": "📈", "政策监管": "⚖️", "投融资": "💰",
}


def _agi_date_cn(date_str: str) -> str:
    """'20261008' → '2026年10月8日'"""
    try:
        return (f"{date_str[:4]}年{int(date_str[4:6])}月"
                f"{int(date_str[6:])}日")
    except (ValueError, IndexError):
        return date_str


def _agi_theme_for(date_str: str) -> str:
    """按期号轮换主题(每天换气质, 连续日期不重样)"""
    if date_str and date_str.isdigit():
        return AGI_THEME_ORDER[int(date_str) % len(AGI_THEME_ORDER)]
    return "ember"


def build_agi_html(title: str, meta: Dict, items: List[Dict],
                   keywords: Optional[List[str]] = None,
                   theme_key: Optional[str] = None) -> str:
    """AGI 日报总览长图 HTML(富信息版):
    header(品牌+日期+期号) + 本期看点统计条 + 新闻卡片(TOP3焦点+分类chip
    +标语+两条要点) + 关键词标签云 + footer; 主题四选一轮换"""
    date_str = meta.get("date") or ""
    theme_key = theme_key or _agi_theme_for(date_str)
    T = AGI_THEMES[theme_key]
    issue_date = _agi_date_cn(date_str)
    today = datetime.datetime.now()
    weekday = "一二三四五六日"[today.weekday()]

    n_items = len(items or [])
    cats = [it.get("category") for it in (items or []) if it.get("category")]
    n_cats = len(set(cats)) if cats else 0
    n_facts = sum(len([p for p in (it.get("img_points") or [])
                       if str(p).strip()]) for it in (items or []))
    kws = [k for k in (keywords or []) if str(k).strip()][:5]
    kw_chips = "".join(f'<span class="kw-chip">#{_esc(k)}</span>' for k in kws)

    cards = []
    for it in items or []:
        num = int(it.get("num") or 0)
        focus = num in (1, 2, 3)
        if focus:
            badge = (f'<div class="rank-badge" style="background:'
                     f'{T["focus_badge"]};color:{T["bg"]}">TOP {num}</div>')
            card_cls = "card focus"
        else:
            badge = f'<div class="rank-badge rank-n">{num}</div>'
            card_cls = "card"
        cat = (it.get("category") or "行业动态").strip()
        chip = (f'<span class="cat-chip">{CAT_ICONS.get(cat, "📌")} '
                f'{_esc(cat)}</span>')
        headline = _esc(it.get("headline") or "")
        tagline = _esc(it.get("tagline") or "")
        facts = "".join(
            f'<div class="fact"><span class="fact-bullet"></span>'
            f'<span>{_esc(str(p))}</span></div>'
            for p in (it.get("img_points") or [])[:2] if str(p).strip())
        facts_html = f'<div class="facts">{facts}</div>' if facts else ""
        cards.append(f"""
      <div class="{card_cls}">
        <div class="card-head">
          {badge}
          <div class="news-title">{headline}</div>
          {chip}
        </div>
        <div class="tagline">{tagline}</div>
        {facts_html}
      </div>""")
    cards_html = "\n".join(cards)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1080px;
    background: {T['bg']};
    font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: {T['text']};
    padding: 40px;
  }}
  .header {{
    background: {T['hd_grad']};
    border: 1px solid {T['card_border']};
    border-radius: 24px;
    padding: 44px 48px 38px;
    position: relative;
    overflow: hidden;
    margin-bottom: 20px;
  }}
  .header::before {{
    content: "";
    position: absolute; right: -70px; top: -70px;
    width: 320px; height: 320px; border-radius: 50%;
    background: radial-gradient(circle, rgba({T['accent_rgb']},0.22) 0%, transparent 70%);
  }}
  .header::after {{
    content: "";
    position: absolute; left: -60px; bottom: -90px;
    width: 260px; height: 260px; border-radius: 50%;
    background: radial-gradient(circle, {T['glow2']} 0%, transparent 70%);
  }}
  .brand {{
    display: flex; align-items: center; gap: 16px;
    margin-bottom: 18px; position: relative;
  }}
  .brand-mark {{
    width: 58px; height: 58px; border-radius: 14px;
    background: linear-gradient(135deg, {T['accent']} 0%, rgba({T['accent_rgb']},0.62) 100%);
    display: flex; align-items: center; justify-content: center;
    font-size: 34px; font-weight: 900; color: {T['bg']};
    box-shadow: 0 6px 22px rgba({T['accent_rgb']},0.35);
  }}
  .brand-text {{ font-size: 30px; color: {T['accent']}; font-weight: 800;
    letter-spacing: 3px; }}
  .brand-theme {{ margin-left: auto; font-size: 19px; color: {T['dim']};
    letter-spacing: 1px; opacity: 0.85; }}
  .date-pill {{
    background: rgba({T['accent_rgb']},0.15); color: {T['accent']};
    border: 1px solid rgba({T['accent_rgb']},0.4); border-radius: 999px;
    padding: 6px 20px; font-size: 22px; font-weight: 600;
    align-self: flex-start; margin-bottom: 20px; display: inline-block;
  }}
  .title {{ font-size: 42px; font-weight: 800; line-height: 1.35;
    color: {T['title']}; position: relative; }}
  .subtitle {{ margin-top: 14px; font-size: 23px; color: {T['dim']};
    position: relative; }}
  .kb-bar {{
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px; flex-wrap: wrap;
    background: {T['card_bg']}; border: 1px solid {T['card_border']};
    border-radius: 14px; padding: 18px 26px; margin-bottom: 20px;
  }}
  .kb-stats {{ font-size: 22px; color: {T['dim']}; }}
  .kb-stats b {{ color: {T['accent']}; font-weight: 800; font-size: 25px; }}
  .kb-stats .vsep {{ margin: 0 12px; color: {T['card_border']}; }}
  .kb-chips {{ display: flex; gap: 10px; flex-wrap: wrap; }}
  .kw-chip {{
    background: rgba({T['accent_rgb']},0.10); color: {T['accent']};
    border: 1px solid rgba({T['accent_rgb']},0.28);
    border-radius: 999px; padding: 3px 15px; font-size: 20px;
    font-weight: 600;
  }}
  .card {{
    background: {T['card_bg']}; border: 1px solid {T['card_border']};
    border-radius: 18px; padding: 28px 32px 26px;
    margin-bottom: 18px;
  }}
  .card.focus {{
    border: 2px solid transparent;
    background:
      linear-gradient({T['card_bg']}, {T['card_bg']}) padding-box,
      linear-gradient(135deg, rgba({T['accent_rgb']},0.65) 0%,
                      rgba({T['accent_rgb']},0.10) 60%,
                      rgba({T['accent_rgb']},0.35) 100%) border-box;
    box-shadow: 0 10px 34px rgba({T['accent_rgb']},0.13);
  }}
  .card-head {{ display: flex; align-items: flex-start; gap: 16px;
    margin-bottom: 12px; }}
  .rank-badge {{
    min-width: 74px; height: 40px; border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; font-weight: 800; flex-shrink: 0;
  }}
  .rank-n {{ background: rgba({T['accent_rgb']},0.10); color: {T['dim']};
    border: 1px solid {T['card_border']}; }}
  .news-title {{ font-size: 28px; font-weight: 700; color: {T['title']};
    line-height: 1.45; padding-top: 2px; flex: 1; }}
  .cat-chip {{
    flex-shrink: 0; margin-top: 3px;
    background: rgba({T['accent_rgb']},0.10); color: {T['accent']};
    border: 1px solid rgba({T['accent_rgb']},0.3);
    border-radius: 999px; padding: 3px 14px; font-size: 19px;
    font-weight: 600; white-space: nowrap;
  }}
  .tagline {{
    margin-left: 90px; font-size: 23px; color: {T['dim']};
    line-height: 1.5; padding: 10px 18px;
    background: {T['chip_bg']};
    border-left: 4px solid {T['accent']};
    border-radius: 0 10px 10px 0;
    margin-bottom: 12px;
  }}
  .facts {{ margin-left: 90px; display: flex; flex-direction: column;
    gap: 8px; }}
  .fact {{ display: flex; align-items: baseline; gap: 12px;
    font-size: 21px; color: {T['text']}; line-height: 1.45; }}
  .fact-bullet {{
    width: 9px; height: 9px; border-radius: 3px; flex-shrink: 0;
    background: {T['accent']}; transform: translateY(-2px);
    display: inline-block;
  }}
  .footer {{
    margin-top: 8px; padding: 26px 30px;
    background: {T['card_bg']}; border: 1px dashed {T['card_border']};
    border-radius: 16px;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .footer-left {{ font-size: 22px; color: {T['dim']}; line-height: 1.6; }}
  .footer-left b {{ color: {T['text']}; }}
  .footer-right {{ font-size: 22px; color: {T['accent']}; font-weight: 700;
    white-space: nowrap; margin-left: 20px; }}
</style></head>
<body>
  <div class="header">
    <div class="brand">
      <div class="brand-mark">⚡</div>
      <div class="brand-text">AGI 日报</div>
      <div class="brand-theme">{issue_date} · 周{weekday}</div>
    </div>
    <div class="date-pill">📰 {issue_date} · 周{weekday} · 第 {date_str} 期</div>
    <div class="title">{_esc(title)}</div>
    <div class="subtitle">今日 AI 圈 {n_items} 条大事速览 · 数据来源于网络整理</div>
  </div>
  <div class="kb-bar">
    <div class="kb-stats">
      <b>{n_items}</b> 条大事<span class="vsep">|</span><b>{n_cats}</b> 个领域<span class="vsep">|</span><b>{n_facts}</b> 个看点
    </div>
    <div class="kb-chips">{kw_chips}</div>
  </div>
  {cards_html}
  <div class="footer">
    <div class="footer-left"><b>AGI 日报</b> · 每天帮你看完 AI 圈新动态<br>数据来源于网络整理, 详情以官方消息为准</div>
    <div class="footer-right">关注不迷路 →</div>
  </div>
</body></html>"""


def render_agi_image(title: str, meta: Dict, items: List[Dict],
                     out_path: str, width: int = 1080,
                     keywords: Optional[List[str]] = None,
                     theme_key: Optional[str] = None) -> str:
    """渲染 AGI 日报总览长图(Playwright 2x 截图, 主题默认按期号轮换)"""
    from playwright.sync_api import sync_playwright

    if not theme_key:
        theme_key = _agi_theme_for(meta.get("date") or "")
    html = build_agi_html(title, meta, items, keywords=keywords,
                          theme_key=theme_key)
    tmp_html = out_path + ".html"
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 800},
                                    device_scale_factor=2)
            page.goto("file://" + os.path.abspath(tmp_html))
            page.wait_for_timeout(500)
            page.screenshot(path=out_path, full_page=True)
            browser.close()
    finally:
        try:
            os.unlink(tmp_html)
        except OSError:
            pass
    print(f"[image] AGI 日报长图已生成(主题:{theme_key}): {out_path}")
    return out_path
