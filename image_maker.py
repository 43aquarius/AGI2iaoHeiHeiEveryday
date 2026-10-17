# -*- coding: utf-8 -*-
"""榜单长图生成器: HTML+CSS → Playwright 截图
设计: GitHub Dark 风格 + 小黑盒橙, 1080px 宽长图
"""
import datetime
import html as html_module
import json
import os
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


def _esc(s: str) -> str:
    return html_module.escape(str(s or ""))


def build_html(title: str, repos: List[Dict], summaries: List[Dict],
               period: str = "daily") -> str:
    """生成榜单长图 HTML"""
    today = datetime.datetime.now()
    date_str = today.strftime("%Y/%m/%d")
    period_cn = "日榜" if period == "daily" else "周榜"
    weekday = "一二三四五六日"[today.weekday()]

    cards = []
    for r in repos:
        rank = r["rank"]
        summary = ""
        for it in summaries:
            if it.get("rank") == rank:
                summary = it.get("summary", "")
                break
        color = LANG_COLORS.get(r.get("language") or "", "#8b949e")
        if rank in RANK_STYLES:
            badge_color, badge_text = RANK_STYLES[rank]
            badge = (f'<div class="rank-badge" style="background:{badge_color};'
                     f'color:#0d1117">{badge_text}</div>')
        else:
            badge = f'<div class="rank-badge rank-n">{rank}</div>'
        stars = r.get("stars", 0)
        stars_str = f"{stars/1000:.1f}k" if stars >= 1000 else str(stars)
        hot = _esc(r.get("period_stars") or "")
        # 新项目徽标: 创建100天以内
        new_pill = ""
        created = r.get("created_at") or ""
        if created:
            try:
                dt = datetime.datetime.fromisoformat(
                    created.replace("Z", "+00:00"))
                age_days = (datetime.datetime.now(
                    datetime.timezone.utc) - dt).days
                if 0 <= age_days <= 100:
                    new_pill = '<span class="new-pill">NEW</span>'
            except ValueError:
                pass
        cards.append(f"""
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
          {f'<span class="sep">·</span><span class="hot">🔥 {_esc(hot)}</span>' if hot else ''}
        </div>
        <div class="summary">{_esc(summary)}</div>
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
  .container {{ padding: 48px 40px 40px; }}
  .header {{
    background: linear-gradient(135deg, #161b22 0%, #1c2431 60%, #233042 100%);
    border: 1px solid #30363d;
    border-radius: 24px;
    padding: 44px 48px 38px;
    margin-bottom: 28px;
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
    mask: url("data:image/svg+xml,{('%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22%3E%3Cpath fill-rule=%22evenodd%22 d=%22M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z%22/%3E%3C/svg%3E')}") no-repeat center / contain;
    -webkit-mask: url("data:image/svg+xml,{('%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22%3E%3Cpath fill-rule=%22evenodd%22 d=%22M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z%22/%3E%3C/svg%3E')}") no-repeat center / contain;
    margin-bottom: 20px;
  }}
  .brand {{ font-size: 26px; color: #ff7d29; font-weight: 700;
    letter-spacing: 2px; margin-bottom: 8px; }}
  .title {{ font-size: 44px; font-weight: 800; line-height: 1.3;
    color: #f0f6fc; }}
  .subtitle {{ margin-top: 16px; font-size: 24px; color: #8b949e;
    display: flex; align-items: center; gap: 14px; }}
  .pill {{
    background: rgba(255,125,41,0.15); color: #ff7d29;
    border: 1px solid rgba(255,125,41,0.4); border-radius: 999px;
    padding: 4px 18px; font-size: 22px; font-weight: 600;
  }}
  .card {{
    background: #161b22; border: 1px solid #30363d;
    border-radius: 18px; padding: 26px 30px; margin-bottom: 18px;
  }}
  .card-head {{ display: flex; align-items: center; gap: 18px; margin-bottom: 12px; }}
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
  .repo-name {{ font-size: 27px; font-weight: 700; color: #58a6ff;
    word-break: break-all; }}
  .meta {{ display: flex; align-items: center; gap: 10px; font-size: 21px;
    color: #8b949e; margin-bottom: 12px; flex-wrap: wrap; }}
  .dot {{ width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0; }}
  .stars {{ color: #e3b341; font-weight: 600; }}
  .hot {{ color: #ff7b72; font-weight: 600; }}
  .sep {{ color: #484f58; }}
  .summary {{ font-size: 23px; line-height: 1.65; color: #c9d1d9; }}
  .footer {{
    margin-top: 26px; text-align: center; font-size: 20px; color: #484f58;
    line-height: 1.7;
  }}
  .footer .accent {{ color: #ff7d29; font-weight: 600; }}
</style></head>
<body>
  <div class="container">
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
    {cards_html}
    <div class="footer">
      每日同步 GitHub 开源热榜 · 榜单由 <span class="accent">小黑盒盒友</span> 为你解读<br>
      已过滤 claude-code 等老牌项目 · 完整项目链接见帖子正文
    </div>
  </div>
</body></html>"""


def render_long_image(title: str, repos: List[Dict], summaries: List[Dict],
                      out_path: str, period: str = "daily",
                      width: int = 1080) -> str:
    """渲染长图并保存到 out_path, 返回路径"""
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
    # 读取尺寸
    from PIL import Image
    with Image.open(out_path) as im:
        w, h = im.size
    print(f"[image] 长图已生成: {out_path} ({w}x{h})")
    return out_path


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/z/my-project/github-hot-heybox")
    from github_trending import fetch_trending
    from summarizer import summarize_repos

    repos = fetch_trending("daily", top_n=10)
    data = summarize_repos(repos, "daily")
    out = render_long_image(
        data["title"], repos, data["items"],
        "/home/z/my-project/github-hot-heybox/output/test_long_image.png",
        "daily")
    print("done:", out)
