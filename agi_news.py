# -*- coding: utf-8 -*-
"""每日 AGI 新闻速递 → 小黑盒图文帖

数据链路(2026-10-18 新增):
  1. ima 分享页(免登录 SSR) → 解析 turbo-stream → 最新一期元数据
     (期号/标题/微信原文链接/摘要)
  2. 微信公众号原文 → 结构化提取(章节=新闻条目, 每条 3 要点+原文链接)
  3. LLM 生成口语化讲解(复用 summarizer 的 _call_llm/_STYLE_RULES 红线)
  4. AGI 日报长图(image_maker.render_agi_image)
  5. 发布到小黑盒盒友杂谈

⚠️ 内容红线: 帖子中【不标明数据来自 ima】, 只说"数据来源于网络整理"。
防重复: state.json 的 agi_last_issue 记录已发布的期号(如 "20261008"),
同一期不重发——知识库同步比公众号源有延迟, 没有新期就跳过。

用法:
    python agi_news.py --dry-run   # 只构造不发布
    python agi_news.py             # 正式发布(最新期未发过时)
    python agi_news.py --force     # 忽略防重标记强制发
"""
import argparse
import datetime
import html as html_module
import json
import os
import re
import sys
import traceback
import urllib.request
from typing import Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from summarizer import _call_llm, _extract_json, _STYLE_RULES
from heybox_client import HeyboxClient
from heybox_publisher import HeyboxPublisher

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
STATE_PATH = os.path.join(BASE_DIR, "state.json")

IMA_SHARE_URL = ("https://ima.qq.com/wiki/?shareId="
                 "c2cfaed7f9378e4869a5588a339dc550a84a25382bd68b661843c6b7e432ce24")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

NUM_EMOJI = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
             6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
             11: "1️⃣1️⃣", 12: "1️⃣2️⃣"}

# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


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


def _http_get(url: str, timeout: int = 30, retries: int = 3) -> str:
    """GET + gzip 自动解压 + 重试(微信文章 3.5MB 裸传易断流, gzip 后 <1MB)"""
    import gzip as _gzip
    import io as _io
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA,
                              "Accept-Encoding": "gzip, deflate"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            if raw[:2] == b"\x1f\x8b":        # gzip magic
                raw = _gzip.GzipFile(fileobj=_io.BytesIO(raw)).read()
            return raw.decode("utf-8", "ignore")
        except Exception as e:
            last_err = e
            print(f"   [warn] 请求失败(第{attempt+1}次): {e}")
    raise RuntimeError(f"GET 重试{retries}次仍失败: {url[:80]} ({last_err})")


# ---------------------------------------------------------------------------
# Step 1: ima 分享页 → 最新一期元数据 (免登录 SSR, turbo-stream 解码)
# ---------------------------------------------------------------------------


def _js_unescape(s: str) -> str:
    """JS 字符串字面量 → 实际字符串(中文原样保留)"""
    def rep(mo):
        e = mo.group(1)
        if e == "n":
            return "\n"
        if e == "r":
            return "\r"
        if e == "t":
            return "\t"
        if e in ('"', "'", "\\"):
            return e
        if e == "b":
            return "\b"
        if e == "f":
            return "\f"
        if e.startswith("u"):
            return chr(int(e[1:], 16))
        if e.startswith("x"):
            return chr(int(e[1:], 16))
        return mo.group(0)
    return re.sub(r'\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)', rep, s)


def _turbo_decode(data: list):
    """解码 Remix turbo-stream 扁平数组(loaderData 部分)"""
    memo = {}

    def decode_at(pos):
        if pos in memo:
            return memo[pos]
        if not (0 <= pos < len(data)):
            return None
        val = data[pos]
        memo[pos] = None  # 防循环
        result = decode_val(val, pos)
        memo[pos] = result
        return result

    def decode_val(val, pos):
        if isinstance(val, dict):
            out = {}
            for k, v in val.items():
                if not k.startswith("_"):
                    out[k] = v
                    continue
                try:
                    key_pos = int(k[1:])
                except ValueError:
                    continue
                key = data[key_pos] if 0 <= key_pos < len(data) else key_pos
                if isinstance(v, bool) or not isinstance(v, int):
                    continue
                if v < 0:       # 类型标记(null/undefined 等)
                    continue
                out[key] = decode_at(v)
            return out
        if isinstance(val, list):
            # 数组被编码为纯值索引数组 [12, 88, ...] → 逐个递归解码
            return [decode_at(i) if isinstance(i, int) else i for i in val]
        if isinstance(val, int) and val < 0:
            nxt = pos + 1
            return data[nxt] if 0 <= nxt < len(data) else None
        return val

    if data and isinstance(data[0], dict):
        return decode_at(0)
    return None


def fetch_share_data(share_url: str = IMA_SHARE_URL) -> List[Dict]:
    """抓 ima 分享页首屏条目列表(按时间倒序, 最新在前)"""
    html = _http_get(share_url)
    chunks = re.findall(
        r'window\.__remixContext\.streamController\.enqueue\("(.*?)"\);',
        html, re.S)
    if not chunks:
        raise RuntimeError("ima 分享页未找到 stream 数据(页面结构可能变了)")
    data = json.loads(_js_unescape(chunks[0]))
    root = _turbo_decode(data)
    loader = (root or {}).get("loaderData") or {}
    route = loader.get("routes/_index") or loader.get("routes/_index.tsx") or {}
    init = route.get("initData") or {}
    return [it for it in (init.get("list") or [])
            if isinstance(it, dict)]


def latest_issue_meta() -> Dict:
    """最新一期速递元数据: date(期号)/title/source_url/abstract/publish_time"""
    items = fetch_share_data()
    best = None
    for it in items:
        title = (it.get("title") or "").strip()
        m = re.search(r"(\d{8})", title)
        if not m:
            continue
        cand = {
            "date": m.group(1),
            "title": title,
            "source_url": (it.get("sourcePath") or "").strip(),
            "abstract": (it.get("abstract") or "").strip(),
            "introduction": (it.get("introduction") or "").strip(),
            "img_url": ((it.get("imgUrls") or [""])[0] or "").strip(),
            "ts_ms": str(it.get("createTime") or "0"),
        }
        if best is None or cand["date"] > best["date"]:
            best = cand
    if not best:
        raise RuntimeError("分享页条目里没有带日期的速递")
    return best


# ---------------------------------------------------------------------------
# Step 2: 微信原文 → 章节切分(每章 = 一条新闻)
# ---------------------------------------------------------------------------

_JS_NOISE = re.compile(
    r"^(var |if \(|document\.|window\.|function|setTimeout|require\(|"
    r"Object\.defineProperty|try \{|})")


def _html_unescape(s: str) -> str:
    for a, b in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&nbsp;", " "), ("&mdash;", "—"),
                 ("&ndash;", "–"), ("&hellip;", "…"), ("&ldquo;", "“"),
                 ("&rdquo;", "”"), ("&middot;", "·")]:
        s = s.replace(a, b)
    return re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)


def extract_wx_text(html: str) -> str:
    """微信正文 → 带换行纯文本(js_content 内, 块级标签→换行, JS 噪音截断)"""
    m = re.search(r'<div[^>]*id="js_content"[^>]*>(.*)', html, re.S)
    body = m.group(1) if m else html
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S)
    body = re.sub(r"</(p|section|div|h\d|li|blockquote)>", "\n", body)
    body = re.sub(r"<br[^>]*>", "\n", body)
    body = re.sub(r"<[^>]+>", "", body)
    body = _html_unescape(body)
    lines, stop = [], False
    for ln in body.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if _JS_NOISE.match(ln) or ln.startswith("腾讯研究院AI速递") and len(ln) > 200:
            stop = True
        if stop:
            break
        lines.append(ln)
    return "\n".join(lines)


# 微信页面 UI/推广杂质黑名单(速递正文结束后会拖带这些短行)
_WX_NOISE_EXACT = {
    "关注该公众号", "微信扫一扫可打开此内容，", "使用完整服务",
    "，轻点两下取消赞", "，轻点两下取消在看", "预览时标签不可点",
    "喜欢此内容的人还喜欢", "继续访问", "轻点两下取消赞", "轻点两下取消在看",
}


def _is_wx_noise(ln: str) -> bool:
    if ln.startswith("👇") or ln in _WX_NOISE_EXACT:
        return True
    if "加入AGI数据库" in ln or "订阅下方合集" in ln:
        return True
    return False


def split_sections(text: str) -> List[Dict]:
    """按 '一、标题' 切分章节; 每章 = 标题 + 若干要点行 + 原文链接"""
    pat = re.compile(r"^([一二三四五六七八九十]{1,3})、(.{4,80})$")
    sections, cur = [], None
    for ln in text.split("\n"):
        m = pat.match(ln)
        if m:
            if cur:
                sections.append(cur)
            cur = {"num_cn": m.group(1),
                   "title": m.group(2).strip(), "points": [], "link": ""}
            continue
        if cur is None:
            continue
        if _is_wx_noise(ln):
            continue
        lm = re.match(r"^(https?://\S+)$", ln)
        if lm and "mp.weixin.qq.com" in ln:
            if not cur["link"]:
                cur["link"] = lm.group(1)
            continue
        # 要点行: "N.xxx" 编号格式 或 ≥40字的实质内容行
        if re.match(r"^\d+[\.、]", ln) and len(ln) >= 6:
            cur["points"].append(ln)
        elif len(ln) >= 40:
            cur["points"].append(ln)
    if cur:
        sections.append(cur)
    # 噪音防御: 单章要点超长则截断
    for s in sections:
        if len("".join(s["points"])) > 900:
            s["points"] = s["points"][:6]
    return sections


def fetch_issue_content(meta: Dict) -> List[Dict]:
    """抓微信原文并切分章节"""
    url = meta.get("source_url") or ""
    if not url:
        raise RuntimeError("最新一期没有微信原文链接")
    html = _http_get(url, timeout=40)
    text = extract_wx_text(html)
    sections = split_sections(text)
    if len(sections) < 3:
        raise RuntimeError(f"章节切分异常: 只切出 {len(sections)} 章"
                           f"(预期 8-12), 原文结构可能变了")
    return sections


# ---------------------------------------------------------------------------
# Step 3: LLM 文案
# ---------------------------------------------------------------------------

AGI_SYSTEM = (
    "你在小黑盒(游戏社区)数码区写《AGI 日报》专栏, 读者是关注数码科技和"
    "AI 动态的普通玩家。你的文风像一个天天刷 AI 新闻的朋友在聊天: 句子有"
    "长有短, 直接说事, 信息密度高, 偶尔吐槽, 没有套话。输出必须是纯 JSON,"
    "不要任何多余文字。"
)

AGI_EXTRA_RULES = """
【AGI 日报特有红线(叠加在通用红线之上)】
- 数据来源描述: 全文【禁止】出现 "ima"、"知识库"、"腾讯研究院"字样;
  来源只能模糊表述为 "网络"/"公开报道"(如 "数据来源于网络整理"),
  不解释具体来源渠道
- 事实铁律: 只能使用输入要点里的事实(数字/机构名/产品名/结论),
  一个新事实都不许编; 不确定的数字宁可不用
- 每条新闻的 summary 必须覆盖该条全部3个要点的核心信息,
  不许只挑一点说
- 禁新闻腔: "据悉""值得关注的是""引发了广泛关注""业界普遍认为"
  "无疑""或将重塑格局"
- 禁翻译腔: "这是…的重要一步""标志着…的时刻"
"""

_GEN_PROMPT = """你要为小黑盒写一篇《AGI 日报》帖子。今天是{today},内容是 {issue_date_display} 的 AI 圈新闻速递(数据来源于网络整理)。

输入素材(JSON, sections 每条 = 一条新闻, points 是官方速递的3条要点, link 是微信原文):
{sections_json}

输出 JSON:
{{
  "title": "帖子标题, ≤32字, 三套路任选: ①数字钩子(如'今天的AI圈: 谷歌砍价四成,OpenAI日更四连发') ②悬念反差(如'研发交给AI之后,会发生什么?') ③利益直给(如'今天值得知道的10件AI大事'); 必须包含至少一个具体新闻细节; 禁新闻腔空话",
  "intro": "开场白, 60~110字, 口语化, 说明这是每日AI新闻栏目+本期速递对应的具体日期({issue_date_display})+有几条新闻+数据来源于网络整理; 不要喊口号",
  "items": [
    {{
      "num": 与输入一致的序号数字,
      "headline": "新闻标题(可直接复用或微调素材里的标题, ≤28字)",
      "summary": "口语化讲解, 110~170字, 覆盖3个要点的核心, 数字必须保留(价格/参数/成绩), 事实不增不减",
      "tagline": "10~16字短标语, 长图卡片用, 有画面感"
    }}
  ],
  "outro": "收尾, 40~80字, 平实收束或抛一个讨论话题; 禁金句禁拔高"
}}

{style_rules}
{extra_rules}"""


def _repair_truncated_json(text: str) -> Optional[dict]:
    """修复被截断的 JSON(找最后一个完整对象边界, 补齐闭合括号)"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    idx = text.rfind("}")
    if idx < 0:
        return None
    frag = text[:idx + 1]
    # 逐字符扫描字符串感知的括号闭合(忽略字符串字面量内的括号)
    stack, in_str, escape = [], False, False
    for ch in frag:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    if in_str:      # 截断在字符串中间 → 丢掉最后一个不完整字符串值
        cut = frag.rfind('"', 0, len(frag) - 1)
        frag = frag[:cut] if cut > 0 else frag
    closers = {"{": "}", "[": "]"}
    for ch in reversed(stack):
        frag += closers[ch]
    try:
        return json.loads(frag)
    except json.JSONDecodeError:
        return None


def generate_post_content(meta: Dict, sections: List[Dict]) -> Dict:
    """LLM 生成帖子文案(生成 + humanize 终审两段, 解析失败自动重试)"""
    today = datetime.date.today()
    issue_date_display = (f"{meta['date'][:4]}年{int(meta['date'][4:6])}月"
                          f"{int(meta['date'][6:])}日")
    slim = [{"num": i + 1, "title": s["title"], "points": s["points"],
             "link": s["link"]} for i, s in enumerate(sections)]
    prompt = _GEN_PROMPT.format(
        today=today.isoformat(), issue_date_display=issue_date_display,
        sections_json=json.dumps(slim, ensure_ascii=False, indent=1),
        style_rules=_STYLE_RULES, extra_rules=AGI_EXTRA_RULES)

    data = None
    for attempt in range(4):
        print(f"   [LLM] 生成文案中(第{attempt+1}次)...")
        raw = _call_llm(prompt, system=AGI_SYSTEM)
        data = _extract_json(raw)
        if data and data.get("items") and len(data["items"]) >= 3:
            break
        # 截断/解析失败 → 多半是服务端限流, 退避后重试
        data = _repair_truncated_json(raw)
        if data and data.get("items") and len(data["items"]) >= 3:
            print(f"   [warn] JSON 被截断, 修复后得 {len(data['items'])} 条")
            break
        data = None
        import time as _time
        wait = 20 * (attempt + 1)
        print(f"   [warn] 输出不完整({len(raw)}字), {wait}s 后重试")
        _time.sleep(wait)
    if not data or not data.get("items"):
        raise RuntimeError(f"LLM 输出解析失败(4次): {raw[:300]}")

    # 校验条目数对齐
    if len(data["items"]) != len(slim):
        print(f"   [warn] LLM 条目数 {len(data['items'])} != 素材 {len(slim)}, 对齐修复")
        by_num = {it.get("num"): it for it in data["items"]}
        fixed = []
        for s in slim:
            it = by_num.get(s["num"])
            if it:
                it["num"] = s["num"]
                fixed.append(it)
        if len(fixed) < 3:
            raise RuntimeError("LLM 条目对齐后不足3条")
        data["items"] = fixed

    # humanize 终审(一轮, 事实红线)
    review = _call_llm(_review_prompt(data, slim), system=AGI_SYSTEM)
    fixed = _extract_json(review)
    if fixed and fixed.get("items") and len(fixed["items"]) == len(data["items"]):
        # 终审版数字/链接以素材为准, 标题若被改坏则回退
        for a, b in zip(data["items"], fixed["items"]):
            fs = (b.get("summary") or "").strip()
            if len(fs) >= 90:
                a["summary"] = fs
            if (b.get("tagline") or "").strip():
                a["tagline"] = b["tagline"].strip()
        if (fixed.get("intro") or "").strip() and len(fixed["intro"]) >= 40:
            data["intro"] = fixed["intro"].strip()
        if (fixed.get("outro") or "").strip():
            data["outro"] = fixed["outro"].strip()
    else:
        print("   [warn] 终审输出异常, 使用初稿")

    # 兜底: tagline 缺失时从 headline 提取
    for it in data["items"]:
        if not (it.get("tagline") or "").strip():
            it["tagline"] = (it.get("headline") or "")[:14]
    return data


_REVIEW_TMPL = """下面是准备发到小黑盒的《AGI 日报》帖子文案(JSON)。请做"去AI味"终审编辑。

【终审要点】
1. 逐句检查写作红线, 有问题就重写该句
2. 事实核对: 与输入素材对照, summary 里的数字/产品名/结论不允许有素材之外的编造; 数字错了必须改回素材值
3. 全文禁止出现 "ima"、"知识库"、"腾讯研究院"字样
4. 每条 summary 保持 110~170字, 覆盖要点核心
5. 保持口语化, 像朋友聊今天的AI新闻

【输出】重写后的完整 JSON(结构不变: title/intro/items[num,headline,summary,tagline]/outro), 不要其他文字。

【输入素材(事实基准)】
{slim}

【待审文案】
{draft}

{style_rules}"""


def _review_prompt(data: Dict, slim: List[Dict]) -> str:
    return _REVIEW_TMPL.format(
        slim=json.dumps(slim, ensure_ascii=False, indent=1),
        draft=json.dumps(data, ensure_ascii=False, indent=1),
        style_rules=_STYLE_RULES)


# ---------------------------------------------------------------------------
# Step 4: 帖子组装(纯文字流 + 末尾一张 AGI 日报长图)
# ---------------------------------------------------------------------------


def _clean_text(s: str) -> str:
    s = (s or "").replace("**", "").replace("`", "")
    return s.strip()


def _split_paragraphs(text: str, min_side: int = 55) -> List[str]:
    """长段落在句号/分号附近分两段(手机阅读节奏, 与 GitHub 帖一致)"""
    t = (text or "").strip()
    if len(t) < 170:
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
    first = t[:best].strip()
    if first.endswith(("；", ";")):      # 段尾悬空分号 → 句号
        first = first[:-1] + "。"
    return [first, t[best:].strip()]


def build_post_segments(title: str, data: Dict, sections: List[Dict],
                        meta: Dict, long_path: Optional[str] = None
                        ) -> List[Tuple[str, str]]:
    """segments: intro → 每条新闻[编号头+标题行+分段正文+原文链接] → outro → 话题 → 长图"""
    segs: List[Tuple[str, str]] = []
    intro = _clean_text(data.get("intro") or "")
    if intro:
        segs.append(("text", intro))

    link_by_num = {i + 1: s.get("link", "") for i, s in enumerate(sections)}
    for it in data.get("items") or []:
        num = int(it.get("num") or 0)
        emoji = NUM_EMOJI.get(num, f"NO.{num}")
        headline = _clean_text(it.get("headline") or "")
        parts = [f"{emoji} {headline}"]
        for para in _split_paragraphs(_clean_text(it.get("summary") or "")):
            if para:
                parts.append(para)
        link = link_by_num.get(num, "")
        if link:
            parts.append(f"🔗 原文 {link}")
        segs.append(("text", "\n\n".join(parts)))

    outro = _clean_text(data.get("outro") or
                        "以上就是这个板块的内容, 数据来源于网络整理。")
    if outro:
        segs.append(("text", outro))
    segs.append(("text", "#AGI# #人工智能# #AI资讯#"))
    if long_path:
        segs.append(("img", long_path))
    return segs


def segments_to_markdown(title: str, segs, base_dir: str) -> str:
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


# ---------------------------------------------------------------------------
# Step 5: 主流程
# ---------------------------------------------------------------------------


def already_posted_issue(issue_date: str) -> bool:
    return load_state().get("agi_last_issue", "") >= issue_date


def mark_day_processed():
    """写入'今日已处理'标记(scheduler 判定成败用; 无新期跳过时也要写)"""
    state = load_state()
    state["last_agi"] = datetime.date.today().isoformat()
    save_state(state)


def mark_issue_posted(issue_date: str, link: str, title: str):
    state = load_state()
    state["agi_last_issue"] = issue_date
    state["last_agi"] = datetime.date.today().isoformat()
    state.setdefault("history", []).append({
        "period": "agi", "issue": issue_date,
        "date": datetime.date.today().isoformat(),
        "url": link, "title": title})
    save_state(state)


def _cleanup_images(keep: int = 3):
    files = sorted(
        [f for f in os.listdir(OUTPUT_DIR)
         if f.startswith("agi_news_") and f.endswith(".png")],
        reverse=True)
    for f in files[keep:]:
        try:
            os.unlink(os.path.join(OUTPUT_DIR, f))
        except OSError:
            pass


def run(dry_run: bool, force: bool, no_image: bool = False,
        keep_image: bool = False):
    print(f"===== AGI 日报 → 小黑盒 {'DRY-RUN' if dry_run else 'LIVE'} =====")

    print("[1/5] 获取最新一期(ima 分享页)...")
    meta = latest_issue_meta()
    print(f"   最新期: {meta['date']} {meta['title']}")
    if not force and already_posted_issue(meta["date"]):
        print(f"[跳过] {meta['date']} 这一期已经发过了"
              f"(state.agi_last_issue={load_state().get('agi_last_issue')},"
              f" 知识库没有更新新期)。强制重发加 --force")
        mark_day_processed()   # 今日已检查, 防 scheduler 重试
        return None

    print("[2/5] 抓取微信原文并切分新闻条目...")
    sections = fetch_issue_content(meta)
    print(f"   切出 {len(sections)} 条新闻:")
    for s in sections:
        print(f"   {s['num_cn']}、{s['title']} ({len(''.join(s['points']))}字"
              f" {'有链接' if s['link'] else '无链接'})")

    print("[3/5] LLM 生成口语化文案(生成+终审)...")
    data = generate_post_content(meta, sections)
    print(f"   标题: {data.get('title')}")
    lens = [len(it.get("summary") or "") for it in data["items"]]
    print(f"   条目 {len(data['items'])} 条, summary 长度 "
          f"min{min(lens)}/avg{sum(lens)//len(lens)}/max{max(lens)}")

    print("[4/5] 渲染 AGI 日报长图...")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = os.path.join(OUTPUT_DIR, f"agi_news_{ts}.png")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if no_image:
        image_path = None
        print("   (跳过图片)")
    else:
        from image_maker import render_agi_image
        render_agi_image(data.get("title", ""), meta, data["items"],
                         image_path)

    print("[5/5] 组装并发布(盒友杂谈)...")
    segments = build_post_segments(data.get("title", ""), data, sections,
                                   meta, long_path=image_path)
    preview_path = os.path.join(OUTPUT_DIR, f"agi_post_{ts}.md")
    with open(preview_path, "w") as f:
        f.write(segments_to_markdown(data.get("title", ""), segments,
                                     OUTPUT_DIR))
    print(f"   预览已保存: {preview_path}")

    cfg = load_config()
    client = HeyboxClient(cfg["cookie"])
    if not client.check_login_ok():
        print("   [错误] Cookie 已失效, 请更新 config.json!")
        return None
    publisher = HeyboxPublisher(client,
                                topic_ids=str(cfg.get("topic_ids", "7214")))
    result = publisher.publish(title=data.get("title", "AGI 日报"),
                               segments=segments, dry_run=dry_run)

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
            print("   ", json.dumps(result.get("body", {}),
                                    ensure_ascii=False)[:300])
        return result

    link = result.get("post_url", "")
    print(f"   ✅ 发布成功: {link}")
    mark_issue_posted(meta["date"], link, data.get("title", ""))
    if not keep_image:
        _cleanup_images()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="只构造不发帖")
    parser.add_argument("--force", action="store_true",
                        help="忽略期号防重标记强制发布")
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument("--keep-image", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    dry = args.dry_run or bool(cfg.get("dry_run", True))

    try:
        run(dry, args.force, args.no_image, args.keep_image)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
