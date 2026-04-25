import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime, timedelta, timezone
import os
import sys
from urllib.parse import urlparse, quote, unquote, urljoin
import socket
import ssl
import re
from typing import List, Tuple, Set, Dict, Optional
import logging
import subprocess

# ==============================================
# 路径配置
# ==============================================
SCRIPT_ABS_PATH = os.path.abspath(__file__)
SCRIPT_DIR = os.path.dirname(SCRIPT_ABS_PATH)
ASSETS_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)
MY_URLS_DIR = os.path.join(ASSETS_DIR, "my_urls")
FILE_PATHS = {
    "urls": os.path.join(ASSETS_DIR, "urls.txt"),
    "blacklist_auto": os.path.join(SCRIPT_DIR, "blacklist_auto.txt"),
    "whitelist_manual": os.path.join(SCRIPT_DIR, "whitelist_manual.txt"),
    "whitelist_auto": os.path.join(SCRIPT_DIR, "whitelist_auto.txt"),
    "whitelist_respotime": os.path.join(SCRIPT_DIR, "whitelist_respotime.txt"),
    "log": os.path.join(SCRIPT_DIR, "log.txt"),
}

# ==============================================
# 日志配置（优化：增加DEBUG级别和详细格式）
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logger.info("=" * 80)
logger.info(f"项目根目录: {PROJECT_ROOT}")
logger.info(f"脚本目录: {SCRIPT_DIR}")
logger.info(f"assets目录: {ASSETS_DIR}")
logger.info(f"本地源目录: {MY_URLS_DIR} ({'存在' if os.path.isdir(MY_URLS_DIR) else '不存在'})")
logger.info("=" * 80)

# ==============================================
# 全局配置
# ==============================================
class Config:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    TIMEOUT_FETCH = 15
    TIMEOUT_CHECK = 3.0
    TIMEOUT_WHITELIST = 4.5
    MAX_WORKERS = 30
    MAX_FASTEST_PER_CHANNEL = 20  # 每个频道保留20条
    # ---- 优化参数 ----
    MIN_RESPONSE_TIME = 10  # 最小有效响应时间（ms），屏蔽0ms等无效源
    MAX_RETRIES = 2  # 重试次数
    RETRY_TIMEOUT = 2.0  # 重试超时时间

# ==============================================
# 正则：从任意文本中提取所有 http(s) URL
# ==============================================
RE_ALL_URLS = re.compile(r'https?://[^\s,\'"<>}\])]+')

# ==============================================
# Git 提交推送
# ==============================================
def git_commit_push():
    try:
        logger.info("正在同步到GitHub仓库...")
        os.chdir(PROJECT_ROOT)
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Auto-Bot"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@noreply.github.com"], check=True, capture_output=True, text=True)
        
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if not status:
            logger.info("✅ 无文件变更，无需提交")
            return True
        
        subprocess.run(
            [
                "git", "add", 
                FILE_PATHS["blacklist_auto"], 
                FILE_PATHS["whitelist_auto"], 
                FILE_PATHS["whitelist_respotime"], 
                FILE_PATHS["log"],
            ], 
            check=True, capture_output=True, text=True
        )
        
        subprocess.run(["git", "commit", "-m", "Auto update whitelist and blacklist"], check=True, capture_output=True, text=True)
        
        gh_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        if gh_token and repo:
            push_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
            subprocess.run(["git", "push", push_url, "HEAD"], check=True, capture_output=True, text=True)
        else:
            subprocess.run(["git", "push"], check=True, capture_output=True, text=True)
        
        logger.info("✅ 已同步到GitHub仓库！")
        return True
    except subprocess.CalledProcessError as e:
        hint = ""
        try:
            hint = f" [ACTIONS={os.getenv('GITHUB_ACTIONS','?')} REPO={os.getenv('GITHUB_REPOSITORY','?')}]"
        except Exception:
            pass
        logger.warning(f"Git推送失败:{hint} {e.stderr if e.stderr else ''}")
        return False
    except Exception as e:
        logger.warning(f"Git异常: {e}")
        return False

# ==============================================
# 域名黑名单
# ==============================================
DOMAIN_BLACKLIST: Set[str] = {
    "iptv.catvod.com", "dd.ddzb.fun", "goodiptv.club", "jiaojirentv.top", "alist.xicp.fun", "rihou.cc",
    "php.jdshipin.com", "t.freetv.fun", "stream1.freetv.fun", "hlsztemgsplive.miguvideo", "stream2.freetv.fun",
}

def url_matches_domain_blacklist(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in DOMAIN_BLACKLIST)
    except Exception as e:
        logger.error(f"域名黑名单匹配异常: {url}, 错误: {e}")
        return False

# ==============================================
# 点播/图片过滤
# ==============================================
VOD_DOMAINS: Set[str] = {
    "kwimgs.com", "kuaishou.com", "ixigua.com", "douyin.com", "tiktokcdn.com", "bdstatic.com", "byteimg.com"
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
    except Exception as e:
        logger.error(f"点播/图片URL检测异常: {url}, 错误: {e}")
        return False

# ==============================================
# 行格式清洗（用于"组名,URL"格式的逐行处理）
# ==============================================
CLEAN_OK = "ok"
CLEAN_NO_FORMAT = "no_format"
CLEAN_EMPTY_NAME = "empty_name"
CLEAN_BAD_URL = "bad_url"
CLEAN_DOMAIN_BL = "domain_blacklist"
CLEAN_VOD = "vod_filtered"

def clean_source_line(line: str) -> Tuple[Optional[Tuple[str, str]], str]:
    if not line:
        logger.debug(f"行无效: 空行, 原因: CLEAN_NO_FORMAT")
        return None, CLEAN_NO_FORMAT
    
    line = line.replace("\r", "").replace("\n", " ").strip()
    if "," not in line or "://" not in line:
        logger.debug(f"行格式无效: {line[:50]}..., 原因: CLEAN_NO_FORMAT")
        return None, CLEAN_NO_FORMAT
    
    idx = line.find("://")
    if idx < 1:
        logger.debug(f"URL格式无效: {line[:50]}..., 原因: CLEAN_BAD_URL")
        return None, CLEAN_BAD_URL
    
    prefix = line[: idx - 1]
    pos = prefix.rfind(",")
    if pos < 0:
        logger.debug(f"行格式无效: 缺少逗号分隔, 原因: CLEAN_NO_FORMAT")
        return None, CLEAN_NO_FORMAT
    
    name = re.sub(r"\s{2,}", " ", prefix[:pos].strip())
    if not name:
        logger.debug(f"频道名无效: 空名称, 原因: CLEAN_EMPTY_NAME")
        return None, CLEAN_EMPTY_NAME
    
    rest = line[pos + 1:].strip()
    url = rest.split(",")[0].strip().split("$")[0].split("#")[0].strip()
    if not url or "://" not in url:
        logger.debug(f"URL无效: {url}, 原因: CLEAN_BAD_URL")
        return None, CLEAN_BAD_URL
    
    if url_matches_domain_blacklist(url):
        logger.debug(f"URL被域名黑名单拦截: {url}, 原因: CLEAN_DOMAIN_BL")
        return None, CLEAN_DOMAIN_BL
    
    if is_vod_or_image_url(url):
        logger.debug(f"URL被点播/图片过滤: {url}, 原因: CLEAN_VOD")
        return None, CLEAN_VOD
    
    logger.debug(f"行清洗成功: 频道名={name}, URL={url}")
    return (name, url), CLEAN_OK

# ==============================================
# 媒体类型判定
# ==============================================
STREAM_CTS = [
    "video/mp2t", "video/mp4", "video/x-flv", "application/vnd.apple.mpegurl",
    "application/octet-stream", "application/x-mpegURL"
]

def is_stream_ct(ct):
    return any(p in ct.lower() for p in STREAM_CTS) if ct else False

def is_html_ct(ct):
    return "text/html" in ct.lower() if ct else False

def _read_chunk(resp, n=4096):
    try:
        return resp.read(n)
    except Exception as e:
        logger.error(f"读取响应数据异常: {e}")
        return b""

def _looks_media(d):
    if not d:
        return False
    return (
        d[:3] == b"FLV" or 
        (len(d) >= 8 and d[4:8] == b"ftyp") or 
        d[:3] == b"ID3" or 
        (len(d) >= 188 and d[0] == 0x47)
    )

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
        self.clean_stats: Dict[str, int] = {
            CLEAN_NO_FORMAT: 0,
            CLEAN_EMPTY_NAME: 0,
            CLEAN_BAD_URL: 0,
            CLEAN_DOMAIN_BL: 0,
            CLEAN_VOD: 0
        }
        self.fetch_stats = {
            "total_urls": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0
        }
        self.channel_results: Dict[str, List[Tuple[str, float, str, str]]] = {}  # 频道名 -> (url, ms, code, kind)
        self.results: List[Tuple[str, float, str, str]] = []  # 全量测速结果
        self.fastest_results: List[Tuple[str, float, str, str]] = []  # 优选结果

    # ---------- 本地源读取（assets/my_urls/*.txt） ----------
    def read_my_urls_dir(self, dirpath: str) -> List[str]:
        """读取指定目录下所有 .txt 文件，支持两种行格式：- 组名,URL（标准）- 纯URL 返回清洗后的 '组名,URL' 行列表。"""
        lines: List[str] = []
        if not os.path.isdir(dirpath):
            logger.warning(f"本地源目录不存在，已跳过: {dirpath}")
            return lines
        
        txt_files = sorted(
            [f for f in os.listdir(dirpath) if f.lower().endswith(".txt") and os.path.isfile(os.path.join(dirpath, f))]
        )
        
        if not txt_files:
            logger.warning(f"本地源目录下无 .txt 文件，已跳过: {dirpath}")
            return lines
        
        logger.info(f"开始读取本地源目录 {dirpath}，共 {len(txt_files)} 个文件")
        total_before = len(lines)
        
        for fn in txt_files:
            fpath = os.path.join(dirpath, fn)
            try:
                with open(fpath, "r", encoding="utf-8") as fp:
                    for raw_line in fp:
                        raw_line = raw_line.strip()
                        if not raw_line or raw_line.startswith("#"):
                            continue
                        
                        # 先尝试清洗为"组名,URL"标准行
                        res, reason = clean_source_line(raw_line)
                        if res:
                            name, url = res
                            lines.append(f"{name},{url}")
                            continue
                        
                        # 如果标准格式解析失败，尝试当作纯URL
                        if re.match(r'https?://', raw_line, re.I):
                            u = raw_line.split(",")[0].split("$")[0].split("#")[0].strip()
                            if u and not url_matches_domain_blacklist(u) and not is_vod_or_image_url(u):
                                lines.append(f"本地,{u}")
            except Exception as e:
                logger.warning(f"读取本地源文件失败（已跳过）: {fpath}，原因: {e}")
        
        added = len(lines) - total_before
        if added > 0:
            logger.info(f"本地源目录读取完成，新增 {added} 条源（目录: {dirpath}）")
        else:
            logger.info(f"本地源目录未产生有效源（目录: {dirpath}）")
        
        return lines

    # ---------- 黑名单读写 ----------
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
            logger.debug("无新失败URL，跳过黑名单更新")
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
                    "blacklist,#genre#"
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

    # ---------- 文件读取工具 ----------
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

    # ---------- HTTP 请求与测速 ----------
    def check_http(self, url, timeout):
        s = time.perf_counter()
        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=timeout) as r:
                code = r.getcode()
                ct = r.headers.get("Content-Type", "")
                data = _read_chunk(r)
                ms = round((time.perf_counter() - s) * 1000, 2)
                
                # ---- 优化：屏蔽0ms等无效响应 ----
                if ms < Config.MIN_RESPONSE_TIME:
                    logger.debug(f"URL响应过快（{ms}ms < {Config.MIN_RESPONSE_TIME}ms）: {url}")
                    return False, ms, f"{ms}ms_too_fast", "timeout"
                
                ok = 200 <= code < 400 or code in (301, 302)
                if not ok:
                    logger.debug(f"URL响应状态码无效: {code}, URL: {url}")
                    return False, ms, str(code), None
                
                if is_html_ct(ct) or _looks_html(data):
                    logger.debug(f"URL返回HTML内容，非流媒体: {url}")
                    return False, ms, f"{code}/html", "timeout"
                
                if is_stream_ct(ct) and _looks_media(data):
                    logger.debug(f"URL确认是流媒体: {url}, 响应时间: {ms}ms")
                    return True, ms, str(code), "stream"
                
                if b"#EXTM3U" in data:
                    logger.debug(f"URL是M3U播放列表: {url}, 响应时间: {ms}ms")
                    return True, ms, str(code), "playlist"
                
                logger.debug(f"URL类型未知但有效: {url}, 响应时间: {ms}ms")
                return True, ms, str(code), "unknown"
        except Exception as e:
            ms = round((time.perf_counter() - s) * 1000, 2)
            logger.error(f"检查URL异常: {url}, 错误: {e}", exc_info=True)
            return False, ms, str(e), "timeout"

    def check_url(self, url, is_whitelist=False):
        t = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK
        if url_matches_domain_blacklist(url):
            logger.debug(f"URL被域名黑名单拦截: {url}")
            return False, 0, "blacklist", "blacklist"
        
        if url.startswith(("http://", "https://")):
            return self.check_http(url, t)
        
        logger.debug(f"URL格式无效（非http/https）: {url}")
        return True, 0, "ok", "stream"

    # ---------- 远程源拉取 ----------
    def fetch_remote(self, urls):
        all_lines = []
        self.fetch_stats["total_urls"] = len(urls)
        logger.info(f"开始拉取 {len(urls)} 个远程节点")
        
        for raw_url in urls:
            self.fetch_stats["total_urls"] += 1
            try:
                safe_url = quote(unquote(raw_url), safe=":/?&=#%")
            except Exception:
                safe_url = raw_url
            
            try:
                ctx = ssl._create_unverified_context()
                req = urllib.request.Request(safe_url, headers={"User-Agent": Config.USER_AGENT})
                with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=15) as r:
                    c = r.read().decode("utf-8", errors="replace")
                    before = len(all_lines)
                    
                    # ---------- M3U 格式 ----------
                    if "#EXTM3U" in c[:200]:
                        name = ""
                        for l in c.splitlines():
                            l = l.strip()
                            if not l:
                                continue
                            
                            if l.startswith("#EXTINF"):
                                m_group = re.search(r'group-title\s*=\s*["\']?([^"\',]+)', l)
                                name = m_group.group(1).strip() if m_group else (l.split(",")[-1].strip() if "," in l else "")
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
                        # ---------- 非 M3U ----------
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
                        self.fetch_stats["success"] += 1
                    elif got == 1:
                        diag = c[:500].replace("\n", "\\n").replace("\r", "")
                        logger.warning(f" ⚠ {raw_url[:90]} → 仅 {got} 个源 | 内容诊断: {diag}")
                        self.fetch_stats["success"] += 1
                    else:
                        preview = c[:200].replace("\n", "\\n")
                        logger.warning(f" ✗ {raw_url[:90]} → 0 个源 | 内容: {preview}")
                        self.fetch_stats["failed"] += 1
            except Exception as e:
                logger.error(f" ✗ {raw_url[:90]} → 异常: {e}", exc_info=True)
                self.fetch_stats["failed"] += 1
        
        logger.info(f"远程源拉取完成: 总节点 {self.fetch_stats['total_urls']} → 有效源 {len(all_lines)}")
        return all_lines

    # ---------- 手动白名单 ----------
    def load_whitelist(self):
        for line in self.read_file(FILE_PATHS["whitelist_manual"]):
            if line.startswith("#"):
                continue
            res, _ = clean_source_line(line)
            if res:
                self.whitelist_urls.add(res[1])
                self.whitelist_lines.append(line)
        logger.info(f"手动白名单: {len(self.whitelist_urls)} 个频道")

    # ---------- 去重/黑名单过滤 ----------
    def prepare_lines(self, lines):
        to_check, seen = [], set()
        for line in lines:
            res, reason = clean_source_line(line)
            if not res:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
                continue
            
            _, url = res
            if url in seen:
                logger.debug(f"URL重复: {url}, 已跳过")
                continue
            
            seen.add(url)
            if url in self.blacklist_urls:
                logger.debug(f"URL在黑名单中: {url}, 已跳过")
                continue
            
            to_check.append((url, line))
        
        logger.info(f"待检测 {len(to_check)} 条（去重后 {len(seen)} 条）")
        return to_check, []

    # ---------- 主流程 ----------
    def run(self):
        logger.info("===== 开始流媒体检测 =====")
        self.load_whitelist()
        
        lines = []
        # 远程源（urls.txt）
        start_fetch = time.time()
        urls = self.read_file(FILE_PATHS["urls"], split_by_space=True)
        if urls:
            logger.info(f"开始拉取 urls.txt 中的 {len(urls)} 个节点")
            fetched = self.fetch_remote(urls)
            logger.info(f"urls.txt 完成: {len(urls)} 个节点 → {len(fetched)} 个源，耗时: {time.time() - start_fetch:.2f}s")
            lines.extend(fetched)
        else:
            logger.warning("未找到 urls.txt")
        
        # 本地源（assets/my_urls/*.txt）
        start_local = time.time()
        my_urls_lines = self.read_my_urls_dir(MY_URLS_DIR)
        if my_urls_lines:
            logger.info(f"本地源（目录）产生 {len(my_urls_lines)} 条源，耗时: {time.time() - start_local:.2f}s")
            lines.extend(my_urls_lines)
        
        # 手动白名单 + 脚本传入
        lines.extend(self.whitelist_lines)
        lines.extend(self.manual_urls)
        
        # 去重、过滤黑名单后进行测速
        start_prepare = time.time()
        to_check, _ = self.prepare_lines(lines)
        logger.info(f"准备完成: 耗时 {time.time() - start_prepare:.2f}s, 待检测 {len(to_check)} 条")
        
        self.results = []
        start_check = time.time()
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as pool:
            fmap = {pool.submit(self.check_url, u, u in self.whitelist_urls): u for u, _ in to_check}
            for fut in as_completed(fmap):
                url = fmap[fut]
                try:
                    s, ms, code, kind = fut.result()
                    self.results.append((url, ms, code, kind))
                    if not s and url not in self.whitelist_urls:
                        self.new_failed_urls.add(url)
                except Exception as e:
                    logger.error(f"检测异常 {url}: {e}", exc_info=True)
                    self.new_failed_urls.add(url)
        
        self._save_blacklist()
        logger.info(f"测速完成: 耗时 {time.time() - start_check:.2f}s, 检测 {len(to_check)} 条URL")
        
        # 按频道分组
        self.channel_results = {}
        for url, ms, code, kind in self.results:
            if kind in ("timeout", "blacklist"):
                continue
            
            # 从URL中提取频道名（假设格式为 "频道名,URL"）
            if "," in url:
                channel_name = url.split(",")[0].strip()
            else:
                channel_name = "未知频道"
            
            if channel_name not in self.channel_results:
                self.channel_results[channel_name] = []
            
            self.channel_results[channel_name].append((url, ms, code, kind))
        
        # 速度优选每个频道
        self.optimize_by_speed_per_channel()

    # ==============================
    # 速度优选核心逻辑（按频道分组）
    # ==============================
    def optimize_by_speed_per_channel(self):
        logger.info("===== 开始按频道速度优选 =====")
        self.fastest_results = []
        total_channels = len(self.channel_results)
        total_optimized = 0
        
        for channel_name, channel_results in self.channel_results.items():
            # 按响应速度升序（ms 越小越快）
            channel_results.sort(key=lambda x: x[1])
            
            # 取速度最快的前 N 条（N = Config.MAX_FASTEST_PER_CHANNEL = 20）
            max_fast = Config.MAX_FASTEST_PER_CHANNEL
            channel_fastest = channel_results[:max_fast]
            
            kept, discarded = len(channel_fastest), len(channel_results) - len(channel_fastest)
            
            if discarded > 0:
                if channel_fastest:
                    slowest_ms = channel_fastest[-1][1]
                    fastest_ms = channel_fastest[0][1]
                    logger.info(
                        f"⚡ 频道 {channel_name} 速度优选: 有效源 {len(channel_results)} 条 → 保留最快 {kept} 条 | "
                        f"速度范围: {fastest_ms}ms ~ {slowest_ms}ms | 淘汰 {discarded} 条慢源"
                    )
                else:
                    logger.info(f"⚡ 频道 {channel_name} 速度优选: 有效源 {len(channel_results)} 条（≤{max_fast}），全部保留")
            else:
                logger.info(f"⚡ 频道 {channel_name} 速度优选: 有效源 {len(channel_results)} 条，全部保留")
            
            self.fastest_results.extend(channel_fastest)
            total_optimized += kept
        
        logger.info(f"===== 速度优选完成 | 总共优化 {total_channels} 个频道 | 保留 {total_optimized} 条源 =====")
        
        # 写入文件
        self.write_files()

    # ==============================
    # 写入文件
    # ==============================
    def write_files(self):
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        
        # whitelist_respotime.txt —— 保留全量测速记录（含超时/黑名单），方便排查
        start_write = time.time()
        self.results.sort(key=lambda x: ({"stream": 0, "playlist": 1, "unknown": 2}.get(x[3], 3), x[1]))
        with open(FILE_PATHS["whitelist_respotime"], "w", encoding="utf-8") as f:
            f.write(f"更新时间,#genre#\n{bj.strftime('%Y%m%d %H:%M')}\n\n")
            for url, ms, code, kind in self.results:
                f.write(f"{ms},{url},{code},{kind}\n")
        logger.info(f"写入whitelist_respotime.txt完成: 耗时 {time.time() - start_write:.2f}s")
        
        # whitelist_auto.txt —— 仅写入速度最快的前 N 条（最终给 live.txt 使用）
        start_write_auto = time.time()
        with open(FILE_PATHS["whitelist_auto"], "w", encoding="utf-8") as f:
            f.write(f"更新时间,#genre#\n{bj.strftime('%Y%m%d %H:%M')}\n\n")
            for url, _, _, kind in self.fastest_results:
                f.write(f"自动,{url}\n")
        logger.info(f"写入whitelist_auto.txt完成: 耗时 {time.time() - start_write_auto:.2f}s")

    # ==============================
    # 统计日志
    # ==============================
    def log_statistics(self):
        total = len(self.results)
        stream = sum(1 for *_, k in self.results if k == "stream")
        playlist = sum(1 for *_, k in self.results if k == "playlist")
        unknown = sum(1 for *_, k in self.results if k == "unknown")
        timeout_count = sum(1 for *_, k in self.results if k == "timeout")
        elapsed = (datetime.now() - self.start_time).seconds
        
        logger.info(
            f"===== 检测完成 | 总计:{total} | 流:{stream} | 列表:{playlist} | "
            f"未知:{unknown} | 超时:{timeout_count} | 最快{len(self.fastest_results)}条入白名单 | 耗时:{elapsed}s ====="
        )
        
        # 打印清洗统计
        logger.info(f"清洗统计: {self.clean_stats}")
        
        # 打印拉取统计
        logger.info(f"远程源拉取统计: {self.fetch_stats}")

# ==============================================
# 主函数
# ==============================================
def main():
    try:
        logger.info("===== 开始执行 =====")
        checker = StreamChecker()
        checker.run()
        
        # 统计日志
        checker.log_statistics()
        
        git_commit_push()
        logger.info("===== 全部流程执行完成 =====")
    except Exception as e:
        logger.error(f"主程序异常: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
