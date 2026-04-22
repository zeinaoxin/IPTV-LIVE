import urllib.request
import os
import re
import ssl
import subprocess
from concurrent.futures import ThreadPoolExecutor
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, quote, unquote, urljoin, parse_qs, urlencode
import socket
from typing import List, Tuple, Set, Dict, Optional
import logging

# ===================== 日志配置 =====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message>',
    handlers=[
        logging.FileHandler("log.txt", mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===================== 核心路径配置 =====================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MY_URLS_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "my_urls.txt")

# ===================== 1. 获取TaoIPTV真实Token =====================
def get_taoiptv_token():
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        req = urllib.request.Request("https://www.taoiptv.com", headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        
        tokens = re.findall(r'[a-f0-9]{16}', html, re.I)
        if tokens:
            token = tokens[0]
            logger.info(f"✅ 获取Token成功: {token}")
            return token
        logger.error("❌ 未匹配到Token")
        return None
    except Exception as e:
        logger.error(f"❌ 获取Token失败: {str(e)}")
        return None

# ===================== 2. 修改my_urls.txt文件 =====================
def update_token_file(token):
    if not token:
        return False
    try:
        # 读取文件
        with open(MY_URLS_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 替换所有Token
        new_content = re.sub(r'token=[a-f0-9]{16}', f'token={token}', content, re.I)
        replace_count = len(re.findall(r'token=[a-f0-9]{16}', content, re.I))
        
        # 写入文件
        with open(MY_URLS_PATH, 'w', encoding='utf-8') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        
        logger.info(f"✅ 文件修改成功！替换 {replace_count} 个链接")
        return True
    except Exception as e:
        logger.error(f"❌ 修改文件失败: {str(e)}")
        return False

# ===================== 3. 自动Git提交推送（同步到GitHub仓库） =====================
def git_commit_push():
    try:
        os.chdir(os.path.dirname(SCRIPT_DIR))  # 切换到assets目录
        # Git配置
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Bot"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "bot@example.com"], check=False)
        # 添加、提交、推送
        subprocess.run(["git", "add", "my_urls.txt"], check=False)
        subprocess.run(["git", "commit", "-m", "Auto update TaoIPTV token"], check=False)
        subprocess.run(["git", "push"], check=False)
        logger.info("✅ 已同步修改到GitHub仓库！")
    except Exception as e:
        logger.error(f"❌ Git提交失败: {str(e)}")

# ===================== 以下为原项目完整代码（无任何修改） =====================
class Config:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    TIMEOUT_FETCH = 20
    TIMEOUT_CHECK = 3.0
    TIMEOUT_WHITELIST = 4.5
    MAX_WORKERS = 30

DOMAIN_BLACKLIST: Set[str] = {
    "iptv.catvod.com", "dd.ddzb.fun", "goodiptv.club", "jiaojirentv.top",
    "alist.xicp.fun", "rihou.cc", "php.jdshipin.com", "t.freetv.fun",
    "stream1.freetv.fun", "hlsztemgsplive.miguvideo", "stream2.freetv.fun",
}

def url_matches_domain_blacklist(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        if not host:
            return False
        host_lower = host.lower()
        for domain in DOMAIN_BLACKLIST:
            if host_lower == domain or host_lower.endswith(f".{domain}"):
                return True
        return False
    except Exception:
        return False

VOD_DOMAINS: Set[str] = {
    "kwimgs.com", "kuaishou.com", "ixigua.com", "douyin.com", "tiktok.com",
    "bdstatic.com", "byteimg.com"
}
VOD_EXTENSIONS: Set[str] = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".rmvb", ".flv"}
IMAGE_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}

def is_vod_or_image_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname
        if host and host.lower() in VOD_DOMAINS:
            return True
        path = urlparse(url).path.lower()
        return path.endswith(tuple(VOD_EXTENSIONS)) or path.endswith(tuple(IMAGE_EXTENSIONS))
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
    line = line.strip()
    if ',' not in line or '://' not in line:
        return None, CLEAN_NO_FORMAT
    parts = line.split(',', 1)
    if len(parts) != 2:
        return None, CLEAN_BAD_URL
    name, url = parts[0].strip(), parts[1].strip()
    if not name:
        return None, CLEAN_EMPTY_NAME
    if not url:
        return None, CLEAN_BAD_URL
    if url_matches_domain_blacklist(url):
        return None, CLEAN_DOMAIN_BL
    if is_vod_or_image_url(url):
        return None, CLEAN_VOD
    return (name, url), CLEAN_OK

STREAM_LIKE_CT: List[str] = [
    "video/mp2t", "video/mp4", "video/x-flv", "video/fmp4",
    "application/vnd.apple.mpegurl", "application/octet-stream"
]

def is_stream_like_ct(content_type: str) -> bool:
    if not content_type:
        return False
    return any(ct in content_type.lower() for ct in STREAM_LIKE_CT)

def is_html_ct(content_type: str) -> bool:
    if not content_type:
        return False
    return "text/html" in content_type.lower()

def _read_first_chunk(response, size: int = 4096) -> bytes:
    try:
        return response.read(size)
    except Exception:
        return b""

def _looks_like_media(data: bytes) -> bool:
    if not data:
        return False
    return (data[:3] == b"FLV" or (len(data) >= 8 and data[4:8] == b"ftyp") or
            data[:3] == b"ID3" or (len(data) >= 188 and data[0] == 0x47))

def _looks_like_html(data: bytes) -> bool:
    if not data:
        return False
    data = data.lstrip(b'\xef\xbb\xbf')
    return data[:5].lower().startswith((b"<!doc", b"<html"))

def parse_m3u8_segments(content: str) -> List[str]:
    segments = []
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("#EXTINF"):
            for j in range(idx + 1, len(lines)):
                current_line = lines[j].strip()
                if current_line and not current_line.startswith("#"):
                    segments.append(current_line)
                    break
    return segments

class StreamChecker:
    def __init__(self, manual_urls: List[str] = None):
        self.start_time = datetime.now()
        self.ipv6_available = self._check_ipv6()
        self.blacklist_urls = self._load_blacklist()
        self.whitelist_urls: Set[str] = set()
        self.whitelist_lines: List[str] = []
        self.new_failed_urls: Set[str] = set()
        self.manual_urls = manual_urls or []

    def _check_ipv6(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('2001:4860:4860::8888', 53))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _load_blacklist(self) -> Set[str]:
        blacklist = set()
        try:
            with open("blacklist_auto.txt", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith(("更新时间", "#")):
                        continue
                    url = line.split(',')[-1].split('$')[0].split('#')[0].strip()
                    if url:
                        blacklist.add(url)
        except Exception:
            pass
        logger.info(f"加载黑名单: {len(blacklist)} 条")
        return blacklist

    def _save_blacklist(self):
        if not self.new_failed_urls:
            return
        try:
            existing_lines = []
            if os.path.exists("blacklist_auto.txt"):
                with open("blacklist_auto.txt", "r", encoding="utf-8") as f:
                    existing_lines = [l.rstrip('\n') for l in f]
            now = datetime.now(timezone.utc) + timedelta(hours=8)
            if not any(l.startswith("更新时间") for l in existing_lines[:5]):
                existing_lines = [
                    "更新时间,#genre#",
                    f"{now.strftime('%Y%m%d %H:%M')},url",
                    "",
                    "blacklist,#genre#"
                ]
            existing_urls = set()
            for line in existing_lines:
                if line and not line.startswith(("更新时间", "#")):
                    url = line.split(',')[-1].strip()
                    if url:
                        existing_urls.add(url)
            for url in self.new_failed_urls:
                if url not in existing_urls:
                    existing_lines.append(url)
            with open("blacklist_auto.txt", "w", encoding="utf-8") as f:
                f.write('\n'.join(existing_lines))
            logger.info(f"黑名单更新完成")
        except Exception:
            pass

    def read_file(self, file_path: str, split_by_space: bool = False) -> List[str]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if split_by_space:
                return [l.strip() for l in re.split(r'[\s\t\n]+', content) if l.strip().startswith("http")]
            return [l.strip() for l in content.splitlines() if l.strip()]
        except Exception:
            return []

    def check_http(self, url: str, timeout: float) -> Tuple[bool, float, str, str]:
        start = time.perf_counter()
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=timeout) as resp:
                code = resp.getcode()
                content_type = resp.headers.get("Content-Type", "")
                data = _read_first_chunk(resp)
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                success = 200 <= code < 400 or code in (301, 302)
                if not success:
                    return False, elapsed, str(code), ""
                if is_html_ct(content_type) or _looks_like_html(data):
                    return False, elapsed, f"{code}", "timeout"
                if is_stream_like_ct(content_type) and _looks_like_media(data):
                    return True, elapsed, str(code), "stream"
                if b"#EXTM3U" in data:
                    return True, elapsed, str(code), "playlist"
                return True, elapsed, str(code), "unknown"
        except Exception as e:
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            return False, elapsed, "error", "timeout"

    def _hls_validate(self, url: str, timeout: float) -> bool:
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=timeout) as resp:
                content = resp.read(65536).decode('utf-8', 'ignore')
            segments = parse_m3u8_segments(content)
            if not segments:
                return False
            for seg in segments[:2]:
                seg_url = urljoin(url, seg)
                self.check_http(seg_url, 2.5)
            return True
        except Exception:
            return False

    def check_url(self, url: str, is_whitelist: bool = False) -> Tuple[bool, float, str, str]:
        timeout = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK
        if url_matches_domain_blacklist(url):
            return False, 0.0, "blacklist", "blacklist"
        if url.startswith(("http://", "https://")):
            ok, elapsed, code, kind = self.check_http(url, timeout)
            if ok and kind == "playlist":
                self._hls_validate(url, 3.5)
            return ok, elapsed, code, kind
        return True, 0.0, "local", "stream"

    def fetch_remote(self, urls: List[str]) -> List[str]:
        result = []
        for url in urls:
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
                with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=Config.TIMEOUT_FETCH) as resp:
                    content = resp.read().decode('utf-8', 'replace')
                if "#EXTM3U" in content[:200]:
                    name = ""
                    for line in content.splitlines():
                        if line.startswith("#EXTINF"):
                            name = line.split(',', 1)[-1] if ',' in line else ""
                        elif line.startswith(("http://", "https://", "rtmp://")) and name:
                            result.append(f"{name.strip()},{line.strip()}")
                            name = ""
                else:
                    for line in content.splitlines():
                        line = line.strip()
                        if line and ',' in line and '://' in line:
                            result.append(line)
            except Exception:
                continue
        return result

    def load_whitelist(self):
        for line in self.read_file("whitelist_manual.txt"):
            if line.startswith("#"):
                continue
            res, _ = clean_source_line(line)
            if res:
                self.whitelist_urls.add(res[1])
                self.whitelist_lines.append(line)

    def run(self):
        logger.info("===== 开始检测流媒体 =====")
        self.load_whitelist()
        lines = []
        urls_list = self.read_file("../urls.txt", True)
        lines.extend(self.fetch_remote(urls_list))
        my_urls_list = self.read_file(MY_URLS_PATH, True)
        lines.extend(self.fetch_remote(my_urls_list))
        lines.extend(self.whitelist_lines)
        lines.extend(self.manual_urls)

        check_list = []
        seen_urls = set()
        for line in lines:
            res, _ = clean_source_line(line)
            if not res:
                continue
            name, url = res
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if url in self.blacklist_urls:
                continue
            check_list.append((url, line))

        results = []
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
            future_map = {executor.submit(self.check_url, url, url in self.whitelist_urls): (url, line) for url, line in check_list}
            for future in as_completed(future_map):
                url, line = future_map[future]
                try:
                    ok, elapsed, code, kind = future.result()
                    results.append((url, elapsed, code, kind))
                    if not ok and url not in self.whitelist_urls:
                        self.new_failed_urls.add(url)
                except Exception:
                    results.append((url, 0.0, "error", "timeout"))

        self._save_blacklist()
        现在 = datetime.now(timezone.utc) + timedelta(hours=8)
        results.sort(key=lambda x: ({"stream": 0, "playlist": 1, "unknown": 2}.get(x[3], 3), x[1]))
        with open("whitelist_respotime.txt", "w", encoding="utf-8") as f:
            f.write("更新时间,#genre#\n")
            f.write(f"{now.strftime('%Y%m%d %H:%M')}\n\n")
            for url, elapsed, code, kind in results:
                f.write(f"{elapsed},{url},{code},{kind}\n")
        with open("whitelist_auto.txt", "w", encoding="utf-8") as f:
            f.write("更新时间,#genre#\n")
            f.write(f"{now.strftime('%Y%m%d %H:%M')}\n\n")
            for url, elapsed, code, kind in results:
                if kind not in ("timeout", "blacklist"):
                    f.write(f"自动,{url}\n")
        logger.info("===== 检测完成 =====")

# ===================== 主程序执行（先更新Token，再运行检测） =====================
if __name__ == "__main__":
    # 1. 获取Token
    token = get_taoiptv_token()
    # 2. 更新文件
    update_success = update_token_file(token)
    # 3. 提交到GitHub
    if update_success:
        git_commit_push()
    # 4. 运行原项目流媒体检测
    checker = StreamChecker()
    checker.run()
