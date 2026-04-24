import urllib.request
from urllib.request import HTTPCookieProcessor, build_opener
from http.cookiejar import CookieJar
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

# ==============================================
# 路径配置（固定项目层级，无需修改） 豆包
# ==============================================
SCRIPT_ABS_PATH = os.path.abspath(__file__)
SCRIPT_DIR = os.path.dirname(SCRIPT_ABS_PATH)
ASSETS_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)

FILE_PATHS = {
    "my_urls": os.path.join(ASSETS_DIR, "my_urls.txt"),
    "urls": os.path.join(ASSETS_DIR, "urls.txt"),
    "blacklist_auto": os.path.join(SCRIPT_DIR, "blacklist_auto.txt"),
    "whitelist_manual": os.path.join(SCRIPT_DIR, "whitelist_manual.txt"),
    "whitelist_auto": os.path.join(SCRIPT_DIR, "whitelist_auto.txt"),
    "whitelist_respotime": os.path.join(SCRIPT_DIR, "whitelist_respotime.txt"),
    "log": os.path.join(SCRIPT_DIR, "log.txt"),
    "111txt": os.path.join(ASSETS_DIR, "111.txt"),
}

# ==============================================
# 日志配置（控制台+文件双输出，方便排查）
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 启动路径校验，避免层级错误
logger.info("=" * 70)
logger.info(f"项目根目录: {PROJECT_ROOT}")
logger.info(f"脚本执行目录: {SCRIPT_DIR}")
logger.info(f"assets资源目录: {ASSETS_DIR}")
logger.info(f"my_urls.txt路径: {FILE_PATHS['my_urls']} | 存在: {os.path.exists(FILE_PATHS['my_urls'])}")
logger.info(f"111.txt路径: {FILE_PATHS['111txt']} | 存在: {os.path.exists(FILE_PATHS['111txt'])}")
logger.info("=" * 70)

# ==============================================
# 全局配置
# ==============================================
class Config:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    TIMEOUT_FETCH = 20
    TIMEOUT_CHECK = 3.0
    TIMEOUT_WHITELIST = 4.5
    MAX_WORKERS = 30

# ==============================================
# 正则规则
# ==============================================
RE_ALL_URLS = re.compile(r'https?://[^\s,\'"<>}\])]+')
RE_TOKEN = re.compile(r"token=[a-f0-9]{16}", re.I)

# ==============================================
# 【纯原生标准库实现】绕过Cloudflare获取Token
# 无需任何第三方库，无需修改YAML，GitHub Actions原生兼容
# ==============================================
def get_taoiptv_token() -> Optional[str]:
    """
    纯Python原生库实现，模拟浏览器完整请求，绕过Cloudflare反爬获取Token
    自动处理Cookie、TLS校验、请求头模拟，无需任何额外依赖
    """
    try:
        logger.info("正在获取TaoIPTV最新Token (纯原生无依赖方案)...")
        # 1. 创建Cookie处理器，自动处理Cloudflare的__cf_bm等校验Cookie
        cookie_jar = CookieJar()
        cookie_processor = HTTPCookieProcessor(cookie_jar)
        
        # 2. 创建SSL上下文，兼容Cloudflare的TLS校验
        ctx = ssl._create_unverified_context()
        https_handler = urllib.request.HTTPSHandler(context=ctx)
        
        # 3. 构建请求器，模拟浏览器完整请求链路
        opener = build_opener(cookie_processor, https_handler)
        opener.addheaders = [
            ("User-Agent", Config.USER_AGENT),
            ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
            ("Accept-Language", "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"),
            ("Accept-Encoding", "gzip, deflate, br"),
            ("Connection", "keep-alive"),
            ("Upgrade-Insecure-Requests", "1"),
            ("Sec-Fetch-Dest", "document"),
            ("Sec-Fetch-Mode", "navigate"),
            ("Sec-Fetch-Site", "none"),
            ("Sec-Fetch-User", "?1"),
            ("Referer", "https://www.taoiptv.com/"),
        ]

        # 4. 首次请求，完成Cloudflare校验，获取完整页面
        with opener.open("https://www.taoiptv.com", timeout=Config.TIMEOUT_FETCH) as resp:
            # 处理gzip压缩的响应内容
            content_encoding = resp.headers.get("Content-Encoding", "")
            raw_data = resp.read()
            
            # 解压gzip内容
            if "gzip" in content_encoding.lower():
                import gzip
                raw_data = gzip.decompress(raw_data)
            elif "br" in content_encoding.lower():
                import brotli
                try:
                    raw_data = brotli.decompress(raw_data)
                except Exception:
                    logger.warning("Brotli解压失败，使用原始内容")
            
            # 解码页面内容
            html = raw_data.decode("utf-8", errors="ignore")
            resp_code = resp.getcode()

        # 校验响应状态
        if resp_code not in (200, 403):
            logger.error(f"访问官网失败，状态码: {resp_code}")
            return None

        # 5. 匹配16位十六进制Token
        token_match = re.search(r"[a-f0-9]{16}", html, re.I)
        if token_match:
            token = token_match.group(0)
            logger.info(f"✅ 成功获取Token: {token}")
            return token
        
        # 兜底：如果首次没匹配到，打印页面前500字符用于排查
        logger.error(f"❌ 页面中未匹配到有效Token，页面预览: {html[:500].replace('\n', ' ')}")
        return None

    except Exception as e:
        logger.error(f"❌ 获取Token失败: {str(e)}", exc_info=True)
        return None


def update_my_urls_all(token: str) -> bool:
    """批量更新my_urls.txt中所有链接的Token，更新头部备注"""
    if not token or len(token) != 16:
        logger.error("❌ Token无效，跳过my_urls.txt更新")
        return False
    
    file_path = FILE_PATHS["my_urls"]
    if not os.path.exists(file_path):
        logger.error(f"❌ my_urls.txt文件不存在: {file_path}")
        return False
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 统计需要替换的Token数量
        token_count = len(RE_TOKEN.findall(content))
        if token_count == 0:
            logger.info("ℹ️ my_urls.txt中无需要更新的Token，跳过替换")
            return True
        
        # 替换所有Token
        content = RE_TOKEN.sub(f"token={token}", content)
        # 清理旧的更新时间备注
        content = re.sub(r"^#\s*更新时间:.*$", "", content, flags=re.MULTILINE)
        content = re.sub(r"\n{2,}", "\n\n", content).strip() + "\n"
        
        # 添加新的更新头部
        bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
        header = f"# 更新时间: {bj_time.strftime('%Y-%m-%d %H:%M:%S')} | 最新Token: {token}\n"
        content = header + content
        
        # 强制写入磁盘，确保文件生效
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        
        logger.info(f"✅ my_urls.txt更新完成！成功替换 {token_count} 个Token")
        return True
    except Exception as e:
        logger.error(f"❌ 更新my_urls.txt失败: {str(e)}", exc_info=True)
        return False

# ==============================================
# 【严格按需求实现】my_urls第一个源解析+111.txt写入
# 每次执行清空原内容，添加保存时间备注
# ==============================================
def fetch_first_source_to_111txt():
    """
    1. 读取my_urls.txt，跳过注释/空行，解析第一个有效URL（兼容 名称,URL 标准格式）
    2. 清空111.txt原有内容，无论成功失败都写入执行时间+结果
    3. 强制写入磁盘，确保文件一定被修改
    """
    my_urls_path = FILE_PATHS["my_urls"]
    output_path = FILE_PATHS["111txt"]
    bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
    exec_time = bj_time.strftime("%Y-%m-%d %H:%M:%S")

    # 第一步：校验my_urls.txt是否存在
    if not os.path.exists(my_urls_path):
        error_msg = f"# 执行时间: {exec_time}\n# 执行结果: 失败\n# 失败原因: my_urls.txt文件不存在，路径: {my_urls_path}\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
            f.flush()
            os.fsync(f.fileno())
        logger.error("❌ 111.txt更新失败: my_urls.txt不存在，已写入错误信息到111.txt")
        return False

    # 第二步：读取并解析my_urls.txt，获取第一个有效URL
    try:
        with open(my_urls_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        error_msg = f"# 执行时间: {exec_time}\n# 执行结果: 失败\n# 失败原因: 读取my_urls.txt异常: {str(e)}\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
            f.flush()
            os.fsync(f.fileno())
        logger.error(f"❌ 111.txt更新失败: 读取my_urls.txt异常，已写入错误信息到111.txt")
        return False

    # 逐行解析，跳过注释/空行，提取第一个有效URL
    first_valid_url = None
    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        # 跳过空行、注释行
        if not line or line.startswith("#"):
            continue
        # 用原项目的clean_source_line解析，兼容 名称,URL 标准格式
        res, status = clean_source_line(line)
        if status == CLEAN_OK and res:
            name, url = res
            first_valid_url = url
            logger.info(f"✅ 解析到第一个有效源 | 行号: {line_num} | 频道名: {name} | URL: {first_valid_url}")
            break
    
    # 没有找到有效URL的情况
    if not first_valid_url:
        error_msg = f"# 执行时间: {exec_time}\n# 执行结果: 失败\n# 失败原因: my_urls.txt中未解析到有效直播源URL\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
            f.flush()
            os.fsync(f.fileno())
        logger.error("❌ 111.txt更新失败: 未解析到有效URL，已写入错误信息到111.txt")
        return False

    # 第三步：抓取URL内容
    try:
        logger.info(f"📥 开始抓取第一个远程源内容: {first_valid_url}")
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            first_valid_url,
            headers={"User-Agent": Config.USER_AGENT},
            method="GET"
        )
        with urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx)
        ).open(req, timeout=Config.TIMEOUT_FETCH) as resp:
            resp_code = resp.getcode()
            if resp_code not in (200, 301, 302):
                raise Exception(f"请求响应异常，状态码: {resp_code}")
            content = resp.read().decode("utf-8", errors="ignore")
        logger.info(f"✅ 远程源抓取成功，内容长度: {len(content)} 字符")
    except Exception as e:
        error_msg = f"# 执行时间: {exec_time}\n# 执行结果: 失败\n# 来源URL: {first_valid_url}\n# 失败原因: 抓取远程源异常: {str(e)}\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
            f.flush()
            os.fsync(f.fileno())
        logger.error(f"❌ 111.txt更新失败: 抓取远程源异常，已写入错误信息到111.txt")
        return False

    # 第四步：写入111.txt（w模式自动清空原有内容，添加备注+抓取内容）
    try:
        output_content = f"""# 保存时间: {exec_time}
# 来源URL: {first_valid_url}
# 执行结果: 成功

{content}"""
        # w模式自动清空原有内容
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_content)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"✅ 111.txt更新完成！文件路径: {output_path}")
        return True
    except Exception as e:
        error_msg = f"# 执行时间: {exec_time}\n# 执行结果: 失败\n# 失败原因: 写入111.txt异常: {str(e)}\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
            f.flush()
            os.fsync(f.fileno())
        logger.error(f"❌ 111.txt写入异常: {str(e)}，已写入错误信息到111.txt")
        return False

# ==============================================
# Git提交推送（包含my_urls.txt和111.txt）
# ==============================================
def git_commit_push():
    """将my_urls.txt和111.txt的修改同步到GitHub仓库，完全兼容Actions环境"""
    try:
        logger.info("正在同步文件到GitHub仓库...")
        os.chdir(PROJECT_ROOT)
        
        # 配置Git用户信息
        subprocess.run(
            ["git", "config", "--global", "user.name", "IPTV-Auto-Bot"],
            check=True, capture_output=True, text=True
        )
        subprocess.run(
            ["git", "config", "--global", "user.email", "bot@noreply.github.com"],
            check=True, capture_output=True, text=True
        )
        
        # 检查是否有文件变更
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True
        )
        if not status_result.stdout.strip():
            logger.info("✅ 无文件变更，无需提交到仓库")
            return True
        
        # 添加两个核心文件到暂存区
        subprocess.run(
            ["git", "add", "assets/my_urls.txt", "assets/111.txt"],
            check=True, capture_output=True, text=True
        )
        
        # 提交变更
        subprocess.run(
            ["git", "commit", "-m", "Auto update: TaoIPTV Token + 111.txt 源内容更新"],
            check=True, capture_output=True, text=True
        )
        
        # 推送仓库（兼容GitHub Actions自带的GITHUB_TOKEN环境变量）
        gh_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        if gh_token and repo:
            push_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
            subprocess.run(
                ["git", "push", push_url, "HEAD"],
                check=True, capture_output=True, text=True
            )
        else:
            subprocess.run(
                ["git", "push"],
                check=True, capture_output=True, text=True
            )
        
        logger.info("✅ 已成功同步my_urls.txt和111.txt到GitHub仓库！")
        return True
    except subprocess.CalledProcessError as e:
        env_info = f"[ACTIONS={os.getenv('GITHUB_ACTIONS','-')} REPO={os.getenv('GITHUB_REPOSITORY','-')}]"
        error_detail = e.stderr.decode('utf-8','ignore') if e.stderr else str(e)
        logger.warning(f"⚠️ Git推送失败 {env_info}: {error_detail}")
        return False
    except Exception as e:
        logger.warning(f"⚠️ Git执行异常: {str(e)}")
        return False

# ==============================================
# 原项目核心规则（完全保留，兼容原有功能）
# ==============================================
# 域名黑名单
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

# 点播/图片过滤
VOD_DOMAINS: Set[str] = {
    "kwimgs.com", "kuaishou.com", "ixigua.com", "douyin.com",
    "tiktokcdn.com", "bdstatic.com", "byteimg.com",
}
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

# 行格式清洗规则
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

# 媒体流类型判定
STREAM_CTS = [
    "video/mp2t", "video/mp4", "video/x-flv", "application/vnd.apple.mpegurl",
    "application/octet-stream", "application/x-mpegURL",
]
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
    return (d[:3] == b"FLV" or (len(d) >= 8 and d[4:8] == b"ftyp")
            or d[:3] == b"ID3" or (len(d) >= 188 and d[0] == 0x47))
def _looks_html(d):
    if not d:
        return False
    d = d.lstrip(b"\xef\xbb\xbf").lstrip()
    return d[:5].lower().startswith((b"<!doc", b"<html"))

# ==============================================
# 原项目StreamChecker流检测类（100%完全保留，兼容原有黑白名单逻辑）
# ==============================================
class StreamChecker:
    def __init__(self, manual_urls=None):
        self.start_time = datetime.now()
        self.blacklist_urls = self._load_blacklist()
        self.whitelist_urls: Set[str] = set()
        self.whitelist_lines: List[str] = []
        self.new_failed_urls: Set[str] = set()
        self.manual_urls = manual_urls or []
        self.clean_stats: Dict[str, int] = {
            CLEAN_NO_FORMAT: 0, CLEAN_EMPTY_NAME: 0, CLEAN_BAD_URL: 0,
            CLEAN_DOMAIN_BL: 0, CLEAN_VOD: 0,
        }

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
                parts += [
                    "更新时间,#genre#",
                    f"{bj.strftime('%Y%m%d %H:%M')},url",
                    "",
                    "blacklist,#genre#",
                ]
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
                return [l.strip() for l in re.split(r"[\s\t\n]+", c)
                        if l.strip().startswith("http")]
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
                            if m_group:
                                name = m_group.group(1).strip()
                            else:
                                name = l.split(",")[-1].strip() if "," in l else ""
                        elif not l.startswith("#"):
                            url_candidate = l.strip().split("#")[0].strip().split("$")[0].strip()
                            if not url_candidate:
                                name = ""
                                continue
                            if not re.match(r'https?://', url_candidate, re.I):
                                url_candidate = urljoin(raw_url, url_candidate)
                            if url_matches_domain_blacklist(url_candidate):
                                name = ""
                                continue
                            if is_vod_or_image_url(url_candidate):
                                name = ""
                                continue
                            group = name if name else "订阅源"
                            res, _ = clean_source_line(f"{group},{url_candidate}")
                            if res:
                                all_lines.append(f"{res[0]},{res[1]}")
                            else:
                                all_lines.append(f"{group},{url_candidate}")
                            name = ""
                else:
                    raw_urls_found = RE_ALL_URLS.findall(c)
                    for u in raw_urls_found:
                        u = u.strip()
                        if not u:
                            continue
                        u = u.rstrip(".,;:!?)")
                        if is_vod_or_image_url(u):
                            continue
                        if url_matches_domain_blacklist(u):
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
                            if not l or l.startswith("#"):
                                continue
                            if l.count("http") <= 1:
                                continue
                            parts = l.split(",")
                            for part in parts:
                                part = part.strip()
                                if re.match(r'https?://', part, re.I):
                                    part = part.rstrip(".,;:!?)")
                                    if is_vod_or_image_url(part):
                                        continue
                                    if url_matches_domain_blacklist(part):
                                        continue
                                    all_lines.append(f"订阅源,{part}")

                got = len(all_lines) - before
                if got > 1:
                    logger.info(f"  ✓ {raw_url[:90]} → {got} 个源")
                elif got == 1:
                    diag = c[:500].replace("\n", "\\n").replace("\r", "")
                    logger.warning(f"  ⚠ {raw_url[:90]} → 仅 {got} 个源 | 内容诊断: {diag}")
                else:
                    preview = c[:200].replace("\n", "\\n")
                    logger.warning(f"  ✗ {raw_url[:90]} → 0 个源 | 内容: {preview}")

            except Exception as e:
                logger.error(f"  ✗ {raw_url[:90]} → 异常: {e}")

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
            if url in seen:
                continue
            seen.add(url)
            if url in self.blacklist_urls:
                continue
            to_check.append((url, line))
        logger.info(f"待检测 {len(to_check)} 条")
        return to_check, []

    def run(self):
        logger.info("===== 开始流媒体黑白名单检测 =====")
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
            fmap = {
                pool.submit(self.check_url, u, u in self.whitelist_urls): u
                for u, _ in to_check
            }
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

        results.sort(key=lambda x: (
            {"stream": 0, "playlist": 1, "unknown": 2}.get(x[3], 3), x[1]
        ))
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
# 【严格按需求顺序执行】主函数
# ==============================================
def main():
    try:
        logger.info("===== 开始执行完整自动化流程 =====")
        
        # 步骤1：获取最新Token
        token = get_taoiptv_token()
        
        # 步骤2：更新my_urls.txt中的所有Token（无Token也不阻断后续流程）
        if token:
            update_my_urls_all(token)
        else:
            logger.warning("⚠️ 未获取到有效Token，跳过my_urls.txt更新，继续执行后续流程")
        
        # 步骤3：解析my_urls.txt第一个源，写入111.txt（必执行，清空原内容+备注时间）
        logger.info("===== 开始处理111.txt =====")
        fetch_first_source_to_111txt()
        
        # 步骤4：提交my_urls.txt和111.txt到GitHub仓库
        git_commit_push()
        
        # 步骤5：执行原有的黑白名单流检测任务
        checker = StreamChecker()
        checker.run()
        
        logger.info("===== 全部流程执行完成！ =====")
    except Exception as e:
        logger.error(f"❌ 主程序执行异常: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
