# -*- coding: utf-8 -*-
"""LLM 项目简介生成器(两段式,去AI味)

方法论来源(GitHub 开源项目调研):
- blader/humanizer (56k★) — 26 种 AI 痕迹结构模式:假对比(Not X but Y)、
  单句金句收尾、伪深度格言、起跑式铺垫、强凑排比、意义拔高、销售腔…
  社区实测结论:结构性 AI 味必须写进生成规则,事后润色救不回来
- op7418/Humanizer-zh (18.9k★) — 中文特有检查:四字词排比、层叠的"的"、
  "进行+动词"、"随着…的发展"式开头、套话收尾;具象优先于抽象
- KKKKhazix/human-writing — 素材不够时提醒而非硬凑(所以抓 README 喂料)
- MrGeDiao/shuorenhua — 先锁事实再动语气:数字/结论/名词改写时不增不减
- hardikpandya/stop-slop — 少铺垫,直接说人、动作、事实和结果

两段式流程:
  Stage 1 生成:规则内嵌的初稿(每项目 230-330 字,信息密度优先,
           靠真实细节撑长度而不是形容词注水)
  Stage 2 终审:逐句清理 AI 痕迹,铁律是事实不增不减(失败则沿用初稿)
"""
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional


def _parse_gained(period_stars: str) -> int:
    """'1,383 stars today' → 1383(降级模板用,容错复用)"""
    if not period_stars:
        return 0
    m = re.search(r"([\d,]+)", period_stars)
    return int(m.group(1).replace(",", "")) if m else 0


def _call_llm(prompt: str, system: str = "", timeout: int = 420) -> str:
    """调用 z-ai CLI,返回 completion 文本"""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        out_path = tf.name
    try:
        cmd = ["z-ai", "chat", "--prompt", prompt, "-o", out_path]
        if system:
            cmd += ["--system", system]
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
        with open(out_path) as f:
            data = json.load(f)
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return content.strip()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _fix_unescaped_quotes(text: str) -> str:
    """转义字符串值内部未转义的 ASCII 双引号(2026-10-21 实测根因)

    LLM 写中文引语时直接输出 "..."(U+0022) 不转义, json.loads 报
    "Expecting ',' delimiter"。判定规则(字符串内遇到 "):
      跳过其后空白 → 若到结尾 / 下一字符是 : } ] → 收尾引号;
      若是 , 且逗号后(跳空白)是 " (下一键或数组下一项) → 收尾引号;
      其余(中文字文/标点/数字等) → 内容引号, 转义为 \" 。
    """
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "\\":                    # 已转义, 原样保留转义对
            out.append(ch)
            if i + 1 < n:
                out.append(text[i + 1])
            i += 2
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j >= n or text[j] in ":}]":
                in_str = False
                out.append('"')
                i += 1
                continue
            if text[j] == ",":
                k = j + 1
                while k < n and text[k] in " \t\r\n":
                    k += 1
                if k < n and text[k] in '"{[':  # , "key": / , {…} / , […]
                    in_str = False
                    out.append('"')
                    i += 1
                    continue
            out.append('\\"')               # 内容引号 → 转义
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出提取 JSON(容错: ```json 围栏/前后杂文字/未转义引号)"""
    if not text:
        return None
    text = text.strip()
    # 去围栏
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    # 找到第一个 { 起解析
    start = text.find("{")
    if start < 0:
        return None
    for end in range(len(text), start, -1):
        if text[end - 1] != "}":
            continue
        frag = text[start:end]
        try:
            return json.loads(frag)
        except json.JSONDecodeError:
            pass
        try:                          # 未转义引号修复后再试
            return json.loads(_fix_unescaped_quotes(frag))
        except json.JSONDecodeError:
            continue
    return None


def _repair_truncated_json(text: str) -> Optional[dict]:
    """修复被截断的 JSON: 迭代回退到干净的语法边界, 补齐闭合括号后尝试解析

    场景: LLM 服务端限流/截断导致输出不完整(实测偶发, 大 JSON 更易触发)。
    逐字符回退, 每步尝试"去尾逗号→字符串感知补括号→解析"。
    """
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    # 与 _extract_json 对齐: 容忍 JSON 前的前导文字/残缺围栏
    brace = text.find("{")
    if brace > 0:
        text = text[brace:]
    if not text.startswith("{"):
        return None
    try:                             # 本来就完整 → 直接用
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fixed = _fix_unescaped_quotes(text)
    if fixed != text:                # 未转义引号: 修复后再试
        try:
            parsed = json.loads(fixed)
            print("   [fix] 未转义引号已修复, JSON 完整解析")
            return parsed
        except json.JSONDecodeError:
            pass
        text = fixed                 # 截断+引语并用: 回退闭合也基于修复文本

    def _try_close(frag: str) -> Optional[dict]:
        frag = frag.rstrip()
        while frag.endswith(","):
            frag = frag[:-1].rstrip()
        if frag.endswith(":") or frag.endswith('"'):
            return None
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
        if in_str or not stack:
            return None
        closers = {"{": "}", "[": "]"}
        try:
            return json.loads(frag + "".join(closers[c] for c in reversed(stack)))
        except json.JSONDecodeError:
            return None

    frag = text
    for _ in range(600):
        data = _try_close(frag)
        if data is not None:
            return data
        frag = frag[:-1]
        if len(frag) < 10:
            return None
    return None


# ---------------------------------------------------------------- Stage 1

SYSTEM_PROMPT = (
    "你在小黑盒(游戏社区)数码区写 GitHub 开源榜单专栏,读者是关注数码科技的"
    "普通玩家和入门开发者。你的文风像一个懂技术的朋友在聊天:句子有长有短,"
    "直接说事,信息密度高,偶尔吐槽,没有套话。输出必须是纯 JSON,"
    "不要任何多余文字。"
)

# 写作红线:来自 humanizer / Humanizer-zh 的 AI 痕迹清单,生成阶段直接禁掉
_STYLE_RULES = """【写作红线(出现任何一条都会被退稿)】
- 禁假对比:"不仅…更是…""不是…而是…""与其说…不如说…"
- 禁单句金句收尾:如"这才是开源的魅力。""这,就是XX的意义。"
- 禁意义拔高:"标志着""开启了新篇章""改变了/可能会改变…方式"
  "引领潮流""里程碑""见证…的形成"
- 禁四字词排比堆砌:"功能强大、性能卓越、易于上手"
- 禁互联网黑话:"赋能""闭环""抓手""打造""深耕""护城河""生态布局"
- 禁空转连接语:"值得注意的是""值得一提的是""不得不说""众所周知""可以说""值得关注"
- 禁排比开场:"从X到Y,从A到B,看看…"
- 禁热度套词(正文):"强势登顶""霸榜""引爆""强势占领""一夜爆红"
  "屠榜""登顶"不许在正文出现(星数增长用平实说法:
  "单日新进1100多星");标题里允许至多1个口语热度词
  ("火了""杀疯了""被玩坏了"),但必须搭配具体细节,
  空洞的热度词堆砊仍然禁止
- 禁万能比喻:"相当于给XX装上了XX"(全篇至多出现1次)
- 禁导游腔:"让我们来看看""来看看""接下来""废话不多说"
- 禁客服腔:"希望对你有帮助""总的来说"
- 禁万能受众句("适合…的用户"的新马甲):"…会喜欢它/会喜欢这个"
  "…的用户会需要这个"“这个项目很合适/很实用”——受众要么
  写成具体场景("经常要读英文文档的"),要么揉进功能句,要么
  直接不提受众只说事
- 每条 summary 的收尾方式必须轮换:平铺直叙收尾/问句收尾/场景收尾/
  一句建议收尾交替使用——禁止每条都以"适合…用户"或"适合需要…的…"结尾
- 句子长短必须交替;10条summary不许是同一个模子(每条的开头方式、
  句数、收尾方式都要有变化)
- 禁万能受益句:"提供了(一个)…解决方案""能有效提升/减少/帮助"——
  全篇至多1次;收益要写具体:省了什么/快了多少/少干了哪一步
- 禁机械数据句:"项目X个月前创建,已有Y颗星"——数据要么用自然说法
  揉进叙述("上个月才开源,星数已经奔三万去了"),要么不提(星数在
  标题行已展示);同一种数据句式全篇至多出现2次
- 开头禁雷同:至多3条用「项目名+是(个)…」的定义句开头,其余换
  场景句/问句/对比句/动词开头
- 中文表达:别堆"的"字长定语,别用"进行+动词"(直接说"测试"不说"进行测试")"""""


def _fmt_age(created_at: str) -> str:
    """项目年龄(给 LLM 的事实素材): '创建于天前'/'创建于N个月前'"""
    if not created_at:
        return ""
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00")
                                    ).astimezone(timezone.utc)
    except ValueError:
        return ""
    days = (datetime.now(timezone.utc) - dt).days
    if days < 0:
        days = 0
    if days <= 60:
        return f"创建于{days}天前"
    months = round(days / 30.4)
    if months < 24:
        return f"创建于{months}个月前"
    return f"创建于{round(days / 365)}年前"


def _build_repo_brief(repos: List[Dict]) -> List[Dict]:
    brief = []
    for r in repos:
        item = {
            "rank": r["rank"],
            "name": r["full_name"],
            "desc": (r.get("description") or "")[:500],
            "lang": r.get("language") or "未知",
            "stars": r.get("stars", 0),
            "hot": r.get("period_stars", ""),
            "topics": (r.get("topics") or [])[:8],
        }
        age = _fmt_age(r.get("created_at", ""))
        if age:
            item["age"] = age
        if r.get("source") == "search_api":
            item["note"] = "新星项目(近期创建,非trending页,没有单日增量数据)"
        rd = r.get("readme_excerpt") or ""
        if rd:
            item["readme"] = rd[:2200]
        brief.append(item)
    return brief


def _generation_prompt(repos: List[Dict], period: str) -> str:
    period_cn = "今日" if period == "daily" else "本周"
    n = len(repos)
    brief = json.dumps(_build_repo_brief(repos), ensure_ascii=False, indent=1)
    return f"""以下是 GitHub {period_cn}热榜 Top{n} 项目数据(JSON)。
榜单已过滤老牌知名项目(claude-code/ECC这类早就火过的不会再出现),
主打新面孔和新爆发;age 字段是项目创建时间,note 标记的是补位的新星项目。
readme 字段是项目 README 开头原文(英文,供你理解项目细节,不要逐句翻译):

{brief}

为小黑盒帖子生成文案,输出严格 JSON:
{{
  "title": "30字以内,自媒体爆款风,让人有点进来的冲动。三个套路选一:①数字钩子——用榜单真实数据制造冲击('单日新进1100星,这个Rust搜索工具火了')②悬念反差——抛问题或颠覆认知('不联网也能跑大模型?一张游戏显卡就够')③利益直给——直接说能白嫖到什么('免登录免API Key,免费用上前沿模型')。必须包含至少一个具体细节(项目名/数字/功能),禁止空洞的'强势登顶''霸榜''屠榜';口语热度词('火了''杀疯了''被玩坏了')至多用1个且必须搭配具体细节。数字只用星数/名次/项目数这类榜单自身数据,禁止引用README里的性能数字(百分比/倍数容易失真)",
  "intro": "90~150字,直接从本期榜单内容说起:点名2~3个有意思的项目,带上具体数字或细节,告诉盒友今天有什么值得看的。禁止宏大开场,禁止'强势占领''一夜涨15k星'这种写法(数字可以提,但要用平实的说法)",
  "outro": "30~60字收尾。写一句跟本期内容相关的观察或具体的问题。禁止'你最看好哪个项目?评论区聊聊'这类模板句",
  "items": [
    {{"rank": 1, "tagline": "10~16字标语,一句话说清这个项目是干嘛的(像贴在卡片上,不带句号,不用引号,要具体)", "summary": "对应项目的中文介绍(260~360字,至少6句话)"}}
  ]
}}

【tagline】每个项目一句 10~16 字标语,说白了这项目是干什么的,
要具体到功能(如"给AI助手装的跨平台长期记忆""在普通显卡上跑
1250亿参数模型"),禁止"强大好用""值得一试"这类空话

【summary 字数(硬性要求,不达标退稿)】每条 260~360 字、至少 6 句话
(素材太少的项目可到 180 字,宁短勿空)。字数按汉字计数,
写到 260 字以下就是没写完:功能点不够就展开安装方式、
使用流程、适用场景,素材真的不够才允许短

【summary 内容要求(信息密度优先,篇幅靠真实细节撑,不靠形容词注水)】
1. 第一句直接说清:这项目是干嘛的、给谁用。说人话,不绕弯子
2. 从 desc/readme 挑 3~5 个真实、具体的功能点展开:能干什么、
   怎么触发、和同类有什么不一样。每个功能点写透一点:
   命令行工具就说命令长什么样、参数怎么给;网页/桌面工具就
   说操作流程;库就说代码里怎么调用。数字(支持多少种语言、
   快多少、多少星)直接引用
3. 有安装/上手方式的话,具体说怎么装怎么用(pip install /
   cargo install / 一条 docker 命令 / 应用商店下载这种),
   直接写进功能句里,别单独成段
4. 说一句什么人会用到它:给具体场景(比如"想给家里NAS搞自动
   下载的""经常要读英文技术文档的"),不要笼统的"开发者"。
   【重要】"适合…(的人/用户)"这种句型全篇至多出现3次,其余的
   把受众揉进功能句里(如"重训模型的时候一条命令就能把数据准备好")
   或用场景句("经常看外文文献的应该都用过类似的")
5. 允许一句主观看法或轻微吐槽,像真人聊天会说的,但别每条都硬凹
6. 项目够新的话(age 字段显示创建于几天/几周前),可以自然带一句时间
   细节,比如"上个月才开源""创建两周就攒了这么多星",但全篇至多
   提 3 次,别每条都念叨年龄
7. 排名靠后、素材少的项目,有什么写什么,没有的别编
8. 星数/日增量在帖子的标题行已经展示了,summary 里最多提 1 次,且
   要用自然说法揉进句子里(如"已经攒了28万星""单日又进账两千星"),
   禁止在结尾机械重复"目前有X星,一天增长X星"

【事实纪律】
- 只能使用上面 JSON 里的信息,不得编造功能、数字、公司、人物、对比
- 百分比、倍数、参数量不许换算或数错零:素材写40%就写40%,写125B就
  写1250亿;拿不准的数字直接不写
- readme 是英文,理解后用自己的中文说,不要翻译腔
- 全部中文(项目名/专有名词除外),数字用阿拉伯数字

{_STYLE_RULES}

items 数组按 rank 顺序包含全部 {n} 个项目。"""


# ---------------------------------------------------------------- Stage 2

_REVIEW_PROMPT = """下面是一篇准备发到小黑盒社区的 GitHub 榜单帖文案(JSON)。请你做"去AI味"终审编辑,逐句检查并重写有问题的句子。

{payload}

【AI痕迹检查清单(参考 humanizer/Humanizer-zh 项目)】
1. "不仅…更是…""不是…而是…"假对比 → 直接说结论
2. 单句金句/总结腔收尾("这才是…"、"可见…") → 删掉或换成有信息量的收尾
3. 意义拔高("标志着/改变了/引领/开创/展示了…的能力/
   彰显了/体现了…的价值") → 只留事实,把能力说成具体能干什么
4. 四字词排比、空泛形容词("强大、优秀、功能齐全、界面美观") →
   换成具体功能或数字;实在没素材就删掉这半句
5. 黑话(赋能/闭环/打造/深耕/生态) → 换成大白话
6. "值得注意的是/值得一提的是/不得不说/众所周知" → 删掉,直接说事
7. 万能比喻("相当于给XX装上了XX") → 出现超过1次就删到只剩1次或全删
8. 【重点】结构雷同:如果超过3条都含"适合…(的人/用户)"句型,
   或多条开头都是"XX是…",必须重写,让开头方式、句数、收尾方式
   错开(平叙/问句/场景/建议轮换)。【特别警告】重写"适合…"时
   禁止统一改成"…会喜欢它/会喜欢这个/…的用户会需要这个"——
   这是打地鼠,把一种雷同换成另一种雷同;要改成具体场景句
   (如"经常要读英文文档的,装上能省不少事")或干脆删掉受众句
9. 层叠的"的"字长定语、"进行+动词"、"随着…的发展" → 改成自然中文
10. 翻译腔(被动句堆叠、"被广泛认为") → 改主动句
11. 正文里的热度套词(登顶/强势登顶/霸榜/引爆/强势占领/
    一夜爆红) → 换成平实但有信息的说法;【title 除外】title
    允许保留至多1个口语热度词(火了/杀疯了/被玩坏了,前提是
    后面跟着具体细节),但'强势登顶/霸榜/屠榜'这类新闻腔
    空话在 title 里也要换成具体说法;"今天这些项目值得关注"
    这类空泛句 → 删掉或换成具体的看点提示
12. "可能会改变…方式""正在见证…""…会是未来趋势"这类拔高预测
    → 删掉,或改成基于现有事实的观察
13. "提供了(一个)…解决方案""能有效提升/减少"万能受益句 →
    换成具体收益(省了什么/快了多少/少干了哪一步),全篇至多保留1次
14. "项目X个月前创建,已有Y颗星"机械数据句 → 揉进叙述或删掉
    (星数标题行已展示;年龄全篇自然地提至多3次)
15. 开头"项目名+是(个)…"定义句超过3条 → 换场景/问句/动词/
    对比开头
16. 数字逐一核对素材原文:40%不许写成40倍,125B不许写成125亿,
    倍数/百分比/参数量一个数零都不能差

【改写示例】
- 改前:"这个项目展示了AI在数学领域的强大能力,适合研究者使用"
  改后:"它已经生成了700多篇证明手稿,还没全部验证,但速度确实快"
- 改前:"界面美观,功能齐全,是一款优秀的开源软件"
  改后:"界面长得很像Word,快捷键也照搬了,上手基本零成本"
- 改前:"适合需要轻量级解决方案的用户"
  改后:"家里老电脑跑不动Office的,可以试试这个"
- 改前:"这个仓库提供了宝贵的资料,展示了AI在数学领域的能力"
  改后:"719份手稿摆在那就是素材库,哪些题能秒、哪些解不动,翻翻就知道"

【铁律(违反即事故)】
- 项目名、数字、功能点、星数信息一字不能丢,也不能新增任何事实
- 已经自然、有信息量的句子不要为了改而改
- 【长度红线】终审是改句子不是压缩:不得整句删掉有信息量的
  句子;每条 summary 改写后不得低于原长的 95%,也不得低于
  260 字(原文更短的除外)。改短了就是事故
- title 里的项目名必须保留

输出:同样结构的 JSON(含 title/intro/outro/items 全部字段),不要任何多余文字。"""


def _carry_taglines(old_items: List[Dict], new_items: List[Dict]):
    """Stage 2/3 采用 LLM 返回的 items 时, 把初稿的 tagline 按_rank
    补回(终审 prompt 只关心 summary,LLM 可能丢弃 tagline 字段)"""
    tl = {it.get("rank"): (it.get("tagline") or "").strip()
          for it in old_items or [] if it.get("rank") is not None}
    for it in new_items or []:
        r = it.get("rank")
        if r in tl and not (it.get("tagline") or "").strip():
            it["tagline"] = tl[r]


def _humanize_pass(data: Dict) -> Dict:
    """Stage 2: 去AI味终审(失败时静默沿用初稿)"""
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    try:
        raw = _call_llm(_REVIEW_PROMPT.format(payload=payload), SYSTEM_PROMPT)
        cleaned = _extract_json(raw)
        if not cleaned or not cleaned.get("items"):
            print("[humanize] 终审返回无法解析,沿用初稿")
            return data
        # 校验:items 数量和 rank 齐全才采用
        ranks = {it.get("rank") for it in cleaned["items"]}
        if len(cleaned["items"]) == len(data["items"]) and \
                ranks == {it["rank"] for it in data["items"]}:
            for k in ("title", "intro", "outro"):
                if not cleaned.get(k) and data.get(k):
                    cleaned[k] = data[k]
            changed = sum(
                1 for a, b in zip(data["items"], cleaned["items"])
                if (a.get("summary") or "").strip() != (b.get("summary") or "").strip())
            print(f"[humanize] 终审完成:重写了 {changed}/{len(data['items'])} 条")
            _carry_taglines(data["items"], cleaned["items"])
            return cleaned
        print("[humanize] 终审返回条目不齐,沿用初稿")
        return data
    except Exception as e:
        print(f"[humanize] 终审阶段失败,沿用初稿: {e}")
        return data


# ---------------------------------------------------------------- Stage 3
# 确定性检测:LLM 禁了 A 句式会换用同功能的 B 句式(打地鼠),
# 用代码统计高频句式,再定向修复
_REPETITION_PATTERNS = [
    "可以试试", "适合", "值得一试", "看一看", "不妨",
    "尤其", "特别是", "开发者", "大幅", "显著",
    "解决方案", "提供了可能", "提供了便利", "个月前创建", "颗星",
    "能有效", "值得关注",
]

# 结尾软推荐句的马甲变体(单独看都不超阈,合并统计才能抓住“禁A换B”:
# 禁掉“适合…的用户”之后 LLM 改写“这个项目很合适/很实用/会需要这个”)
_SOFT_RECO_PATTERNS = [
    "这个项目很", "这个工具很", "会需要这个", "特别有用",
    "很合适", "很实用", "会很方便", "是个不错的选择", "不错的选择",
    "正适合你", "值得一试", "会特别喜欢", "派上用场",
    "会喜欢它", "会喜欢这个",
]

# 正则检测类:跨词变体模式(同一功能不同说法,子串抓不住)
_REGEX_PATTERNS = [
    (r"\d+(天|日|周|个月|年)前创建|这个项目才|才\d+(天|周|个月)",
     "年龄数据句(N天前创建/这个项目才N天)"),
    (r"已经(获得|攒|收获|积累)",
     "星数获得句(已经获得了N星)"),
    (r"看来大家|看来很多|说明很多人|说明大家",
     "脑补群众反应(看来很多人都在…)"),
    (r"尤其|特别是",
     "补充强调半句(尤其是/特别是)"),
    (r"的会喜欢|的会觉得|的会需要|的特别有用|的会很有收获"
     r"|用户会喜欢|用户会需要|会喜欢这个|会喜欢它|会需要这个|觉得实用",
     "受众收尾句(…的会喜欢它/用户会需要这个)"),
    (r"(想|需要|经常|使用|研究|觉得|天天)[^，。]{2,25}的，(它|这个)",
     "悬垂受众句(想xx的，它…主语悬空)"),
]


# 无谓语收尾句检测用谓语标记(只选几乎只做谓语的词;
# “切换/喜欢/使用”等常出现在定语从句里,会误挡检测)
_TAIL_VERBS = re.compile(r"可以|能|会|可用|支持|提供|安装|"
                         r"查看|避免|简化|用它|拿它|装上|够用|"
                         r"交给|直接用|省下|上手")


def _detect_repetition(items: List[Dict]) -> List[tuple]:
    """统计句式在各条 summary 中的出现次数,超过 1/3 视为重复"""
    n = max(len(items), 1)
    limit = max(3, n // 3)
    findings = []
    for pat in _REPETITION_PATTERNS:
        hits = [it.get("rank") for it in items
                if pat in (it.get("summary") or "")]
        if len(hits) > limit:
            findings.append((pat, hits))
    # 正则类:跨词变体(同一功能不同说法)
    for rx, label in _REGEX_PATTERNS:
        hits = [it.get("rank") for it in items
                if re.search(rx, it.get("summary") or "")]
        if len(hits) > limit:
            findings.append((label, hits))
    # 开头「项目名+是(个)…」定义句检测(结构性雷同重灾区)
    opening_is = [it.get("rank") for it in items
                  if re.match(r"^[^,。;!?]{0,30}是[个一]?",
                              (it.get("summary") or ""))]
    if len(opening_is) > limit:
        findings.append(("开头『…是(个)…』定义句", opening_is))
    # 结尾软推荐句(变体合并统计)
    soft_hits = [it.get("rank") for it in items
                 if any(p in (it.get("summary") or "")
                        for p in _SOFT_RECO_PATTERNS)]
    if len(soft_hits) > limit:
        findings.append(("结尾软推荐句(这个项目很合适/很实用/会需要"
                          "这个/很合适/很实用等)", soft_hits))
    # 无谓语收尾句(LLM 删受众句谓语留下的残迹,是语病):
    # 最后一句以"…的人/用户/工作者"结尾且句中没有任何谓语动词
    tail_hits = []
    for it in items:
        s = (it.get("summary") or "").rstrip()
        if not s:
            continue
        last = s[:-1].rsplit("。", 1)[-1] if s.endswith("。") else ""
        if (last and 0 < len(last) < 45
                and re.search(r"(人|用户|工作者|设计师|程序员|工程师|读者)$",
                              last) and not _TAIL_VERBS.search(last)):
            tail_hits.append(it.get("rank"))
    if len(tail_hits) > 1:
        findings.append(("无谓语收尾句(整句只有名词短语,如『…的设计师。』)",
                         tail_hits))
    return findings


_DIVERSIFY_PROMPT = """下面是 GitHub 榜单帖文案(JSON)。代码检测到这些句式在多条
简介里重复出现(同一功能的话换个说法,是AI味的典型特征):

{findings}

{payload}

请改写上面列出的条目中超出 {limit} 次的部分(未列出的条目原样
保留),使每个检测句式在全文出现的次数降到 {limit} 次以内。
宁可多改几条,不要少改。改法参考:
- 推荐句换成场景句:"经常要读英文文档的,装上能省不少事"
- 推荐句换成平叙:"批量下载、字幕、转码它都包了,star数还在涨"
- 推荐句换成问句:"天天对着终端的,谁不想让它聪明点?"
- "尤其/特别是"补充句直接删掉或揉进前一句
- 开头定义句「XX是(个)…」换成:场景("还在挨个文件翻代码?")/
  问句("Photoshop的订阅费又涨了?")/动词开头("直接把1250亿
  参数的模型塞进游戏PC")/对比("被GIMP劝退的可以看这个")
- "提供了…解决方案"删掉套壳,写具体收益:省了什么、快了多少、
  少干了哪一步
- "项目X个月前创建,已有Y颗星"揉进叙述("上个月才开源,星数
  已经奔三万去了")或直接删掉
- "开发者"这个泛称换成具体的人:"天天写Android的""自己搞homelab
  的""经常写脚本的",或干脆去掉称谓直接说事
- 结尾软推荐/受众句"这个项目很合适/很实用/…的会喜欢这个工具/…的会觉得实用/…用户会需要这个" →
  换具体场景或平叙收尾:如"家里老电脑跑不动Office的,装上就能用";
  或直接以功能/事实收尾:"表单、加密、图层它都包了";
  或问句收尾:"天天对着终端的,谁不想让它聪明点?"
- 年龄数据句"N天前创建,已经获得了N星" → 揉进叙述("三周前才
  冒头,星数直接奔三万去了")或删掉;全篇年龄提示至多保留3处,
  其余条目删掉年龄信息别硬凑
- "看来很多人/说明大家…"这种脑补群众反应 → 删掉,或换成基于
  项目事实的观察
- "尤其/特别是…"补充半句 → 揉进前一句,别单独成半句
- 悬垂受众句"想xx的,它/这个工具…" → 主语悬空是语病:要么补全
  主语重写("经常跟PDF打交道的,可以用它批量合并、加密"补上
  谓语),要么删掉受众半句,直接从"它/这个工具"那半句起重写
- 无谓语收尾句"…的设计师和创意工作者。""…的用户,特别是…的
  人。" → 整句只有名词短语没有谓语:补上谓语("…的设计师拿它"
  替代订阅版完全够用")或整句删掉,以前一句的功能句收尾

铁律:项目名、数字、功能点、安装命令一字不能丢,不能新增事实;
【年龄和星数除外——可以删】"N天/月前创建"的年龄信息全篇保留
至多3处,其余直接删掉;星数在帖子标题行已展示,summary里的
"已经获得/积累了N颗星"句子可以整句删,不算丢信息;
【长度红线】每条长度不得低于原长的 95%;改动后不要产生新的
重复句式;结尾"目前有X星,一天增长X星"这类机械数据句改成
自然说法或删掉。

输出:同样结构的 JSON(含 title/intro/outro/items 全部字段),不要任何多余文字。"""


def _diversify_pass(data: Dict) -> Dict:
    """Stage 3(按需): 高频句式去重"""
    findings = _detect_repetition(data.get("items") or [])
    if not findings:
        return data
    n = len(data.get("items") or [])
    limit = max(3, n // 3)
    lines = "\n".join(
        f"- 「{pat}」出现在第 {ranks} 条" for pat, ranks in findings)
    print(f"[diversify] 检测到重复句式: "
          f"{', '.join(p for p, _ in findings)} → 触发定向去重")
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    try:
        raw = _call_llm(
            _DIVERSIFY_PROMPT.format(findings=lines, payload=payload,
                                     limit=limit),
            SYSTEM_PROMPT)
        cleaned = _extract_json(raw)
        ranks = {(it or {}).get("rank") for it in (cleaned or {}).get("items", [])}
        expect = {it["rank"] for it in data["items"]}
        if cleaned and cleaned.get("items") and \
                len(cleaned["items"]) == n and ranks == expect:
            for k in ("title", "intro", "outro"):
                if not cleaned.get(k) and data.get(k):
                    cleaned[k] = data[k]
            after = _detect_repetition(cleaned["items"])
            print(f"[diversify] 去重完成:剩余重复句式 "
                  f"{len(after)} 组{('(' + ', '.join(p for p, _ in after) + ')') if after else ''}")
            _carry_taglines(data["items"], cleaned["items"])
            return cleaned
        print("[diversify] 返回异常,沿用终审稿")
        return data
    except Exception as e:
        print(f"[diversify] 去重失败,沿用终审稿: {e}")
        return data


# ---------------------------------------------------------------- 主入口

def _post_dedupe(data: Dict, keep: int = 3) -> Dict:
    """Stage 4(确定性): 删除超限的年龄句/受众收尾句
    LLM 对这两类句子执行率低(年龄数字被当"不能丢的事实";
    受众句改写总不彻底),用代码直接删,保证结果:
      - 年龄句(22天前创建…): 前 keep 条命中保留,其余整句删
      - 受众句(…的会喜欢这个工具): 只删最后一句,前 keep 条保留
    这些句子不含独有信息(星数标题行已展示,受众是空泛推荐),
    删除不丢事实;删后过短(<40字)则保留原句防残篇。
    """
    age_rx = re.compile(r"\d+(天|日|周|个月|年)前创建|这个项目才\d+"
                        r"|才\d+(天|周|个月)")
    aud_rx = re.compile(
        r"的会喜欢|的会觉得|的会需要|的特别有用|的会很有收获"
        r"|用户会喜欢|用户会需要|会喜欢这个|会喜欢它|会需要这个"
        r"|觉得实用")

    def is_bare_tail(s: str) -> bool:
        """无谓语收尾句:最后一句名词短语结尾且无任何谓语动词"""
        if not s.endswith("。"):
            return False
        last = s[:-1].rsplit("。", 1)[-1]
        return (0 < len(last) < 45
                and bool(re.search(
                    r"(人|用户|工作者|设计师|程序员|工程师|读者)$", last))
                and not _TAIL_VERBS.search(last))

    def strip_sentences(s: str, rx, last_only: bool = False) -> str:
        sentences = re.split(r"(?<=[。！？?!])", s)
        if last_only:
            for i in range(len(sentences) - 1, -1, -1):
                if rx.search(sentences[i]):
                    sentences.pop(i)
                    break
        else:
            sentences = [x for x in sentences if not rx.search(x)]
        new_s = "".join(sentences).strip()
        return new_s if len(new_s) >= 40 else s

    kept_age = kept_aud = kept_tail = 0
    rm_age = rm_aud = rm_tail = 0
    for it in data.get("items", []):
        s = (it.get("summary") or "").strip()
        if not s:
            continue
        if age_rx.search(s):
            kept_age += 1
            if kept_age > keep:
                s2 = strip_sentences(s, age_rx)
                if s2 != s:
                    rm_age += 1
                    s = s2
        if aud_rx.search(s):
            kept_aud += 1
            if kept_aud > keep:
                s2 = strip_sentences(s, aud_rx, last_only=True)
                if s2 != s:
                    rm_aud += 1
                    s = s2
        if is_bare_tail(s):
            kept_tail += 1
            if kept_tail > 0:  # 语病零容忍,全部删除
                s2 = strip_sentences(s, re.compile(r"\S"), last_only=True)
                if s2 != s:
                    rm_tail += 1
                    s = s2
        it["summary"] = s
    if rm_age or rm_aud or rm_tail:
        print(f"[post-dedupe] 确定性后处理:删年龄句 {rm_age} 条、"
              f"受众收尾句 {rm_aud} 条、无谓语收尾句 {rm_tail} 条")
    return data


def _hotword_cleanup(data: Dict) -> Dict:
    """Stage 5(确定性): 热度套词替换。
    LLM 对"登顶/霸榜/引爆"这类词执行率不稳(prompt 禁了也会偶尔漏),
    用固定替换表兑底,保证结果零残留。替换词均为平实说法。"""
    REPL = [
        ("强势登顶", "冲到第一"), ("登顶", "排第一"),
        ("屠榜", "包揽前排"), ("霸榜", "稳坐第一"),
        ("一夜爆红", "突然火起来"), ("强势占领", "占住"),
        ("引爆", "带火"), ("杀疯了", "热度暴涨"),
        ("杀疯", "热度暴涨"), ("强势出击", "集中发力"),
    ]
    n = 0

    def _sub(s: str) -> str:
        nonlocal n
        for a, b in REPL:
            if a in s:
                s = s.replace(a, b)
                n += 1
        return s

    for k in ("title", "intro", "outro"):
        if data.get(k):
            data[k] = _sub(data[k])
    for it in data.get("items", []):
        if it.get("summary"):
            it["summary"] = _sub(it["summary"])
    if n:
        print(f"[hotword] 热度套词替换 {n} 处")
    return data


def _ensure_taglines(data: Dict, repos: List[Dict]) -> Dict:
    """最终兑底: 每条 item 必须有合法 tagline(4~24字)。
    LLM 没给/被终审丢弃/超长的, 从最终 summary 确定性提取"""
    from image_maker import card_tagline
    repo_desc = {r["rank"]: (r.get("description") or "") for r in repos}
    fixed = 0
    for it in data.get("items", []):
        tl = (it.get("tagline") or "").strip().strip("。．.!！?？ ")
        if not (4 <= len(tl) <= 24):
            tl = card_tagline(it.get("summary", ""),
                              repo_desc.get(it.get("rank"), ""))
            fixed += 1
        it["tagline"] = tl
    if fixed:
        print(f"[tagline] 兑底提取 {fixed} 条")
    return data


def _len_stats(items: List[Dict]) -> str:
    """阶段长度日志: min/avg/max"""
    lens = [len(it.get("summary") or "") for it in items]
    if not lens:
        return "无条目"
    return (f"min={min(lens)} avg={sum(lens)//len(lens)} "
            f"max={max(lens)} 总计={sum(lens)}")


def summarize_repos(repos: List[Dict], period: str = "daily") -> Dict:
    """两段式生成:初稿(规则内嵌) → 去AI味终审
    返回 {title, intro, outro, items: [{rank, tagline, summary}]}
    """
    prompt = _generation_prompt(repos, period)
    parsed = None
    import time as _time
    for attempt in (1, 2, 3):  # 解析失败退避重试
        try:
            raw = _call_llm(prompt, SYSTEM_PROMPT)
        except Exception as e:
            print(f"[summarize] 第{attempt}次LLM调用失败: {e}")
            _time.sleep(15 * attempt)
            continue
        parsed = _extract_json(raw)
        if parsed and "items" in parsed and parsed["items"]:
            break
        # 截断修复(服务端限流时输出常被切断, 多数可修复出部分条目)
        parsed = _repair_truncated_json(raw)
        if parsed and parsed.get("items"):
            parsed["items"] = [it for it in parsed["items"]
                               if (it.get("summary") or "").strip()]
        if parsed and parsed.get("items"):
            print(f"[summarize] 第{attempt}次输出被截断, "
                  f"修复得 {len(parsed['items'])} 条, 继续")
            break
        print(f"[summarize] 第{attempt}次输出无法解析,开头200字: "
              f"{(raw or '')[:200]!r}")
        parsed = None
        _time.sleep(15 * attempt)
    if not parsed or "items" not in parsed or not parsed["items"]:
        # 降级:模板拼接
        print("[summarize] 三次均失败,使用模板降级")
        return _fallback_result(repos, period)

    # 校验并补齐
    items = parsed.get("items") or []
    if len(items) < len(repos):
        have = {it.get("rank") for it in items}
        for r in repos:
            if r["rank"] not in have:
                items.append({"rank": r["rank"],
                              "summary": _template_summary(r)})
        items.sort(key=lambda x: x.get("rank", 99))
    parsed["items"] = items[:len(repos)]
    if not parsed.get("title"):
        parsed["title"] = _fallback_title(repos, period)
    if not parsed.get("intro"):
        parsed["intro"] = _fallback_intro(repos, period)
    if not parsed.get("outro"):
        parsed["outro"] = _fallback_outro(repos, period)

    # Stage 2: 去AI味终审
    print(f"[len] 初稿: {_len_stats(parsed['items'])}")
    parsed = _humanize_pass(parsed)
    print(f"[len] 终审后: {_len_stats(parsed['items'])}")
    # Stage 3: 句式去重(最多两轮;实测第三轮无改善还会磨短文本)
    for _ in range(2):
        if not _detect_repetition(parsed.get("items") or []):
            break
        nxt = _diversify_pass(parsed)
        if nxt is parsed:
            break
        parsed = nxt
        print(f"[len] 去重后: {_len_stats(parsed['items'])}")
    # Stage 4: 确定性后处理(删超限年龄句,代码保证)
    parsed = _post_dedupe(parsed)
    # Stage 5: 热度套词确定性替换
    parsed = _hotword_cleanup(parsed)
    # Stage 6: tagline 兑底(卡片标语)
    parsed = _ensure_taglines(parsed, repos)
    print(f"[len] 最终: {_len_stats(parsed['items'])}")
    return parsed


def _template_summary(r: Dict) -> str:
    desc = (r.get("description") or "一个近期热度飙升的开源项目").strip()
    topics = "、".join((r.get("topics") or [])[:4])
    parts = [f"{r.get('language') or '开源'}写的,已经 {r.get('stars', 0)} 星。{desc}"]
    if topics:
        parts.append(f"标签:{topics}。")
    parts.append("具体玩法可以点链接看仓库说明。")
    return "".join(parts)


def _fallback_result(repos: List[Dict], period: str) -> Dict:
    """LLM 失败时的完整降级结果"""
    return {
        "title": _fallback_title(repos, period),
        "intro": _fallback_intro(repos, period),
        "outro": _fallback_outro(repos, period),
        "items": [{"rank": r["rank"], "summary": _template_summary(r)}
                  for r in repos],
    }


def _repo_name(r: Dict) -> str:
    """兼容原始repo(full_name)/brief(name)两种结构"""
    fn = r.get("full_name") or r.get("name") or "明星项目"
    return fn.rsplit("/", 1)[-1]


def _fallback_title(repos: List[Dict], period: str) -> str:
    """降级标题: 也带钩子(数字优先)"""
    if not repos:
        return "GitHub 今日新项目榜TOP10"
    period_cn = "每日" if period == "daily" else "周度"
    top = repos[0]
    gained = _parse_gained(top.get("period_stars", "") or
                           top.get("hot", ""))
    name = _repo_name(top)
    if gained >= 500:
        return f"{gained}星({_period_cn_short(period)})!{name}冲上GitHub榜首"
    if top.get("stars", 0) >= 10000:
        return f"GitHub{period_cn}榜:{name}等项目都破万星了"
    return f"GitHub{period_cn}新项目榜TOP{len(repos)}:{name}排第一"


def _period_cn_short(period: str) -> str:
    return "单日" if period == "daily" else "本周"


def _fallback_intro(repos: List[Dict], period: str) -> str:
    period_cn = "今天" if period == "daily" else "本周"
    top = repos[0] if repos else None
    gained = _parse_gained((top.get("period_stars", "") or
                            top.get("hot", "")) if top else "")
    if gained:
        hot_cn = f"最近涨了{gained}星"
    else:
        hot_cn = "是近期新冒头的项目"
    return (f"盒友们,{period_cn}的 GitHub 新项目榜单。排第一的是 "
            f"{_repo_name(top) if top else '明星项目'},{hot_cn},"
            f"后面还有几个值得逛的,都在下面了。")


def _fallback_outro(repos: List[Dict], period: str) -> str:
    return "榜单和数据来自 GitHub Trending,看上哪个直接搜名字就能玩。"


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/z/my-project/github-hot-heybox")
    from github_trending import fetch_trending, enrich_details, fetch_readme_excerpts

    repos = fetch_trending("daily", top_n=5)
    repos = enrich_details(repos)
    repos = fetch_readme_excerpts(repos)
    result = summarize_repos(repos, "daily")
    print("TITLE:", result["title"])
    print("INTRO:", result["intro"])
    print("OUTRO:", result["outro"])
    for it in result["items"]:
        print(f"\n--- {it['rank']} ({len(it['summary'])}字) ---")
        print(it["summary"])
