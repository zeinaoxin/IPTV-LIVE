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
# 环境与工具
# ==============================================
def is_running_in_ci() -> bool:
    return bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))

def _read_repo_secret(name: str) -> Optional[str]:
    return os.getenv(name)

def get_clipboard_content() -> Optional[str]:
    try:
        if sys.platform.startswith("linux"):
            for cmd in [["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"], ["wl-paste"]]:
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if r.returncode == 0: return r.stdout.strip()
                except Exception: continue
        elif sys.platform == "darwin":
            r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0: return r.stdout.strip()
        elif sys.platform == "win32":
            r = subprocess.run(["powershell", "-command", "Get-Clipboard"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0: return r.stdout.strip()
    except Exception as e:
        logger.debug(f"获取剪贴板失败: {e}")
    return None

# ==============================================
# Token：获取 + 验证
# ==============================================
def _install_drissionpage() -> bool:
    try:
        import DrissionPage  # noqa: F401
        return True
    except Exception: pass
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "DrissionPage", "-q"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
        import DrissionPage  # noqa: F401
        return True
    except Exception as e:
        logger.warning(f"DrissionPage 安装失败: {e}")
        return False

def get_taoiptv_token_by_drissionpage() -> Optional[str]:
    if not _install_drissionpage():
        return None
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
    except Exception as e:
        logger.warning(f"DrissionPage 导入失败: {e}")
        return None

    browser_path = None
    for p in ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]:
        if os.path.isfile(p):
            browser_path = p
            break

    logger.info("尝试使用 DrissionPage 获取 Token ...")
    try:
        co = ChromiumOptions()
        if browser_path:
            co.set_paths(browser_path=browser_path)
            logger.info(f"使用浏览器路径: {browser_path}")
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        co.headless(True)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-gpu")

        page = ChromiumPage(co)
        page.get("https://taoiptv.com")
        page.wait.doc_loaded()
        page.wait.load_start()

        clicked = False
        selectors = ["text=获取Token", "text=获取 Token", "text=Token"]
        for sel in selectors:
            try:
                el = page.ele(sel, timeout=10)
                if el:
                    el.click()
                    clicked = True
                    logger.info(f"已点击元素: {sel}")
                    break
            except Exception: continue

        if not clicked:
            try:
                elems = page.eles("text:Token")
                for e in elems:
                    txt = (e.text or "").strip()
                    if txt and ("获取Token" in txt or "获取 Token" in txt):
                        e.click()
                        clicked = True
                        logger.info("已通过文本包含'Token'元素点击")
                        break
            except Exception: pass

        page.wait(3)
        token = None
        clip = get_clipboard_content()
        if clip:
            m = re.search(r"\b([a-f0-9]{16})\b", clip, re.I)
            if m:
                token = m.group(1)
                logger.info(f"DrissionPage 从剪贴板拿到 Token: {token}")

        if not token:
            try:
                body = page.ele("tag:body").text
                if body:
                    for line in body.splitlines():
                        if "token" in line.lower():
                            m = re.search(r"\b([a-f0-9]{16})\b", line, re.I)
                            if m:
                                token = m.group(1)
                                logger.info(f"DrissionPage 从页面文本拿到 Token: {token}")
                                break
            except Exception: pass

        if not token:
            try:
                storage_text = page.run_js("return JSON.stringify({...localStorage, ...sessionStorage})")
                storage = json.loads(storage_text) if storage_text else {}
                for k, v in storage.items():
                    if isinstance(v, str) and "token" in k.lower() and len(v.strip()) == 16:
                        m2 = re.match(r"^[a-f0-9]{16}$", v.strip(), re.I)
                        if m2:
                            token = v.strip()
                            logger.info(f"DrissionPage 从 storage 拿到 Token: {token}")
                            break
            except Exception: pass

        # 调试截图
        try:
            debug_dir = os.path.join(SCRIPT_DIR, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            page.get_screenshot(path=os.path.join(debug_dir, "drissionpage.png"), full_page=True)
            logger.info("DrissionPage 截图已保存到 debug/drissionpage.png")
        except Exception: pass

        page.quit()

        if token and len(token) == 16 and re.match(r"^[a-f0-9]{16}$", token, re.I):
            logger.info(f"✅ DrissionPage 成功获取 Token: {token}")
            return token
        else:
            logger.warning("DrissionPage 未拿到有效 Token（可能被 Cloudflare 挑战页拦截）")
            return None
    except Exception as e:
        logger.error(f"DrissionPage 异常: {e}", exc_info=True)
        return None

def get_taoiptv_token_by_playwright() -> Optional[str]:
    try:
        logger.info("正在通过 Playwright 模拟点击获取 TaoIPTV 最新 Token ...")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright 未安装，尝试安装 ...")
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "-q"], check=True, capture_output=True, timeout=120)
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, capture_output=True, timeout=300)
                from playwright.sync_api import sync_playwright
                logger.info("Playwright 安装成功")
            except Exception as e:
                logger.warning(f"Playwright 安装失败: {e}")
                return None

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox", "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu", "--disable-web-security",
                ],
            )
            context = browser.new_context(user_agent=Config.USER_AGENT, viewport={"width": 1920, "height": 1080})
            page = context.new_page()
            try:
                logger.info(" 步骤1: 打开 https://taoiptv.com ...")
                page.goto("https://taoiptv.com", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                logger.info(" 步骤2: 查找'获取Token'元素 ...")
                selectors = [
                    "text=获取Token", "text=获取 Token", "text=Token",
                    "a:has-text('Token')", "span:has-text('Token')",
                    "div:has-text('Token')", "[onclick*='token']", "#token", ".token",
                ]
                clicked = False
                for selector in selectors:
                    try:
                        element = page.locator(selector).first
                        if element.is_visible(timeout=2000):
                            logger.info(f" 找到元素: {selector}")
                            element.click()
                            clicked = True
                            logger.info(" 步骤3: 已点击'获取Token'元素")
                            break
                    except Exception: continue

                if not clicked:
                    logger.info(" 尝试通过 JS 查找 ...")
                    try:
                        elements = page.locator("*:has-text('Token')").all()
                        for elem in elements:
                            try:
                                text = elem.inner_text()
                                if "获取Token" in text or "获取 Token" in text:
                                    elem.click()
                                    clicked = True
                                    logger.info(" 通过 JS 点击成功")
                                    break
                            except Exception: continue
                    except Exception as e:
                        logger.warning(f" JS 查找失败: {e}")

                if clicked:
                    page.wait_for_timeout(2000)
                    logger.info(" 步骤4: 从页面获取 Token ...")
                    token = None
                    try:
                        token = page.evaluate("navigator.clipboard.readText()")
                        if token and len(token) == 16 and re.match(r"^[a-f0-9]{16}$", token, re.I):
                            logger.info(f"✅ 成功从剪贴板获取 Token: {token}")
                            return token
                    except Exception: pass

                    if not token:
                        content = page.content()
                        patterns = [r"Token[：:]\s*([a-f0-9]{16})", r"token[：:]\s*([a-f0-9]{16})"]
                        for pat in patterns:
                            matches = re.findall(pat, content, re.I)
                            if matches:
                                logger.info(f"✅ 从页面获取 Token: {matches[0]}")
                                return matches[0]

                    if not token:
                        try:
                            storage = page.evaluate("JSON.stringify({...localStorage, ...sessionStorage})")
                            storage_dict = json.loads(storage)
                            for key, value in storage_dict.items():
                                if "token" in key.lower() and len(str(value)) == 16:
                                    if re.match(r"^[a-f0-9]{16}$", str(value), re.I):
                                        logger.info(f"✅ 成功从 storage 获取 Token: {value}")
                                        return value
                        except Exception: pass
                else:
                    logger.warning(" 未找到'获取Token'元素")
            finally:
                browser.close()
        return None
    except Exception as e:
        logger.error(f"Playwright 获取 Token 失败: {e}", exc_info=True)
        return None

def _verify_token_once(token: str) -> bool:
    """用一个小接口验证 Token 是否被 taoiptv 认可，避免再用假 Token 导致‘仅 3 个源’"""
    try:
        url = "https://taoiptv.com/lives/51025.txt"
        if token:
            url += f"?token={token}"
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="replace")
        if "认证token参数不正确" in html or "Authentication Failed" in html:
            logger.warning(f"验证失败：Token {token} 导致认证错误")
            return False
        return True
    except Exception as e:
        logger.debug(f"Token 验证请求异常（不作为判定）: {e}")
        return True

def get_taoiptv_token() -> Optional[str]:
    """
    优先级：
    - 仓库密钥 REPO_SECRET_TAOTOKEN（最稳）
    - 非 CI 环境下尝试 DrissionPage/Playwright（CI 下基本过不了 Cloudflare）
    - 拿到候选 Token 后会先验证，不再盲目写入
    """
    # 1) 优先仓库密钥
    secret_token = _read_repo_secret("REPO_SECRET_TAOTOKEN")
    if secret_token:
        secret_token = str(secret_token).strip()
        if re.match(r"^[a-f0-9]{16}$", secret_token, re.I):
            logger.info(f"从仓库密钥读取到 Token: {secret_token}")
            if _verify_token_once(secret_token):
                return secret_token
            else:
                logger.warning("仓库密钥中的 Token 已失效，将尝试其他方式")
        else:
            logger.warning("仓库密钥格式不正确，将尝试其他方式")

    # 2) CI 环境下不再尝试自动点击（基本过不了 Cloudflare）
    if is_running_in_ci():
        logger.error("❌ CI 环境且未配置有效 REPO_SECRET_TAOTOKEN，无法获取 Token（已跳过自动化以避免被拦截）")
        return None

    # 3) 非 CI 环境：尝试 DrissionPage -> Playwright
    token = get_taoiptv_token_by_drissionpage()
    if token:
        if _verify_token_once(token):
            return token
        else:
            logger.warning("DrissionPage 拿到的 Token 验证失败")

    token = get_taoiptv_token_by_playwright()
    if token:
        if _verify_token_once(token):
            return token
        else:
            logger.warning("Playwright 拿到的 Token 验证失败")

    logger.error("❌ 未能获取有效 Token")
    return None

# ==============================================
# 更新 my_urls.txt
# ==============================================
def update_my_urls_all(token: str) -> bool:
    if not token or len(token) != 16:
        logger.error("❌ Token无效，跳过更新")
        return False
    file_path = FILE_PATHS["my_urls"]
    if not os.path.exists(file_path):
        logger.error(f"❌ my_urls.txt不存在: {file_path}")
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        count = len(re.findall(r"token=[a-f0-9]{16}", content, re.I))
        if count == 0:
            logger.info("✅ 文件中无需更新的Token")
            return False
        content = re.sub(r"token=[a-f0-9]{16}", f"token={token}", content, flags=re.I)
        content = re.sub(r"^#\s*更新时间:.*$", "", content, flags=re.MULTILINE)
        content = re.sub(r"\n{2,}", "\n\n", content).strip() + "\n"
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        header = f"# 更新时间: {bj.strftime('%Y-%m-%d %H:%M:%S')} | Token: {token}\n"
        content = header + content
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"✅ my_urls.txt更新成功！替换 {count} 个Token（{bj.strftime('%Y-%m-%d %H:%M:%S')}）")
        return True
    except Exception as e:
        logger.error(f"❌ 更新失败: {e}", exc_info=True)
        return False

# ==============================================
# 解析第一个远程源并保存到 assets/111.txt
# ==============================================
def fetch_and_save_first_source(token: Optional[str] = None) -> bool:
    try:
        file_path = FILE_PATHS["my_urls"]
        if not os.path.exists(file_path):
            logger.error(f"❌ my_urls.txt不存在: {file_path}")
            return False
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip() and not l.strip().startswith("#")]
        if not lines:
            logger.error("❌ my_urls.txt 中没有有效的URL")
            return False
        first_url = None
        for line in lines:
            urls = RE_ALL_URLS.findall(line)
            if urls:
                first_url = urls[0]
                break
        if not first_url:
            logger.error("❌ my_urls.txt 中没有找到有效的URL")
            return False
        if token and "token=" in first_url:
            first_url = re.sub(r"token=[a-f0-9]{16}", f"token={token}", first_url, flags=re.I)
        logger.info(f"正在解析第一个远程源: {first_url}")
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(first_url, headers={"User-Agent": Config.USER_AGENT})
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=Config.TIMEOUT_FETCH) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        if "认证token参数不正确" in content or "Authentication Failed" in content:
            logger.error("❌ Token无效，获取第一个远程源失败")
            return False
        output_path = FILE_PATHS["first_source"]
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        time_header = f"# 保存时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}\n# 来源: {first_url}\n\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(time_header)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"✅ 第一个远程源内容已保存到: {output_path}")
        return True
    except Exception as e:
        logger.error(f"❌ 解析第一个远程源失败: {e}", exc_info=True)
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
            push_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
            subprocess.run(["git", "push", push_url, "HEAD"], check=True, capture_output=True)
        else:
            subprocess.run(["git", "push"], check=True, capture_output=True)
        logger.info("✅ 已同步到GitHub仓库！")
        return True
    except subprocess.CalledProcessError as e:
        hint = ""
        try:
            hint = f" [ACTIONS={os.getenv('GITHUB_ACTIONS','?')} REPO={os.getenv('GITHUB_REPOSITORY','?')}]"
        except Exception: pass
        logger.warning(f"Git推送失败:{hint} {e.stderr.decode('utf-8','ignore') if e.stderr else ''}")
        return False
    except Exception as e:
        logger.warning(f"Git异常: {e}")
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
    except Exception: return False

# ==============================================
# 点播/图片过滤
# ==============================================
VOD_DOMAINS: Set[str] = {"kwimgs.com", "kuaishou.com", "ixigua.com", "douyin.com", "tiktokcdn.com", "bdstatic.com", "byteimg.com"}
VOD_EXTS: Set[str] = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".rmvb"}
IMG_EXTS: Set[str] = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
def is_vod_or_image_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in VOD_DOMAINS): return True
        path = urlparse(url).path.lower()
        return path.endswith(tuple(VOD_EXTS)) or path.endswith(tuple(IMG_EXTS))
    except Exception: return False

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
    if not line: return None, CLEAN_NO_FORMAT
    line = line.replace("\r", "").replace("\n", " ").strip()
    if "," not in line or "://" not in line: return None, CLEAN_NO_FORMAT
    idx = line.find("://")
    if idx < 1: return None, CLEAN_BAD_URL
    prefix = line[: idx - 1]
    pos = prefix.rfind(",")
    if pos < 0: return None, CLEAN_NO_FORMAT
    name = re.sub(r"\s{2,}", " ", prefix[:pos].strip())
    if not name: return None, CLEAN_EMPTY_NAME
    rest = line[pos + 1:].strip()
    url = rest.split(",")[0].strip().split("$")[0].split("#")[0].strip()
    if not url or "://" not in url: return None, CLEAN_BAD_URL
    if url_matches_domain_blacklist(url): return None, CLEAN_DOMAIN_BL
    if is_vod_or_image_url(url): return None, CLEAN_VOD
    return (name, url), CLEAN_OK

# ==============================================
# 媒体类型判定
# ==============================================
STREAM_CTS = ["video/mp2t", "video/mp4", "video/x-flv", "application/vnd.apple.mpegurl", "application/octet-stream", "application/x-mpegURL"]
def is_stream_ct(ct): return any(p in ct.lower() for p in STREAM_CTS) if ct else False
def is_html_ct(ct): return "text/html" in ct.lower() if ct else False
def _read_chunk(resp, n=4096):
    try: return resp.read(n)
    except Exception: return b""
def _looks_media(d):
    if not d: return False
    return (d[:3] == b"FLV" or (len(d) >= 8 and d[4:8] == b"ftyp") or d[:3] == b"ID3" or (len(d) >= 188 and d[0] == 0x47))
def _looks_html(d):
    if not d: return False
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
                        if not line or line.startswith(("更新时间", "#")): continue
                        url = line.split(",")[-1].split("$")[0].split("#")[0].strip()
                        if "://" in url: bl.add(url)
                logger.info(f"加载URL黑名单: {len(bl)} 条")
        except Exception as e:
            logger.error(f"加载黑名单失败: {e}")
        return bl

    def _save_blacklist(self):
        if not self.new_failed_urls: return
        try:
            existing, has_h = [], False
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], "r", encoding="utf-8") as f: existing = [l.rstrip("\n") for l in f]
                for l in existing[:5]:
                    if l.startswith("更新时间"): has_h = True; break
            parts = []
            if not has_h:
                bj = datetime.now(timezone.utc) + timedelta(hours=8)
                parts += ["更新时间,#genre#", f"{bj.strftime('%Y%m%d %H:%M')},url", "", "blacklist,#genre#"]
            seen = set()
            for l in existing:
                if l and not l.startswith(("更新时间", "#")):
                    u = l.split(",")[-1].strip()
                    if u: seen.add(u); parts.append(l)
            for u in self.new_failed_urls:
                if u not in seen: parts.append(u)
            with open(FILE_PATHS["blacklist_auto"], "w", encoding="utf-8") as f: f.write("\n".join(parts))
            logger.info(f"黑名单更新，新增{len(self.new_failed_urls)}条")
        except Exception as e:
            logger.error(f"保存黑名单失败: {e}")

    def read_file(self, path, split_by_space=False):
        try:
            with open(path, "r", encoding="utf-8") as f: c = f.read()
            if split_by_space: return [l.strip() for l in re.split(r"[\s\t\n]+", c) if l.strip().startswith("http")]
            return [l.strip() for l in c.splitlines() if l.strip()]
        except Exception as e:
            logger.warning(f"读取失败 {path}: {e}"); return []

    def check_http(self, url, timeout):
        s = time.perf_counter()
        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=timeout) as r:
                code = r.getcode(); ct = r.headers.get("Content-Type", ""); data = _read_chunk(r)
                ms = round((time.perf_counter() - s) * 1000, 2)
                ok = 200 <= code < 400 or code in (301, 302)
                if not ok: return False, ms, str(code), None
                if is_html_ct(ct) or _looks_html(data): return False, ms, f"{code}/html", "timeout"
                if is_stream_ct(ct) and _looks_media(data): return True, ms, str(code), "stream"
                if b"#EXTM3U" in data: return True, ms, str(code), "playlist"
                return True, ms, str(code), "unknown"
        except Exception as e:
            ms = round((time.perf_counter() - s) * 1000, 2)
            return False, ms, str(e), "timeout"

    def check_url(self, url, is_whitelist=False):
        t = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK
        if url_matches_domain_blacklist(url): return False, 0, "blacklist", "blacklist"
        if url.startswith(("http://", "https://")): return self.check_http(url, t)
        return True, 0, "ok", "stream"

    def fetch_remote(self, urls):
        all_lines = []
        for raw_url in urls:
            try: safe_url = quote(unquote(raw_url), safe=":/?&=#%")
            except Exception: safe_url = raw_url
            try:
                ctx = ssl._create_unverified_context()
                req = urllib.request.Request(safe_url, headers={"User-Agent": Config.USER_AGENT})
                with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=15) as r:
                    c = r.read().decode("utf-8", errors="replace")
                before = len(all_lines)
                if "#EXTM3U" in c[:200]:
                    name = ""
                    for l in c.splitlines():
                        l = l.strip()
                        if not l: continue
                        if l.startswith("#EXTINF"):
                            m_group = re.search(r'group-title\s*=\s*["\']?([^"\',]+)', l)
                            name = m_group.group(1).strip() if m_group else (l.split(",")[-1].strip() if "," in l else "")
                        elif not l.startswith("#"):
                            url_candidate = l.strip().split("#")[0].strip().split("$")[0].strip()
                            if not url_candidate: name = ""; continue
                            if not re.match(r'https?://', url_candidate, re.I): url_candidate = urljoin(raw_url, url_candidate)
                            if url_matches_domain_blacklist(url_candidate) or is_vod_or_image_url(url_candidate): name = ""; continue
                            group = name if name else "订阅源"
                            res, _ = clean_source_line(f"{group},{url_candidate}")
                            all_lines.append(f"{res[0]},{res[1]}" if res else f"{group},{url_candidate}")
                            name = ""
                else:
                    raw_urls_found = RE_ALL_URLS.findall(c)
                    for u in raw_urls_found:
                        u = u.strip().rstrip(".,;:!?)")
                        if not u or is_vod_or_image_url(u) or url_matches_domain_blacklist(u): continue
                        all_lines.append(f"订阅源,{u}")
                    if len(all_lines) == before:
                        for l in c.splitlines():
                            l = l.strip()
                            if not l or l.startswith("#"): continue
                            res, _ = clean_source_line(l)
                            if res: all_lines.append(f"{res[0]},{res[1]}")
                    if len(all_lines) == before:
                        for l in c.splitlines():
                            l = l.strip()
                            if not l or l.startswith("#") or l.count("http") <= 1: continue
                            for part in l.split(","):
                                part = part.strip().rstrip(".,;:!?)")
                                if re.match(r'https?://', part, re.I) and not is_vod_or_image_url(part) and not url_matches_domain_blacklist(part):
                                    all_lines.append(f"订阅源,{part}")
                got = len(all_lines) - before
                if got > 1: logger.info(f" ✓ {raw_url[:90]} → {got} 个源")
                elif got == 1: logger.warning(f" ⚠ {raw_url[:90]} → 仅 {got} 个源 | 内容诊断: {c[:500].replace(chr(10), ' ')}")
                else: logger.warning(f" ✗ {raw_url[:90]} → 0 个源 | 内容: {c[:200].replace(chr(10), ' ')}")
            except Exception as e: logger.error(f" ✗ {raw_url[:90]} → 异常: {e}")
        return all_lines

    def load_whitelist(self):
        for line in self.read_file(FILE_PATHS["whitelist_manual"]):
            if line.startswith("#"): continue
            res, _ = clean_source_line(line)
            if res: self.whitelist_urls.add(res[1]); self.whitelist_lines.append(line)
        logger.info(f"手动白名单: {len(self.whitelist_urls)} 个频道")

    def prepare_lines(self, lines):
        to_check, seen = [], set()
        for line in lines:
            res, reason = clean_source_line(line)
            if not res: self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1; continue
            _, url = res
            if url in seen or url in self.blacklist_urls: continue
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
        else: logger.warning("未找到 urls.txt")
        my_urls = self.read_file(FILE_PATHS["my_urls"], split_by_space=True)
        if my_urls:
            logger.info(f"开始拉取 my_urls.txt 中的 {len(my_urls)} 个节点")
            fetched = self.fetch_remote(my_urls)
            logger.info(f"my_urls.txt 完成：{len(my_urls)} 个节点 → {len(fetched)} 个源")
            lines.extend(fetched)
        else: logger.warning("未找到 my_urls.txt")
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
                    if not s and url not in self.whitelist_urls: self.new_failed_urls.add(url)
                except Exception as e: logger.error(f"检测异常 {url}: {e}"); self.new_failed_urls.add(url)
        self._save_blacklist()
        results.sort(key=lambda x: ({"stream": 0, "playlist": 1, "unknown": 2}.get(x[3], 3), x[1]))
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        with open(FILE_PATHS["whitelist_respotime"], "w", encoding="utf-8") as f:
            f.write(f"更新时间,#genre#\n{bj.strftime('%Y%m%d %H:%M')}\n\n")
            for url, ms, code, kind in results: f.write(f"{ms},{url},{code},{kind}\n")
        with open(FILE_PATHS["whitelist_auto"], "w", encoding="utf-8") as f:
            f.write(f"更新时间,#genre#\n{bj.strftime('%Y%m%d %H:%M')}\n\n")
            for url, _, _, kind in results:
                if kind not in ("timeout", "blacklist"): f.write(f"自动,{url}\n")
        total = len(results)
        stream = sum(1 for *_, k in results if k == "stream")
        playlist = sum(1 for *_, k in results if k == "playlist")
        unknown = sum(1 for *_, k in results if k == "unknown")
        timeout = sum(1 for *_, k in results if k == "timeout")
        elapsed = (datetime.now() - self.start_time).seconds
        logger.info(f"===== 检测完成 | 总计:{total} | 流:{stream} | 列表:{playlist} | 未知:{unknown} | 超时:{timeout} | 耗时:{elapsed}s =====")

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
            logger.warning("未获取到有效 Token，跳过 my_urls.txt / 111.txt 更新，直接进入检测流程")
        checker = StreamChecker()
        checker.run()
        logger.info("===== 全部完成 =====")
    except Exception as e:
        logger.error(f"主程序异常: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
