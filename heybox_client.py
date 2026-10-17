# -*- coding: utf-8 -*-
"""
小黑盒 (Heybox) Web API 客户端
签名算法移植自 WenfuRainbow/heybox (VSCode 插件) 的 src/api/signature.ts
以及 plasta2steel/dsh-xiaoheihe 的 lib/index.js，字节级一致。

用法:
    from heybox_client import HeyboxClient
    client = HeyboxClient(cookie="...")
    result = client.get("/bbs/app/topic/categories")
"""
import hashlib
import json
import math
import random
import time
import urllib.parse
from typing import Any, Dict, Optional

import requests

API_BASE = "https://api.xiaoheihe.cn"
REFERER = "https://www.xiaoheihe.cn/"
ORIGIN = "https://www.xiaoheihe.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

ALPHABET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"


# ---------------------------------------------------------------------------
# hkey 签名算法（移植自 JS，字节级一致）
# ---------------------------------------------------------------------------
def _md5hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _vm(e: int) -> int:
    # JS: 128 & e ? 255 & ((e << 1) ^ 27) : e << 1
    if e & 128:
        return 255 & ((e << 1) ^ 27)
    return e << 1


def _qm(e: int) -> int:
    return _vm(e) ^ e


def _dm(e: int) -> int:  # dollarM
    return _qm(_vm(e))


def _ym(e: int) -> int:
    return _dm(_qm(_vm(e)))


def _gm(e: int) -> int:
    return _ym(e) ^ _dm(e) ^ _qm(e)


def _km(e: list) -> list:
    # JS 原版: 原地修改前 4 个元素并返回整个数组(可能超过 4 个元素,求和含全部)
    t = [0, 0, 0, 0]
    t[0] = _gm(e[0]) ^ _ym(e[1]) ^ _dm(e[2]) ^ _qm(e[3])
    t[1] = _qm(e[0]) ^ _gm(e[1]) ^ _ym(e[2]) ^ _dm(e[3])
    t[2] = _dm(e[0]) ^ _qm(e[1]) ^ _gm(e[2]) ^ _ym(e[3])
    t[3] = _ym(e[0]) ^ _dm(e[1]) ^ _qm(e[2]) ^ _gm(e[3])
    e[0], e[1], e[2], e[3] = t[0], t[1], t[2], t[3]
    return e


def _av(text: str, alphabet: str, offset: int) -> str:
    # JS: alphabet.slice(0, offset) — Python 切片语义与 JS slice(0, n) 完全一致
    # (负数时同样是"从头到倒数第|n|个字符之前")
    sliced = alphabet[:offset]
    result = []
    for ch in text:
        result.append(sliced[ord(ch) % len(sliced)])
    return "".join(result)


def _sv(text: str, alphabet: str) -> str:
    result = []
    for ch in text:
        result.append(alphabet[ord(ch) % len(alphabet)])
    return "".join(result)


def _interleave(arr) -> str:
    result = []
    max_len = max(len(s) for s in arr)
    for i in range(max_len):
        for s in arr:
            if i < len(s):
                result.append(s[i])
    return "".join(result)


def compute_hkey(path: str, timestamp: int, nonce: str) -> str:
    normalized_path = "/" + "/".join([s for s in path.split("/") if s]) + "/"
    enc_t = _av(str(timestamp), ALPHABET, -2)
    enc_p = _sv(normalized_path, ALPHABET)
    enc_n = _sv(nonce, ALPHABET)
    interleaved = _interleave([enc_t, enc_p, enc_n])[:20]
    h = _md5hex(interleaved)
    last6 = [ord(c) for c in h[-6:]]
    transformed = _km(last6)
    checksum = str(sum(transformed) % 100)
    if len(checksum) < 2:
        checksum = "0" + checksum
    prefix = _av(h[:5], ALPHABET, -4)
    return prefix + checksum


def generate_signature(api_path: str) -> Dict[str, Any]:
    timestamp = int(time.time())
    nonce = _md5hex(str(timestamp) + str(random.random())).upper()
    hkey = compute_hkey(api_path, timestamp + 1, nonce)
    return {"hkey": hkey, "_time": timestamp, "nonce": nonce}


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class HeyboxClient:
    def __init__(self, cookie: str, device_id: Optional[str] = None,
                 heybox_id: Optional[str] = None, web_version: str = "2.5"):
        self.cookie = cookie.strip()
        if device_id is None:
            device_id = hashlib.sha256(cookie.encode()).hexdigest()[:32]
        self.device_id = device_id
        if heybox_id is None:
            import re
            m = re.search(r"heybox_id=(\d+)", cookie)
            heybox_id = m.group(1) if m else ""
        self.heybox_id = str(heybox_id)
        self.web_version = web_version
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": REFERER,
            "Origin": ORIGIN,
            "Cookie": self.cookie,
        })

    # ---- 基础 ----
    def common_params(self) -> Dict[str, str]:
        return {
            "os_type": "web",
            "app": "heybox",
            "client_type": "web",
            "version": "999.0.4",
            "web_version": self.web_version,
            "x_client_type": "web",
            "x_app": "heybox_website",
            "heybox_id": self.heybox_id,
            "x_os_type": "Windows",
            "device_info": "Chrome",
            "device_id": self.device_id,
        }

    def build_url(self, path: str, extra_params: Optional[Dict] = None) -> str:
        sig = generate_signature(path)
        params = dict(self.common_params())
        params.update({"hkey": sig["hkey"], "_time": str(sig["_time"]),
                       "nonce": sig["nonce"]})
        if extra_params:
            params.update({k: str(v) for k, v in extra_params.items()})
        return API_BASE + path + "?" + urllib.parse.urlencode(params)

    # ---- 请求 ----
    def get(self, path: str, params: Optional[Dict] = None,
            timeout: int = 20) -> Dict[str, Any]:
        url = self.build_url(path, params)
        resp = self.session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def post_form(self, path: str, form: Optional[Dict] = None,
                  params: Optional[Dict] = None,
                  timeout: int = 20) -> Dict[str, Any]:
        url = self.build_url(path, params)
        headers = {"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"}
        resp = self.session.post(url, data=form or {}, headers=headers,
                                 timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def post_json(self, path: str, payload: Any,
                  params: Optional[Dict] = None,
                  timeout: int = 20) -> Dict[str, Any]:
        """部分接口(如发帖)可能用 JSON body"""
        url = self.build_url(path, params)
        headers = {"Content-Type": "application/json;charset=utf-8"}
        resp = self.session.post(url, data=json.dumps(payload, ensure_ascii=False)
                                 .encode("utf-8"),
                                 headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # ---- 语义化接口 ----
    def check_login(self) -> Dict[str, Any]:
        """验证登录态:拉取消息列表"""
        return self.get("/bbs/app/user/message", {
            "list_type": "0", "offset": "0", "limit": "1", "no_more": "false"})

    def get_topic_categories(self) -> Dict[str, Any]:
        """板块(话题)分类列表"""
        return self.get("/bbs/app/topic/categories")

    def get_topic_feeds(self, topic_id: int, offset: int = 0,
                        limit: int = 30) -> Dict[str, Any]:
        return self.get("/bbs/app/topic/feeds", {
            "topic_id": str(topic_id), "offset": str(offset),
            "limit": str(limit)})

    def get_feed(self, offset: int = 0) -> Dict[str, Any]:
        return self.get("/bbs/app/feeds", {"offset": str(offset), "pull": "0",
                                           "dw": "800"})

    def search(self, q: str, offset: int = 0, limit: int = 20) -> Dict[str, Any]:
        return self.get("/bbs/app/api/general/search/v1", {
            "q": q, "search_type": "link", "is_pull_down": "0",
            "offset": str(offset), "limit": str(limit), "dw": "800"})

    def get_post_tree(self, link_id: str) -> Dict[str, Any]:
        return self.get("/bbs/app/link/tree", {
            "link_id": link_id, "offset": "0", "limit": "20"})

    def favourite_post(self, link_id: str) -> Dict[str, Any]:
        """收藏帖子(已知可用的 POST 写操作,用于验证写权限)"""
        return self.post_form("/bbs/app/link/favour", {"link_id": link_id},
                              {"link_id": link_id})

    def unwrap(self, data: Dict[str, Any]) -> Any:
        """API 返回 {status, result, msg};status!=ok 时抛异常"""
        status = data.get("status")
        if status == "ok":
            return data.get("result")
        raise RuntimeError(f"Heybox API error: status={status} msg={data.get('msg')}")

    def check_login_ok(self) -> bool:
        try:
            data = self.check_login()
            return data.get("status") == "ok"
        except Exception:
            return False


if __name__ == "__main__":
    # 自测签名算法
    sig = generate_signature("/bbs/app/topic/categories")
    print("signature sample:", sig)
    assert len(sig["hkey"]) >= 6
    print("hkey len:", len(sig["hkey"]))
