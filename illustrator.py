# -*- coding: utf-8 -*-
"""小黑怪诞正文配图生成器

把帖子里的关键判断/流程/状态/隐喻变成 16:9 手绘配图, 插在正文对应
段落后面(不是末尾堆图)。

风格与工作流内化自 Ian 的 ian-xiaohei-illustrations skill (MIT License):
  https://github.com/helloianneo/ian-xiaohei-illustrations
  - 风格 DNA: 纯白背景 + 黑色手绘线稿 + 大量留白 + 红橙蓝中文手写批注
  - 小黑 IP: 黑色实心/白点眼/细腿/空表情, 必须承担画面核心动作
  - 构图: 8 种结构类型, 每次为当前内容重新发明低科技隐喻, 不复用旧构图
  - QA: 生成后 VLM 质检, 不合格重生成一次, 仍不合格则跳过该图

管线: plan_shots(LLM 挑认知锚点) → build_prompt(模板组装)
      → generate(z-ai image 1344x768) → qa_check(z-ai vision)
任一环节失败只影响单张图, 不阻断发帖(长图与文字流照发)。
"""
import json
import os
import re
import subprocess
import tempfile
from typing import Dict, List, Optional

from summarizer import _call_llm, _extract_json, _repair_truncated_json

# ---------------------------------------------------------------------------
# 内化的风格知识(供 LLM 规划用) — 浓缩自 skill 的 references/
# ---------------------------------------------------------------------------

STRUCTURE_TYPES = (
    "Workflow流程(输入→处理→输出) / 系统局部(3-5个核心模块) / "
    "前后对比(左乱右稳) / 角色状态(2-4个小状态) / 概念隐喻(一个大怪物件) / "
    "方法分层(一层层盒子) / 地图路线(弯曲路径少量节点) / 小漫画分镜(2-4格)"
)

METAPHOR_METHOD = """三步发明隐喻(必须每次重新发明, 禁止复用已有构图):
1. 把抽象概念换成一个物理动作: 卡住/漏掉/变重/分拣/沉淀/发酵/开门/折叠/拆包/回流/按压/称重
2. 把系统结构换成一个低科技物件(只选1-2个, 不要堆满): 纸箱/抽屉/旧机器/漏斗/秤/邮筒/门/井/梯子/水管/线团/闸门/转盘/黑盒/打孔器/压面机/晾衣绳/怪工位
3. 让小黑承担这个动作(不是站旁边看): 卡在机器里/拉线/守门/搬运/修补/称重/把东西塞进怪装置"""

XIAOHEI_RULES = """小黑: 黑色实心剪影小怪物(不是火柴人不是轮廓线), 白色圆点眼睛,
细腿, 空而认真的表情, 身形略不规则有手绘感。很认真但做的事有点荒诞,
冷幽默不卖萌, 像白板草图里真的负责某项工作的系统操作员。
禁止: 可爱吉祥物化/儿童卡通/复杂服装/表情包/只站角落当装饰。"""

# 生图提示词模板 — 内化自 skill 的 prompt-template.md, 经实测迭代:
# ① 小黑强调 solid filled silhouette(否则模型画成火柴人)
# ② 明确禁画楼房/服务器/城市剪影(否则混入扁平矢量素材, 破坏手绘统一性)
# ③ 标注文字逐条精确给出(降低中文错字率)
_PROMPT_TMPL = """Generate one standalone 16:9 horizontal Chinese article illustration, hand-drawn ink sketch style.

Visual DNA:
Pure clean white background with absolutely no noise, no texture, no gradient. Minimalist black hand-drawn ink line art with slightly wobbly pen strokes. Lots of empty white space. Only a few short handwritten Chinese annotations. Absurd clean product-sketch feeling on white paper. No flat vector icons, no PPT infographic look, no commercial illustration, no cute mascot, no children style.

Recurring IP character required:
小黑: a small completely solid-black filled silhouette creature (NOT a stick figure, NOT an outline drawing), with two white dot eyes, tiny thin stick legs, blank deadpan serious expression, hand-drawn uneven round body. 小黑 must perform the core action of the picture, not stand aside. Serious, deadpan, slightly bizarre, NOT cute.

Theme:
{theme}

Core idea:
{core_idea}

Composition:
{scene}

Chinese handwritten labels (hand-drawn wobbly style, only these {n_labels}, do not add any other text):
{labels_block}

Color use:
Black ink for all main line art, objects and 小黑. {color_notes}

Constraints:
One picture tells one idea. Main subject occupies about half the canvas, at least 40% pure white space. Do not draw any buildings, any servers, any city skyline, any realistic UI. Every object and creature must be drawn in the same loose naive hand-drawn ink style: no realistic rendering, no detailed fur or shading, no 3D volume. Do not write a title anywhere. Do not make it a diagram or flowchart. Strange but clean, clear but not instructional."""

_LABEL_ROLE = {          # role → (英文色名, 用途说明)
    "subject": ("black", "主题词"),
    "flow":    ("orange", "路径/箭头/流向"),
    "key":     ("red", "重点/结果/惊叹"),
    "note":    ("blue", "补充/系统状态"),
}

# ---------------------------------------------------------------------------
# Step 1: LLM 规划 shot list(挑认知锚点, 不平均配图)
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = "你是给中文帖子画怪诞手绘配图的美术指导, 只输出 JSON。"

_PLAN_TMPL = """给下面这个帖子规划 {max_count} 张正文配图(shot list)。

【选点原则】不要平均配图。只挑最有画面感、最能一张图讲清楚的条目,
优先: 核心判断 / 前后对比 / 一多转换 / 角色状态变化 / 荒诞反差大的新闻。
{extra_hint}

【隐喻发明法】
{metaphor_method}

【小黑 IP】
{xiaohei_rules}

【构图结构类型(8选1)】
{structure_types}

【输出 JSON】
{{"shots": [{{
  "after_num": <配图放在第几条正文后面(1~{n_items}的整数, {max_count}张各不相同)>,
  "theme": "<16字内主题>",
  "core_idea": "<一句话: 这张图要让读者秒懂的意思>",
  "structure": "<8种结构类型之一>",
  "scene": "<3句英文或中文画面描述: 左/中/右布局, 主物件是什么, 小黑具体在做什么动作, 情绪如何。要具体到能照着画>",
  "labels": [
    {{"text": "<2~8个汉字或数字>", "role": "subject|flow|key|note"}},
    ... 共3~5条, 每条role不同, 文字要短
  ]
}}]}}

注意: labels 总数 3~5 条; scene 里的物件必须低科技; 不同 shot 的隐喻
物件不能重复; 直接输出 JSON, 不要解释。

【帖子内容】
标题: {title}
{items_block}"""


def plan_shots(title: str, items: List[Dict], max_count: int = 3,
               extra_hint: str = "") -> List[Dict]:
    """LLM 挑认知锚点 → shot list

    items: [{num, headline, summary}] (周榜时 num=rank, headline=仓库名)
    返回: [{after_num, theme, core_idea, structure, scene, labels:[{text,role}]}]
    """
    n_items = len(items)
    if n_items == 0 or max_count <= 0:
        return []
    items_block = "\n".join(
        f"第{it['num']}条: {it.get('headline', '')} — "
        f"{(it.get('summary') or '')[:160]}"
        for it in items)
    prompt = _PLAN_TMPL.format(
        max_count=max_count, n_items=n_items, extra_hint=extra_hint,
        metaphor_method=METAPHOR_METHOD, xiaohei_rules=XIAOHEI_RULES,
        structure_types=STRUCTURE_TYPES, title=title, items_block=items_block)

    for attempt in range(3):
        raw = _call_llm(prompt, system=_PLAN_SYSTEM)
        data = _extract_json(raw) or _repair_truncated_json(raw)
        shots = (data or {}).get("shots")
        if isinstance(shots, list) and shots:
            return _validate_shots(shots, items, max_count)
        print(f"   [warn] shot list 解析失败(第{attempt+1}次), 重试...")
    return []


def _validate_shots(shots: List[Dict], items: List[Dict],
                    max_count: int) -> List[Dict]:
    """清洗 LLM 输出: after_num 合法、labels 规范、去重、截断到 max_count"""
    valid_nums = {int(it["num"]) for it in items}
    seen_num, seen_theme, out = set(), set(), []
    for sh in shots:
        if len(out) >= max_count:
            break
        try:
            num = int(sh.get("after_num"))
        except (TypeError, ValueError):
            continue
        if num not in valid_nums or num in seen_num:
            continue
        theme = str(sh.get("theme") or "").strip()[:24]
        core = str(sh.get("core_idea") or "").strip()
        scene = str(sh.get("scene") or "").strip()
        if not (theme and core and scene):
            continue
        if theme in seen_theme:            # 同主题去重
            continue
        labels = []
        for lb in (sh.get("labels") or [])[:5]:
            text = str(lb.get("text") or "").strip() if isinstance(lb, dict) else ""
            role = str(lb.get("role") or "").strip() if isinstance(lb, dict) else ""
            if text and role in _LABEL_ROLE and 1 <= len(text) <= 10:
                labels.append({"text": text, "role": role})
        if not labels:
            labels = [{"text": theme[:6], "role": "subject"}]
        seen_num.add(num)
        seen_theme.add(theme)
        out.append({"after_num": num, "theme": theme, "core_idea": core,
                    "structure": str(sh.get("structure") or "")[:30],
                    "scene": scene, "labels": labels})
    return out


# ---------------------------------------------------------------------------
# Step 2: 组装生图提示词
# ---------------------------------------------------------------------------

def build_prompt(shot: Dict) -> str:
    labels_block = "\n".join(f'- "{lb["text"]}"' for lb in shot["labels"])
    color_notes = []
    for lb in shot["labels"]:
        en, cn = _LABEL_ROLE[lb["role"]]
        color_notes.append(f'{en} for the handwritten label "{lb["text"]}"')
    return _PROMPT_TMPL.format(
        theme=shot["theme"], core_idea=shot["core_idea"], scene=shot["scene"],
        n_labels=len(shot["labels"]), labels_block=labels_block,
        color_notes="; ".join(color_notes))


# ---------------------------------------------------------------------------
# Step 3/4: 生图 + VLM 质检
# ---------------------------------------------------------------------------

def _generate_image(prompt: str, out_path: str, timeout: int = 600) -> bool:
    try:
        r = subprocess.run(
            ["z-ai", "image", "-p", prompt, "-o", out_path, "-s", "1344x768"],
            capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0 and os.path.exists(out_path) \
            and os.path.getsize(out_path) > 20000
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"   [warn] 生图异常: {e}")
        return False


_QA_TMPL = ("这是一张中文帖子配图, 请严格质检。逐项回答: "
            "1)背景是否纯白干净(允许轻微纸张感, 不允许明显噪点/灰色块) "
            "2)是否有黑色实心、白色圆点眼睛的怪物角色, 且它在执行核心动作"
            "(不是站着当装饰) "
            "3)是否手绘墨线风格(出现扁平矢量图标/PPT感/商业插画即不合格) "
            "4)中文标注是否只有这几条且无严重错字: {labels} "
            "5)是否无标题、无楼房、无服务器、无城市剪影 "
            "6)留白是否充足(主体不超过画面60%)。"
            "第一行只输出\"合格\"或\"不合格\", 第二行起简短列问题。")


def qa_check(image_path: str, shot: Dict) -> bool:
    """VLM 按 skill 的 QA checklist 质检; 首行含'合格'且不含'不合格'即通过"""
    labels = " / ".join(lb["text"] for lb in shot["labels"])
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            out_json = tf.name
        try:
            subprocess.run(
                ["z-ai", "vision", "-p", _QA_TMPL.format(labels=labels),
                 "-i", image_path, "-o", out_json],
                capture_output=True, text=True, timeout=300, check=True)
            with open(out_json) as f:
                data = json.load(f)
        finally:
            try:
                os.unlink(out_json)
            except OSError:
                pass
        content = ((data.get("choices") or [{}])[0]
                   .get("message", {}).get("content", ""))
    except Exception as e:
        print(f"   [warn] VLM 质检异常(视为通过, 不因质检故障丢图): {e}")
        return True
    first = (content.strip().splitlines() or [""])[0]
    verdict = "不合格" not in first and "合格" in first
    if not verdict:
        print(f"   [warn] 质检意见: {content.strip()[:150]}")
    return verdict


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def make_illustrations(title: str, items: List[Dict], out_dir: str,
                       prefix: str = "illus", max_count: int = 3,
                       extra_hint: str = "",
                       oneshot: Optional[Dict] = None) -> Dict[int, str]:
    """生成配图, 返回 {after_num: image_path}

    items: [{num, headline, summary}]
    oneshot: 手动指定单张(测试/补图用), 如
             {"after_num":1, "theme":..., "core_idea":..., "scene":..., "labels":[...]}
    任一张失败不影响其他; 全部失败返回 {}(调用方照常发帖)。
    """
    os.makedirs(out_dir, exist_ok=True)
    if oneshot:
        shots = _validate_shots([oneshot], items, 1)
    else:
        print(f"   [插图] LLM 挑认知锚点(最多{max_count}张)...")
        shots = plan_shots(title, items, max_count, extra_hint)
    if not shots:
        print("   [插图] 没有可用 shot, 跳过配图")
        return {}

    result: Dict[int, str] = {}
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    for i, shot in enumerate(shots, 1):
        num = shot["after_num"]
        print(f"   [插图 {i}/{len(shots)}] 第{num}条后: {shot['theme']}"
              f" ({shot.get('structure') or '构图'})")
        path = os.path.join(out_dir, f"{prefix}_{ts}_{num}.png")
        prompt = build_prompt(shot)
        ok = _generate_image(prompt, path)
        if ok and not qa_check(path, shot):
            print("   [插图] 质检不合格, 重生成一次...")
            ok = _generate_image(prompt, path)
            if ok and not qa_check(path, shot):
                ok = False
        if ok:
            result[num] = path
            print(f"   [插图] ✅ {path}")
        else:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass
            print(f"   [插图] ❌ 第{num}条配图两轮未过, 跳过(帖子照发)")
    return result
