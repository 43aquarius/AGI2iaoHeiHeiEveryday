# -*- coding: utf-8 -*-
"""
小黑盒发帖器: POST /bbs/app/api/link/post
payload 结构来自对 Web 编辑器的逆向分析(API_RESEARCH.md)

支持三种组装方式:
  1. 旧式: content_text + image_paths (文字在前,图片全部附在末尾)
  2. segments(图文模式, link_tag=27): [("text", "..."), ("img", path), ...]
     ⚠️ 服务器会把图片归一化到所有文字块之后(2026-10-19 实测),
     无法真正图文交错, 已被文章模式取代
  3. segments(article=True, link_tag=11 文章): segments 合成一个
     {"type":"html"} 富文本块, 图片变成行内 <img data-*> 标签,
     另附末尾 {"type":"img"} 引用块(封面/图库用)。
     行内位置服务器保留、渲染端显示在文字中间(2026-10-19 验证)。
     文章帖必须需 topic_ids(分区), 否则报"请添加分区"。
     发帖响应直接含 link_id。
"""
import json
import os
import re
from typing import List, Optional, Tuple

from heybox_client import HeyboxClient
from heybox_uploader import HeyboxUploader


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_content_blocks(text: Optional[str] = None,
                         images: Optional[List[dict]] = None) -> str:
    """构造富文本 content JSON 数组
    images: [{"url": "...", "width": 600, "height": 400}, ...]
    """
    blocks = []
    if text:
        for para in text.split("\n"):
            if para.strip():
                blocks.append({"type": "text", "text": para.strip()})
    for img in images or []:
        blocks.append({
            "type": "img",
            "url": img["url"],
            "width": img.get("width", 800),
            "height": img.get("height", 600),
        })
    return json.dumps(blocks, ensure_ascii=False)


def count_words(text: str) -> int:
    """粗略字数统计(与前端 words_count 一致性不严格,服务端不校验)"""
    cleaned = re.sub(r"\s", "", text or "")
    return len(cleaned)


class HeyboxPublisher:
    def __init__(self, client: HeyboxClient, topic_ids: str = "7214"):
        self.client = client
        self.uploader = HeyboxUploader(client)
        self.topic_ids = topic_ids

    def _blocks_from_segments(
            self, segments: List[Tuple[str, str]]) -> Tuple[list, str]:
        """segments → (块数组, 纯文本全文) [图文模式 link_tag=27, 已弃用]
        text 段: 一个段一个 text 块, 块内 \\n 原样保留(break-spaces 渲染换行/空行)
        img 段: 上传后插入 img 块, 失败则跳过(帖子照发,不让单图故障阻断)"""
        blocks = []
        texts = []
        for kind, val in segments or []:
            if kind == "text":
                t = (val or "").strip("\n")
                if t.strip():
                    blocks.append({"type": "text", "text": t})
                    texts.append(t)
            elif kind == "img":
                if not val or not os.path.exists(val):
                    print(f"   [warn] 图片不存在,跳过: {val}")
                    continue
                try:
                    b = self.upload_local_image(val)
                except Exception as e:
                    print(f"   [warn] 图片上传失败,跳过 {val}: {e}")
                    continue
                blocks.append({"type": "img", **b})
        return blocks, "\n".join(texts)

    def _blocks_from_segments_article(
            self, segments: List[Tuple[str, str]]) -> Tuple[list, str]:
        """segments → 文章格式 (link_tag=11): 行内插图真正夹在文字中间

        text 段: HTML转义后按空行分段, 每段一个 <p>; 段内单换行 → <br>
        img 段: 上传后变行内 <img data-type="image" data-original=...>
                并在块数组末尾追加 {"type":"img"} 引用块(封面/图库)
        返回 (块数组, 纯文本全文)"""
        html_parts: List[str] = []
        img_refs: List[dict] = []
        texts: List[str] = []
        for kind, val in segments or []:
            if kind == "text":
                t = (val or "").strip("\n")
                if not t.strip():
                    continue
                texts.append(t)
                esc = _html_escape(t)
                for para in re.split(r"\n\s*\n", esc):
                    if para.strip():
                        html_parts.append(
                            "<p>" + para.strip().replace("\n", "<br>") + "</p>")
            elif kind == "img":
                if not val or not os.path.exists(val):
                    print(f"   [warn] 图片不存在,跳过: {val}")
                    continue
                try:
                    b = self.upload_local_image(val)
                except Exception as e:
                    print(f"   [warn] 图片上传失败,跳过 {val}: {e}")
                    continue
                img_refs.append(b)
                html_parts.append(
                    f'<p><img data-type="image" data-original="{b["url"]}" '
                    f'data-width="{b["width"]}" data-height="{b["height"]}" '
                    f'data-description=""></p>')
        if not html_parts:
            return [], ""
        blocks = [{"type": "html", "text": "".join(html_parts)}]
        for b in img_refs:
            blocks.append({"type": "img", "url": b["url"],
                           "width": str(b["width"]),
                           "height": str(b["height"])})
        return blocks, "\n".join(texts)

    def upload_local_image(self, image_path: str) -> dict:
        """上传本地图片,返回编辑器需要的 block dict"""
        data, width, height = self.uploader._read_image(image_path)
        filename = image_path.rsplit("/", 1)[-1]
        mimetype = "image/png" if filename.endswith(".png") else "image/jpeg"
        url, _thumb = self.uploader.upload_bytes(
            data, filename, mimetype, width, height)
        return {"url": url, "width": width, "height": height}

    def get_latest_post_id(self) -> str:
        """发帖后从创作者中心拉最新一条帖子, 兼容发帖响应不含 link_id 的情况"""
        try:
            data = self.client.get("/bbs/app/author/concept/article/list", {
                "article_type": "all", "artilce_state": "all",
                "sort_filter": "create_at", "offset": "0", "limit": "1"})
            links = (data.get("result") or {}).get("links") or []
            if links:
                return str(links[0].get("linkid") or links[0].get("link_id") or "")
        except Exception:
            pass
        return ""

    def publish(self, title: str, content_text: str = "",
                image_paths: Optional[List[str]] = None,
                image_blocks: Optional[List[dict]] = None,
                segments: Optional[List[Tuple[str, str]]] = None,
                desc: str = "", topic_ids: Optional[str] = None,
                view_limit: int = 1, original: int = 1,
                dry_run: bool = True,
                article: bool = True) -> dict:
        """发布帖子(默认文章模式)
        article=True(默认, link_tag=11): segments 合成一个 html 富文本块,
        插图行内夹在文字中间(服务器保留位置, 2026-10-19 验证)。
        article=False: 旧图文模式(27), 图片会被归一化到文字后面。
        dry_run=True 时只构造 payload 并返回,不真正发送
        """
        link_tag = "11" if article else "27"
        if segments is not None:
            if article:
                blocks, full_text = self._blocks_from_segments_article(segments)
            else:
                blocks, full_text = self._blocks_from_segments(segments)
            if not desc:
                first_text = next((v for k, v in segments if k == "text"), "")
                desc = re.sub(r"\s", " ", first_text)[:80]
        else:
            image_blocks = list(image_blocks or [])
            for p in image_paths or []:
                image_blocks.append(self.upload_local_image(p))
            blocks = json.loads(build_content_blocks(content_text,
                                                     image_blocks))
            full_text = content_text

        text_param = json.dumps(blocks, ensure_ascii=False)
        body = {
            "link_tag": link_tag,         # 11=文章(行内插图) / 27=图文
            "text": text_param,
            "title": title,
            "desc": desc or re.sub(r"\s", " ", full_text)[:80],
            "words_count": str(count_words(full_text)),
            "topic_ids": topic_ids or self.topic_ids,   # 文章模式必须需分区
            "hashtags": "[]",
            "schedule_ts": "0",
            "view_limit": str(view_limit),  # 1=所有人
            "post_type": "1",           # 无
            "original": str(original),  # 1=原创
            "declaration": "0",
        }
        n_img = sum(1 for b in blocks if b.get("type") == "img")
        n_inline = (blocks[0]["text"].count("<img ")
                    if blocks and blocks[0].get("type") == "html" else 0)
        print(f"   组装完成: {len(blocks)} 块 [link_tag={link_tag} "
              f"{'文章' if article else '图文'}] 行内图 {n_inline} / 图库 {n_img}")

        if dry_run:
            return {"dry_run": True, "endpoint": "/bbs/app/api/link/post",
                    "body": body}

        resp = self.client.post_form("/bbs/app/api/link/post", body)
        status = resp.get("status")
        if status != "ok":
            raise RuntimeError(f"发帖失败: {json.dumps(resp, ensure_ascii=False)[:400]}")
        result = resp.get("result") or {}
        if not isinstance(result, dict):
            result = {}
        # link_id: 文章模式响应直接含; 图文模式可能为空, 创作者中心兑底
        link_id = (result.get("link_id") or result.get("linkid")
                   or resp.get("link_id") or resp.get("linkid") or "")
        if not link_id:
            link_id = self.get_latest_post_id()
        post_url = (f"https://www.xiaoheihe.cn/community/"
                    f"{topic_ids or self.topic_ids}/list/{link_id}")
        return {"dry_run": False, "link_id": link_id, "post_url": post_url,
                "raw": result, "raw_resp": resp}

    def delete_post(self, link_id: str) -> dict:
        """删除帖子(2026-10-19 逆向: POST /bbs/app/link/delete, form: link_id)"""
        return self.client.post_form("/bbs/app/link/delete",
                                     {"link_id": str(link_id)})


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/z/my-project/github-hot-heybox")
    with open("/home/z/my-project/github-hot-heybox/config.json") as f:
        cfg = json.load(f)
    client = HeyboxClient(cfg["cookie"])
    pub = HeyboxPublisher(client)
    # 干跑测试
    out = pub.publish(
        title="GitHub每日热榜TOP10（测试）",
        content_text="这是干跑测试内容。\n第二段落。",
        image_paths=["/home/z/my-project/github-hot-heybox/output/test_upload.png"],
        dry_run=True)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:1500])
