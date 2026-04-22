import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse, quote, unquote, urljoin, parse_qs, urlencode, urlunparse
import socket
import ssl
import re
from typing import List, Tuple, Set, Dict, Optional
import logging
import sys
import pyperclip
import subprocess  # 用于JavaScript执行

# ===================== 文件路径 =====================           元宝
def get_file_paths():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.dirname(current_dir)
    return {
        "urls": os.path.join(assets_dir, 'urls.txt'),
        "my_urls": os.path.join(assets_dir, 'my_urls.txt'),
        "blacklist_auto": os.path.join(current_dir, 'blacklist_auto.txt'),
        "whitelist_manual": os.path.join(current_dir, 'whitelist_manual.txt'),
        "whitelist_auto": os.path.join(current_dir, 'whitelist_auto.txt'),
        "whitelist_respotime": os.path.join(current_dir, 'whitelist_respotime.txt'),
        "log": os.path.join(current_dir, 'log.txt'),
    }

FILE_PATHS = get_file_paths()

# ===================== 日志 =====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===================== 全局配置 =====================
class Config:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    TIMEOUT_FETCH = 15       
    TIMEOUT_CHECK = 3.0
    TIMEOUT_WHITELIST = 4.5
    TIMEOUT_CONNECT = 1.5
    TIMEOUT_READ = 2.0
    MAX_WORKERS = 30
    FIRST_CHUNK_BYTES = 4096
    MIN_FIRST_CHUNK_FOR_STREAM = 256
    HLS_SAMPLE_SEGMENTS = 2
    HLS_SEGMENT_TIMEOUT = 2.5
    TOKEN_SITE_URL = "https://www.taoiptv.com"  # 获取token的网站
    TOKEN_UPDATE_INTERVAL = 3600  # token更新间隔（秒）

# ===================== 域名黑名单 =====================
DOMAIN_BLACKLIST: Set[str] = {
    "iptv.catvod.com",
    "dd.ddzb.fun",
    "goodiptv.club",
    "jiaojirentv.top",
    "alist.xicp.fun",
    "rihou.cc",
    "php.jdshipin.com",
    "t.freetv.fun",
    "stream1.freetv.fun",
    "hlsztemgsplive.miguvideo",
    "stream2.freetv.fun",
}

logger.info(f"域名黑名单: 仅使用硬编码绝对坏域名 {len(DOMAIN_BLACKLIST)} 个")

def url_matches_domain_blacklist(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        if not host:
            return False
        host_lower = host.lower()
        for d in DOMAIN_BLACKLIST:
            if host_lower == d or host_lower.endswith("." + d):
                return True
    except Exception:
        pass
    return False

# ===================== 点播/图片过滤 =====================
VOD_DOMAINS: Set[str] = {
    "kwimgs.com", "kuaishou.com", "ixigua.com", "douyin.com", "tiktokcdn.com",
    "bdstatic.com", "byteimg.com", "a.kwimgs.com", "txmov2.a.kwimgs.com",
    "alimov2.a.kwimgs.com", "p6-dy.byteimg.com"
}
VOD_EXTENSIONS: Set[str] = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".rmvb"}
IMAGE_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}

def is_vod_or_image_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        for vd in VOD_DOMAINS:
            if host == vd or host.endswith("." + vd):
                return True
        path = urlparse(url).path.lower()
        for ext in IMAGE_EXTENSIONS:
            if path.endswith(ext):
                return True
        for ext in VOD_EXTENSIONS:
            if path.endswith(ext):
                return True
    except Exception:
        pass
    return False

# ===================== Token获取与URL更新 =====================
def get_token_from_website() -> Optional[str]:
    """
    从taoiptv.com网站获取最新的token
    通过模拟浏览器执行JavaScript来获取token
    """
    try:
        logger.info(f"正在访问网站获取token: {Config.TOKEN_SITE_URL}")
        
        # 尝试从JavaScript执行中获取token
        # 这里我们使用一个简单的JavaScript模拟
        token = get_token_from_javascript_execution()
        
        if token and is_valid_token(token):
            logger.info(f"成功获取token: {token[:8]}...")
            return token
        else:
            logger.warning("从JavaScript执行中未获取到有效token")
            
        # 尝试从剪贴板获取
        try:
            clipboard_content = pyperclip.paste()
            if clipboard_content:
                # 在剪贴板内容中查找16位十六进制token
                token_match = re.search(r'[a-fA-F0-9]{16}', clipboard_content)
                if token_match:
                    token = token_match.group(0)
                    if is_valid_token(token):
                        logger.info(f"从剪贴板中找到token: {token[:8]}...")
                        return token
        except Exception as e:
            logger.debug(f"剪贴板检查失败: {e}")
        
        logger.error("无法从网站获取token")
        return None
        
    except Exception as e:
        logger.error(f"获取token失败: {e}")
        return None

def get_token_from_javascript_execution() -> Optional[str]:
    """
    模拟JavaScript执行来获取token
    由于网站可能需要JavaScript交互，我们通过模拟点击行为获取
    """
    try:
        # 尝试从网页HTML中提取token
        req = urllib.request.Request(
            Config.TOKEN_SITE_URL,
            headers={
                "User-Agent": Config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1"
            }
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            
            # 查找16位十六进制token的模式
            token_patterns = [
                r'token[=:\s]+([a-fA-F0-9]{16})',  # token=xxxx 或 token: xxxx
                r'"token"\s*:\s*"([a-fA-F0-9]{16})"',  # JSON格式
                r"'token'\s*:\s*'([a-fA-F0-9]{16})'",  # 单引号JSON
                r'token\s*=\s*"([a-fA-F0-9]{16})"',  # JavaScript变量
                r'token\s*=\s*\'([a-fA-F0-9]{16})\'',  # 单引号JavaScript
                r'value\s*=\s*["\']([a-fA-F0-9]{16})["\']',  # 输入框值
                r'data-token\s*=\s*["\']([a-fA-F0-9]{16})["\']',  # 数据属性
            ]
            
            for pattern in token_patterns:
                match = re.search(pattern, html_content, re.IGNORECASE)
                if match:
                    token = match.group(1)
                    if is_valid_token(token):
                        return token
            
            # 尝试在JavaScript代码块中查找
            js_blocks = re.findall(r'<script[^>]*>([\s\S]*?)</script>', html_content)
            for js in js_blocks:
                # 查找token赋值
                token_matches = re.findall(r'[^a-zA-Z0-9]([a-fA-F0-9]{16})[^a-zA-Z0-9]', js)
                for token in token_matches:
                    if is_valid_token(token):
                        return token
            
            return None
            
    except Exception as e:
        logger.error(f"JavaScript执行模拟失败: {e}")
        return None

def is_valid_token(token: str) -> bool:
    """验证token是否为有效的16位十六进制字符串"""
    return bool(token and len(token) == 16 and re.match(r'^[a-fA-F0-9]{16}$', token))

def update_urls_with_single_token(token: str) -> bool:
    """
    用单个token统一更新my_urls.txt中的所有URL
    返回是否成功更新
    """
    if not is_valid_token(token):
        logger.error(f"无效的token格式: {token}")
        return False
    
    try:
        my_urls_path = FILE_PATHS["my_urls"]
        if not os.path.exists(my_urls_path):
            logger.warning(f"文件不存在: {my_urls_path}")
            return False
        
        with open(my_urls_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if not content.strip():
            logger.warning(f"文件为空: {my_urls_path}")
            return False
        
        # 按行处理
        lines = content.split('\n')
        updated_lines = []
        update_count = 0
        
        for line in lines:
            original_line = line
            line = line.strip()
            
            # 如果不是以http开头的行，直接保留
            if not line or not line.startswith('http'):
                updated_lines.append(original_line)
                continue
            
            # 解析URL
            try:
                parsed = urlparse(line)
                query_dict = parse_qs(parsed.query)
                
                # 检查是否有token参数
                if 'token' in query_dict:
                    old_token = query_dict['token'][0]
                    if old_token == token:
                        # token相同，无需更新
                        updated_lines.append(original_line)
                        continue
                
                # 更新或添加token参数
                query_dict['token'] = [token]
                new_query = urlencode(query_dict, doseq=True)
                
                # 重建URL
                new_url = urlunparse((
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    new_query,
                    parsed.fragment
                ))
                
                updated_lines.append(new_url)
                update_count += 1
                
            except Exception as e:
                logger.warning(f"处理URL失败 '{line[:50]}...': {e}")
                updated_lines.append(original_line)
        
        if update_count > 0:
            # 保存更新后的文件
            with open(my_urls_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(updated_lines))
            
            logger.info(f"成功更新 {update_count} 个URL的token为: {token[:8]}...")
            
            # 验证更新结果
            with open(my_urls_path, 'r', encoding='utf-8') as f:
                updated_content = f.read()
            
            # 统计新的token使用情况
            token_counts = {}
            for line in updated_content.split('\n'):
                if 'token=' in line:
                    match = re.search(r'token=([a-fA-F0-9]{16})', line)
                    if match:
                        t = match.group(1)
                        token_counts[t] = token_counts.get(t, 0) + 1
            
            if len(token_counts) == 1 and token in token_counts:
                logger.info(f"所有 {token_counts[token]} 个URL已统一使用token: {token[:8]}...")
            else:
                logger.warning(f"警告: 文件中仍有 {len(token_counts)} 个不同的token: {token_counts}")
            
            return True
        else:
            logger.info("没有需要更新的URL，所有URL已使用最新的token")
            return True
            
    except Exception as e:
        logger.error(f"更新URL失败: {e}")
        return False

def refresh_token_and_update_urls() -> bool:
    """
    主函数：获取token并更新所有URL
    只获取一次token，然后统一应用到所有URL
    """
    logger.info("=== 开始token自动更新流程 ===")
    
    # 获取新token
    new_token = get_token_from_website()
    
    if not new_token:
        logger.error("无法获取新token，跳过URL更新")
        return False
    
    # 用新token更新所有URL
    success = update_urls_with_single_token(new_token)
    
    if success:
        logger.info("=== token更新完成 ===")
    else:
        logger.error("=== token更新失败 ===")
    
    return success

# ===================== 行格式清洗 =====================
# ... (保留原有的清洗函数，与之前相同)
CLEAN_OK = "ok"
CLEAN_NO_FORMAT = "no_format"
CLEAN_EMPTY_NAME = "empty_name"
CLEAN_BAD_URL = "bad_url"
CLEAN_DOMAIN_BL = "domain_blacklist"
CLEAN_VOD = "vod_filtered"

def clean_source_line(line: str) -> Tuple[Optional[Tuple[str, str]], str]:
    if not line:
        return None, CLEAN_NO_FORMAT
    line = line.replace('\r', '').replace('\n', ' ').strip()
    if not line:
        return None, CLEAN_NO_FORMAT
    if ',' not in line or '://' not in line:
        return None, CLEAN_NO_FORMAT
    proto_idx = line.find('://')
    if proto_idx < 1:
        return None, CLEAN_BAD_URL
    prefix = line[:proto_idx - 1]
    comma_pos = prefix.rfind(',')
    if comma_pos < 0:
        return None, CLEAN_NO_FORMAT
    name = prefix[:comma_pos].strip()
    name = re.sub(r'\s{2,}', ' ', name).strip()
    if not name:
        return None, CLEAN_EMPTY_NAME
    rest = line[comma_pos + 1:].strip()
    url = rest.split(',')[0].strip() if ',' in rest else rest
    url = url.split('$')[0].strip().split('#')[0].strip()
    if not url or '://' not in url:
        return None, CLEAN_BAD_URL
    if url_matches_domain_blacklist(url):
        return None, CLEAN_DOMAIN_BL
    if is_vod_or_image_url(url):
        return None, CLEAN_VOD
    return (name, url), CLEAN_OK

# ===================== 媒体类型判定 =====================
# ... (保留原有的媒体类型判定函数，与之前相同)
STREAM_LIKE_CT = [
    "video/mp2t", "video/mp4", "video/x-flv", "video/fmp4", "application/octet-stream",
    "application/vnd.apple.mpegurl", "application/x-mpegURL", "application/dash+xml",
    "audio/mpegurl", "audio/mpeg", "audio/aac", "audio/x-aac", "text/xml", "text/plain",
]

def is_stream_like_ct(ct: str) -> bool:
    if not ct: return False
    return any(p in ct.lower() for p in STREAM_LIKE_CT)

def is_html_ct(ct: str) -> bool:
    if not ct: return False
    return "text/html" in ct.lower()

def _read_first_chunk(resp, max_bytes=4096):
    try:
        chunk = resp.read(max_bytes)
        return chunk if chunk else b""
    except Exception:
        return b""

def _looks_like_media(data: bytes) -> bool:
    if not data: return False
    if data[:3] == b"FLV": return True
    if len(data) >= 8 and data[:4] == b"\x00\x00\x00" and data[4:8] == b"ftyp": return True
    if data[:3] == b"ID3": return True
    if len(data) >= 188 and data[0] == 0x47: return True
    if len(data) >= 8 and data[4:8] == b"ftyp": return True
    return False

def _looks_like_html(data: bytes) -> bool:
    if not data: return False
    d = data.lstrip(b'\xef\xbb\xbf').lstrip()
    if len(d) < 5: return False
    head = d[:20].lower()
    if head.startswith(b"<!doc") or head.startswith(b"<html") or head.startswith(b"<head"): return True
    if d[0:1] == b"{" and (b'"code"' in d[:500] or b'"error"' in d[:500] or b'"msg"' in d[:500]): return True
    if len(d) < 200 and (b"403" in d[:50] or b"404" in d[:50] or b"forbidden" in d[:100].lower()): return True
    return False

# ===================== HLS 解析 =====================
def parse_m3u8_segments(content: str) -> List[str]:
    lines = content.splitlines()
    segments: List[str] = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line: continue
        if line.startswith("#EXTINF"):
            for j in range(i + 1, len(lines)):
                l = lines[j].strip()
                if not l or l.startswith("#"): continue
                segments.append(l)
                break
        elif line.startswith("#EXT-X-ENDLIST"):
            break
    return segments

# ===================== StreamChecker 主体 =====================
# ... (保留原有的StreamChecker类，与之前相同)
class StreamChecker:
    def __init__(self, manual_urls=None):
        self.start_time = datetime.now()
        self.ipv6_available = self._check_ipv6()
        self.blacklist_urls = self._load_blacklist()
        self.whitelist_urls: Set[str] = set()
        self.whitelist_lines: List[str] = []
        self.new_failed_urls: Set[str] = set()
        self.manual_urls = manual_urls or []
        self.clean_stats: Dict[str, int] = {
            CLEAN_NO_FORMAT: 0, CLEAN_EMPTY_NAME: 0, CLEAN_BAD_URL: 0,
            CLEAN_DOMAIN_BL: 0, CLEAN_VOD: 0,
        }

    def _check_ipv6(self):
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sock.settimeout(1)
            r = sock.connect_ex(('2001:4860:4860::8888', 53))
            sock.close()
            return r == 0
        except Exception:
            return False

    def _load_blacklist(self) -> Set[str]:
        blacklist: Set[str] = set()
        try:
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith(('更新时间', 'blacklist', '#')): continue
                        url = line.split(',')[-1].strip() if ',' in line else line
                        url = url.split('$')[0].split('#')[0].strip()
                        if '://' in url:
                            blacklist.add(url)
            logger.info(f"加载 URL 精确黑名单: {len(blacklist)} 条")
        except Exception as e:
            logger.error(f"加载黑名单失败: {e}")
        return blacklist

    def _save_blacklist(self):
        if not self.new_failed_urls:
            return
        try:
            existing_lines: List[str] = []
            has_header = False
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], 'r', encoding='utf-8') as f:
                    existing_lines = [line.rstrip('\n') for line in f]
                for line in existing_lines[:5]:
                    if line.startswith('更新时间') or line.startswith('blacklist'):
                        has_header = True
                        break

            all_content: List[str] = []
            if not has_header:
                bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
                all_content.extend([
                    "更新时间,#genre#",
                    f"{bj_time.strftime('%Y%m%d %H:%M')},url",
                    "",
                    "blacklist,#genre#",
                ])

            existing_urls: Set[str] = set()
            for line in existing_lines:
                if line and not line.startswith('更新时间') and not line.startswith('blacklist') and line.strip():
                    url = line.split(',')[-1].strip() if ',' in line else line.strip()
                    if url and '://' in url and url not in existing_urls:
                        existing_urls.add(url)
                        all_content.append(line)

            for url in self.new_failed_urls:
                if url not in existing_urls:
                    existing_urls.add(url)
                    all_content.append(url)

            os.makedirs(os.path.dirname(FILE_PATHS["blacklist_auto"]), exist_ok=True)
            with open(FILE_PATHS["blacklist_auto"], 'w', encoding='utf-8') as f:
                f.write('\n'.join(all_content))
            logger.info(f"黑名单已更新: 新增 {len(self.new_failed_urls)} 条")
        except Exception as e:
            logger.error(f"保存黑名单失败: {e}")

    def read_file(self, file_path, split_by_space=False):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if split_by_space:
                return [line.strip() for line in re.split(r'[\s\t\n]+', content) if line.strip() and line.strip().startswith('http')]
            else:
                return [line.strip() for line in content.splitlines() if line.strip()]
        except Exception as e:
            logger.warning(f"读取文件失败 {file_path}: {e}")
            return []

    def _ssl_ctx(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def check_http(self, url: str, timeout: float):
        start = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": Config.USER_AGENT, "Connection": "close",
            }, method="GET")
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self._ssl_ctx()))
            with opener.open(req, timeout=timeout) as resp:
                code = resp.getcode()
                ct = resp.headers.get("Content-Type") or ""
                data = _read_first_chunk(resp, Config.FIRST_CHUNK_BYTES)
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                success = (200 <= code < 400) or code in (301, 302)
                if not success:
                    return (False, elapsed, str(code), None)

                if is_html_ct(ct) or _looks_like_html(data):
                    return (False, elapsed, f"{code}/html", "timeout")

                if is_stream_like_ct(ct) and not ct.lower().startswith("text/"):
                    if _looks_like_media(data) and len(data) >= Config.MIN_FIRST_CHUNK_FOR_STREAM:
                        return (True, elapsed, str(code), "stream")
                    return (True, elapsed, str(code), "unknown")

                if ct.lower().startswith("text/") or ct.lower().startswith("application/xml"):
                    if b"#EXTM3U" in data or b"#EXTINF" in data or b"#EXT-X-" in data:
                        return (True, elapsed, str(code), "playlist")
                    return (True, elapsed, str(code), "unknown")

                if _looks_like_media(data):
                    return (True, elapsed, str(code), "stream" if len(data) >= Config.MIN_FIRST_CHUNK_FOR_STREAM else "unknown")

                return (True, elapsed, str(code), "unknown")

        except urllib.error.HTTPError as e:
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            code = getattr(e, "code", None) or 0
            return (True, elapsed, str(code), None) if code in (301, 302) else (False, elapsed, str(code), None)
        except Exception as e:
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            return (False, elapsed, str(e) or "unknown", "timeout")

    def _hls_probe_segment(self, seg_url: str, timeout: float) -> bool:
        try:
            req = urllib.request.Request(seg_url, headers={
                "User-Agent": Config.USER_AGENT, "Connection": "close",
            }, method="GET")
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self._ssl_ctx()))
            with opener.open(req, timeout=timeout) as resp:
                if not (200 <= resp.getcode() < 400 or resp.getcode() in (301, 302)):
                    return False
                data = _read_first_chunk(resp, 2048)
                return _looks_like_media(data) or len(data) >= 64
        except Exception:
            return False

    def _hls_validate(self, playlist_url: str, timeout: float) -> bool:
        try:
            req = urllib.request.Request(playlist_url, headers={
                "User-Agent": Config.USER_AGENT, "Connection": "close",
            }, method="GET")
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self._ssl_ctx()))
            with opener.open(req, timeout=timeout) as resp:
                if not (200 <= resp.getcode() < 400 or resp.getcode() in (301, 302)):
                    return False
                content = resp.read(64 * 1024).decode("utf-8", errors="replace")
                segments = parse_m3u8_segments(content)
                if not segments: return False
                abs_segs = [urljoin(playlist_url, s) if not s.startswith("http") else s for s in segments]
                samples = [abs_segs[0]]
                if len(abs_segs) > 1: samples.append(abs_segs[-1])
                if len(abs_segs) > 2: samples.append(abs_segs[len(abs_segs) // 2])
                samples = list(dict.fromkeys(samples))[:Config.HLS_SAMPLE_SEGMENTS]
                ok = sum(1 for s in samples if self._hls_probe_segment(s, Config.HLS_SEGMENT_TIMEOUT))
                return ok > 0
        except Exception:
            return False

    def check_rtmp_rtsp(self, url, timeout):
        start = time.perf_counter()
        try:
            parsed = urlparse(url)
            if not parsed.hostname: return False, 0
            port = parsed.port or (1935 if url.startswith('rtmp') else 554)
            ips: List[Tuple[str, int]] = []
            try:
                addrs = socket.getaddrinfo(parsed.hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                ips = [(a[4][0], a[0]) for a in addrs[:2]]
            except Exception: pass
            
            for ip, af in ips:
                s = None
                try:
                    s = socket.socket(af, socket.SOCK_STREAM)
                    s.settimeout(timeout)
                    s.connect((ip, port))
                    if url.startswith('rtmp'):
                        s.send(b'\x03')
                        s.settimeout(Config.TIMEOUT_READ)
                        return bool(s.recv(1)), round((time.perf_counter() - start) * 1000, 2)
                    return True, round((time.perf_counter() - start) * 1000, 2)
                except Exception: continue
                finally:
                    if s: s.close()
            return False, round((time.perf_counter() - start) * 1000, 2)
        except Exception:
            return False, round((time.perf_counter() - start) * 1000, 2)

    def check_url(self, url: str, is_whitelist=False):
        start = time.perf_counter()
        try:
            u = quote(unquote(url), safe=':/?&=#')
            t = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK

            if url_matches_domain_blacklist(u):
                return (False, 0, "domain_blacklist", "blacklist")

            if u.startswith(('http://', 'https://')):
                succ, elapsed, code_or_reason, kind = self.check_http(u, t)
                if succ and kind == "playlist":
                    try:
                        with urllib.request.urlopen(
                            urllib.request.Request(u, headers={"User-Agent": Config.USER_AGENT, "Connection": "close"}, method="GET"),
                            timeout=t,
                        ) as r:
                                sample = r.read(4096)
                                if b"#EXTM3U" in sample and (b"#EXT-X-" in sample or b"#EXTINF" in sample):
                                    if not self._hls_validate(u, Config.HLS_SEGMENT_TIMEOUT + 1):
                                        return (True, elapsed, code_or_reason, "unknown")
                    except Exception: pass
                return (succ, elapsed, code_or_reason, kind)

            elif u.startswith(('rtmp://', 'rtsp://')):
                ok, ms = self.check_rtmp_rtsp(u, t)
                return (ok, ms, None if ok else "rtmp/rtsp_fail", "stream" if ok else "timeout")

            else:
                parsed = urlparse(u)
                if not parsed.hostname: return (False, 0, "no_host", None)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(Config.TIMEOUT_CONNECT)
                s.connect((parsed.hostname, parsed.port or 80))
                s.close()
                return (True, round((time.perf_counter() - start) * 1000, 2), "tcp_ok", None)

        except Exception as e:
            return (False, 0, str(e), "timeout")

    def fetch_remote(self, urls):
        all_lines: List[str] = []
        for url in urls:
            try:
                req = urllib.request.Request(
                    quote(unquote(url), safe=':/?&=#'),
                    headers={"User-Agent": Config.USER_AGENT}
                )
                with urllib.request.urlopen(req, timeout=Config.TIMEOUT_FETCH) as r:
                    c = r.read().decode('utf-8', 'replace')
                    if "#EXTM3U" in c[:200]:
                        lines = self._parse_m3u(c)
                    else:
                        lines = self._parse_text(c)
                    all_lines.extend(lines)
                    logger.info(f"获取 {url[:60]}... → {len(lines)} 条（清洗后）")
            except Exception as e:
                logger.error(f"拉取失败 {url[:60]}... : {e}")
        return all_lines

    def _parse_m3u(self, content):
        lines: List[str] = []
        name = ""
        for l in content.split('\n'):
            l = l.strip()
            if l.startswith("#EXTINF"):
                m = re.search(r',(.+)$', l)
                if m: name = m.group(1).strip()
            elif l.startswith(('http://', 'https://', 'rtmp://', 'rtsp://')) and name:
                result, reason = clean_source_line(f"{name},{l}")
                if result:
                    lines.append(f"{result[0]},{result[1]}")
                else:
                    self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
                name = ""
        return lines

    def _parse_text(self, content):
        lines: List[str] = []
        for l in content.split('\n'):
            l = l.strip()
            if not l or l.startswith('#') or l.endswith(',#genre#'): continue
            result, reason = clean_source_line(l)
            if result:
                lines.append(f"{result[0]},{result[1]}")
            else:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
        return lines

    def load_whitelist(self):
        for line in self.read_file(FILE_PATHS["whitelist_manual"]):
            if line.startswith('#'): continue
            result, reason = clean_source_line(line)
            if result:
                name, url = result
                self.whitelist_urls.add(url)
                self.whitelist_lines.append(f"{name},{url}")
            else:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
        logger.info(f"手动白名单: {len(self.whitelist_urls)} 个频道")

    def prepare_lines(self, lines):
        to_check: List[Tuple[str, str]] = []
        pre_fail: List[str] = []
        skip = 0
        seen_urls: Set[str] = set()

        for line in lines:
            result, reason = clean_source_line(line)
            if not result:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
                continue

            name, url = result
            if url in seen_urls: continue
            seen_urls.add(url)

            if url in self.blacklist_urls and url not in self.whitelist_urls:
                pre_fail.append(f"{name},{url}")
                skip += 1
            else:
                to_check.append((url, f"{name},{url}"))

        logger.info(f"待检测 {len(to_check)} 条，跳过 {skip} 条（URL黑名单）")
        stats_parts = [f"{k}={v}" for k, v in self.clean_stats.items() if v > 0]
        if stats_parts:
            logger.info(f"格式清洗统计: {', '.join(stats_parts)}")

        return to_check, pre_fail

    def _ensure_single_line(self, text: str) -> str:
        return text.replace('\r', '').replace('\n', ' ').strip()

    def save_respotime(self, items: List[Tuple[str, float, str, str]]):
        try:
            bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
            with open(FILE_PATHS["whitelist_respotime"], 'w', encoding='utf-8') as f:
                f.write("白名单测速,#genre#\n更新时间,#genre#\n")
                f.write(f"{bj_time.strftime('%Y%m%d %H:%M')},url,耗时ms,状态码/备注,媒体类型\n\n")
                for url, elapsed, code_or_reason, kind in items:
                    url_single = self._ensure_single_line(url)
                    f.write(f"{elapsed},{url_single},{code_or_reason or '-'},{kind or '-'}\n")
            logger.info(f"测速结果 → {os.path.basename(FILE_PATHS['whitelist_respotime'])} ({len(items)} 条)")
        except Exception as e:
            logger.error(f"保存测速结果失败: {e}")

    def save_whitelist_auto(self, items: List[Tuple[str, float, str, str]]):
        try:
            bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
            with open(FILE_PATHS["whitelist_auto"], 'w', encoding='utf-8') as f:
                f.write(f"更新时间,#genre#\n{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
                count = 0
                for url, elapsed, code_or_reason, kind in items:
                    if kind not in ("timeout", "blacklist"):
                        f.write(self._ensure_single_line(url) + "\n")
                        count += 1
            logger.info(f"自动白名单 → {os.path.basename(FILE_PATHS['whitelist_auto'])} ({count} 条)")
        except Exception as e:
            logger.error(f"保存自动白名单失败: {e}")

    def run(self):
        logger.info(f"===== 程序开始: {self.start_time.strftime('%Y%m%d %H:%M:%S')} =====")
        self.load_whitelist()

        lines: List[str] = []
        
        # 读取并拉取标准 urls.txt
        urls = self.read_file(FILE_PATHS["urls"], split_by_space=True)
        if urls:
            logger.info(f"开始拉取 urls.txt 中的 {len(urls)} 个远程节点...")
            lines.extend(self.fetch_remote(urls))
        else:
            logger.warning(f"未找到或未能读取 urls.txt 内容")
        
        # 读取并拉取自定义 my_urls.txt
        my_urls = self.read_file(FILE_PATHS["my_urls"], split_by_space=True)
        if my_urls:
            logger.info(f"开始拉取 my_urls.txt 中的 {len(my_urls)} 个远程节点...")
            lines.extend(self.fetch_remote(my_urls))
        else:
            logger.warning(f"未找到或未能读取 my_urls.txt 内容")
        
        lines.extend(self.whitelist_lines)
        for url in self.manual_urls:
            lines.append(url)

        to_check, pre_fail = self.prepare_lines(lines)

        results: List[Tuple[str, float, str, str]] = []
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
            future_to_url = {
                executor.submit(self.check_url, u, is_whitelist=(u in self.whitelist_urls)): u
                for u, _ in to_check
            }
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    succ, elapsed, code_or_reason, kind = future.result()
                    results.append((url, elapsed, code_or_reason, kind))
                    if not succ and url not in self.whitelist_urls:
                        self.new_failed_urls.add(url)
                except Exception as e:
                    logger.error(f"检测异常 {url}: {e}")
                    self.new_failed_urls.add(url)

        self._save_blacklist()

        def sort_key(item):
            _, elapsed, _, kind = item
            order = {"stream": 0, "playlist": 1, "unknown": 2}.get(kind, 3)
            return (order, elapsed)

        results_sorted = sorted(results, key=sort_key)
        self.save_respotime(results_sorted)
        self.save_whitelist_auto(results_sorted)

        total = len(results)
        stream_n = sum(1 for _, _, _, k in results if k == "stream")
        playlist_n = sum(1 for _, _, _, k in results if k == "playlist")
        unknown_n = sum(1 for _, _, _, k in results if k == "unknown")
        timeout_n = sum(1 for _, _, _, k in results if k == "timeout")
        blacklist_n = sum(1 for _, _, _, k in results if k == "blacklist")
        elapsed_s = (datetime.now() - self.start_time).seconds

        logger.info(
            f"===== 检测完成 =====\n"
            f"  总计: {total} 条\n"
            f"  ✅ 流: {stream_n}\n"
            f"  ✅ 列表: {playlist_n}\n"
            f"  ⚠️ 未知: {unknown_n}\n"
            f"  ❌ 超时: {timeout_n}\n"
            f"  🚫 域名黑名单: {blacklist_n}\n"
            f"  耗时: {elapsed_s}s"
        )


def main():
    try:
        # 第一步：获取新token并统一更新my_urls.txt中的所有URL
        logger.info("=== 开始token自动更新流程 ===")
        token_updated = refresh_token_and_update_urls()
        
        if token_updated:
            logger.info("=== token更新完成，开始WhiteList BlackList流程 ===")
        else:
            logger.warning("=== token更新失败，继续使用现有token执行流程 ===")
        
        # 第二步：继续原来的流程
        manual_urls: List[str] = []
        if not sys.stdin.isatty():
            for chunk in sys.stdin:
                chunk = chunk.strip()
                if not chunk: continue
                manual_urls.extend(
                    p for p in re.split(r'[\s,]+', chunk)
                    if p.startswith(('http://', 'https://', 'rtmp://', 'rtsp://'))
                )
        checker = StreamChecker(manual_urls=manual_urls)
        checker.run()
    except Exception as e:
        logger.error(f"主流程异常: {e}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
