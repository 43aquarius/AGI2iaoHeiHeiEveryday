# -*- coding: utf-8 -*-
"""单项目安利帖: 自媒体运营类 GitHub 项目 → 每个项目一篇深挖帖

与 main.py 的榜单帖互补: 榜单帖(每周一)覆盖新面孔, 本脚本对
指定项目做单篇深挖(开头钩子/正文细节/亮点海报/风险提示)。

用法:
    python project_post.py --list                 # 查看待发清单与已发状态
    python project_post.py --repo NanmiCoder/MediaCrawler [--dry-run]
    python project_post.py --all [--interval 90]  # 逐个发布
state 记录: state.json project_posts.{full_name} (防重发, --force 忽略)
"""
import argparse
import datetime
import json
import os
import sys
import time
import traceback

import requests as _requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from github_trending import fetch_repo_meta, fetch_readme_excerpts, _clean_readme
from summarizer import (_call_llm, _extract_json, _STYLE_RULES,
                        _fmt_age, SYSTEM_PROMPT)
from image_maker import render_project_poster
from heybox_client import HeyboxClient
from heybox_publisher import HeyboxPublisher

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

API_BASE = "https://api.github.com"

# 上轮调研选定的自媒体运营项目(名称, 中文定位备注, 官方描述预填——API 失败时兕底)
REPOS = [
    ("NanmiCoder/MediaCrawler", "多平台内容采集爬虫",
     "小红书/抖音/快手/B站/微博/贴吧/知乎 多平台笔记与评论爬虫"),
    ("dreammis/social-auto-upload", "短视频自动分发上传",
     "自动化上传视频到社交媒体：抖音、小红书、视频号、tiktok、youtube、bilibili"),
    ("BetaStreetOmnis/xhs_ai_publisher", "AI生成内容+自动发布",
     "AI-powered Xiaohongshu/Rednote content creation and publishing tool"),
    ("aki66938/xhs-toolkit", "创作者MCP工具包",
     "小红书创作者MCP工具包，支持与AI客户端集成的内容创作和发布"),
    ("cv-cat/XHS_ALL_IN_ONE", "账号运营全链路工具箱",
     "XHS_ALL_IN_ONE 小红书全域运营"),
    ("jobsonlook/xhs-mcp", "小红书MCP服务",
     "小红书MCP服务，x-s x-t 签名js逆向"),
    ("hl897tech/xhs_content_agent", "内容运营智能体系统",
     "基于大模型的小红书内容运营智能 Agent 系统，从数据采集到内容生成再到自动发布"),
]

GEN_EXTRA = """
本帖特殊要求:
- 读者多数不是程序员,但关注"用 AI/自动化工具运营自媒体账号"这个话题
- 讲清楚: 这项目是什么、能干什么、怎么跑起来、有什么坑
- 风险(协议限制/封号/维护状态)必须如实写进 audience,不回避也不夸大
- 单帖深挖,不是榜单: 可以展开讲部署方式和具体功能细节
- 数字铁律: README 里"第N章"是连载章节编号,不是条目数量,禁止把章号
  写成"N个案例/方法"; 条目数量必须逐一点数后才能写,数不准就不写数"""

GEN_SCHEMA = """
输出纯 JSON(不要任何多余文字):
{
  "title": "帖子标题,8~20字。三选一: ①数字钩子(星数/平台数等具体数字) ②悬念反差(反常识事实) ③利益直给(能干什么+给谁用)。允许至多1个口语热度词(火了/杀疯了),禁新闻腔(强势登顶/霸榜/屠榜)",
  "tagline": "海报标语,10~18字,一句话卖点,不带句号",
  "intro": "正文开头,60~110字,从具体场景或反常识事实切入直接说事,禁'今天给大家介绍/最近发现'",
  "body": "正文主体,450~620字,用\\n\\n分成3~4段。必须用 README 里的具体细节: 功能名/支持平台/部署命令/依赖环境/实际用法。句子长短交替,像懂技术的朋友在聊天",
  "points": ["海报亮点,4~5条,每条14~30字,动词开头,必须带具体名词(平台名/功能名/数字)"],
  "audience": "适合谁+注意事项,90~160字。受众写成具体场景的人(如'想批量存档自己账号内容的')。风险如实: 协议限制/封号风险/维护状态/上手难度",
  "outro": "收尾,40~80字,平实收尾或一句实在建议,禁金句禁拔高禁'希望对你有帮助'"
}"""

REVIEW_PROMPT = """下面是一篇准备发到小黑盒社区的单项目安利帖文案(JSON)。
请你做"去AI味"终审编辑,逐句检查并重写有问题的句子:
假对比("不是…而是…")/单句金句收尾/意义拔高("改变了…方式")/
四字排比/互联网黑话(赋能/闭环/打造)/空转连接语(值得一提的是)/
万能比喻/导游腔(让我们看看)/客服腔(希望对你有帮助)/
万能受众句("…的用户会喜欢")/机械数据句("项目N个月前创建已获Y星")。

铁律:
- 事实(数字/功能名/平台名/风险点)一个字不能动、不能加、不能丢
- 每个字段改写后长度不小于原文的 90%(不许磨短)
- JSON 结构与字段名保持不变

输出修正后的完整 JSON,不要其他文字。

原文:
"""


# ---------------------------------------------------------------------------
# state / repo 数据
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 仓库元数据: 本地缓存 → core API → search API 兕底(core 配额有限且与
# 其他进程共享IP, search 是独立配额 10/min)
# ---------------------------------------------------------------------------

META_CACHE = os.path.join(BASE_DIR, ".cache", "project_meta.json")
META_TTL = 12 * 3600


def _load_pm_cache() -> dict:
    try:
        with open(META_CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_pm_cache(cache: dict):
    try:
        os.makedirs(os.path.dirname(META_CACHE), exist_ok=True)
        with open(META_CACHE, "w") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


def _fetch_meta_via_search(full_name: str) -> dict:
    """search API 拿全字段(独立配额,不占 core 60/h)"""
    try:
        r = _requests.get(
            f"{API_BASE}/search/repositories",
            params={"q": f"repo:{full_name}", "per_page": 1},
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "heybox-bot"}, timeout=20)
        items = (r.json() or {}).get("items") or []
        if not items:  # repo: 限定符不命中 → 全名文本搜索
            r = _requests.get(
                f"{API_BASE}/search/repositories",
                params={"q": full_name, "per_page": 5},
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "heybox-bot"}, timeout=20)
            items = [it for it in (r.json() or {}).get("items", [])
                     if it.get("full_name") == full_name]
        if items:
            it = items[0]
            return {
                "description": it.get("description") or "",
                "language": it.get("language") or "",
                "stars": it.get("stargazers_count", 0),
                "topics": it.get("topics") or [],
                "license": ((it.get("license") or {}).get("spdx_id") or ""),
                "created_at": it.get("created_at", ""),
                "pushed_at": it.get("pushed_at", ""),
            }
    except Exception:
        pass
    return {}


def build_repo(full_name: str, note: str, desc: str = "") -> dict:
    """拉取仓库元数据, 构造管线可用的 repo dict
    优先级: 本地缓存 → core /repos → search API → 预填描述兕底"""
    cache = _load_pm_cache()
    meta = {}
    hit = cache.get(full_name)
    if hit and time.time() - hit.get("_ts", 0) < META_TTL:
        meta = {k: v for k, v in hit.items() if not k.startswith("_")}
    if not meta:
        try:
            r = _requests.get(
                f"{API_BASE}/repos/{full_name}",
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "heybox-bot"}, timeout=20)
            if r.status_code == 200:
                d = r.json()
                meta = {
                    "description": d.get("description") or "",
                    "language": d.get("language") or "",
                    "stars": d.get("stargazers_count", 0),
                    "topics": d.get("topics") or [],
                    "license": ((d.get("license") or {}).get("spdx_id") or ""),
                    "created_at": d.get("created_at", ""),
                    "pushed_at": d.get("pushed_at", ""),
                }
        except Exception:
            pass
        if not meta:  # core 限流/失败 → search 兕底
            meta = _fetch_meta_via_search(full_name)
        if meta:
            cache[full_name] = {**meta, "_ts": time.time()}
            _save_pm_cache(cache)
    elif hit and "description" not in hit:  # 旧缓存缺字段, 补 search
        extra = _fetch_meta_via_search(full_name)
        if extra:
            meta = {**meta, **extra}
            cache[full_name] = {**meta, "_ts": time.time()}
            _save_pm_cache(cache)

    repo = {
        "rank": 1,
        "full_name": full_name,
        "description": meta.get("description") or desc or note,
        "language": meta.get("language", ""),
        "stars": meta.get("stars", 0),
        "period_stars": "",
        "topics": meta.get("topics", []),
        "license": meta.get("license", ""),
        "created_at": meta.get("created_at", ""),
        "pushed_at": meta.get("pushed_at", ""),
        "url": f"https://github.com/{full_name}",
        "note": note,
    }
    return repo


def fetch_readme_robust(full_name: str, max_chars: int = 2400) -> str:
    """三路 README 抓取: core api(占配额) → jsdelivr CDN(免配额) → raw
    core 配额与本环境其他进程共享 IP, 经常瞬时耗尽, jsdelivr 是主力兕底"""
    # 路1: core api
    try:
        r = _requests.get(
            f"{API_BASE}/repos/{full_name}/readme",
            headers={"Accept": "application/vnd.github.raw+json",
                     "User-Agent": "heybox-bot"}, timeout=15)
        if r.status_code == 200 and len(r.text) > 200:
            return _clean_readme(r.text)[:max_chars]
    except Exception:
        pass
    # 路2: jsdelivr CDN(main/master × 多文件名 × 2轮)
    for _attempt in range(2):
        for br in ("main", "master"):
            for fn in ("README.md", "readme.md", "README_zh.md",
                       "README-CN.md", "README_en.md"):
                try:
                    r = _requests.get(
                        f"https://cdn.jsdelivr.net/gh/{full_name}@{br}/{fn}",
                        timeout=15)
                    if r.status_code == 200 and len(r.text) > 200:
                        return _clean_readme(r.text)[:max_chars]
                except Exception:
                    continue
        time.sleep(3)
    # 路3: raw.githubusercontent(常被重置, 聊胜于无)
    for br in ("main", "master"):
        try:
            r = _requests.get(
                f"https://raw.githubusercontent.com/{full_name}/{br}/README.md",
                timeout=15)
            if r.status_code == 200 and len(r.text) > 200:
                return _clean_readme(r.text)[:max_chars]
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# LLM 文案生成(生成 + 终审两段)
# ---------------------------------------------------------------------------

def _gen_prompt(repo: dict) -> str:
    readme = (repo.get("readme_excerpt") or "")[:2400]
    lic = repo.get("license") or "未标注"
    facts = {
        "名称": repo["full_name"],
        "定位": repo["note"],
        "星数": repo.get("stars", 0),
        "语言": repo.get("language") or "未知",
        "协议": lic,
        "年龄": _fmt_age(repo.get("created_at", "")) or "未知",
        "最近更新": repo.get("pushed_at", "")[:10] or "未知",
        "一句话介绍": (repo.get("description") or "")[:300],
        "topics": ", ".join((repo.get("topics") or [])[:8]),
    }
    lines = [f"{k}: {v}" for k, v in facts.items() if v]
    if readme:
        lines.append("README 素材(节选):\n" + readme)
    return ("你要为下面这个 GitHub 开源项目写一篇小黑盒社区的"
            "单项目安利帖。\n\n项目信息:\n" + "\n".join(lines)
            + "\n" + GEN_SCHEMA + "\n" + _STYLE_RULES + GEN_EXTRA)


def _facts_fallback(repo: dict, content: dict) -> dict:
    """确定性兜底: 字段缺失时从事实素材补, 保证流程不断"""
    note = repo["note"]
    if not (content.get("title") or "").strip():
        name = repo["full_name"].split("/")[-1]
        content["title"] = f"{name}：{note},现在有{repo.get('stars', 0)}星"
    if not (content.get("tagline") or "").strip():
        content["tagline"] = (repo.get("description") or note)[:18]
    if not (content.get("intro") or "").strip():
        content["intro"] = (f"这轮调研自媒体自动化工具时翻到的项目,"
                            f"{repo['full_name']},主打{note}。"
                            f"星数{repo.get('stars', 0)},"
                            f"下面把玩法和坑一起说说。")
    if not (content.get("body") or "").strip():
        rd = (repo.get("readme_excerpt") or
              (repo.get("description") or ""))[:400]
        content["body"] = rd or f"项目地址里有完整文档,主打{note}。"
    if not (content.get("points") or []):
        content["points"] = [f"定位是{note}",
                             f"仓库现有{repo.get('stars', 0)}颗星",
                             f"主要语言 {repo.get('language') or '开源'}"]
    if not (content.get("audience") or "").strip():
        lic = repo.get("license") or ""
        warn = "注意看仓库协议再决定用途。" if lic in ("NOASSERTION", "") else ""
        content["audience"] = (f"想折腾自媒体自动化的可以拿它练手,"
                               f"部署要有 Python 基础。{warn}")
    if not (content.get("outro") or "").strip():
        content["outro"] = "感兴趣的直接去仓库看文档,链接在下面。"
    return content


def generate_content(repo: dict) -> dict:
    """生成 + 终审; 重试 3 次, 全部失败返回空 dict(由调用方跳过不发)
    长度硬校验: body < 320 字视为磨短失败, 重试时带加长反馈(2026-10-21)"""
    last_err = ""
    gen_prompt = _gen_prompt(repo)
    for attempt in (1, 2, 3):
        try:
            raw = _call_llm(gen_prompt, system=SYSTEM_PROMPT)
            data = _extract_json(raw)
            if not isinstance(data, dict) or not (data.get("body") or "").strip():
                raise ValueError("LLM 未返回有效 JSON/body")
            if len(_clean(data["body"])) < 320:
                got = len(_clean(data["body"]))
                raise ValueError(f"body 仅 {got} 字(<320), 要求加长")
            # 终审(去AI味, 事实不动)
            try:
                raw2 = _call_llm(REVIEW_PROMPT + json.dumps(
                    data, ensure_ascii=False), system=SYSTEM_PROMPT)
                data2 = _extract_json(raw2)
                if isinstance(data2, dict) and data2.get("body"):
                    data = {**data, **{k: v for k, v in data2.items()
                                       if k in data}}
            except Exception as e:
                print(f"   [warn] 终审失败用初稿: {e}")
            if len(_clean(data["body"])) < 300:   # 终审磨短硬线
                print(f"   [warn] 终审后 body 仅 {len(_clean(data['body']))} 字, 采用初稿")
                data_raw = _extract_json(raw)
                if data_raw and len(_clean(data_raw.get("body", ""))) >= 300:
                    data["body"] = data_raw["body"]
            return _facts_fallback(repo, data)
        except ValueError as e:
            last_err = str(e)[:120]
            print(f"   [warn] 生成第{attempt}次失败: {last_err}")
            # 带反馈重试: 明确要求加长并展开细节, 而非盲目重掷
            gen_prompt = (_gen_prompt(repo) +
                          "\n\n【上一版正文太短被退回。这一版必须满足:"
                          "body 至少 450 字、分 3~4 段,把 README 里的"
                          "玩法类别、案例章节、术语解释、姊妹仓库逐项"
                          "展开,宁可细不可略。】")
            time.sleep(5)
        except Exception as e:
            last_err = str(e)[:120]
            print(f"   [warn] 生成第{attempt}次失败: {last_err}")
            time.sleep(10)
    print("   [错误] LLM 三次均失败, 跳过本帖不发")
    return {}


# ---------------------------------------------------------------------------
# 组装与发布
# ---------------------------------------------------------------------------

def _clean(s: str) -> str:
    return ((s or "").replace("**", "").replace("`", "")
            .replace("\\n\\n", "\n\n").strip())


def build_segments(repo: dict, content: dict,
                   poster_path: str = None) -> list:
    segs = []
    for key in ("intro", "body", "audience", "outro"):
        t = _clean(content.get(key, ""))
        if t:
            segs.append(("text", t))
    segs.append(("text", f"🔗 {repo['url']}"))
    segs.append(("text", "#GitHub# #开源# #程序员# #AI#"))
    if poster_path:
        segs.append(("img", poster_path))
    return segs


def post_one(full_name: str, note: str, cfg: dict, desc: str = "",
             dry_run: bool = False, force: bool = False) -> dict:
    state = load_state()
    posted = (state.get("project_posts") or {}).get(full_name)
    if posted and not force and not dry_run:
        print(f"[跳过] {full_name} 已于 {posted['date']} 发布过: "
              f"{posted['url']} (--force 重发)")
        return {"skipped": True}

    print(f"===== [{full_name}] {note} =====")
    print("[1/5] 拉取仓库元数据 + README ...")
    repo = build_repo(full_name, note, desc)
    repo["readme_excerpt"] = fetch_readme_robust(full_name)
    print(f"   ★{repo['stars']} {repo['language']} "
          f"{_fmt_age(repo.get('created_at', ''))} "
          f"README {len(repo.get('readme_excerpt') or '')}字")

    print("[2/5] LLM 生成文案(生成+终审)...")
    content = generate_content(repo)
    if not content:
        return {"error": "llm_failed"}
    print(f"   标题: {content['title']}")
    body_len = len(_clean(content.get("body", "")))
    print(f"   正文 {body_len}字, 亮点 {len(content['points'])} 条")

    print("[3/5] 渲染单项目海报...")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    poster_path = os.path.join(
        OUTPUT_DIR, f"project_{full_name.split('/')[-1]}_{ts}.png")
    try:
        render_project_poster(repo, content, poster_path)
    except Exception as e:
        print(f"   [warn] 海报渲染失败,纯文字帖发布: {e}")
        poster_path = None

    print("[4/5] 组装帖子内容...")
    segments = build_segments(repo, content, poster_path)
    preview = os.path.join(
        OUTPUT_DIR, f"project_post_{full_name.split('/')[-1]}_{ts}.md")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(preview, "w") as f:
        f.write(f"# {content['title']}\n\n")
        for kind, val in segments:
            if kind == "text":
                f.write(val + "\n\n")
            else:
                f.write(f"![海报]({os.path.basename(val)})\n\n")
    print(f"   预览: {preview}")

    print("[5/5] 发布到小黑盒(盒友杂谈)...")
    client = HeyboxClient(cfg["cookie"])
    if not client.check_login_ok():
        print("   [错误] Cookie 已失效!")
        return {"error": "cookie"}
    publisher = HeyboxPublisher(client, topic_ids=str(cfg.get("topic_ids", "7214")))
    result = publisher.publish(title=content["title"], segments=segments,
                               dry_run=dry_run)
    if dry_run:
        print("   [DRY-RUN] 未真实发布")
        return {"dry_run": True, "content": content, "segments": segments}

    link = result.get("post_url", "")
    print(f"   ✅ 发布成功: {link}")
    state = load_state()
    state.setdefault("project_posts", {})[full_name] = {
        "date": datetime.date.today().isoformat(),
        "url": link, "title": content["title"]}
    save_state(state)
    return {"ok": True, "url": link, "title": content["title"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", help="指定 repo full_name")
    ap.add_argument("--all", action="store_true", help="清单逐个发布")
    ap.add_argument("--list", action="store_true", help="只看清单状态")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--interval", type=int, default=90,
                    help="--all 模式两帖间隔秒数(默认90)")
    args = ap.parse_args()

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    state = load_state()
    done = state.get("project_posts") or {}

    if args.list or (not args.repo and not args.all):
        print(f"{'状态':<4} {'项目':<40} {'星数':>7}  定位 / 已发记录")
        for full_name, note, _desc in REPOS:
            meta = _load_pm_cache().get(full_name) or {}
            rec = done.get(full_name)
            tag = "已发" if rec else "待发"
            extra = f"→ {rec['date']} {rec['title'][:18]}" if rec else ""
            print(f"{tag:<4} {full_name:<40} {meta.get('stars', '?'):>7}  {note} {extra}")
        return

    targets = []
    if args.repo:
        targets = [t for t in REPOS if t[0] == args.repo] or \
            [(args.repo, "开源项目", "")]
    else:
        targets = [t for t in REPOS
                   if args.force or t[0] not in done]

    results = []
    for i, (full_name, note, desc) in enumerate(targets):
        try:
            r = post_one(full_name, note, cfg, desc=desc,
                         dry_run=args.dry_run, force=args.force)
            results.append((full_name, r))
        except Exception:
            traceback.print_exc()
            results.append((full_name, {"error": "exception"}))
        if i < len(targets) - 1 and not args.dry_run:
            print(f"--- 休眠 {args.interval}s 后发下一篇 ---")
            time.sleep(args.interval)

    print("\n===== 汇总 =====")
    for full_name, r in results:
        if r.get("skipped"):
            status = "跳过(已发)"
        elif r.get("ok"):
            status = f"成功 {r['url']}"
        elif r.get("dry_run"):
            status = "干跑"
        else:
            status = f"失败 {r.get('error', '')}"
        print(f"  {full_name}: {status}")


if __name__ == "__main__":
    main()
