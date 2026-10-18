# GitHub 热榜 → 小黑盒自动发帖

自动抓取 GitHub Trending 热门项目,用大模型生成面向盒友的中文解读,
渲染榜单长图,自动发布到小黑盒「盒友杂谈」板块。
支持两种帖子形态: **榜单帖**(每周一自动发周榜 Top10)和**单项目安利帖**
(手动指定项目深挖,自带海报图)。

## 功能特性

- **榜单帖 + 单项目帖**: 每周一 10:00 自动发周榜 Top10;另可用
  `project_post.py` 对任意项目发单篇深挖帖(开头钩子/正文细节/亮点海报/
  风险提示),支持批量连发与防重发
- **双数据源**: github.com/trending 页面解析(主) + GitHub Search API(自动降级)
- **老项目过滤**: 黑名单(claude-code/ECC 等早就火过的永不入选) +
  旧闻判定(总星高但本期增速占比低 = 火过劲了) + 新项目优先排序 +
  Search API 补位,保证榜单以新面孔为主;近 2 天发过的仓库自动降权
- **档期可配**: 每日日榜 / 每周一 10:00 周榜由 `config.json` 的
  `post.daily_enabled` / `post.weekly_enabled` 开关控制(默认只开周榜,
  避免日榜内容重复)
- **README 素材抓取**: api.github.com `/repos/{x}/readme`(主) +
  jsdelivr CDN(免配额兑底) + raw.githubusercontent.com(备),
  给 LLM 提供真实细节,不靠套话凑字数
- **五段式去AI味文案**: 每项目 260~360 字中文解读,含安装命令、
  使用示例、具体场景。管线: 规则内嵌生成(素材事实锁定) →
  humanizer 终审 → 确定性句式检测+定向去重 → 超限句删除 →
  热度套词替换,方法论参考 blader/humanizer、op7418/Humanizer-zh、
  MrGeDiao/shuorenhua 等开源项目
- **图文版式**: 正文纯文字流(导语 → 每个项目[编号头+数据行+分段正文+
  链接] → 结尾互动 → 话题标签) + 末尾一张完整榜单总览长图;
  正文块内用空行分段(小黑盒正文容器为 break-spaces,
  块内 `\n\n` 会渲染为空行,已在真实帖验证)
- **自动发帖**: 腾讯云 COS 直传图片 + `/bbs/app/api/link/post` 发帖,自动签名(hkey)
- **防重发**: state.json 记录发布历史,当日/当周已发自动跳过
- **安全模式**: config.json `dry_run: true` 时只构造 payload 不真发

## 快速开始

### 环境要求

- Python 3.10+(依赖 requests / Pillow / playwright)
- z-ai CLI(本环境内置;自部署时可换成任意 LLM 接口,见下文)
- Chromium(Playwright 截长图用): `playwright install chromium`

### 安装

```bash
pip install requests pillow playwright
playwright install chromium
```

### 配置

编辑 `config.json`:

```json
{
  "cookie": "你的小黑盒cookie(必须包含 pkey / x_xhh_tokenid / heybox_id)",
  "top_n": 10,
  "topic_ids": "7214",
  "post": {
    "daily_enabled": false,
    "weekly_enabled": true,
    "append_long_image": false
  },
  "dry_run": true
}
```

**cookie 获取方法**: 浏览器打开 xiaoheihe.cn 并登录 → F12 → Network →
任意请求的 Request Headers 里复制完整 Cookie 值。

**常用板块 topic_id**: 盒友杂谈=7214, 数码硬件=18745, 百科知识=549998

### 运行

```bash
# 周榜(干跑,不真发)
python main.py --period weekly --dry-run

# 周榜(真实发布)
python main.py --period weekly --force

# 纯文字帖(不生成图片)
python main.py --period weekly --no-image

# 单项目安利帖(干跑 → 真发)
python project_post.py --repo NanmiCoder/MediaCrawler --dry-run
python project_post.py --repo NanmiCoder/MediaCrawler

# 查看单项目帖清单/已发状态
python project_post.py --list

# 清单内未发项目逐个连发(间隔90秒防风控)
python project_post.py --all --interval 90
```

帖子版式: 导语 → 每个项目[1️⃣编号头+⭐数据行+
分段正文+🔗链接] → 结尾互动 → 话题标签 → 末尾附一张完整总览长图。
单项目帖: 开头钩子 → 正文深挖(2~3段) → 适合谁+风险 → 链接 →
话题标签 → 末尾附单项目海报图。

命令行参数:

| 参数 | 说明 |
|------|------|
| `--period daily/weekly` | 日榜或周榜 |
| `--dry-run` | 只构造 payload,不真发 |
| `--force` | 忽略防重复发标记 |
| `--no-image` | 不生成长图 |
| `--keep-image` | 保留历史图片(默认只留最近5张) |

project_post.py 参数: `--repo`(指定项目) / `--all`(清单内未发的逐个发) /
`--list`(看状态) / `--interval`(连发间隔秒数,默认90) / `--force` / `--dry-run`

### 定时运行

**容器/无 cron 环境(本仓库自带调度器)**:

```bash
bash run_scheduler.sh   # 常驻进程 + 看门狗自动重启,日志 scheduler.log
```

时间表由 config 开关控制(默认只开周榜): 每周一 10:00 周榜(北京时间),
`daily_enabled: true` 可加开每日 09:00 日榜。
state.json 防重发保证同一期不会发两次;启动时自动对账小黑盒创作者中心,
漏发档期立即补发,失败每30分钟重试(最多3次)。

**crontab(自部署服务器)**:

```cron
# 每周一 10:00 发周榜
0 10 * * 1 cd /path/to/github-hot-heybox && python main.py --period weekly >> cron.log 2>&1
# 如需日榜,再加: 每天 09:00
# 0 9 * * * cd /path/to/github-hot-heybox && python main.py --period daily >> cron.log 2>&1
```

**青龙面板**: 添加定时任务 `task main.py --period daily`(环境变量方式可自行改造)。

## 文件结构

```
github-hot-heybox/
├── main.py              # 主流程(入口)
├── config.json          # 配置(cookie/板块/数量,不入库,见 config.example.json)
├── config.example.json  # 配置模板
├── state.json           # 发布历史(自动生成,不入库)
├── scheduler.py         # 常驻定时调度器(v3: 档期开关+启动补发+失败重试)
├── run_scheduler.sh     # 调度器启动脚本(带看门狗)
├── project_post.py      # 单项目安利帖(meta双路+README三路+生成/终审+海报)
├── heybox_client.py     # 小黑盒API客户端(hkey签名)
├── heybox_uploader.py   # 图片上传(腾讯云COS直传)
├── heybox_publisher.py  # 发帖器(segments 图文交错模式 + link_id 兑底获取)
├── github_trending.py   # GitHub热榜抓取(双数据源+老项目过滤+README素材)
├── summarizer.py        # LLM中文简介(五段式去AI味管线 + tagline 标语)
├── image_maker.py       # 图片渲染(封面+项目卡片切片+长图备份)
├── API_RESEARCH.md      # 小黑盒API逆向研究文档
├── examples/            # 示例产物(长图+文案)
└── output/              # 长图与文案产物(不入库)
```

## 技术原理(摘要)

完整逆向分析见 `API_RESEARCH.md`。

1. **hkey 签名**: 移植自开源的 VSCode 小黑盒插件签名实现(GF(2^8) 变换 + MD5),
   Python/JS 交叉验证字节级一致
2. **图片上传四步**: `upload/info/v2` 取 key → `upload/token/v2` 取临时凭证 →
   PUT 腾讯云 COS(标准 COS 签名) → `upload/callback/v2` 换最终 URL
3. **发帖**: `POST /bbs/app/api/link/post`,form 表单,
   `link_tag=27`(图文) + `text`(JSON富文本块) + `topic_ids`(板块)。
   实测响应 status=ok 但不含 link_id,发帖后从创作者中心接口
   `/bbs/app/author/concept/article/list` 兑底取最新帖子 ID
4. **LLM 文案(五段式)**: 通过 z-ai CLI 调用。
   - Stage 1 生成:写作规则内嵌(禁假对比/金句收尾/意义拔高/四字排比/
     黑话/热度套词,句式长短交替,收尾方式轮换),每项目 260~360 字,
     含安装命令/使用示例/具体场景,只用 README/desc 里的真实素材
   - Stage 2 终审:逐句清理残留 AI 痕迹,事实(项目名/数字/功能点)不增不减,
     长度红线(不低于原长 95%)
   - Stage 3 去重(按需,最多两轮):代码统计高频句式("可以试试"/"适合"/
     "尤其"等超过 1/3 条目出现即触发),LLM 定向改写——防"禁了A句式
     换B句式"的打地鼠问题
   - Stage 4 确定性后处理:超限年龄句/受众收尾句/无谓语残句直接删除,
     代码保证结果
   - Stage 5 热度套词替换:"登顶/霸榜/引爆"等固定替换为平实说法,
     零残留
   - Stage 6 tagline 兑底:每项目一句 10~16 字卡片标语,
     LLM 没给或被终审丢弃的从最终 summary 确定性提取
   - 任何阶段失败自动降级到上一阶段结果,最终兜底为模板拼接
   - 自部署时可在 `summarizer.py` 的 `_call_llm()` 替换为 OpenAI 兼容接口

## 注意事项

- Cookie 属于敏感凭证,切勿提交到公开仓库
- Cookie 失效后脚本会提示,重新抓取更新 config.json 即可
- 发帖频率请保持克制(榜单帖每周1条,单项目帖建议每天不超过5条且间隔90秒+),避免触发平台风控
- 本项目仅供学习交流,请遵守小黑盒平台规则

## 常见问题

**Q: 提示 Cookie 已失效?**
A: 小黑盒 cookie 有效期较长,但会过期。重新登录网页版抓取最新 cookie 更新配置。

**Q: GitHub trending 抓取超时?**
A: 脚本会自动降级到 GitHub Search API(近似热榜:近期新建+星标最多)。
国内服务器建议配置代理或确保网络可达 github.com。

**Q: 想换发帖板块?**
A: 修改 config.json 的 `topic_ids`。常用: 盒友杂谈=7214, 数码硬件=18745。

**Q: LLM 生成失败?**
A: 自动降级为模板拼接简介,不影响发帖。
