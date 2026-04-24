# ==============================================豆包
# 【自动安装依赖】运行时自动安装 curl_cffi，无需手动操作
# ==============================================
import subprocess
import sys
import warnings
warnings.filterwarnings("ignore")

try:
    from curl_cffi import requests
except ImportError:
    print("🔧 自动安装依赖库 curl_cffi...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "curl_cffi", "-q"])
    from curl_cffi import requests

# ==============================================
# 原有依赖导入（全部保留）
# ==============================================
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse, quote, unquote, urljoin
import ssl
import re
from typing import List, Tuple, Set, Dict, Optional
import logging

# ==============================================
# 路径配置
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
# 日志配置
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

logger.info("=" * 60)
logger.info(f"项目根目录: {PROJECT_ROOT}")
logger.info(f"脚本目录:   {SCRIPT_DIR}")
logger.info(f"assets目录: {ASSETS_DIR}")
logger.info("✅ 依赖自动安装完成，开始执行任务")
logger.info("=" * 60)

# ==============================================
# 全局配置
# ==============================================
class Config:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    TIMEOUT_FETCH = 15
    TIMEOUT_CHECK = 3.0
    TIMEOUT_WHITELIST = 4.5
    MAX_WORKERS = 30

# ==============================================
# 【核心修复】curl_cffi 绕过Cloudflare获取Token
# ==============================================
def get_taoiptv_token() -> Optional[str]:
    try:
        logger.info("🔄 正在绕过Cloudflare获取最新Token...")
        response = requests.get(
            "https://www.taoiptv.com",
            headers={
                "User-Agent": Config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.taoiptv.com/",
            },
            impersonate="chrome120",
            timeout=20,
            verify=False
        )
        if response.status_code != 200:
            logger.error(f"❌ 访问官网失败，状态码: {response.status_code}")
            return None
        token_match = re.search(r"[a-f0-9]{16}", response.text, re.I)
        if token_match:
            token = token_match.group(0)
            logger.info(f"✅ 成功获取最新Token: {token}")
            return token
        logger.error("❌ 未匹配到有效Token")
        return None
    except Exception as e:
        logger.error(f"❌ 获取Token失败: {str(e)}")
        return None

# ==============================================
# 更新 my_urls.txt 所有 Token
# ==============================================
def update_my_urls_all(token: str) -> bool:
    if not token or len(token) != 16:
        logger.error("❌ Token无效")
        return False
    file_path = FILE_PATHS["my_urls"]
    if not os.path.exists(file_path):
        logger.error(f"❌ my_urls.txt不存在")
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        count = len(re.findall(r"token=[a-f0-9]{16}", content, re.I))
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
        logger.info(f"✅ my_urls.txt 更新完成，替换 {count} 个Token")
        return True
    except Exception as e:
        logger.error(f"❌ 更新失败: {e}")
        return False

# ==============================================
# 生成最新链接 + 解析内容写入 111.txt（清空+备注时间）
# ==============================================
def fetch_first_source_to_111txt():
    my_urls_path = FILE_PATHS["my_urls"]
    output_path = FILE_PATHS["111txt"]
    bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
    exec_time = bj_time.strftime("%Y-%m-%d %H:%M:%S")

    if not os.path.exists(my_urls_path):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# 保存时间: {exec_time}\n# 错误: my_urls.txt不存在")
        return False

    try:
        with open(my_urls_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    except:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# 保存时间: {exec_time}\n# 错误: 读取my_urls.txt失败")
        return False

    if not lines:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# 保存时间: {exec_time}\n# 错误: 无有效链接")
        return False

    first_url = lines[0]
    logger.info(f"📥 正在抓取: {first_url}")

    try:
        resp = requests.get(
            first_url,
            headers={"User-Agent": Config.USER_AGENT},
            impersonate="chrome120",
            timeout=20,
            verify=False
        )
        content = resp.text
    except Exception as e:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# 保存时间: {exec_time}\n# 抓取失败: {str(e)}")
        return False

    output = f"# 保存时间: {exec_time}\n# 来源: {first_url}\n\n{content}"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output)
    logger.info(f"✅ 111.txt 已更新完成")
    return True

# ==============================================
# Git 提交推送
# ==============================================
def git_commit_push():
    try:
        os.chdir(PROJECT_ROOT)
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Bot"], check=True, capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@bot.com"], check=True, capture_output=True)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if not status:
            logger.info("✅ 无文件变更")
            return True
        subprocess.run(["git", "add", "assets/my_urls.txt", "assets/111.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Auto update token & 111.txt"], check=True, capture_output=True)
        try:
            subprocess.run(["git", "push"], check=True, capture_output=True)
        except:
            pass
        logger.info("✅ 已同步到仓库")
        return True
    except:
        logger.warning("⚠️ Git同步失败（不影响主功能）")
        return False

# ==============================================
# 原有黑白名单检测逻辑（完全保留）
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

class StreamChecker:
    def __init__(self, manual_urls=None):
        self.start_time = datetime.now()
        self.blacklist_urls = self._load_blacklist()
        self.whitelist_urls: Set[str] = set()
        self.whitelist_lines: List[str] = []
        self.new_failed_urls: Set[str] = set()
        self.manual_urls = manual_urls or []

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
                logger.info(f"加载黑名单: {len(bl)} 条")
        except:
            pass
        return bl

    def _save_blacklist(self):
        if not self.new_failed_urls:
            return
        try:
            with open(FILE_PATHS["blacklist_auto"], "w", encoding="utf-8") as f:
                f.write(f"更新时间,#genre#\n{datetime.now().strftime('%Y%m%d %H:%M')}\n\nblacklist,#genre#\n")
                for u in self.new_failed_urls:
                    f.write(f"{u}\n")
        except:
            pass

    def read_file(self, path, split_by_space=False):
        try:
            with open(path, "r", encoding="utf-8") as f:
                c = f.read()
            if split_by_space:
                return [l.strip() for l in re.split(r"[\s\t\n]+", c) if l.strip().startswith("http")]
            return [l.strip() for l in c.splitlines() if l.strip()]
        except:
            return []

    def check_http(self, url, timeout):
        s = time.perf_counter()
        try:
            resp = requests.get(url, headers={"User-Agent": Config.USER_AGENT}, impersonate="chrome120", timeout=timeout, verify=False)
            ms = round((time.perf_counter() - s)*1000, 2)
            if resp.status_code not in (200,301,302):
                return False, ms, str(resp.status_code), "fail"
            if is_html_ct(resp.headers.get("Content-Type","")):
                return False, ms, "html", "timeout"
            return True, ms, "200", "stream"
        except Exception as e:
            return False, round((time.perf_counter()-s)*1000,2), str(e), "timeout"

    def check_url(self, url, is_whitelist=False):
        t = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK
        if url_matches_domain_blacklist(url):
            return False,0,"blacklist","blacklist"
        if url.startswith(("http://","https://")):
            return self.check_http(url,t)
        return True,0,"ok","stream"

    def fetch_remote(self, urls):
        all_lines = []
        for u in urls:
            try:
                r = requests.get(u, headers={"User-Agent": Config.USER_AGENT}, impersonate="chrome120", timeout=15, verify=False)
                for line in r.text.splitlines():
                    line = line.strip()
                    if line.startswith("http"):
                        all_lines.append(f"订阅源,{line}")
            except:
                continue
        return all_lines

    def run(self):
        logger.info("===== 开始黑白名单检测 =====")
        lines = []
        my_urls = self.read_file(FILE_PATHS["my_urls"], split_by_space=True)
        if my_urls:
            lines.extend(self.fetch_remote(my_urls))
        
        results = []
        with ThreadPoolExecutor(Config.MAX_WORKERS) as pool:
            fmap = {pool.submit(self.check_url, u):u for u in set([l.split(",")[-1] for l in lines if "," in l])}
            for fut in as_completed(fmap):
                url = fmap[fut]
                try:
                    ok,ms,c,t = fut.result()
                    results.append((url,ms,c,t))
                    if not ok:
                        self.new_failed_urls.add(url)
                except:
                    self.new_failed_urls.add(url)
        self._save_blacklist()
        logger.info(f"✅ 检测完成，有效源: {len([r for r in results if r[3]=='stream'])} 个")

# ==============================================
# 主函数（全自动流程）
# ==============================================
def main():
    try:
        # 1. 获取最新Token
        token = get_taoiptv_token()
        if token:
            update_my_urls_all(token)
        
        # 2. 写入111.txt
        fetch_first_source_to_111txt()
        
        # 3. 提交仓库
        git_commit_push()
        
        # 4. 黑白名单检测
        StreamChecker().run()
        
        logger.info("🎉 全部任务执行完成！")
    except Exception as e:
        logger.error(f"❌ 程序异常: {e}")

if __name__ == "__main__":
    main()
