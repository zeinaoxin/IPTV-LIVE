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

# ==============================================     豆包
# 工具函数：过滤不可打印字符，解决乱码核心问题
# ==============================================
def clean_printable_text(text: str, max_len: int = None) -> str:
    """过滤文本中的不可打印字符、控制字符，可选限制长度，避免乱码"""
    # 保留可打印ASCII、中文、常用标点，过滤控制字符、二进制残留
    cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    # 限制最大长度，避免大量内容写入日志导致文件异常
    if max_len and len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + " [内容截断]"
    return cleaned

def safe_decode_content(raw_data: bytes) -> str:
    """安全解码网页内容，兼容多种编码，避免硬编码UTF-8导致的乱码"""
    # 优先尝试UTF-8解码
    try:
        return raw_data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    #  fallback 中文常用编码GBK/GB2312
    try:
        return raw_data.decode("gbk")
    except UnicodeDecodeError:
        pass
    #  最终容错：忽略错误，保证不会生成乱码
    return raw_data.decode("utf-8", errors="replace")

# ==============================================
# 路径配置（固定项目层级，兼容原项目结构）
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
# 日志系统全面优化（解决乱码核心问题）
# ==============================================
class SafeLogFilter(logging.Filter):
    """日志过滤器，自动过滤所有不可打印字符，避免日志乱码"""
    def filter(self, record):
        record.msg = clean_printable_text(record.msg)
        return True

# 日志配置：强制UTF-8编码，纯文本格式，无特殊符号，强制落盘
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
# 给所有日志处理器添加乱码过滤
logger = logging.getLogger(__name__)
for handler in logger.handlers:
    handler.addFilter(SafeLogFilter())
# 启动时强制刷新日志
logger.info("=" * 70)
logger.info("项目根目录: %s", PROJECT_ROOT)
logger.info("脚本执行目录: %s", SCRIPT_DIR)
logger.info("assets资源目录: %s", ASSETS_DIR)
logger.info("my_urls.txt路径: %s | 存在: %s", FILE_PATHS["my_urls"], os.path.exists(FILE_PATHS["my_urls"]))
logger.info("111.txt路径: %s | 存在: %s", FILE_PATHS["111txt"], os.path.exists(FILE_PATHS["111txt"]))
logger.info("log.txt路径: %s", FILE_PATHS["log"])
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
# 优化版：Token获取（兼容Cloudflare，无乱码日志）
# ==============================================
def get_taoiptv_token() -> Optional[str]:
    """纯Python原生库实现，绕过Cloudflare获取Token，优化编码和日志输出"""
    try:
        logger.info("正在获取TaoIPTV最新Token (纯原生无依赖方案)")
        # 1. 创建Cookie处理器，自动处理Cloudflare校验Cookie
        cookie_jar = CookieJar()
        cookie_processor = HTTPCookieProcessor(cookie_jar)
        
        # 2. 创建SSL上下文，兼容Cloudflare TLS校验
        ctx = ssl._create_unverified_context()
        https_handler = urllib.request.HTTPSHandler(context=ctx)
        
        # 3. 构建请求器，模拟浏览器完整请求头，提升绕过成功率
        opener = build_opener(cookie_processor, https_handler)
        opener.addheaders = [
            ("User-Agent", Config.USER_AGENT),
            ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
            ("Accept-Language", "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"),
            ("Accept-Encoding", "gzip, deflate"),
            ("Connection", "keep-alive"),
            ("Upgrade-Insecure-Requests", "1"),
            ("Sec-Fetch-Dest", "document"),
            ("Sec-Fetch-Mode", "navigate"),
            ("Sec-Fetch-Site", "none"),
            ("Sec-Fetch-User", "?1"),
            ("Referer", "https://www.taoiptv.com/"),
        ]

        # 4. 发起请求，安全解码内容
        with opener.open("https://www.taoiptv.com", timeout=Config.TIMEOUT_FETCH) as resp:
            content_encoding = resp.headers.get("Content-Encoding", "")
            raw_data = resp.read()
            
            # 解压压缩内容（移除br压缩，避免依赖第三方库）
            if "gzip" in content_encoding.lower():
                import gzip
                raw_data = gzip.decompress(raw_data)
            
            # 安全解码，避免乱码
            html = safe_decode_content(raw_data)
            resp_code = resp.getcode()

        # 校验响应状态
        if resp_code not in (200, 403):
            logger.error("访问官网失败，状态码: %s", resp_code)
            return None

        # 5. 匹配16位十六进制Token
        token_match = re.search(r"[a-f0-9]{16}", html, re.I)
        if token_match:
            token = token_match.group(0)
            logger.info("[OK] 成功获取Token: %s", token)
            return token
        
        # 安全预览页面内容，避免乱码写入日志
        preview_content = clean_printable_text(html, max_len=300)
        logger.error("[ERROR] 页面中未匹配到有效Token，页面预览: %s", preview_content)
        return None

    except Exception as e:
        logger.error("[ERROR] 获取Token失败: %s", str(e), exc_info=True)
        return None


def update_my_urls_all(token: str) -> bool:
    """批量更新my_urls.txt中所有链接的Token，优化编码和容错"""
    if not token or len(token) != 16:
        logger.error("[ERROR] Token无效，跳过my_urls.txt更新")
        return False
    
    file_path = FILE_PATHS["my_urls"]
    if not os.path.exists(file_path):
        logger.error("[ERROR] my_urls.txt文件不存在: %s", file_path)
        return False
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        token_count = len(RE_TOKEN.findall(content))
        if token_count == 0:
            logger.info("[INFO] my_urls.txt中无需要更新的Token，跳过替换")
            return True
        
        # 替换所有Token
        content = RE_TOKEN.sub(f"token={token}", content)
        # 清理旧备注
        content = re.sub(r"^#\s*更新时间:.*$", "", content, flags=re.MULTILINE)
        content = re.sub(r"\n{2,}", "\n\n", content).strip() + "\n"
        
        # 添加新头部
        bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
        header = f"# 更新时间: {bj_time.strftime('%Y-%m-%d %H:%M:%S')} | 最新Token: {token}\n"
        content = header + content
        
        # 强制写入磁盘，确保文件完整
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        
        logger.info("[OK] my_urls.txt更新完成！成功替换 %s 个Token", token_count)
        return True
    except Exception as e:
        logger.error("[ERROR] 更新my_urls.txt失败: %s", str(e), exc_info=True)
        return False

# ==============================================
# 优化版：111.txt处理逻辑（解决乱码，兼容所有URL格式）
# ==============================================
def fetch_first_source_to_111txt():
    """
    优化版解析逻辑：
    1. 双重解析兼容「频道名,URL」和纯URL格式
    2. 安全解码网页内容，过滤乱码字符
    3. 强制清空原内容，写入完整备注+内容
    4. 全链路日志，无乱码输出
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
        logger.error("[ERROR] 111.txt更新失败: my_urls.txt不存在，已写入错误信息到111.txt")
        return False

    # 第二步：读取my_urls.txt内容，安全预览
    try:
        with open(my_urls_path, "r", encoding="utf-8") as f:
            raw_content = f.read()
            lines = raw_content.splitlines()
        # 安全预览文件内容，避免乱码
        preview_content = clean_printable_text(raw_content, max_len=300)
        logger.info("[INFO] my_urls.txt内容预览: %s", preview_content)
    except Exception as e:
        error_msg = f"# 执行时间: {exec_time}\n# 执行结果: 失败\n# 失败原因: 读取my_urls.txt异常: {str(e)}\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
            f.flush()
            os.fsync(f.fileno())
        logger.error("[ERROR] 111.txt更新失败: 读取my_urls.txt异常", exc_info=True)
        return False

    # 第三步：逐行解析，双重逻辑提取第一个有效URL
    first_valid_url = None
    valid_line_num = None
    for line_num, line in enumerate(lines, 1):
        line_strip = line.strip()
        # 跳过空行、注释行
        if not line_strip or line_strip.startswith("#"):
            logger.info("[INFO] 跳过第%s行: 空行/注释行", line_num)
            continue
        
        line_preview = clean_printable_text(line_strip, max_len=100)
        logger.info("[INFO] 正在解析第%s行: %s", line_num, line_preview)
        
        # 解析逻辑1：优先用原项目的clean_source_line（兼容标准格式）
        res, status = clean_source_line(line_strip)
        if status == CLEAN_OK and res:
            name, url = res
            first_valid_url = url
            valid_line_num = line_num
            logger.info("[OK] 第%s行解析成功（标准格式） | 频道名: %s | URL: %s", line_num, name, first_valid_url)
            break
        
        # 解析逻辑2：兜底正则提取纯URL
        url_match = RE_ALL_URLS.search(line_strip)
        if url_match:
            candidate_url = url_match.group(0).rstrip(".,;:!?)")
            # 过滤黑名单、点播/图片链接，和原项目逻辑一致
            if not url_matches_domain_blacklist(candidate_url) and not is_vod_or_image_url(candidate_url):
                first_valid_url = candidate_url
                valid_line_num = line_num
                logger.info("[OK] 第%s行解析成功（纯URL格式） | URL: %s", line_num, first_valid_url)
                break
            else:
                logger.warning("[WARN] 第%s行提取到URL，但在黑名单/是点播图片链接，跳过", line_num)
    
    # 未找到有效URL的情况，写入详细排查信息
    if not first_valid_url:
        error_msg = f"""# 执行时间: {exec_time}
# 执行结果: 失败
# 失败原因: my_urls.txt中未解析到有效直播源URL
# 排查说明:
# 1. 请确保my_urls.txt中有非注释、非空的有效行
# 2. 支持格式1: 频道名,https://xxx.xxx/xxx.m3u8
# 3. 支持格式2: 直接一行纯URL: https://xxx.xxx/xxx.m3u8
# 4. 请确保URL不在黑名单、不是点播/图片链接
# 文件内容预览:
{clean_printable_text(raw_content, max_len=1000)}
"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
            f.flush()
            os.fsync(f.fileno())
        logger.error("[ERROR] 111.txt更新失败: 未解析到有效URL，已写入详细排查信息到111.txt")
        return False

    # 第四步：安全抓取URL内容，避免乱码
    try:
        logger.info("[INFO] 开始抓取第%s行的远程源内容: %s", valid_line_num, first_valid_url)
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
            raw_data = resp.read()
            # 安全解码，避免乱码
            content = safe_decode_content(raw_data)
        logger.info("[OK] 远程源抓取成功，内容长度: %s 字符", len(content))
    except Exception as e:
        error_msg = f"""# 执行时间: {exec_time}
# 执行结果: 失败
# 来源URL: {first_valid_url}
# 来源行号: 第{valid_line_num}行
# 失败原因: 抓取远程源异常: {str(e)}
"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
            f.flush()
            os.fsync(f.fileno())
        logger.error("[ERROR] 111.txt更新失败: 抓取远程源异常", exc_info=True)
        return False

    # 第五步：写入111.txt（w模式自动清空原有内容，强制UTF-8编码）
    try:
        output_content = f"""# 保存时间: {exec_time}
# 来源行号: 第{valid_line_num}行
# 来源URL: {first_valid_url}
# 执行结果: 成功

{content}"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_content)
            f.flush()
            os.fsync(f.fileno())
        logger.info("[OK] 111.txt更新完成！文件路径: %s", output_path)
        return True
    except Exception as e:
        error_msg = f"# 执行时间: {exec_time}\n# 执行结果: 失败\n# 失败原因: 写入111.txt异常: {str(e)}\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
            f.flush()
            os.fsync(f.fileno())
        logger.error("[ERROR] 111.txt写入异常", exc_info=True)
        return False

# ==============================================
# Git提交推送（兼容GitHub Actions）
# ==============================================
def git_commit_push():
    """同步my_urls.txt和111.txt到GitHub仓库，优化日志输出"""
    try:
        logger.info("[INFO] 正在同步文件到GitHub仓库...")
        os.chdir(PROJECT_ROOT)
        
        # 配置Git用户
        subprocess.run(
            ["git", "config", "--global", "user.name", "IPTV-Auto-Bot"],
            check=True, capture_output=True, text=True
        )
        subprocess.run(
            ["git", "config", "--global", "user.email", "bot@noreply.github.com"],
            check=True, capture_output=True, text=True
        )
        
        # 检查文件变更
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True
        )
        if not status_result.stdout.strip():
            logger.info("[OK] 无文件变更，无需提交")
            return True
        
        # 添加文件到暂存区
        subprocess.run(
            ["git", "add", "assets/my_urls.txt", "assets/111.txt"],
            check=True, capture_output=True, text=True
        )
        
        # 提交变更
        subprocess.run(
            ["git", "commit", "-m", "Auto update: TaoIPTV Token + 111.txt 源内容更新"],
            check=True, capture_output=True, text=True
        )
        
        # 推送仓库
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
        
        logger.info("[OK] 已成功同步到GitHub仓库！")
        return True
    except subprocess.CalledProcessError as e:
        env_info = f"[ACTIONS={os.getenv('GITHUB_ACTIONS','-')} REPO={os.getenv('GITHUB_REPOSITORY','-')}]"
        error_detail = e.stderr.decode('utf-8','ignore') if e.stderr else str(e)
        logger.warning("[WARN] Git推送失败 %s: %s", env_info, clean_printable_text(error_detail, max_len=500))
        return False
    except Exception as e:
        logger.warning("[WARN] Git执行异常: %s", str(e))
        return False

# ==============================================
# 原项目核心规则（完全保留，兼容原有逻辑）
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
# 原项目StreamChecker流检测类（完全保留，优化乱码日志）
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
                logger.info("[INFO] 加载URL黑名单: %s 条", len(bl))
        except Exception as e:
            logger.error("[ERROR] 加载黑名单失败: %s", str(e))
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
            logger.info("[INFO] 黑名单更新，新增%s条", len(self.new_failed_urls))
        except Exception as e:
            logger.error("[ERROR] 保存黑名单失败: %s", str(e))

    def read_file(self, path, split_by_space=False):
        try:
            with open(path, "r", encoding="utf-8") as f:
                c = f.read()
            if split_by_space:
                return [l.strip() for l in re.split(r"[\s\t\n]+", c)
                        if l.strip().startswith("http")]
            return [l.strip() for l in c.splitlines() if l.strip()]
        except Exception as e:
            logger.warning("[WARN] 读取失败 %s: %s", path, str(e))
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
                    c = safe_decode_content(r.read())

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
                    logger.info("[INFO]   ✓ %s → %s 个源", raw_url[:90], got)
                elif got == 1:
                    diag_content = clean_printable_text(c, max_len=500).replace('\n', '\\n')
                    logger.warning("[WARN]   ⚠ %s → 仅 %s 个源 | 内容诊断: %s", raw_url[:90], got, diag_content)
                else:
                    preview_content = clean_printable_text(c, max_len=200).replace('\n', '\\n')
                    logger.warning("[WARN]   ✗ %s → 0 个源 | 内容: %s", raw_url[:90], preview_content)

            except Exception as e:
                logger.error("[ERROR]   ✗ %s → 异常: %s", raw_url[:90], str(e))

        return all_lines

    def load_whitelist(self):
        for line in self.read_file(FILE_PATHS["whitelist_manual"]):
            if line.startswith("#"):
                continue
            res, _ = clean_source_line(line)
            if res:
                self.whitelist_urls.add(res[1])
                self.whitelist_lines.append(line)
        logger.info("[INFO] 手动白名单: %s 个频道", len(self.whitelist_urls))

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
        logger.info("[INFO] 待检测 %s 条", len(to_check))
        return to_check, []

    def run(self):
        logger.info("[INFO] ===== 开始流媒体黑白名单检测 =====")
        self.load_whitelist()
        lines = []

        urls = self.read_file(FILE_PATHS["urls"], split_by_space=True)
        if urls:
            logger.info("[INFO] 开始拉取 urls.txt 中的 %s 个节点", len(urls))
            fetched = self.fetch_remote(urls)
            logger.info("[INFO] urls.txt 完成：%s 个节点 → %s 个源", len(urls), len(fetched))
            lines.extend(fetched)
        else:
            logger.warning("[WARN] 未找到 urls.txt")

        my_urls = self.read_file(FILE_PATHS["my_urls"], split_by_space=True)
        if my_urls:
            logger.info("[INFO] 开始拉取 my_urls.txt 中的 %s 个节点", len(my_urls))
            fetched = self.fetch_remote(my_urls)
            logger.info("[INFO] my_urls.txt 完成：%s 个节点 → %s 个源", len(my_urls), len(fetched))
            lines.extend(fetched)
        else:
            logger.warning("[WARN] 未找到 my_urls.txt")

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
                    logger.error("[ERROR] 检测异常 %s: %s", url, str(e))
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
            "[INFO] ===== 检测完成 | 总计:%s | 流:%s | 列表:%s | 未知:%s | 超时:%s | 耗时:%ss =====",
            total, stream, playlist, unknown, timeout, elapsed
        )

# ==============================================
# 主函数（严格执行顺序，优化异常容错）
# ==============================================
def main():
    try:
        logger.info("[INFO] ===== 开始执行完整自动化流程 =====")
        
        # 1. 获取最新Token
        token = get_taoiptv_token()
        
        # 2. 更新my_urls.txt
        if token:
            update_my_urls_all(token)
        else:
            logger.warning("[WARN] 未获取到有效Token，跳过my_urls.txt更新，继续执行后续流程")
        
        # 3. 处理111.txt
        logger.info("[INFO] ===== 开始处理111.txt =====")
        fetch_first_source_to_111txt()
        
        # 4. Git提交
        git_commit_push()
        
        # 5. 黑白名单检测
        checker = StreamChecker()
        checker.run()
        
        # 强制刷新日志，确保文件完整
        for handler in logger.handlers:
            handler.flush()
        logger.info("[INFO] ===== 全部流程执行完成！ =====")
    except Exception as e:
        logger.error("[ERROR] 主程序执行异常: %s", str(e), exc_info=True)
        # 异常时也强制刷新日志
        for handler in logger.handlers:
            handler.flush()
        sys.exit(1)

if __name__ == "__main__":
    main()
