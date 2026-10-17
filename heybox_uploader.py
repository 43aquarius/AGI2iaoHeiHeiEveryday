# -*- coding: utf-8 -*-
"""
小黑盒图片上传器: 腾讯云 COS 直传四步流程
1. POST /bbs/app/api/qcloud/cos/upload/info/v2   → 获取上传 key/bucket/region
2. POST /bbs/app/api/qcloud/cos/upload/token/v2   → 获取临时凭证
3. PUT  https://{bucket}.cos.{region}.myqcloud.com{key}  → 直传(标准COS签名)
4. POST /bbs/app/api/qcloud/cos/upload/callback/v2 → 换取最终 URL
"""
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Optional, Tuple

import requests

from heybox_client import HeyboxClient


# ---------------------------------------------------------------------------
# 腾讯云 COS XML API 签名(临时凭证)
# ---------------------------------------------------------------------------
def _hmac_sha1_hex(key: str, msg: str) -> str:
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"),
                    hashlib.sha1).hexdigest()


def _sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def cos_sign(tmp_secret_id: str, tmp_secret_key: str, method: str,
             path: str, host: str, content_length: int,
             key_time: str, params: Optional[dict] = None) -> str:
    """生成 COS Authorization 头(与官方 cos-js-sdk-v5 一致)"""
    params = params or {}
    # url-param-list / header-list: 小写 key 排序
    url_params = "&".join(
        f"{urllib.parse.quote(k, safe='').lower()}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(params.items()))
    headers = {"content-length": str(content_length), "host": host}
    header_str = "&".join(f"{k}={v}" for k, v in sorted(headers.items()))

    http_string = f"{method.lower()}\n{path}\n{url_params}\n{header_str}\n"
    string_to_sign = f"sha1\n{key_time}\n{_sha1_hex(http_string)}\n"
    sign_key = _hmac_sha1_hex(tmp_secret_key, key_time)
    signature = _hmac_sha1_hex(sign_key, string_to_sign)

    return (f"q-sign-algorithm=sha1&q-ak={tmp_secret_id}"
            f"&q-sign-time={key_time}&q-key-time={key_time}"
            f"&q-header-list={';'.join(sorted(headers.keys()))}"
            f"&q-url-param-list={';'.join(sorted(k.lower() for k in params))}"
            f"&q-signature={signature}")


class HeyboxUploader:
    def __init__(self, client: HeyboxClient, timeout: int = 60):
        self.client = client
        self.timeout = timeout

    def _read_image(self, image_path: str) -> Tuple[bytes, int, int]:
        from PIL import Image
        import io
        with Image.open(image_path) as im:
            width, height = im.size
        with open(image_path, "rb") as f:
            data = f.read()
        return data, width, height

    def upload_image(self, image_path: str,
                     scope: str = "bbs") -> Tuple[str, str]:
        """上传本地图片,返回 (最终URL, 缩略图URL)"""
        data, width, height = self._read_image(image_path)
        filename = image_path.rsplit("/", 1)[-1]
        mimetype = "image/png" if filename.endswith(".png") else "image/jpeg"
        return self.upload_bytes(data, filename, mimetype, width, height, scope)

    def upload_bytes(self, data: bytes, filename: str, mimetype: str,
                     width: int, height: int,
                     scope: str = "bbs") -> Tuple[str, str]:
        # ---- Step 1: info/v2 ----
        file_infos = json.dumps([{
            "name": filename, "mimetype": mimetype, "fsize": len(data),
            "width": width, "height": height,
        }], ensure_ascii=False)
        resp = self.client.post_form(
            "/bbs/app/api/qcloud/cos/upload/info/v2",
            {"file_infos": file_infos, "scope": scope, "need_cache": "0"})
        if resp.get("status") != "ok":
            raise RuntimeError(f"upload info 失败: {resp}")
        result = resp["result"]
        key = result["keys"][0]
        bucket = result["bucket"]
        region = result["region"]
        host = result.get("host", "imgheybox.max-c.com")

        # ---- Step 2: token/v2 ----
        token_body = {
            "bucket": bucket,
            "keys": json.dumps([key]),
            "mimetypes": json.dumps([mimetype]),
            "is_multipart_upload": "0",
        }
        resp = self.client.post_form(
            "/bbs/app/api/qcloud/cos/upload/token/v2", token_body)
        if resp.get("status") != "ok":
            raise RuntimeError(f"upload token 失败: {resp}")
        creds = resp["result"]["credentials"]
        tmp_secret_id = creds["tmpSecretId"]
        tmp_secret_key = creds["tmpSecretKey"]
        session_token = creds["sessionToken"]

        # ---- Step 3: PUT 到 COS ----
        cos_host = f"{bucket}.cos.{region}.myqcloud.com"
        now = int(time.time())
        key_time = f"{now};{now + 180}"
        auth = cos_sign(tmp_secret_id, tmp_secret_key, "PUT", key, cos_host,
                        len(data), key_time)
        put_url = f"https://{cos_host}{urllib.parse.quote(key)}"
        put_resp = requests.put(
            put_url, data=data,
            headers={
                "Content-Type": mimetype,
                "Authorization": auth,
                "x-cos-security-token": session_token,
                "User-Agent": self.client.session.headers["User-Agent"],
            }, timeout=self.timeout)
        if put_resp.status_code not in (200, 204):
            raise RuntimeError(
                f"COS PUT 失败: HTTP {put_resp.status_code} "
                f"{put_resp.text[:200]}")

        # ---- Step 4: callback/v2 ----
        resp = self.client.post_form(
            "/bbs/app/api/qcloud/cos/upload/callback/v2",
            {"keys": json.dumps([key])},
            {"is_finished": "true"})
        if resp.get("status") != "ok":
            raise RuntimeError(f"upload callback 失败: {resp}")
        cb = resp["result"]
        url = cb["preview_urls"][0]
        thumb = cb["thumbs"][0] if cb.get("thumbs") else url
        return url, thumb


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/z/my-project/github-hot-heybox")
    import json as _json
    with open("/home/z/my-project/github-hot-heybox/config.json") as f:
        cfg = _json.load(f)
    client = HeyboxClient(cfg["cookie"])
    uploader = HeyboxUploader(client)
    url, thumb = uploader.upload_image(
        "/home/z/my-project/github-hot-heybox/output/test_upload.png")
    print("上传成功!")
    print("URL:", url)
    print("THUMB:", thumb)
