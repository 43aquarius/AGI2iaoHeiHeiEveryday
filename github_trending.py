# -*- coding: utf-8 -*-
"""GitHub Trending 抓取器(日榜/周榜) — 新项目优先
数据源:
  1. 主源: github.com/trending 页面解析(真实榜单,全部候选)
  2. 补位/降级: GitHub Search API(近期新建且星标多的仓库)

老项目过滤(需求: claude-code / ECC / claude-plugins-official 这类早就
火过的项目没有介绍价值,榜单主打新项目):
  - 黑名单: 用户点名 + 常青大牌,永不入选
  - 旧闻判定: 「总星数高 + 本期增速占比低」= 大部分星是之前涨的,火过劲了
      stars >= 30k  且 ratio < 0.10  → 旧闻
      stars >= 100k 且 ratio < 0.25  → 旧闻
      年龄 >= 30个月 且 ratio < 0.05 → 旧闻
    其中 ratio = 本期新增星 / 总星数(本期星里新粉丝占比)
    反例: rea 本周 83% 的星都是新涨的 → 真正在爆发,保留
  - 新颖度排序: ratio(45分) + 创建时间新(25分) + 绝对热度(20分)
  - 候选不足 top_n: 先用 Search API 补"最近创建"的新项目,
    实在不够才让旧闻项目垫底(黑名单仍然绝不入选)
"""
import html as html_module
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

TRENDING_URL = "https://github.com/trending"
API_BASE = "https://api.github.com"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 用户点名"早就火过"的项目 + 常青大牌(偶尔上榜,对读者没有信息量)
FAMOUS_BLACKLIST = {
    "anthropics/claude-code",
    "anthropics/claude-plugins-official",
    "affaan-m/ecc",
    "nationalsecurityagency/ghidra",
    "freecodecamp/freecodecamp",
    "sindresorhus/awesome",
    "kamranahmedse/developer-roadmap",
    "donnemartin/system-design-primer",
    "krahets/hello-algo",
    "doocs/advanced-java",
    "labuladong/fucking-algorithm",
    "snailclimb/javaguide",
}

# ---------------------------------------------------------------- 元数据缓存

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_BASE_DIR, ".cache")
META_CACHE_PATH = os.path.join(CACHE_DIR, "repo_meta.json")
META_CACHE_TTL = 12 * 3600  # 12小时


def _env_token(token: str = "") -> str:
    return token or os.environ.get("GITHUB_TOKEN", "")


def _load_meta_cache() -> dict:
    try:
        with open(META_CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_meta_cache(cache: dict):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(META_CACHE_PATH, "w") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


def fetch_repo_meta(full_name: str, token: str = "", timeout: int = 20) -> dict:
    """仓库元数据(12h 磁盘缓存)。created_at 是老项目过滤的关键字段。
    API 失败时允许用过期缓存兜底。"""
    token = _env_token(token)
    cache = _load_meta_cache()
    hit = cache.get(full_name)
    now = time.time()
    if hit and now - hit.get("_ts", 0) < META_CACHE_TTL:
        return {k: v for k, v in hit.items() if not k.startswith("_")}
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(f"{API_BASE}/repos/{full_name}",
                            headers=headers, timeout=timeout)
        if resp.status_code == 200:
            d = resp.json()
            meta = {
                "created_at": d.get("created_at", ""),
                "stars": d.get("stargazers_count", 0),
                "topics": d.get("topics", []),
                "license": ((d.get("license") or {}).get("spdx_id") or ""),
                "homepage": d.get("homepage") or "",
                "avatar_url": (d.get("owner") or {}).get("avatar_url", ""),
                "open_issues": d.get("open_issues_count", 0),
                "pushed_at": d.get("pushed_at", ""),
            }
            cache[full_name] = {**meta, "_ts": now}
            _save_meta_cache(cache)
            return meta
    except Exception:
        pass
    if hit:  # API 失败,过期缓存兜底
        return {k: v for k, v in hit.items() if not k.startswith("_")}
    return {}


def _apply_meta(repo: Dict, meta: dict) -> Dict:
    if not meta:
        repo.setdefault("created_at", "")
        repo.setdefault("topics", [])
        return repo
    repo["stars"] = meta.get("stars") or repo.get("stars", 0)
    for key in ("created_at", "topics", "license", "homepage",
                "avatar_url", "pushed_at"):
        if meta.get(key) is not None:
            repo[key] = meta[key]
    repo["open_issues"] = meta.get("open_issues", 0)
    return repo


# ---------------------------------------------------------------- 工具

def _parse_gained(period_stars: str) -> int:
    """'1,383 stars today' / 'new repo, 204 stars this period' → 数字"""
    if not period_stars:
        return 0
    m = re.search(r"([\d,]+)", period_stars)
    return int(m.group(1).replace(",", "")) if m else 0


def _age_months(created_at: str) -> Optional[float]:
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(
            created_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    days = (datetime.now(timezone.utc) - dt).days
    return max(days, 0) / 30.4


# ---------------------------------------------------------------- 老项目过滤

def _is_blacklisted(full_name: str) -> bool:
    fn = full_name.lower()
    if fn in FAMOUS_BLACKLIST:
        return True
    if fn.startswith("thealgorithms/"):  # 教学算法系列,常年大牌
        return True
    return False


def is_old_news(repo: Dict) -> Tuple[bool, str]:
    """旧闻判定: 大部分星是之前涨的(火过劲了),现在再介绍没有价值"""
    if repo.get("source") == "search_api":
        return False, ""  # 搜索补位的项目创建时间本身就新
    stars = repo.get("stars", 0)
    gained = _parse_gained(repo.get("period_stars", ""))
    ratio = gained / max(stars, 1) if stars else 1.0
    age = _age_months(repo.get("created_at", ""))
    if stars >= 300_000 and ratio < 0.30:
        return True, "顶级知名项目,该知道早知道了"
    if stars >= 100_000 and ratio < 0.25:
        return True, "早已大红大紫(10万+星,本期增速占比仅{:.0%})".format(ratio)
    if stars >= 30_000 and ratio < 0.10:
        return True, "火过劲了(星数{:.0f}k,本期新增占比仅{:.0%})".format(
            stars / 1000, ratio)
    if age is not None and age >= 30 and ratio < 0.05 and stars >= 5_000:
        return True, "老项目(两年半以上,靠老本偶尔上榜)"
    return False, ""


def novelty_score(repo: Dict, recent_names: Optional[set] = None) -> float:
    """新颖度评分(越高越值得介绍): 增速占比 + 创建新 + 绝对热度"""
    gained = _parse_gained(repo.get("period_stars", ""))
    stars = max(repo.get("stars", 0), 1)
    if repo.get("source") == "search_api":
        ratio = 1.0  # 新建项目,全部星都是"近期新增"
    else:
        ratio = min(gained / stars, 1.0)
    score = ratio * 45.0
    age = _age_months(repo.get("created_at", ""))
    if age is not None:
        score += max(0.0, 25.0 * (1.0 - age / 24.0))  # 24个月衰减到0
    if gained > 0:
        score += min(20.0, math.log10(gained + 1) / 5.0 * 20.0)
    if recent_names and repo["full_name"] in recent_names:
        score -= 20  # 最近发过,重降权换新面孔(2026-10-18: 周末榜单不动,
        # -12 不够把新面孔顶上来,提到 -20 让 Search API 新项目能挤进来)
    return score


def _cleanup_search_repos(repos: List[Dict],
                          recent_names: Optional[set] = None) -> List[Dict]:
    """Search API 结果的清洗: 去黑名单、近期发过的沉底、重排、清显示字段"""
    repos = [r for r in repos if not _is_blacklisted(r["full_name"])]
    if recent_names:
        repos.sort(key=lambda r: (r["full_name"] in recent_names,
                                  -r.get("stars", 0)))
    else:
        repos.sort(key=lambda r: -r.get("stars", 0))
    for i, r in enumerate(repos, 1):
        r["rank"] = i
        r["period_stars"] = ""  # 英文占位串不进帖子展示
    return repos


def select_repos(candidates: List[Dict], top_n: int, since: str,
                 token: str = "", recent_names: Optional[set] = None
                 ) -> Tuple[List[Dict], List[str]]:
    """从全量候选中选出 top_n 个"值得介绍的新项目"
    返回 (选中列表[已重排rank], 过滤日志)
    """
    log: List[str] = []
    fresh: List[Dict] = []
    old: List[Dict] = []
    for r in candidates:
        if _is_blacklisted(r["full_name"]):
            log.append(f"[黑名单] {r['full_name']} ★{r.get('stars', 0)}"
                       f" — 常年大牌/用户点名,永不入选")
            continue
        is_old, reason = is_old_news(r)
        if is_old:
            old.append(r)
            log.append(f"[旧闻过滤] {r['full_name']} ★{r.get('stars', 0)}"
                       f" {r.get('period_stars', '')} — {reason}")
        else:
            fresh.append(r)

    fresh.sort(key=lambda r: novelty_score(r, recent_names), reverse=True)
    selected = fresh[:top_n]

    # 补位1: Search API 找"最近创建且有星"的新项目
    if len(selected) < top_n:
        need = top_n - len(selected)
        exclude = {r["full_name"] for r in selected}
        try:
            fills = fetch_trending_via_api(
                since, top_n=need + 5, token=token,
                created_days=90 if since == "daily" else 150,
                min_stars=250 if since == "daily" else 1200)
        except Exception as e:
            fills = []
            log.append(f"[补位失败] Search API 不可用: {e}")
        for f in fills:
            if len(selected) >= top_n:
                break
            if f["full_name"] in exclude or _is_blacklisted(f["full_name"]):
                continue
            if f.get("stars", 0) >= 50_000:
                continue  # 新建但已经火遍全网,同样没有介绍价值
            selected.append(f)
            exclude.add(f["full_name"])
            log.append(f"[新项目补位] {f['full_name']} ★{f.get('stars', 0)}"
                       f" — 近期新建(Search API)")

    # 换血(2026-10-18): 与近期发过的高度重叠时(榜单僵盘,如周末),
    # 主动拉新项目替换掉已发过的尾部项目,避免连续两期内容几乎一样
    if recent_names:
        overlap = [r for r in selected if r["full_name"] in recent_names]
        if len(overlap) >= 6:
            keep = 3  # 保留 3 个(通常是今天真正在爆的),其余换血
            replace = len(overlap) - keep
            log.append(f"[换血] 与近期发过重叠 {len(overlap)}/{top_n},"
                       f"尝试替换 {replace} 个换新面孔")
            try:
                fills = fetch_trending_via_api(
                    since, top_n=replace + 8, token=token,
                    created_days=90 if since == "daily" else 150,
                    min_stars=250 if since == "daily" else 1200)
            except Exception:
                fills = []
            exclude = {r["full_name"] for r in selected}
            # 沉底排序: 发过的沉底,新面孔优先
            new_faces = [f for f in fills
                         if f["full_name"] not in exclude
                         and not _is_blacklisted(f["full_name"])
                         and f.get("stars", 0) < 50_000
                         and f["full_name"] not in recent_names]
            # 把 selected 尾部的 overlap 项目换掉(novelty 本来就低)
            overlap_sorted = sorted(overlap, key=lambda r: novelty_score(
                r, recent_names))  # 分最低的在前面,先被换掉
            for victim in overlap_sorted[:replace]:
                if not new_faces:
                    break
                fresh_face = new_faces.pop(0)
                selected.remove(victim)
                selected.append(fresh_face)
                exclude.add(fresh_face["full_name"])
                log.append(f"[换血] {victim['full_name']} → "
                           f"{fresh_face['full_name']} "
                           f"★{fresh_face.get('stars', 0)}(近期未发过)")
            # 重排: 按新颖度
            selected.sort(key=lambda r: novelty_score(r, recent_names),
                          reverse=True)

    # 补位2: 候选实在不够,旧闻项目垫底(黑名单仍然绝不入选)
    if len(selected) < top_n and old:
        old.sort(key=lambda r: novelty_score(r, recent_names), reverse=True)
        for r in old:
            if len(selected) >= top_n:
                break
            selected.append(r)
            log.append(f"[垫底回填] {r['full_name']} ★{r.get('stars', 0)}"
                       f" — 候选不足,勉强保留")

    for i, r in enumerate(selected, 1):
        r["rank"] = i
        if r.get("source") == "search_api":
            r["period_stars"] = ""  # "new repo, N stars"英文串不进帖子展示
    return selected, log


# ---------------------------------------------------------------- 抓取

def _get_with_retry(session: requests.Session, url: str, params: dict,
                    timeout: int = 30, retries: int = 4) -> requests.Response:
    last_err = None
    for i in range(retries):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_err = e
            time.sleep(2 * (i + 1))
    raise last_err


def _fetch_trending_page(since: str, language: str,
                         timeout: int) -> List[Dict]:
    """解析 trending 页面的全部候选(通常 13~25 个,数量本身会浮动)"""
    session = requests.Session()
    session.headers.update({"User-Agent": UA,
                            "Accept-Language": "en-US,en;q=0.9"})
    params = {"since": since}
    if language:
        params["language"] = language
    resp = _get_with_retry(session, TRENDING_URL, params, timeout=timeout)
    html = resp.text

    repos = []
    for art in re.findall(r'<article class="Box-row">(.*?)</article>', html, re.S):
        # 仓库全名 owner/repo — 只认 h2 标题内的链接(跳过login/star等链接)
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="/([^"]+)"', art, re.S)
        if not m:
            continue
        full_name = m.group(1).strip().strip("/")
        desc = ""
        md = re.search(r'<p class="col-9[^"]*">\s*(.*?)\s*</p>', art, re.S)
        if md:
            desc = html_module.unescape(
                re.sub(r"<[^>]+>", "", md.group(1))).strip()
        lang = ""
        ml = re.search(r'itemprop="programmingLanguage">([^<]+)<', art)
        if ml:
            lang = ml.group(1).strip()
        stars = 0
        ms = re.search(r'href="/[^"]+/stargazers[^"]*".*?</svg>\s*([\d,]+)', art, re.S)
        if ms:
            stars = int(ms.group(1).replace(",", ""))
        forks = 0
        mf = re.search(r'href="/[^"]+/forks[^"]*".*?</svg>\s*([\d,]+)', art, re.S)
        if mf:
            forks = int(mf.group(1).replace(",", ""))
        period_stars = ""
        mp = re.search(r'([\d,]+)\s+stars\s+(today|this week|this month)', art)
        if mp:
            unit = {"today": "今日", "this week": "本周",
                    "this month": "本月"}.get(mp.group(2), "")
            period_stars = f"{unit}+{mp.group(1)}星"

        repos.append({
            "rank": len(repos) + 1,
            "full_name": full_name,
            "name": full_name.split("/")[-1],
            "url": f"https://github.com/{full_name}",
            "api_url": f"{API_BASE}/repos/{full_name}",
            "description": desc,
            "language": lang,
            "stars": stars,
            "forks": forks,
            "period_stars": period_stars,
            "source": "trending_page",
        })
    if not repos:
        raise RuntimeError("trending 页面解析出 0 个仓库(页面结构可能已变化)")
    return repos


def fetch_trending_via_api(since: str = "daily", top_n: int = 10,
                           token: str = "", timeout: int = 20,
                           created_days: int = None,
                           min_stars: int = None) -> List[Dict]:
    """Search API: 近期新建且星标最多的仓库(补位/降级用)
    默认 daily→最近14天新建 / weekly→最近45天新建
    """
    now = datetime.now(timezone.utc)
    if created_days is None:
        created_days = 14 if since != "weekly" else 45
    if min_stars is None:
        min_stars = 300 if since != "weekly" else 800
    created_after = (now - timedelta(days=created_days)).strftime("%Y-%m-%d")
    token = _env_token(token)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    q = (f"created:>{created_after} stars:>{min_stars} "
         f"pushed:>{(now - timedelta(days=7)).strftime('%Y-%m-%d')} "
         f"fork:false archived:false")
    params = {"q": q, "sort": "stars", "order": "desc",
              "per_page": min(top_n * 2, 30)}
    resp = requests.get(f"{API_BASE}/search/repositories", params=params,
                        headers=headers, timeout=timeout)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    repos = []
    for it in items[:top_n]:
        repos.append({
            "rank": len(repos) + 1,
            "full_name": it["full_name"],
            "name": it["name"],
            "url": it["html_url"],
            "api_url": it["url"],
            "description": it.get("description") or "",
            "language": it.get("language") or "",
            "stars": it.get("stargazers_count", 0),
            "forks": it.get("forks_count", 0),
            "period_stars": f"new repo, {it.get('stargazers_count', 0)} stars this period",
            "topics": it.get("topics", []),
            "license": ((it.get("license") or {}).get("spdx_id") or ""),
            "homepage": it.get("homepage") or "",
            "created_at": it.get("created_at", ""),
            "avatar_url": (it.get("owner") or {}).get("avatar_url", ""),
            "source": "search_api",
        })
    return repos


def fetch_trending(since: str = "daily", language: str = "",
                   top_n: int = 10, timeout: int = 30, token: str = "",
                   recent_names: Optional[set] = None,
                   fresh_filter: bool = True) -> List[Dict]:
    """抓取热榜并选出 top_n 个值得介绍的新项目
    recent_names: 最近已发过的仓库全名(降权,换新面孔)
    """
    token = _env_token(token)
    try:
        candidates = _fetch_trending_page(since, language, timeout=timeout)
    except Exception as e:
        print(f"[trending] 页面抓取失败({e}), 降级到 Search API(近期新项目)")
        repos = fetch_trending_via_api(since, top_n=top_n, token=token)
        return _cleanup_search_repos(repos, recent_names)

    if not fresh_filter:  # 兼容旧行为:不做过滤直接截断
        return candidates[:top_n]

    # 拉元数据(created_at 是过滤关键),12h缓存,重复运行不重复请求
    for r in candidates:
        _apply_meta(r, fetch_repo_meta(r["full_name"], token=token))

    selected, log = select_repos(candidates, top_n, since, token=token,
                                 recent_names=recent_names)
    print(f"[filter] trending 候选 {len(candidates)} 个 → 入选 "
          f"{len(selected)} 个(老项目过滤+新项目优先)")
    if log:
        print("\n".join("   " + line for line in log))
    return selected


# ---------------------------------------------------------------- README

def _clean_readme(text: str) -> str:
    """去掉 README 开头的徽章/图片/HTML 噪音,保留正文"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", "", text)   # badge 链接
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)                # 图片
    text = re.sub(r"<[^>]+>", "", text)                              # HTML 标签
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)                  # 链接→纯文字
    text = re.sub(r"^\s*[#>\-*].{0,80}$", "", text, flags=re.M)      # 极短标题/引用行
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_readme_excerpts(repos: List[Dict], token: str = "",
                          timeout: int = 15,
                          max_chars: int = 2400) -> List[Dict]:
    """抓取每个仓库 README 开头段落,给 LLM 提供真实写作素材
    (素材充足是去AI味的前提:没有细节可写,模型只能输出空话)
    主路: api.github.com /repos/{x}/readme (Accept: raw)
    降级: raw.githubusercontent.com 直链
    """
    token = _env_token(token)
    session = requests.Session()
    session.headers.update({"User-Agent": UA,
                            "Accept": "application/vnd.github.raw+json"})
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    for repo in repos:
        excerpt = ""
        try:
            resp = session.get(f"{API_BASE}/repos/{repo['full_name']}/readme",
                               timeout=timeout)
            if resp.status_code == 200 and len(resp.text) > 200:
                excerpt = _clean_readme(resp.text)[:max_chars]
        except Exception:
            pass
        if not excerpt:
            for branch in ("main", "master"):
                for fname in ("README.md", "readme.md"):
                    url = (f"https://raw.githubusercontent.com/"
                           f"{repo['full_name']}/{branch}/{fname}")
                    try:
                        resp = session.get(url, timeout=timeout)
                        if resp.status_code == 200 and len(resp.text) > 200:
                            excerpt = _clean_readme(resp.text)[:max_chars]
                            break
                    except Exception:
                        continue
                if excerpt:
                    break
        repo["readme_excerpt"] = excerpt
        time.sleep(0.4)  # 温和限速
    ok = sum(1 for r in repos if r.get("readme_excerpt"))
    print(f"[readme] {ok}/{len(repos)} 个仓库抓到 README 素材")
    return repos


def enrich_details(repos: List[Dict], token: str = "",
                   timeout: int = 20) -> List[Dict]:
    """补全仓库详情(fetch_trending 已预热缓存,这里基本走缓存)"""
    token = _env_token(token)
    for repo in repos:
        _apply_meta(repo, fetch_repo_meta(repo["full_name"], token=token,
                                          timeout=timeout))
    return repos


if __name__ == "__main__":
    print("=== 日榜(新项目优先) ===")
    repos = fetch_trending("daily", top_n=10)
    for r in repos:
        print(f"{r['rank']:>2}. {r['full_name']} ⭐{r['stars']} "
              f"[{r['language']}] {r['period_stars']}")
        print(f"    {r['description'][:70]}")
