# -*- coding: utf-8 -*-
"""
小黑盒发帖器: POST /bbs/app/api/link/post
payload 结构来自对 Web 编辑器的逆向分析(API_RESEARCH.md)
"""
import json
import re
from typing import List, Optional

from heybox_client import HeyboxClient
from heybox_uploader import HeyboxUploader


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
                desc: str = "", topic_ids: Optional[str] = None,
                view_limit: int = 1, original: int = 1,
                dry_run: bool = True) -> dict:
        """发布图文帖
        dry_run=True 时只构造 payload 并返回,不真正发送
        实测: 发帖响应 status=ok 但 result 为空, link_id 需从创作者中心接口兑底获取
        """
        image_blocks = list(image_blocks or [])
        for p in image_paths or []:
            image_blocks.append(self.upload_local_image(p))

        text_param = build_content_blocks(content_text, image_blocks)
        body = {
            "link_tag": "27",           # 图文
            "text": text_param,
            "title": title,
            "desc": desc or content_text[:80],
            "words_count": str(count_words(content_text)),
            "topic_ids": topic_ids or self.topic_ids,
            "hashtags": "[]",
            "schedule_ts": "0",
            "view_limit": str(view_limit),  # 1=所有人
            "post_type": "1",           # 无
            "original": str(original),  # 1=原创
            "declaration": "0",
        }

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
        # link_id 可能在 result 内或顶层; 都没有则从创作者中心兑底取最新一条
        link_id = (result.get("link_id") or result.get("linkid")
                   or resp.get("link_id") or resp.get("linkid") or "")
        if not link_id:
            link_id = self.get_latest_post_id()
        post_url = (f"https://www.xiaoheihe.cn/community/"
                    f"{topic_ids or self.topic_ids}/list/{link_id}")
        return {"dry_run": False, "link_id": link_id, "post_url": post_url,
                "raw": result, "raw_resp": resp}


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
