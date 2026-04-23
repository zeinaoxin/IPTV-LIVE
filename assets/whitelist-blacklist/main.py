import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse, quote, unquote, urljoin, parse_qs, urlencode
import socket
import ssl
import re
from typing import List, Tuple, Set, Dict, Optional
import logging
import sys
import subprocess

# ==============================================
# 智普清言
# 【核心修复】绝对路径100%正确，杜绝嵌套错误
# ==============================================
# 脚本位置：assets/whitelist-blacklist/main.py
# 正确路径：项目根目录/assets/my_urls.txt
SCRIPT_ABS_PATH = os.path.abspath(__file__)
SCRIPT_DIR = os.path.dirname(SCRIPT_ABS_PATH)      # assets/whitelist-blacklist
ASSETS_DIR = os.path.dirname(SCRIPT_DIR)            # 正确的assets目录（上一级）
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)          # 项目根目录

# 固定文件路径（绝对不会错）
FILE_PATHS = {
    "my_urls": os.path.join(ASSETS_DIR, "my_urls.txt"),
    "urls": os.path.join(ASSETS_DIR, "urls.txt"),
    "blacklist_auto": os.path.join(SCRIPT_DIR, "blacklist_auto.txt"),
    "whitelist_manual": os.path.join(SCRIPT_DIR, "whitelist_manual.txt"),
    "whitelist_auto": os.path.join(SCRIPT_DIR, "whitelist_auto.txt"),
    "whitelist_respotime": os.path.join(SCRIPT_DIR, "whitelist_respotime.txt"),
    "log": os.path.join(SCRIPT_DIR, "log.txt"),
}

# ==============================================
# 日志配置（全量打印路径，方便排查）
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

# 启动时强制打印路径，日志里一眼看对错
logger.info("="*60)
logger.info(f"项目根目录: {PROJECT_ROOT}")
logger.info(f"脚本所在目录: {SCRIPT_DIR}")
logger.info(f"assets目录: {ASSETS_DIR}")
logger.info(f"my_urls.txt路径: {FILE_PATHS['my_urls']}")
logger.info(f"my_urls.txt是否存在: {os.path.exists(FILE_PATHS['my_urls'])}")
logger.info("="*60)

# ==============================================
# 全局配置
# ==============================================
class Config:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    TIMEOUT_FETCH = 15
    TIMEOUT_CHECK = 3.0
    TIMEOUT_WHITELIST = 4.5
    MAX_WORKERS = 30

# ==============================================
# 【优化】只获取一次Token，批量更新所有链接
# ==============================================
def get_taoiptv_token() -> Optional[str]:
    """只访问一次官网，获取有效Token"""
    try:
        logger.info("正在获取TaoIPTV最新Token...")
        ctx = ssl._create_unverified_context()
        headers = {
            "User-Agent": Config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.taoiptv.com/"
        }
        req = urllib.request.Request("https://www.taoiptv.com", headers=headers, method="GET")
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=15) as resp:
            if resp.getcode() != 200:
                logger.error(f"访问官网失败，状态码: {resp.getcode()}")
                return None
            html = resp.read().decode('utf-8', errors='ignore')
            # 精准匹配16位Token
            token_match = re.search(r'[a-f0-9]{16}', html, re.I)
            if token_match:
                token = token_match.group(0)
                logger.info(f"✅ 成功获取Token: {token}")
                return token
            logger.error("❌ 未在页面中匹配到有效Token")
            return None
    except Exception as e:
        logger.error(f"❌ 获取Token失败: {str(e)}", exc_info=True)
        return None

def update_my_urls_all(token: str) -> bool:
    """用同一个Token，一次性更新文件里所有链接"""
    if not token or len(token) != 16:
        logger.error("❌ Token无效，跳过更新")
        return False
    file_path = FILE_PATHS["my_urls"]
    if not os.path.exists(file_path):
        logger.error(f"❌ my_urls.txt文件不存在: {file_path}")
        return False
    try:
        # 读取文件
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
        # 统计需要替换的链接数量
        old_token_count = len(re.findall(r'token=[a-f0-9]{16}', original_content, re.I))
        if old_token_count == 0:
            logger.info("✅ 文件中没有需要更新的Token，无需修改")
            return False
        # 一次性全局替换所有Token
        new_content = re.sub(r'token=[a-f0-9]{16}', f'token={token}', original_content, flags=re.I)
        # 强制写入文件
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"✅ my_urls.txt更新成功！共替换 {old_token_count} 个链接的Token")
        return True
    except Exception as e:
        logger.error(f"❌ 更新my_urls.txt失败: {str(e)}", exc_info=True)
        return False

# ==============================================
# 【核心修复】自动Git提交推送（同步到GitHub仓库）
# ==============================================
def git_commit_push():
    """修改后自动提交到GitHub，网页上立刻看到变化"""
    try:
        logger.info("正在同步修改到GitHub仓库...")
        # 切换到项目根目录
        os.chdir(PROJECT_ROOT)
        # Git基础配置
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Auto-Bot"], check=True, capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@noreply.github.com"], check=True, capture_output=True)
        # 检查是否有变更
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if not status:
            logger.info("✅ 无文件变更，无需提交")
            return True
        # 添加、提交、推送
        subprocess.run(["git", "add", "assets/my_urls.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Auto update TaoIPTV token"], check=True, capture_output=True)
        # 适配GitHub Actions的Token推送
        github_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        if github_token and repo:
            push_url = f"https://x-access-token:{github_token}@github.com/{repo}.git"
            subprocess.run(["git", "push", push_url, "HEAD"], check=True, capture_output=True)
        else:
            subprocess.run(["git", "push"], check=True, capture_output=True)
        logger.info("✅ 已成功同步修改到GitHub仓库！")
        return True
    except subprocess.CalledProcessError as e:
        logger.warning(f"Git推送失败: {e.stderr.decode('utf-8', errors='ignore')}")
        return False
    except Exception as e:
        logger.warning(f"Git操作异常: {str(e)}")
        return False

# ==============================================
# 以下为原项目完整功能（无任何修改，保证兼容）
# ==============================================
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

def url_matches_domain_blacklist(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        host_lower = host.lower()
        for d in DOMAIN_BLACKLIST:
            if host_lower == d or host_lower.endswith(f".{d}"):
                return True
        return False
    except Exception:
        return False

VOD_DOMAINS: Set[str] = {
    "kwimgs.com",
    "kuaishou.com",
    "ixigua.com",
    "douyin.com",
    "tiktokcdn.com",
    "bdstatic.com",
    "byteimg.com"
}
VOD_EXTENSIONS: Set[str] = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".rmvb"}
IMAGE_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}

def is_vod_or_image_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        for vd in VOD_DOMAINS:
            if host == vd or host.endswith(f".{vd}"):
                return True
        path = urlparse(url).path.lower()
        return path.endswith(tuple(IMAGE_EXTENSIONS)) or path.endswith(tuple(VOD_EXTENSIONS))
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
    line = line.replace('\r', '').replace('\n', ' ').strip()
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

STREAM_LIKE_CT = [
    "video/mp2t",
    "video/mp4",
    "video/x-flv",
    "application/vnd.apple.mpegurl",
    "application/octet-stream",
    "application/x-mpegURL"
]

def is_stream_like_ct(ct: str) -> bool:
    return any(p in ct.lower() for p in STREAM_LIKE_CT) if ct else False

def is_html_ct(ct: str) -> bool:
    return "text/html" in ct.lower() if ct else False

def _read_first_chunk(resp, max_bytes=4096):
    try:
        return resp.read(max_bytes)
    except Exception:
        return b""

def _looks_like_media(data: bytes) -> bool:
    if not data:
        return False
    return (data[:3] == b"FLV" or (len(data)>=8 and data[4:8]==b"ftyp") or data[:3] == b"ID3" or (len(data)>=188 and data[0]==0x47))

def _looks_like_html(data: bytes) -> bool:
    if not data:
        return False
    d = data.lstrip(b'\xef\xbb\xbf').lstrip()
    return d[:5].lower().startswith((b"<!doc", b"<html"))

def parse_m3u8_segments(content: str) -> List[str]:
    segments = []
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF"):
            for j in range(i+1, len(lines)):
                l = lines[j].strip()
                if l and not l.startswith("#"):
                    segments.append(l)
                    break
    return segments

class StreamChecker:
    def __init__(self, manual_urls=None):
        self.start_time = datetime.now()
        self.blacklist_urls = self._load_blacklist()
        self.whitelist_urls: Set[str] = set()
        self.whitelist_lines: List[str] = []
        self.new_failed_urls: Set[str] = set()
        self.manual_urls = manual_urls or []
        self.clean_stats: Dict[str, int] = {
            CLEAN_NO_FORMAT:0, CLEAN_EMPTY_NAME:0, CLEAN_BAD_URL:0,
            CLEAN_DOMAIN_BL:0, CLEAN_VOD:0
        }

    def _check_ipv6(self):
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sock.settimeout(1)
            r = sock.connect_ex(('2001:4860:4860::8888', 53))
            sock.close()
            return r == 0
        except:
            return False

    def _load_blacklist(self):
        blacklist = set()
        try:
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith(('更新时间', '#')):
                            continue
                        url = line.split(',')[-1].split('$')[0].split('#')[0].strip()
                        if '://' in url:
                            blacklist.add(url)
                logger.info(f"加载URL黑名单: {len(blacklist)} 条")
        except Exception as e:
            logger.error(f"加载黑名单失败: {e}")
        return blacklist

    def _save_blacklist(self):
        if not self.new_failed_urls:
            return
        try:
            existing_lines = []
            has_header = False
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], 'r', encoding='utf-8') as f:
                    existing_lines = [l.rstrip('\n') for l in f]
                for line in existing_lines[:5]:
                    if line.startswith('更新时间'):
                        has_header = True; break
            all_content = []
            if not has_header:
                bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
                all_content.extend([
                    "更新时间,#genre#",
                    f"{bj_time.strftime('%Y%m%d %H:%M')},url",
                    "",
                    "blacklist,#genre#"
                ])
            existing_urls = set()
            for line in existing_lines:
                if line and not line.startswith(('更新时间', '#')):
                    url = line.split(',')[-1].strip()
                    if url:
                        existing_urls.add(url); all_content.append(line)
            for url in self.new_failed_urls:
                if url not in existing_urls:
                    all_content.append(url)
            with open(FILE_PATHS["blacklist_auto"], 'w', encoding='utf-8') as f:
                f.write('\n'.join(all_content))
            logger.info(f"黑名单更新完成，新增{len(self.new_failed_urls)}条")
        except Exception as e:
            logger.error(f"保存黑名单失败: {e}")

    def read_file(self, file_path, split_by_space=False):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if split_by_space:
                return [l.strip() for l in re.split(r'[\s\t\n]+', content) if l.strip().startswith('http')]
            return [l.strip() for l in content.splitlines() if l.strip()]
        except Exception as e:
            logger.warning(f"读取文件失败 {file_path}: {e}")
            return []

    def check_http(self, url: str, timeout: float):
        start = time.perf_counter()
        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=timeout) as resp:
                code = resp.getcode()
                ct = resp.headers.get("Content-Type", "")
                data = _read_first_chunk(resp)
                elapsed = round((time.perf_counter()-start)*1000, 2)
                success = 200<=code<400 or code in (301,302)
                if not success:
                    return False, elapsed, str(code), None
                if is_html_ct(ct) or _looks_like_html(data):
                    return False, elapsed, f"{code}/html", "timeout"
                if is_stream_like_ct(ct) and _looks_like_media(data):
                    return True, elapsed, str(code), "stream"
                if b"#EXTM3U" in data:
                    return True, elapsed, str(code), "playlist"
                return True, elapsed, str(code), "unknown"
        except Exception as e:
            elapsed = round((time.perf_counter()-start)*1000, 2)
            return False, elapsed, str(e), "timeout"

    def check_url(self, url: str, is_whitelist=False):
        timeout = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK
        if url_matches_domain_blacklist(url):
            return False, 0, "blacklist", "blacklist"
        if url.startswith(('http://','https://')):
            return self.check_http(url, timeout)
        return True, 0, "ok", "stream"

    def fetch_remote(self, urls):
        all_lines = []
        for url in urls:
            try:
                ctx = ssl._create_unverified_context()
                req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
                with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=15) as r:
                    c = r.read().decode('utf-8', 'replace')
                    if "#EXTM3U" in c[:200]:
                        name = ""
                        for l in c.splitlines():
                            if l.startswith("#EXTINF"):
                                name = l.split(',')[-1] if ',' in l else ""
                            elif l.startswith(('http','rtmp')) and name:
                                res, _ = clean_source_line(f"{name.strip()},{l.strip()}")
                                if res:
                                    all_lines.append(f"{res[0]},{res[1]}")
                                name = ""
                    else:
                        # 兼容纯URL行（无逗号）：对纯URL补伪组名，保证能通过clean_source_line
                        for l in c.splitlines():
                            l = l.strip()
                            if not l or l.startswith('#'):
                                continue
                            if '://' in l and ',' not in l:
                                res, _ = clean_source_line(f"直播源,{l}")
                                if res:
                                    all_lines.append(f"{res[0]},{res[1]}")
                                continue
                            res, _ = clean_source_line(l)
                            if res:
                                all_lines.append(f"{res[0]},{res[1]}")
            except Exception as e:
                logger.error(f"拉取远程源失败 {url[:60]}: {e}")
        return all_lines

    def load_whitelist(self):
        for line in self.read_file(FILE_PATHS["whitelist_manual"]):
            if line.startswith('#'):
                continue
            res, _ = clean_source_line(line)
            if res:
                self.whitelist_urls.add(res[1])
                self.whitelist_lines.append(line)
        logger.info(f"手动白名单: {len(self.whitelist_urls)} 个频道")

    def prepare_lines(self, lines):
        to_check = []
        seen_urls = set()
        for line in lines:
            res, reason = clean_source_line(line)
            if not res:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0)+1
                continue
            name, url = res
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if url in self.blacklist_urls:
                continue
            to_check.append((url, line))
        logger.info(f"待检测 {len(to_check)} 条")
        return to_check, []

    def run(self):
        logger.info("===== 开始流媒体检测 =====")
        self.load_whitelist()
        lines = []

        # 拉取 urls.txt
        urls = self.read_file(FILE_PATHS["urls"], split_by_space=True)
        if urls:
            logger.info(f"开始拉取 urls.txt 中的 {len(urls)} 个节点")
            fetched = self.fetch_remote(urls)
            logger.info(f"urls.txt 拉取完成：{len(urls)} 个节点 → 成功获取 {len(fetched)} 个源")
            lines.extend(fetched)
        else:
            logger.warning("未找到或未能读取 urls.txt 内容")

        # 拉取 my_urls.txt
        my_urls = self.read_file(FILE_PATHS["my_urls"], split_by_space=True)
        if my_urls:
            logger.info(f"开始拉取 my_urls.txt 中的 {len(my_urls)} 个节点")
            fetched = self.fetch_remote(my_urls)
            logger.info(f"my_urls.txt 拉取完成：{len(my_urls)} 个节点 → 成功获取 {len(fetched)} 个源")
            lines.extend(fetched)
        else:
            logger.warning("未找到或未能读取 my_urls.txt 内容")

        lines.extend(self.whitelist_lines)
        lines.extend(self.manual_urls)
        to_check, _ = self.prepare_lines(lines)
        results = []
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
            future_map = {executor.submit(self.check_url, u, u in self.whitelist_urls): u for u, _ in to_check}
            for future in as_completed(future_map):
                url = future_map[future]
                try:
                    succ, elapsed, code, kind = future.result()
                    results.append((url, elapsed, code, kind))
                    if not succ and url not in self.whitelist_urls:
                        self.new_failed_urls.add(url)
                except Exception as e:
                    logger.error(f"检测异常 {url}: {e}")
                    self.new_failed_urls.add(url)
        self._save_blacklist()
        results_sorted = sorted(results, key=lambda x: ({"stream":0,"playlist":1,"unknown":2}.get(x[3],3), x[1]))
        with open(FILE_PATHS["whitelist_respotime"], 'w', encoding='utf-8') as f:
            bj_time = datetime.now(timezone.utc)+timedelta(hours=8)
            f.write(f"更新时间,#genre#\n{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
            for url, elapsed, code, kind in results_sorted:
                f.write(f"{elapsed},{url},{code},{kind}\n")
        with open(FILE_PATHS["whitelist_auto"], 'w', encoding='utf-8') as f:
            bj_time = datetime.now(timezone.utc)+timedelta(hours=8)
            f.write(f"更新时间,#genre#\n{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
            for url, _, _, kind in results_sorted:
                if kind not in ("timeout", "blacklist"):
                    f.write(f"自动,{url}\n")
        total = len(results)
        stream_n = sum(1 for _,_,_,k in results if k=="stream")
        logger.info(f"===== 检测完成 | 总计:{total} | 有效流:{stream_n} | 耗时:{(datetime.now()-self.start_time).seconds}s =====")

# ==============================================
# 主程序执行（先改文件，再跑流程）
# ==============================================
def main():
    try:
        logger.info("===== 开始执行Token自动更新 =====")
        # 1. 只获取一次Token
        token = get_taoiptv_token()
        # 2. 一次性更新所有链接
        update_success = False
        if token:
            update_success = update_my_urls_all(token)
        # 3. 同步到GitHub仓库
        if update_success:
            git_commit_push()
        # 4. 执行原项目的黑白名单检测
        checker = StreamChecker()
        checker.run()
        logger.info("===== 全部流程执行完成 =====")
    except Exception as e:
        logger.error(f"主程序异常: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
