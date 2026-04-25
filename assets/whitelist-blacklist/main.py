import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse, quote, unquote, urljoin
import socket
import ssl
import re
from typing import List, Tuple, Set, Dict, Optional
import logging
import sys
import subprocess
import json

# ==============================================
# 路径配置 智普清言
# ==============================================
SCRIPT_ABS_PATH = os.path.abspath(__file__)
SCRIPT_DIR = os.path.dirname(SCRIPT_ABS_PATH)
ASSETS_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)

FILE_PATHS = {
    "my_urls": os.path.join(ASSETS_DIR, "my_urls.txt"),
    "urls": os.path.join(ASSETS_DIR, "urls.txt"),
    "first_source": os.path.join(ASSETS_DIR, "111.txt"),
    "blacklist_auto": os.path.join(SCRIPT_DIR, "blacklist_auto.txt"),
    "whitelist_manual": os.path.join(SCRIPT_DIR, "whitelist_manual.txt"),
    "whitelist_auto": os.path.join(SCRIPT_DIR, "whitelist_auto.txt"),
    "whitelist_respotime": os.path.join(SCRIPT_DIR, "whitelist_respotime.txt"),
    "log": os.path.join(SCRIPT_DIR, "log.txt"),
}

# ==============================================
# 日志配置
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

logger.info("=" * 60)
logger.info(f"项目根目录: {PROJECT_ROOT}")
logger.info(f"脚本目录: {SCRIPT_DIR}")
logger.info(f"assets目录: {ASSETS_DIR}")
logger.info(f"my_urls.txt: {FILE_PATHS['my_urls']} ({'存在' if os.path.exists(FILE_PATHS['my_urls']) else '不存在'})")
logger.info("=" * 60)

# ==============================================
# 全局配置
# ==============================================
class Config:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    TIMEOUT_FETCH = 15
    TIMEOUT_CHECK = 3.0
    TIMEOUT_WHITELIST = 4.5
    MAX_WORKERS = 30

RE_ALL_URLS = re.compile(r"https?://[^\s,\'\"<>}\])]+")

# ==============================================
# 环境与工具（Xvfb + xclip + Chromium）
# ==============================================
def is_running_in_ci() -> bool:
    return bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))

def _setup_xvfb_and_deps() -> bool:
    """
    在 Linux CI 上：
    - 启动 Xvfb（DISPLAY=:99）
    - 安装 xclip（读取剪贴板）
    - 安装 Chromium（/usr/bin/chromium）
    返回：是否就绪（True/False）
    """
    if not sys.platform.startswith("linux"):
        return True

    logger.info("[Linux] 初始化桌面/剪贴板/浏览器...")

    # 1) Xvfb
    try:
        os.environ["DISPLAY"] = ":99"
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-ac", "+extension", "GLX"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        logger.info("✅ Xvfb 已启动 (DISPLAY=:99)")
    except Exception as e:
        logger.warning(f"Xvfb 启动失败: {e}")

    # 2) xclip
    try:
        subprocess.run(["which", "xclip"], check=True, capture_output=True, timeout=5)
    except Exception:
        try:
            subprocess.run(
                ["sudo", "apt-get", "update", "-qq"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            )
            subprocess.run(
                ["sudo", "apt-get", "install", "-y", "-qq", "xclip"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            )
            logger.info("✅ xclip 已安装")
        except Exception as e:
            logger.warning(f"xclip 安装失败: {e}")

    # 3) Chromium（优先已有；若没有则安装）
    chromium_candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
    ]
    installed = any(os.path.isfile(p) for p in chromium_candidates)
    if not installed:
        logger.info("未检测到 Chromium，尝试安装 chromium-browser...")
        try:
            subprocess.run(
                ["sudo", "apt-get", "install", "-y", "-qq", "chromium-browser"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
            )
            logger.info("✅ chromium-browser 已安装")
        except Exception as e:
            logger.warning(f"chromium-browser 安装失败: {e}")

    # 再次确认路径
    for p in chromium_candidates:
        if os.path.isfile(p):
            logger.info(f"检测到浏览器: {p}")
            break
    return True


def get_clipboard_content() -> Optional[str]:
    """跨平台读剪贴板（Linux 依赖 xclip）"""
    try:
        if sys.platform.startswith("linux"):
            r = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        elif sys.platform == "darwin":
            r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return r.stdout.strip()
        elif sys.platform == "win32":
            r = subprocess.run(
                ["powershell", "-command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return r.stdout.strip()
    except Exception as e:
        logger.debug(f"读取剪贴板失败: {e}")
    return None


# ==============================================
# Token：获取 + 验证
# ==============================================
def _ensure_drissionpage() -> bool:
    """确保 DrissionPage 可用；失败返回 False"""
    try:
        import DrissionPage  # noqa: F401
        return True
    except Exception:
        pass
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "DrissionPage", "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
        )
        import DrissionPage  # noqa: F401
        return True
    except Exception as e:
        logger.error(f"DrissionPage 安装失败: {e}")
        return False


def _find_browser_path() -> Optional[str]:
    """按优先级查找 Chrome/Chromium 路径"""
    candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _token_from_clipboard_or_page(page) -> Optional[str]:
    """统一从剪贴板/页面文本/storage 中提取 Token"""
    token = None
    # 1) 剪贴板
    clip = get_clipboard_content()
    if clip:
        m = re.search(r"\b([a-f0-9]{16})\b", clip, re.I)
        if m:
            token = m.group(1)
            logger.info("从系统剪贴板拿到 Token")
            return token
    # 2) 页面文本
    try:
        body = page.ele("tag:body").text
        if body:
            for line in body.splitlines():
                if "token" in line.lower():
                    m = re.search(r"\b([a-f0-9]{16})\b", line, re.I)
                    if m:
                        token = m.group(1)
                        logger.info("从页面文本拿到 Token")
                        return token
    except Exception:
        pass
    # 3) storage
    try:
        storage_text = page.run_js("return JSON.stringify({...localStorage, ...sessionStorage})")
        storage = json.loads(storage_text) if storage_text else {}
        for k, v in storage.items():
            if isinstance(v, str) and "token" in k.lower() and len(v.strip()) == 16:
                if re.match(r"^[a-f0-9]{16}$", v.strip(), re.I):
                    token = v.strip()
                    logger.info("从 storage 拿到 Token")
                    return token
    except Exception:
        pass
    return None


def _try_click_token_button(page) -> bool:
    """在页面上尝试点击‘获取Token’（含滚动重试）"""
    selectors = ["text=获取Token", "text=获取 Token", "text=Token"]
    for sel in selectors:
        try:
            el = page.ele(sel, timeout=5)
            if el:
                el.click()
                return True
        except Exception:
            continue
    # 滚动后重试
    try:
        page.run_js("window.scrollTo(0, document.body.scrollHeight);")
        page.wait(2)
        elems = page.eles("text:Token")
        for e in elems:
            txt = (e.text or "").strip()
            if txt and ("获取Token" in txt or "获取 Token" in txt):
                e.click()
                return True
    except Exception:
        pass
    return False


def get_taoiptv_token_by_drissionpage() -> Optional[str]:
    if not _ensure_drissionpage():
        return None
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
    except Exception as e:
        logger.warning(f"DrissionPage 导入失败: {e}")
        return None

    logger.info("[DrissionPage] 尝试获取 Token...")
    browser_path = _find_browser_path()
    if not browser_path:
        logger.warning("[DrissionPage] 未找到 Chrome/Chromium，跳过")
        return None

    try:
        co = ChromiumOptions()
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-gpu")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_user_agent(Config.USER_AGENT)
        co.set_browser_path(browser_path)  # 关键：显式指定

        page = ChromiumPage(co)
        logger.info("[DrissionPage] 打开 taoiptv.com...")
        page.get("https://taoiptv.com")
        logger.info("[DrissionPage] 等待防人机验证（~10s）...")
        page.wait(10)  # 给 CF 足够时间

        clicked = _try_click_token_button(page)
        if not clicked:
            logger.warning("[DrissionPage] 未找到‘获取Token’按钮（可能仍在验证）")
            # 不再反复重试，直接退出
            try:
                page.quit()
            except Exception:
                pass
            return None

        page.wait(2)
        token = _token_from_clipboard_or_page(page)

        # 调试截图
        try:
            debug_dir = os.path.join(SCRIPT_DIR, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            page.get_screenshot(path=os.path.join(debug_dir, "drissionpage.png"), full_page=True)
        except Exception:
            pass

        page.quit()

        if token and len(token) == 16 and re.match(r"^[a-f0-9]{16}$", token, re.I):
            logger.info(f"[DrissionPage] 拿到 Token: {token}")
            return token
        else:
            logger.warning("[DrissionPage] 未拿到有效 Token")
            return None
    except Exception as e:
        logger.error(f"[DrissionPage] 异常: {e}", exc_info=True)
        return None


def get_taoiptv_token_by_playwright() -> Optional[str]:
    try:
        logger.info("[Playwright] 尝试获取 Token...")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "playwright", "-q"],
                    check=True, capture_output=True, timeout=120,
                )
                subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "chromium"],
                    check=True, capture_output=True, timeout=300,
                )
                from playwright.sync_api import sync_playwright
            except Exception as e:
                logger.error(f"Playwright 安装失败: {e}")
                return None

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=Config.USER_AGENT,
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            try:
                page.goto("https://taoiptv.com", wait_until="domcontentloaded", timeout=30000)
                logger.info("[Playwright] 等待防人机验证...")
                page.wait_for_timeout(10000)

                clicked = False
                for selector in [
                    "text=获取Token",
                    "text=获取 Token",
                    "text=Token",
                    "a:has-text('Token')",
                    "span:has-text('Token')",
                ]:
                    try:
                        element = page.locator(selector).first
                        if element.is_visible(timeout=3000):
                            element.click()
                            clicked = True
                            break
                    except Exception:
                        continue

                if clicked:
                    page.wait_for_timeout(2000)
                    token = None
                    clip = get_clipboard_content()
                    if clip:
                        m = re.search(r"\b([a-f0-9]{16})\b", clip, re.I)
                        if m:
                            token = m.group(1)
                            logger.info("[Playwright] 从剪贴板拿到 Token")
                            return token
            finally:
                browser.close()
        return None
    except Exception as e:
        logger.error(f"[Playwright] 异常: {e}")
        return None


def _verify_token_once(token: str) -> bool:
    """用 lives/51025.txt 验证 Token 是否与本机 IP 匹配且非阉割版（>3 条）"""
    try:
        url = f"https://taoiptv.com/lives/51025.txt?token={token}"
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
        with urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx)
        ).open(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="replace")
        if "认证token参数不正确" in html or "Authentication Failed" in html:
            return False
        lines = [l for l in html.splitlines() if l.strip() and not l.startswith("#")]
        if len(lines) <= 3:
            logger.warning(f"[验证] Token 仅返回 {len(lines)} 行，视为无效/阉割版")
            return False
        return True
    except Exception:
        return False


def get_taoiptv_token() -> Optional[str]:
    """
    统一入口：
    - Token 与 IP 绑定，只能在“当前出口 IP”上实时获取。
    - 非 CI 环境优先尝试自动获取；CI 环境默认跳过（可通过环境变量开启）。
    """
    # CI 环境默认不自动获取（避免被 Cloudflare 卡住）
    if is_running_in_ci():
        force_in_ci = os.getenv("TAO_FORCE_TOKEN_IN_CI", "0").strip().lower() in ("1", "true", "yes")
        if not force_in_ci:
            logger.info(
                "当前为 CI 环境，默认不自动获取 Token（Token 与 IP 绑定，CI 频繁被 Cloudflare 拦截）。"
                "如需启用，请在仓库 Secrets 或 Variables 中添加变量 TAO_FORCE_TOKEN_IN_CI=1"
            )
            return None
        else:
            logger.info("CI 环境已设置 TAO_FORCE_TOKEN_IN_CI=1，尝试自动获取 Token...")

    # 非 CI（或 CI 强制开启）时尝试浏览器获取
    if not _setup_xvfb_and_deps():
        logger.warning("环境初始化失败，跳过 Token 获取")
        return None

    # DrissionPage
    token = get_taoiptv_token_by_drissionpage()
    if token:
        if _verify_token_once(token):
            logger.info(f"✅ Token 获取并验证成功: {token}")
            return token
        else:
            logger.warning("DrissionPage 拿到的 Token 验证失败（与本机 IP 不匹配或已阉割）")

    # Playwright 备选
    token = get_taoiptv_token_by_playwright()
    if token:
        if _verify_token_once(token):
            logger.info(f"✅ Token 获取并验证成功: {token}")
            return token
        else:
            logger.warning("Playwright 拿到的 Token 验证失败")

    logger.error("❌ 未能获取当前 IP 对应的有效 Token")
    return None


# ==============================================
# 更新 my_urls.txt
# ==============================================
def update_my_urls_all(token: str) -> bool:
    if not token or len(token) != 16:
        return False
    file_path = FILE_PATHS["my_urls"]
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        count = len(re.findall(r"token=[a-f0-9]{16}", content, re.I))
        if count == 0:
            return False
        content = re.sub(r"token=[a-f0-9]{16}", f"token={token}", content, flags=re.I)
        content = re.sub(r"^#\s*更新时间:.*$", "", content, flags=re.MULTILINE)
        content = re.sub(r"\n{2,}", "\n\n", content).strip() + "\n"
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        content = f"# 更新时间: {bj.strftime('%Y-%m-%d %H:%M:%S')} | Token: {token}\n" + content
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"✅ my_urls.txt更新成功！替换 {count} 个Token")
        return True
    except Exception as e:
        logger.error(f"❌ 更新失败: {e}")
        return False


# ==============================================
# 解析第一个远程源并保存到 assets/111.txt
# ==============================================
def fetch_and_save_first_source(token: Optional[str] = None) -> bool:
    try:
        file_path = FILE_PATHS["my_urls"]
        if not os.path.exists(file_path):
            return False
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip() and not l.strip().startswith("#")]
        if not lines:
            return False
        first_url = None
        for line in lines:
            urls = RE_ALL_URLS.findall(line)
            if urls:
                first_url = urls[0]
                break
        if not first_url:
            return False
        if token and "token=" in first_url:
            first_url = re.sub(r"token=[a-f0-9]{16}", f"token={token}", first_url, flags=re.I)
        logger.info(f"正在解析第一个远程源: {first_url}")
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(first_url, headers={"User-Agent": Config.USER_AGENT})
        with urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx)
        ).open(req, timeout=Config.TIMEOUT_FETCH) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        if "认证token参数不正确" in content or "Authentication Failed" in content:
            logger.error("❌ Token无效，获取第一个远程源失败")
            return False
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        out = f"# 保存时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}\n# 来源: {first_url}\n\n{content}"
        with open(FILE_PATHS["first_source"], "w", encoding="utf-8") as f:
            f.write(out)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"✅ 111.txt 保存成功")
        return True
    except Exception as e:
        logger.error(f"❌ 解析失败: {e}")
        return False


# ==============================================
# Git 提交推送
# ==============================================
def git_commit_push():
    try:
        logger.info("正在同步到GitHub仓库...")
        os.chdir(PROJECT_ROOT)
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Auto-Bot"], check=True, capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@noreply.github.com"], check=True, capture_output=True)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if not status:
            logger.info("✅ 无文件变更，无需提交")
            return True
        subprocess.run(["git", "add", "assets/my_urls.txt", "assets/111.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Auto update TaoIPTV token and first source"], check=True, capture_output=True)
        gh_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        if gh_token and repo:
            subprocess.run(
                ["git", "push", f"https://x-access-token:{gh_token}@github.com/{repo}.git", "HEAD"],
                check=True, capture_output=True,
            )
        else:
            subprocess.run(["git", "push"], check=True, capture_output=True)
        logger.info("✅ 已同步到GitHub仓库！")
        return True
    except Exception as e:
        logger.warning(f"Git失败: {e}")
        return False


# ==============================================
# 域名黑名单
# ==============================================
DOMAIN_BLACKLIST: Set[str] = {
    "iptv.catvod.com", "dd.ddzb.fun", "goodiptv.club", "jiaojirentv.top",
    "alist.xicp.fun", "rihou.cc", "php.jdshipin.com",
    "t.freetv.fun", "stream1.freetv.fun", "hlsztemgsplive.miguvideo", "stream2.freetv.fun",
}
def url_matches_domain_blacklist(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in DOMAIN_BLACKLIST)
    except Exception:
        return False


# ==============================================
# 点播/图片过滤
# ==============================================
VOD_DOMAINS: Set[str] = {"kwimgs.com", "kuaishou.com", "ixigua.com", "douyin.com", "tiktokcdn.com", "bdstatic.com", "byteimg.com"}
VOD_EXTS: Set[str] = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".rmvb"}
IMG_EXTS: Set[str] = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
def is_vod_or_image_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in VOD_DOMAINS):
            return True
        path = urlparse(url).path.lower()
        return path.endswith(tuple(VOD_EXTS)) or path.endswith(tuple(IMG_EXTS))
    except Exception:
        return False


# ==============================================
# 行格式清洗
# ==============================================
CLEAN_OK = "ok"
CLEAN_NO_FORMAT = "no_format"
CLEAN_EMPTY_NAME = "empty_name"
CLEAN_BAD_URL = "bad_url"
CLEAN_DOMAIN_BL = "domain_blacklist"
CLEAN_VOD = "vod_filtered"

def clean_source_line(line: str) -> Tuple[Optional[Tuple[str, str]], str]:
    if not line:
        return None, CLEAN_NO_FORMAT
    line = line.replace("\r", "").replace("\n", " ").strip()
    if "," not in line or "://" not in line:
        return None, CLEAN_NO_FORMAT
    idx = line.find("://")
    if idx < 1:
        return None, CLEAN_BAD_URL
    prefix = line[: idx - 1]
    pos = prefix.rfind(",")
    if pos < 0:
        return None, CLEAN_NO_FORMAT
    name = re.sub(r"\s{2,}", " ", prefix[:pos].strip())
    if not name:
        return None, CLEAN_EMPTY_NAME
    rest = line[pos + 1:].strip()
    url = rest.split(",")[0].strip().split("$")[0].split("#")[0].strip()
    if not url or "://" not in url:
        return None, CLEAN_BAD_URL
    if url_matches_domain_blacklist(url):
        return None, CLEAN_DOMAIN_BL
    if is_vod_or_image_url(url):
        return None, CLEAN_VOD
    return (name, url), CLEAN_OK


# ==============================================
# 媒体类型判定
# ==============================================
STREAM_CTS = ["video/mp2t", "video/mp4", "video/x-flv", "application/vnd.apple.mpegurl", "application/octet-stream", "application/x-mpegURL"]
def is_stream_ct(ct):
    return any(p in ct.lower() for p in STREAM_CTS) if ct else False
def is_html_ct(ct):
    return "text/html" in ct.lower() if ct else False
def _read_chunk(resp, n=4096):
    try:
        return resp.read(n)
    except Exception:
        return b""
def _looks_media(d):
    if not d:
        return False
    return (d[:3] == b"FLV" or (len(d) >= 8 and d[4:8] == b"ftyp") or d[:3] == b"ID3" or (len(d) >= 188 and d[0] == 0x47))
def _looks_html(d):
    if not d:
        return False
    d = d.lstrip(b"\xef\xbb\xbf").lstrip()
    return d[:5].lower().startswith((b"<!doc", b"<html"))


# ==============================================
# StreamChecker
# ==============================================
class StreamChecker:
    def __init__(self, manual_urls=None):
        self.start_time = datetime.now()
        self.blacklist_urls = self._load_blacklist()
        self.whitelist_urls: Set[str] = set()
        self.whitelist_lines: List[str] = []
        self.new_failed_urls: Set[str] = set()
        self.manual_urls = manual_urls or []
        self.clean_stats: Dict[str, int] = {CLEAN_NO_FORMAT: 0, CLEAN_EMPTY_NAME: 0, CLEAN_BAD_URL: 0, CLEAN_DOMAIN_BL: 0, CLEAN_VOD: 0}

    def _load_blacklist(self):
        bl = set()
        try:
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith(("更新时间", "#")):
                            continue
                        url = line.split(",")[-1].split("$")[0].split("#")[0].strip()
                        if "://" in url:
                            bl.add(url)
                logger.info(f"加载URL黑名单: {len(bl)} 条")
        except Exception as e:
            logger.error(f"加载黑名单失败: {e}")
        return bl

    def _save_blacklist(self):
        if not self.new_failed_urls:
            return
        try:
            existing, has_h = [], False
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], "r", encoding="utf-8") as f:
                    existing = [l.rstrip("\n") for l in f]
                for l in existing[:5]:
                    if l.startswith("更新时间"):
                        has_h = True
                        break
            parts = []
            if not has_h:
                bj = datetime.now(timezone.utc) + timedelta(hours=8)
                parts += ["更新时间,#genre#", f"{bj.strftime('%Y%m%d %H:%M')},url", "", "blacklist,#genre#"]
            seen = set()
            for l in existing:
                if l and not l.startswith(("更新时间", "#")):
                    u = l.split(",")[-1].strip()
                    if u:
                        seen.add(u)
                        parts.append(l)
            for u in self.new_failed_urls:
                if u not in seen:
                    parts.append(u)
            with open(FILE_PATHS["blacklist_auto"], "w", encoding="utf-8") as f:
                f.write("\n".join(parts))
            logger.info(f"黑名单更新，新增{len(self.new_failed_urls)}条")
        except Exception as e:
            logger.error(f"保存黑名单失败: {e}")

    def read_file(self, path, split_by_space=False):
        try:
            with open(path, "r", encoding="utf-8") as f:
                c = f.read()
            if split_by_space:
                return [l.strip() for l in re.split(r"[\s\t\n]+", c) if l.strip().startswith("http")]
            return [l.strip() for l in c.splitlines() if l.strip()]
        except Exception as e:
            logger.warning(f"读取失败 {path}: {e}")
            return []

    def check_http(self, url, timeout):
        s = time.perf_counter()
        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
            with urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=ctx)
            ).open(req, timeout=timeout) as r:
                code = r.getcode()
                ct = r.headers.get("Content-Type", "")
                data = _read_chunk(r)
                ms = round((time.perf_counter() - s) * 1000, 2)
                ok = 200 <= code < 400 or code in (301, 302)
                if not ok:
                    return False, ms, str(code), None
                if is_html_ct(ct) or _looks_html(data):
                    return False, ms, f"{code}/html", "timeout"
                if is_stream_ct(ct) and _looks_media(data):
                    return True, ms, str(code), "stream"
                if b"#EXTM3U" in data:
                    return True, ms, str(code), "playlist"
                return True, ms, str(code), "unknown"
        except Exception as e:
            ms = round((time.perf_counter() - s) * 1000, 2)
            return False, ms, str(e), "timeout"

    def check_url(self, url, is_whitelist=False):
        t = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK
        if url_matches_domain_blacklist(url):
            return False, 0, "blacklist", "blacklist"
        if url.startswith(("http://", "https://")):
            return self.check_http(url, t)
        return True, 0, "ok", "stream"

    def fetch_remote(self, urls):
        all_lines = []
        for raw_url in urls:
            try:
                safe_url = quote(unquote(raw_url), safe=":/?&=#%")
            except Exception:
                safe_url = raw_url
            try:
                ctx = ssl._create_unverified_context()
                req = urllib.request.Request(safe_url, headers={"User-Agent": Config.USER_AGENT})
                with urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=ctx)
                ).open(req, timeout=15) as r:
                    c = r.read().decode("utf-8", errors="replace")
                before = len(all_lines)
                if "#EXTM3U" in c[:200]:
                    name = ""
                    for l in c.splitlines():
                        l = l.strip()
                        if not l:
                            continue
                        if l.startswith("#EXTINF"):
                            m_group = re.search(r'group-title\s*=\s*["\']?([^"\',]+)', l)
                            name = m_group.group(1).strip() if m_group else (
                                l.split(",")[-1].strip() if "," in l else ""
                            )
                        elif not l.startswith("#"):
                            url_candidate = l.strip().split("#")[0].strip().split("$")[0].strip()
                            if not url_candidate:
                                name = ""
                                continue
                            if not re.match(r'https?://', url_candidate, re.I):
                                url_candidate = urljoin(raw_url, url_candidate)
                            if url_matches_domain_blacklist(url_candidate) or is_vod_or_image_url(url_candidate):
                                name = ""
                                continue
                            group = name if name else "订阅源"
                            res, _ = clean_source_line(f"{group},{url_candidate}")
                            all_lines.append(f"{res[0]},{res[1]}" if res else f"{group},{url_candidate}")
                            name = ""
                else:
                    raw_urls_found = RE_ALL_URLS.findall(c)
                    for u in raw_urls_found:
                        u = u.strip().rstrip(".,;:!?)")
                        if not u or is_vod_or_image_url(u) or url_matches_domain_blacklist(u):
                            continue
                        all_lines.append(f"订阅源,{u}")
                    if len(all_lines) == before:
                        for l in c.splitlines():
                            l = l.strip()
                            if not l or l.startswith("#"):
                                continue
                            res, _ = clean_source_line(l)
                            if res:
                                all_lines.append(f"{res[0]},{res[1]}")
                    if len(all_lines) == before:
                        for l in c.splitlines():
                            l = l.strip()
                            if not l or l.startswith("#") or l.count("http") <= 1:
                                continue
                            for part in l.split(","):
                                part = part.strip().rstrip(".,;:!?)")
                                if re.match(r'https?://', part, re.I) and not is_vod_or_image_url(part) and not url_matches_domain_blacklist(part):
                                    all_lines.append(f"订阅源,{part}")
                got = len(all_lines) - before
                if got > 1:
                    logger.info(f" ✓ {raw_url[:90]} → {got} 个源")
                elif got == 1:
                    logger.warning(f" ⚠ {raw_url[:90]} → 仅 {got} 个源 | {c[:500].replace(chr(10), ' ')}")
                else:
                    logger.warning(f" ✗ {raw_url[:90]} → 0 个源 | {c[:200].replace(chr(10), ' ')}")
            except Exception as e:
                logger.error(f" ✗ {raw_url[:90]} → 异常: {e}")
        return all_lines

    def load_whitelist(self):
        for line in self.read_file(FILE_PATHS["whitelist_manual"]):
            if line.startswith("#"):
                continue
            res, _ = clean_source_line(line)
            if res:
                self.whitelist_urls.add(res[1])
                self.whitelist_lines.append(line)
        logger.info(f"手动白名单: {len(self.whitelist_urls)} 个频道")

    def prepare_lines(self, lines):
        to_check, seen = [], set()
        for line in lines:
            res, reason = clean_source_line(line)
            if not res:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
                continue
            _, url = res
            if url in seen or url in self.blacklist_urls:
                continue
            seen.add(url)
            to_check.append((url, line))
        logger.info(f"待检测 {len(to_check)} 条")
        return to_check, []

    def run(self):
        logger.info("===== 开始流媒体检测 =====")
        self.load_whitelist()
        lines = []
        urls = self.read_file(FILE_PATHS["urls"], split_by_space=True)
        if urls:
            logger.info(f"开始拉取 urls.txt 中的 {len(urls)} 个节点")
            fetched = self.fetch_remote(urls)
            logger.info(f"urls.txt 完成：{len(urls)} 个节点 → {len(fetched)} 个源")
            lines.extend(fetched)
        else:
            logger.warning("未找到 urls.txt")
        my_urls = self.read_file(FILE_PATHS["my_urls"], split_by_space=True)
        if my_urls:
            logger.info(f"开始拉取 my_urls.txt 中的 {len(my_urls)} 个节点")
            fetched = self.fetch_remote(my_urls)
            logger.info(f"my_urls.txt 完成：{len(my_urls)} 个节点 → {len(fetched)} 个源")
            lines.extend(fetched)
        else:
            logger.warning("未找到 my_urls.txt")
        lines.extend(self.whitelist_lines)
        lines.extend(self.manual_urls)
        to_check, _ = self.prepare_lines(lines)
        results = []
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as pool:
            fmap = {pool.submit(self.check_url, u, u in self.whitelist_urls): u for u, _ in to_check}
            for fut in as_completed(fmap):
                url = fmap[fut]
                try:
                    s, ms, code, kind = fut.result()
                    results.append((url, ms, code, kind))
                    if not s and url not in self.whitelist_urls:
                        self.new_failed_urls.add(url)
                except Exception as e:
                    logger.error(f"检测异常 {url}: {e}")
                    self.new_failed_urls.add(url)
        self._save_blacklist()
        results.sort(key=lambda x: ({"stream": 0, "playlist": 1, "unknown": 2}.get(x[3], 3), x[1]))
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        with open(FILE_PATHS["whitelist_respotime"], "w", encoding="utf-8") as f:
            f.write(f"更新时间,#genre#\n{bj.strftime('%Y%m%d %H:%M')}\n\n")
            for url, ms, code, kind in results:
                f.write(f"{ms},{url},{code},{kind}\n")
        with open(FILE_PATHS["whitelist_auto"], "w", encoding="utf-8") as f:
            f.write(f"更新时间,#genre#\n{bj.strftime('%Y%m%d %H:%M')}\n\n")
            for url, _, _, kind in results:
                if kind not in ("timeout", "blacklist"):
                    f.write(f"自动,{url}\n")
        total = len(results)
        stream = sum(1 for *_, k in results if k == "stream")
        playlist = sum(1 for *_, k in results if k == "playlist")
        unknown = sum(1 for *_, k in results if k == "unknown")
        timeout = sum(1 for *_, k in results if k == "timeout")
        elapsed = (datetime.now() - self.start_time).seconds
        logger.info(
            f"===== 检测完成 | 总计:{total} | 流:{stream} | 列表:{playlist} | "
            f"未知:{unknown} | 超时:{timeout} | 耗时:{elapsed}s ====="
        )


# ==============================================
# 主函数
# ==============================================
def main():
    try:
        logger.info("===== 开始执行 =====")
        token = get_taoiptv_token()
        if token:
            update_my_urls_all(token)
            fetch_and_save_first_source(token)
            git_commit_push()
        else:
            logger.warning("未获取到有效 Token，跳过 my_urls.txt / 111.txt 更新")
        checker = StreamChecker()
        checker.run()
        logger.info("===== 全部完成 =====")
    except Exception as e:
        logger.error(f"主程序异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
