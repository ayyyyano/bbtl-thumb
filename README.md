# BBTL-THUMB

## 关于项目

**B 站关注者动态点赞助手 (Bilibili-Timeline-Thumb)** —— 为你在 B 站时间线上所关注创作者的动态提供点赞服务

> 本工具**不得用于**为 Bilibili 公开内容刷量，不得跨账号批量操作或借工具特性进行引流行为。
> 本工具只在你自己的账号登录态下，对时间线中关注者发布的动态进行点赞，表达对你所认可创作者的日常支持和反馈。
> 严禁在 Bilibili 以任何形式提及本项目。项目如有侵权之处，请立即联系我删除。

---

### 灵感来源
油猴脚本：[bilibili 动态自动点赞](https://greasyfork.org/zh-CN/scripts/458535-bilibili-%E5%8A%A8%E6%80%81%E8%87%AA%E5%8A%A8%E7%82%B9%E8%B5%9E)

### 许可协议
本项目遵循 MIT license 开源协议，详细查看 [LICENSE](LICENSE) 文件。

### 风险警告与免责声明

**使用本工具可能存在以下风险，请务必知悉：**

- **账号安全风险：** 自动化操作可能违反 Bilibili 用户协议，存在账号被限制、封禁的可能。使用本工具即表示你自愿承担一切后果。
- **Cookie 泄露风险：** `cookies.txt` 包含你的登录凭证，请妥善保管，切勿分享给他人或上传至公开仓库。
- **仅供学习交流：** 本项目仅用于学习 Python 网络编程、API 调用等技术目的，请于下载后 **24 小时内删除**。
- **作者不承担任何责任：** 作者不对因使用本工具导致的账号异常、数据丢失或其他任何损失负责。

## 工作原理

```
┌──────────┐    Cookie     ┌──────────────┐    GET /feed/all    ┌───────────────┐
│ cookies  │─────────────▶│  main.py     │───────────────────▶│  B 站动态 API  │
│  .txt    │               │  (requests)  │◀───────────────────│               │
└──────────┘               └──────┬───────┘   动态列表 JSON      └───────────────┘
                                  │
                                  │ 筛选未点赞的帖子
                                  │
                                  ▼
                          ┌──────────────┐    POST /thumb      ┌───────────────┐
                          │  thumb_like  │───────────────────▶│  B 站点赞 API  │
                          │  (csrf+bili  │◀───────────────────│               │
                          │   _jct)      │   点赞结果 JSON      └───────────────┘
                          └──────────────┘
```

1. **获取登录态** — 优先读取 `cookies.txt`；无效时支持扫码登录或手动填写
2. **加载去重记录** — 从 `liked.json` 中恢复已点赞 ID，避免重复请求
3. **加载过滤规则** — 从 `filter.txt` 读取白名单/黑名单
4. **拉取时间线** — 调用 B 站动态 Feed API，获取你关注者的最新动态（失败自动重试）
5. **筛选 & 过滤** — 跳过已点赞、命中黑名单或不在白名单内的动态
6. **发送点赞** — 对符合条件的关注者动态逐条点赞（失败指数退避重试）
7. **随机等待** — 每轮处理完成后等待 15–60 秒（随机），模拟自然行为
8. **优雅退出** — `Ctrl+C` 时安全退出并保存去重状态

核心 API：

| API | 方法 | 说明 |
|---|---|---|
| `passport.bilibili.com/x/passport-login/web/qrcode/generate` | GET | 申请登录二维码 |
| `passport.bilibili.com/x/passport-login/web/qrcode/poll` | GET | 轮询扫码登录状态 |
| `api.bilibili.com/x/polymer/web-dynamic/v1/feed/all` | GET | 获取时间线动态列表 |
| `api.vc.bilibili.com/dynamic_like/v1/dynamic_like/thumb` | POST | 对指定动态点赞 |

## 目录结构

```
bbtl-thumb/
├── main.py           # 主脚本
├── .gitignore        # Git 忽略规则
├── cookies.txt       # Cookie 配置（模板，启动时自动创建）
├── filter.txt        # 过滤规则（模板，启动时自动创建）
├── liked.json        # 去重记录（运行中自动生成）
├── requirements.txt  # Python 依赖
├── bbtl-thumb.log    # 运行日志（自动轮转）
└── README.md
```

## 使用方式

### 1. 克隆仓库

```bash
git clone https://github.com/ayyyyano/bbtl-thumb.git
cd bbtl-thumb
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 获取登录态（Cookie）

首次运行时，若 `cookies.txt` 不存在或缺少 `SESSDATA` / `bili_jct`，脚本会提示选择登录方式：

```text
1) 扫码登录（推荐）
2) 手动填写 Cookie
```

#### 方式 A — 扫码登录（推荐）

1. 运行 `python main.py`
2. 选择 `1`
3. 使用手机 B 站 App 扫描终端中的二维码并确认
4. 登录成功后，Cookie 自动写入 `cookies.txt`

> 若终端二维码显示异常，脚本会同时输出可在浏览器打开的登录链接。
> Cookie 失效时，删除 `cookies.txt` 后重新运行即可再次扫码。

#### 方式 B — 手动填写 / 导入 Cookie

仍支持手动准备 Cookie，格式如下：

- **Netscape HTTP Cookie File**
- **JSON 数组**（Cookie 编辑器插件导出）
- **Key=Value**

示例：

```
bili_jct=xxxxxxxxxxxxxxxx
SESSDATA=xxxxxxxxxxxxxxxx
DedeUserID=xxxxxxxxxxxxxxxx
DedeUserID__ckMd5=xxxxxxxxxxxxxxxx
```

> 至少需要 `bili_jct` 和 `SESSDATA`，否则无法点赞。

### 3. 运行

```bash
python main.py
```

脚本将持续运行，日志示例：

```
2026-07-31 12:00:00 [INFO] 正在申请登录二维码...
2026-07-31 12:00:20 [INFO] 扫码登录成功，Cookie 已写入 cookies.txt
2026-07-31 12:00:20 [INFO] === bilibili-timeline-thumb | B 站关注者动态点赞助手 ===
2026-07-31 12:00:20 [INFO] 已加载 8 个 Cookie
2026-07-31 12:00:20 [INFO] bili_jct: abcd***
2026-07-31 12:00:21 [INFO] ==== 第 1 轮开始 (间隔 38.2 秒) ====
2026-07-31 12:00:21 [INFO] 获取第 1 页动态...
2026-07-31 12:00:22 [INFO] 点赞: 某UP主 (id=123456789)
2026-07-31 12:00:22 [INFO]   ✓ 成功
2026-07-31 12:00:25 [INFO] 本轮点赞 3 条，累计 3 条，等待 38.2 秒...
```

### 4. 过滤规则（可选）

如果你只希望给部分关注者点赞，或想跳过某些动态发布者，可在项目目录下创建 `filter.txt`：

```ini
# 白名单：仅给这些 UP 主点赞（白名单存在时，名单外的不会点赞）
+哔哩哔哩弹幕网
+123456789

# 黑名单：跳过这些 UP 主（优先级最高）
-某营销号
-987654321
```

> 文件中可同时存在白名单和黑名单。黑名单优先：即使在白名单中，命中黑名单也会跳过。
> 匹配条件：昵称精确匹配，或 UID 匹配。文件不存在或为空则不过滤。

### 5. 去重与状态保存

- 点赞成功后，`dynamic_id` 自动写入 `liked.json`
- 每轮循环前会检查本地记录，避免对同一动态重复发请求
- `Ctrl+C` 退出时自动保存，关闭终端不会丢失进度
- 超出 `MAX_LIKED_HISTORY`（默认 5000 条）时自动截断，防止文件无限膨胀

### 6. 网络重试

所有 API 请求在失败时会自动重试，使用指数退避策略：

| 重试次数 | 等待时间 |
|---|---|
| 第 1 次 | 立即重试 |
| 第 2 次 | 等待 2 秒 |
| 第 3 次 | 等待 4 秒 |

> 通过 `MAX_RETRIES` 可调整最大重试次数（默认 3）。

### 7. 可调参数

在 `main.py` 顶部可修改以下配置：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `REQUEST_INTERVAL` | 1.5 秒 | 每次点赞间隔，避免触发风控 |
| `LOOP_INTERVAL_MIN` | 15 秒 | 每轮等待随机下限 |
| `LOOP_INTERVAL_MAX` | 60 秒 | 每轮等待随机上限（可自行调大） |
| `MAX_PAGES` | 10 | 每轮最多翻页数 |
| `MAX_RETRIES` | 3 | 请求失败最大重试次数 |
| `MAX_LIKED_HISTORY` | 5000 | liked.json 保留最近 N 条记录 |
| `QR_POLL_INTERVAL` | 2 秒 | 扫码登录状态轮询间隔 |
| `QR_TIMEOUT` | 180 秒 | 扫码登录超时时间 |
| `LOG_FILE` | `bbtl-thumb.log` | 日志文件路径 |
| `LOG_MAX_SIZE` | 1 MB | 日志文件大小上限，超出自动轮转 |
