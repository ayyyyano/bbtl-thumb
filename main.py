#!/usr/bin/env python3
"""
bilibili-timeline-thumb — B 站关注者动态点赞助手
==================================================
本工具将为你的 B 站时间线上已关注创作者的动态提供点赞服务。
你的点赞是对关注者创作的支持和反馈——不是面向公开/陌生内容的引流手段。

脱离浏览器运行，依靠 Cookie 登录态完成操作。
仅在你自己的账号登录态下，对你时间线中关注者发布的动态执行点赞。

不得进行以下操作：
  - 给未关注的公开视频/动态刷量
  - 跨账号批量操作
  - 任何形式的引流、刷数据

特性：
  - 扫码登录：终端展示二维码，手机 B 站扫码后自动写入 cookies.txt
  - 去重持久化：已点赞 ID 存入 liked.json，跨轮次避免重复请求
  - 过滤规则：filter.txt 支持 +白名单 / -黑名单（按 UP 主昵称或 UID）
  - 优雅退出：Ctrl+C 安全退出并保存状态
  - 请求重试：网络异常自动重试（指数退避）
  - 日志轮转：bbtl-thumb.log 达上限自动归档
"""

import json
import random
import signal
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import qrcode
import requests

# ===================== 用户可调配置 =====================

COOKIE_FILE = Path(__file__).with_name("cookies.txt")
FILTER_FILE = Path(__file__).with_name("filter.txt")
LIKED_FILE = Path(__file__).with_name("liked.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/109.0.0.0 Safari/537.36"
)

REQUEST_INTERVAL = 1.5         # 每次点赞间隔（秒）
LOOP_INTERVAL_MIN = 15         # 每轮等待最小秒数（随机下限）
LOOP_INTERVAL_MAX = 60         # 每轮等待最大秒数（随机上限）
MAX_PAGES = 10                 # 每轮最多翻页数
MAX_RETRIES = 3                # 请求失败最大重试次数
MAX_LIKED_HISTORY = 5000       # liked.json 保留最近 N 条记录
LOG_FILE = Path(__file__).with_name("bbtl-thumb.log")
LOG_MAX_SIZE = 1 * 1024 * 1024 # 日志文件大小上限（字节），默认 1 MB

# 首次交互时要求填写的必要 Cookie 键
REQUIRED_COOKIE_KEYS = ("bili_jct", "SESSDATA", "DedeUserID", "DedeUserID__ckMd5")
QR_POLL_INTERVAL = 2           # 扫码登录轮询间隔（秒）
QR_TIMEOUT = 180               # 扫码登录超时（秒）

# 日志
logger = logging.getLogger("bbtl-thumb")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
_console = logging.StreamHandler(); _console.setFormatter(_fmt); logger.addHandler(_console)
_file = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_SIZE, backupCount=1, encoding="utf-8")
_file.setFormatter(_fmt); logger.addHandler(_file)

# 全局退出标志（由信号处理函数设置）
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("收到退出信号 (signal=%d)，完成当前操作后安全退出...", signum)
    _shutdown = True

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ===================== Cookie 加载 =====================

def load_cookies(filepath: Path) -> dict[str, str]:
    """从文件加载 Cookie，支持 Netscape / JSON / Key=Value 三种格式。"""
    text = filepath.read_text("utf-8").strip()

    if text.startswith("# Netscape HTTP Cookie File") or text.startswith("# HTTP Cookie File"):
        return _parse_netscape(text)

    if text.startswith("[") and text.endswith("]"):
        try:
            return {it["name"]: it["value"] for it in json.loads(text) if it.get("name")}
        except (json.JSONDecodeError, KeyError):
            pass

    return _parse_key_value(text)


def _parse_netscape(raw: str) -> dict[str, str]:
    cookies = {}
    for line in raw.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    return cookies


def _parse_key_value(raw: str) -> dict[str, str]:
    cookies = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("=", ":", "\t"):
            if sep in line:
                k, v = line.split(sep, 1)
                cookies[k.strip()] = v.strip()
                break
    return cookies


# ===================== Cookie 保存 =====================

def save_cookies(filepath: Path, cookies: dict[str, str]):
    """将 Cookie 以 Key=Value 格式写入文件。"""
    lines = [f"{k}={v}" for k, v in cookies.items() if k and v is not None]
    filepath.write_text("\n".join(lines) + "\n", "utf-8")


def cookies_ready(filepath: Path) -> bool:
    """检查 cookies.txt 是否包含可用登录态。"""
    if not filepath.exists():
        return False
    cookies = load_cookies(filepath)
    return bool(cookies.get("SESSDATA") and cookies.get("bili_jct"))


# ===================== 扫码登录 =====================

def qr_login(filepath: Path) -> dict[str, str]:
    """
    通过 B 站官网扫码登录流程获取 Cookie 并写入文件。
    流程：generate 二维码 → 终端展示 → poll 扫码状态 → 提取 Cookie。
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bilibili.com/",
    })

    logger.info("正在申请登录二维码...")
    resp = session.get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"申请二维码失败: {payload.get('message')}")

    data = payload.get("data") or {}
    qrcode_key = data.get("qrcode_key")
    login_url = data.get("url")
    if not qrcode_key or not login_url:
        raise RuntimeError("二维码响应缺少 qrcode_key 或 url")

    print(f"\n{'='*50}")
    print("  请使用 B 站 App 扫描下方二维码登录")
    print("  （扫码后在手机上确认）")
    print(f"{'='*50}\n")
    qr = qrcode.QRCode(border=1)
    qr.add_data(login_url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print(f"\n若终端二维码无法识别，可在浏览器打开：\n{login_url}\n")

    deadline = time.time() + QR_TIMEOUT
    last_msg = ""
    while time.time() < deadline and not _shutdown:
        poll = session.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": qrcode_key},
            timeout=15,
        )
        poll.raise_for_status()
        result = poll.json()
        if result.get("code") != 0:
            raise RuntimeError(f"轮询登录状态失败: {result.get('message')}")

        info = result.get("data") or {}
        status = info.get("code")
        message = info.get("message") or ""

        if status == 0:
            cookies = _extract_login_cookies(session, info.get("url") or "")
            missing = [k for k in ("SESSDATA", "bili_jct") if not cookies.get(k)]
            if missing:
                raise RuntimeError(f"登录成功但缺少关键 Cookie: {', '.join(missing)}")
            save_cookies(filepath, cookies)
            logger.info("扫码登录成功，Cookie 已写入 %s", filepath.name)
            return cookies

        if status == 86038:
            raise RuntimeError("二维码已失效，请重新运行脚本")

        # 86101 未扫码 / 86090 已扫码未确认
        if message and message != last_msg:
            logger.info("等待扫码确认: %s", message)
            last_msg = message
        time.sleep(QR_POLL_INTERVAL)

    if _shutdown:
        raise RuntimeError("登录已取消")
    raise RuntimeError(f"扫码登录超时（{QR_TIMEOUT}s）")


def _extract_login_cookies(session: requests.Session, jump_url: str) -> dict[str, str]:
    """从 Session Cookie 与登录跳转 URL 中提取登录态。"""
    cookies = dict(session.cookies.get_dict())
    if jump_url:
        qs = parse_qs(urlparse(jump_url).query)
        for key in REQUIRED_COOKIE_KEYS:
            vals = qs.get(key)
            if vals and vals[0]:
                cookies[key] = vals[0]
        # 兼容部分返回字段
        for key in ("sid", "Expires"):
            vals = qs.get(key)
            if vals and vals[0]:
                cookies[key] = vals[0]
    return cookies


# ===================== 交互式初始化 =====================

def setup_cookies(filepath: Path) -> dict[str, str]:
    """
    确保存在可用 Cookie。
    优先读取 cookies.txt；无效时提供扫码登录 / 手动填写。
    """
    if cookies_ready(filepath):
        return load_cookies(filepath)

    print(f"\n{'='*50}")
    print("  未检测到有效的 Cookie 配置")
    print("  1) 扫码登录（推荐）")
    print("  2) 手动填写 Cookie")
    print(f"{'='*50}")
    choice = input("请选择 [1/2，默认 1]: ").strip() or "1"

    if choice == "2":
        print("\n请粘贴 B 站 (bilibili.com) 的以下 Cookie 值：\n")
        cookies = {}
        for key in REQUIRED_COOKIE_KEYS:
            val = input(f"  {key} = ").strip()
            if val:
                cookies[key] = val
        if not (cookies.get("SESSDATA") and cookies.get("bili_jct")):
            logger.error("缺少 SESSDATA 或 bili_jct，无法继续")
            sys.exit(1)
        save_cookies(filepath, cookies)
        logger.info("Cookie 已保存到 %s", filepath.name)
        return cookies

    try:
        return qr_login(filepath)
    except Exception as e:
        logger.error("扫码登录失败: %s", e)
        sys.exit(1)


# ===================== 过滤规则 =====================

FILTER_TEMPLATE = """\
# filter.txt — 关注者过滤规则
#
# 如果你只想给部分关注者点赞，或跳过某些关注者，可在此配置。
# 格式：每行一条规则，支持白名单（+）和黑名单（-）
#   +昵称  或  +UID    → 仅给这些关注者点赞（白名单存在时只匹配白名单）
#   -昵称  或  -UID    → 跳过这些关注者（黑名单优先于白名单）
#   # 开头为注释
#
# ===== 示例 =====
# +哔哩哔哩弹幕网      ← 只给这个关注者的动态点赞
# -123456              ← 跳过 UID=123456 的关注者
# +某某工作室           ← 也允许这个
#
# 如果文件为空或不存在，则不对任何关注者过滤（全部点赞）。
"""

COOKIE_TEMPLATE = """\
# B 站 Cookie — 首次运行时选择「扫码登录」即可自动获取。
# 如果想手动写入，请将下面的 # 去掉并替换为真实值：
#
# bili_jct=你的bili_jct值
# SESSDATA=你的SESSDATA值
# DedeUserID=你的DedeUserID值
# DedeUserID__ckMd5=你的值
#
# 也支持 Netscape HTTP Cookie File 或 JSON 数组格式。
"""


def ensure_template_files():
    """启动时自动创建缺失的模板文件（filter.txt / cookies.txt）。"""
    for filepath, template in ((FILTER_FILE, FILTER_TEMPLATE),
                                (COOKIE_FILE, COOKIE_TEMPLATE)):
        if not filepath.exists():
            filepath.write_text(template, "utf-8")
            logger.info("已创建模板文件: %s", filepath.name)


def load_filters(filepath: Path) -> tuple[set, set]:
    """
    加载 filter.txt：+name/+uid 为白名单，-name/-uid 为黑名单。
    返回 (whitelist, blacklist)，每项为 set of (type, value)，type 为 'name' 或 'uid'。
    文件不存在则返回空集合，相当于不过滤。
    """
    whitelist: set[tuple[str, str]] = set()
    blacklist: set[tuple[str, str]] = set()
    if not filepath.exists():
        return whitelist, blacklist
    for line in filepath.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+"):
            val = line[1:].strip()
            if val:
                whitelist.add(("uid" if val.isdigit() else "name", val))
        elif line.startswith("-"):
            val = line[1:].strip()
            if val:
                blacklist.add(("uid" if val.isdigit() else "name", val))
    return whitelist, blacklist


def should_like(author_name: str, author_mid: int,
                whitelist: set, blacklist: set) -> bool:
    """
    判断是否应该给该作者点赞。
    优先黑名单 → 其次白名单（若白名单非空则必须匹配）→ 否则放行。
    """
    mid_str = str(author_mid)
    # 黑名单优先
    for typ, val in blacklist:
        if (typ == "name" and val == author_name) or (typ == "uid" and val == mid_str):
            return False
    # 白名单存在时必须命中
    if whitelist:
        for typ, val in whitelist:
            if (typ == "name" and val == author_name) or (typ == "uid" and val == mid_str):
                return True
        return False
    return True


# ===================== 去重持久化 =====================

def load_liked(filepath: Path) -> set[str]:
    """从 liked.json 加载已点赞的动态 ID 集合。"""
    if not filepath.exists():
        return set()
    try:
        data = json.loads(filepath.read_text("utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, TypeError):
        return set()


def save_liked(filepath: Path, liked_set: set[str]):
    """保存已点赞 ID，超出上限则截断保留最近 N 条。"""
    lst = list(liked_set)
    if len(lst) > MAX_LIKED_HISTORY:
        lst = lst[-MAX_LIKED_HISTORY:]
    filepath.write_text(json.dumps(lst, ensure_ascii=False), "utf-8")


# ===================== 请求重试 =====================

def retry(func, *args, max_retries=MAX_RETRIES, **kwargs):
    """对 requests 请求做指数退避重试。"""
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_retries:
                wait = 2 ** (attempt - 1)
                logger.warning("请求失败，%d 秒后重试 (%d/%d): %s", wait, attempt, max_retries, e)
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ===================== API 客户端 =====================

class BiliClient:
    """基于 requests.Session + Cookie 登录态的 B 站 API 客户端。"""

    def __init__(self, cookies: dict[str, str]):
        self.session = requests.Session()
        self.session.cookies.update(cookies)
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://t.bilibili.com/",
        })
        self.csrf = cookies.get("bili_jct")

    def get_all_dynamic(self, page: int = 1) -> dict:
        """获取时间线动态 feed（GET）。"""
        resp = self.session.get(
            "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all",
            params={"timezone_offset": "-480", "type": "all", "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def thumb_like(self, dynamic_id: str) -> dict:
        """对指定动态点赞（POST，需要 bili_jct）。"""
        resp = self.session.post(
            "https://api.vc.bilibili.com/dynamic_like/v1/dynamic_like/thumb",
            data={"dynamic_id": dynamic_id, "up": "1", "csrf": self.csrf},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


# ===================== 主逻辑 =====================

def process_feed(client: BiliClient, whitelist: set, blacklist: set,
                 liked_set: set) -> int:
    """遍历动态时间线并点赞，返回本轮成功点赞数。"""
    global _shutdown
    liked = 0
    for page in range(1, MAX_PAGES + 1):
        if _shutdown:
            break
        logger.info("获取第 %d 页动态...", page)
        try:
            data = retry(client.get_all_dynamic, page)
        except requests.RequestException as e:
            logger.error("获取动态失败 (page=%d)，已重试 %d 次: %s", page, MAX_RETRIES, e)
            break

        code = data.get("code")
        if code != 0:
            logger.error("API 返回 code=%s: %s", code, data.get("message", ""))
            if code == -101:
                logger.error("Cookie 已失效，请删除 cookies.txt 后重新运行以扫码登录")
            break

        items = (data.get("data") or {}).get("items") or []
        if not items:
            break

        for item in items:
            if _shutdown:
                break
            modules = item.get("modules") or {}
            stat = modules.get("module_stat") or {}
            like_info = stat.get("like") or {}

            dyn_id = item.get("id_str")
            if not dyn_id:
                continue

            # 已点赞（API 侧或本地记录） → 跳过
            if like_info.get("status", True) or dyn_id in liked_set:
                continue

            # 过滤规则
            author = modules.get("module_author") or {}
            author_name = author.get("name") or ""
            author_mid = author.get("mid") or 0
            if not should_like(author_name, author_mid, whitelist, blacklist):
                continue

            logger.info("点赞: %s (id=%s)", author_name or dyn_id, dyn_id)
            try:
                result = retry(client.thumb_like, dyn_id)
                if result.get("code") == 0:
                    liked += 1
                    liked_set.add(dyn_id)
                    save_liked(LIKED_FILE, liked_set)
                    logger.info("  ✓ 成功")
                else:
                    logger.warning("  ✗ 失败: code=%s msg=%s",
                                   result.get("code"), result.get("message"))
            except requests.RequestException as e:
                logger.error("  ✗ 重试 %d 次后仍失败: %s", MAX_RETRIES, e)

            time.sleep(REQUEST_INTERVAL)

    return liked


def main():
    global _shutdown
    ensure_template_files()
    cookies = setup_cookies(COOKIE_FILE)
    logger.info("=== bilibili-timeline-thumb | B 站关注者动态点赞助手 ===")
    logger.info("已加载 %d 个 Cookie", len(cookies))
    if csrf := cookies.get("bili_jct"):
        logger.info("bili_jct: %s***", csrf[:4])
    else:
        logger.warning("未检测到 bili_jct，点赞功能不可用")

    whitelist, blacklist = load_filters(FILTER_FILE)
    if whitelist or blacklist:
        logger.info("过滤规则: 白名单 %d 条, 黑名单 %d 条", len(whitelist), len(blacklist))

    liked_set = load_liked(LIKED_FILE)
    logger.info("已加载 %d 条历史点赞记录", len(liked_set))

    client = BiliClient(cookies)
    total_liked = 0
    rnd = 0
    try:
        while not _shutdown:
            rnd += 1
            delay = random.uniform(LOOP_INTERVAL_MIN, LOOP_INTERVAL_MAX)
            logger.info("==== 第 %d 轮开始 (间隔 %.1f 秒) ====", rnd, delay)
            liked = process_feed(client, whitelist, blacklist, liked_set)
            total_liked += liked
            logger.info("本轮点赞 %d 条，累计 %d 条，等待 %.1f 秒...",
                        liked, total_liked, delay)
            # 可中断的 sleep
            for _ in range(int(delay)):
                if _shutdown:
                    break
                time.sleep(1)
    finally:
        save_liked(LIKED_FILE, liked_set)
        logger.info("已退出，本轮运行共点赞 %d 条，状态已保存", total_liked)


if __name__ == "__main__":
    main()
