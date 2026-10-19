# 小黑盒 Web API 逆向研究成果

## 已验证的完整技术栈

### 1. hkey 签名算法（Python 版已交叉验证通过）
- 源自 WenfuRainbow/heybox (VSCode插件) + plasta2steel/dsh-xiaoheihe
- ALPHABET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
- generateSignature(path) → {hkey, _time, nonce}
- hkey = ov(path, timestamp+1, nonce)

### 2. 公共参数（每个请求必带）
```
os_type=web, app=heybox, client_type=web, version=999.0.4,
web_version=3.0, x_client_type=web, x_app=heybox_website,
heybox_id=74691929, x_os_type=Mac(或Windows), device_info=Chrome,
device_id=<32hex>, hkey, _time, nonce
```
注: 实际浏览器用 web_version=3.0, x_os_type=Mac

### 3. 请求头
```
User-Agent: Chrome UA
Referer: https://www.xiaoheihe.cn/
Origin: https://www.xiaoheihe.cn
Cookie: <用户cookie>
Content-Type: application/x-www-form-urlencoded (POST)
```

### 4. 关键端点（全部实测 200）

#### 只读
- GET /bbs/app/user/message — 消息(验证登录态)
- GET /bbs/app/topic/categories — 板块分类
- GET /bbs/app/api/topic/index?is_post=1 — 发帖可用话题列表

#### 图片上传（腾讯云COS直传,4步）
1. POST /bbs/app/api/qcloud/cos/upload/info/v2
   - body: file_infos=[{"name":"x.png","mimetype":"image/png","fsize":N,"width":W,"height":H}]&scope=bbs&need_cache=0
   - resp: {keys:["/web/bbs/2026/.../xxx.png"], region:"ap-shanghai", bucket:"imgheybox-1251007209", host:"imgheybox.max-c.com"}
2. POST /bbs/app/api/qcloud/cos/upload/token/v2
   - body: bucket=<bucket>&keys=["<key>"]&mimetypes=["image/png"]&is_multipart_upload=0
   - resp: {credentials:{tmpSecretId,tmpSecretKey,sessionToken}, startTime, expiredTime}
3. PUT https://<bucket>.cos.<region>.myqcloud.com<key>
   - headers: Content-Type: image/png, x-cos-security-token: <sessionToken>,
     Authorization: q-sign-algorithm=sha1&q-ak=<tmpSecretId>&q-sign-time=<s>;<e>&q-key-time=<s>;<e>&q-header-list=content-length;host&q-url-param-list=&q-signature=<hex>
   - body: 图片二进制
4. POST /bbs/app/api/qcloud/cos/upload/callback/v2?is_finished=true
   - body: keys=["<key>"]
   - resp: {preview_urls:["https://imgheybox.max-c.com/web/bbs/...png"], thumbs:[...]}

#### COS 签名算法（标准腾讯云COS XML API）
- KeyTime = "startTs;endTs" (180秒窗口)
- SignKey = hex(hmac_sha1(tmpSecretKey, KeyTime))
- HttpString = "put\n<path>\n\ncontent-length=<len>&host=<host>\n"
- StringToSign = "sha1\n<KeyTime>\n<hex(sha1(HttpString))>\n"
- Signature = hex(hmac_sha1(SignKey, StringToSign))

#### 发帖
- POST /bbs/app/api/link/post
- form body:
  ```
  link_tag=27              (27=图文, 11=文章, 28=动态, 1=视频贴)
  text=<JSON数组: [{"type":"text","text":"..."},{"type":"img","url":"...","width":W,"height":H}]>
  title=<标题>
  desc=<摘要>
  words_count=<字数>
  topic_ids=7214           (盒友杂谈)
  hashtags=[]
  schedule_ts=0
  view_limit=1             (1=所有人, 2=仅粉丝, 3=仅自己)
  post_type=1              (1=无)
  original=1               (1=原创, 0=转载)
  declaration=0            (0=未选择)
  ```
- resp: {link_id: "..."} → 帖子链接 https://www.xiaoheihe.cn/community/7214/list/<link_id>

### 5. 盒友杂谈 topic_id = 7214

### 6. 文章模式发帖(行内插图, 2026-10-19 逆向+实测)

**背景**: 图文模式(27)的 text 里即使把 img 块插在文字块中间, 服务器也会
把图片归一化到所有文字块之后(用编辑API提交交错块后 edit/info 回读验证),
渲染时图片全部堆在文末 — 无法真正图文交错。

**文章模式(11)编码**(从网页版"发布文章"编辑器拦截请求获得):

```
POST /bbs/app/api/link/post
link_tag=11
text=[
  {"type":"html","text":"<p>段1</p><p><img data-type=\"image\"
    data-original=\"https://imgheybox.max-c.com/...png\"
    data-width=\"1344\" data-height=\"768\"
    data-description=\"\"></p><p>段2</p>"},
  {"type":"img","url":"...同上...","width":"1344","height":"768"}
]
topic_ids=7214            ← 文章模式必须传分区, 否则 resp: {status:failed, msg:"请添加分区"}
```

实测结论:
- html 块里的 `<img>` 行内位置**服务器保留**(edit/info 回读一致),
  网页端渲染显示在两段文字中间(link_id=194382399 测试帖验证)
- 末尾 `{"type":"img"}` 引用块(每张行内图附一个, width/height 为字符串)
  供封面/图库; 编辑器行为如此, 建议保持一致
- 发帖响应**直接含 link_id**(图文模式 result 为空需要创作者中心兑底)
- 文章无审核延迟, 立即可见
- 网页版"发布文章"编辑器 = ProseMirror 富文本, 工具栏含 行内插图/视频/
  投票/链接/游戏卡片/标题/加粗/引用

**HTML 细节**(heybox_publisher._blocks_from_segments_article):
- 文本 HTML 转义(& < >), 空行分段(`\n\n` → `</p><p>`), 段内 `\n` → `<br>`

### 7. 删帖 API(2026-10-19 逆向)

```
POST /bbs/app/link/delete      (注意: 无 /api/ 段)
form: link_id=<帖子ID>
resp: {status:"ok"}
```
(创作者中心内容管理页的删除按钮触发; 已封装 HeyboxPublisher.delete_post)

### 8. 其他补充(2026-10-19)

- 网页版图文帖(/app/bbs/link/xxx)只渲染**第一个文字块**+封面图+图库,
  多文字块要看 APP — 这也解释了"编辑器只加载第一个文本块"
- `GET /bbs/app/link/tree?link_id=` 是网页版帖子详情数据源,
  直连易触发 show_captcha(人机验证), 频繁访问会连坐浏览器会话
- edit/info 返回的图片 url 是缩略图 URL(/thumb.png?imageMogr2/...),
  复用图片要拿原图(本地或去掉 query)
