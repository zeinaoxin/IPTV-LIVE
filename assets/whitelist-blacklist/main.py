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

# ===================== 【成功关键】纯相对路径（之前可用的写法！）=====================
# 脚本位置：assets/whitelist-blacklist/main.py
# 目标文件：assets/my_urls.txt
MY_URLS_FILE = "../my_urls.txt"

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler("log.txt", mode='w', encoding='utf-8'), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===================== 【成功版】获取Token =====================
def get_token():
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request("https://www.taoiptv.com", headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=10) as f:
            html = f.read().decode('utf-8')
        token = re.search(r'[a-f0-9]{16}', html, re.I).group()
        logger.info(f"✅ 获取Token成功: {token}")
        return token
    except:
        return None

# ===================== 【成功版】更新my_urls.txt =====================
def update_file(token):
    if not token: return
    try:
        # 读取
        with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        # 替换
        new_content = re.sub(r'token=[a-f0-9]{16}', f'token={token}', content, re.I)
        # 写入
        with open(MY_URLS_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        logger.info("✅ my_urls.txt 更新成功！")
    except Exception as e:
        logger.error(f"错误: {e}")

# ===================== 以下是原项目完整代码，未做任何修改 =====================
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
        return host.lower() in (d for d in DOMAIN_BLACKLIST) or host.lower().endswith(tuple(DOMAIN_BLACKLIST))
    except:
        return False

VOD_DOMAINS = {"kwimgs.com","kuaishou.com","ixigua.com","douyin.com"}
VOD_EXTENSIONS = {".mp4",".mkv",".avi",".wmv",".mov",".rmvb"}
IMAGE_EXTENSIONS = {".jpg",".jpeg",".png",".gif",".webp",".bmp",".svg"}

def is_vod_or_image_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname.lower() if urlparse(url).hostname else ""
        if host in VOD_DOMAINS: return True
        path = urlparse(url).path.lower()
        return path.endswith(tuple(VOD_EXTENSIONS)) or path.endswith(tuple(IMAGE_EXTENSIONS))
    except:
        return False

CLEAN_OK = "ok"
CLEAN_NO_FORMAT = "no_format"
CLEAN_EMPTY_NAME = "empty_name"
CLEAN_BAD_URL = "bad_url"
CLEAN_DOMAIN_BL = "domain_blacklist"
CLEAN_VOD = "vod_filtered"

def clean_source_line(line: str) -> Tuple[Optional[Tuple[str, str]], str]:
    if not line or ',' not in line or '://' not in line:
        return None, CLEAN_NO_FORMAT
    parts = line.split(',')
    if len(parts) < 2: return None, CLEAN_BAD_URL
    name, url = parts[0].strip(), parts[1].strip()
    if not name: return None, CLEAN_EMPTY_NAME
    if not url: return None, CLEAN_BAD_URL
    if url_matches_domain_blacklist(url): return None, CLEAN_DOMAIN_BL
    if is_vod_or_image_url(url): return None, CLEAN_VOD
    return (name, url), CLEAN_OK

STREAM_LIKE_CT = ["video/mp2t","video/mp4","application/vnd.apple.mpegurl"]
def is_stream_like_ct(ct): return any(x in ct.lower() for x in STREAM_LIKE_CT) if ct else False
def is_html_ct(ct): return "text/html" in ct.lower() if ct else False

def _read_first_chunk(resp, size=4096):
    try: return resp.read(size)
    except: return b""

def _looks_like_media(d):
    return d[:3] == b"FLV" or (len(d)>=8 and d[4:8]==b"ftyp") or d[:3]==b"ID3" or (len(d)>=188 and d[0]==0x47)

def _looks_like_html(d):
    d = d.lstrip(b'\xef\xbb\xbf')
    return d[:5].lower().startswith((b'<!doc',b'<html'))

def parse_m3u8_segments(c):
    segs = []
    lines = c.splitlines()
    for i,l in enumerate(lines):
        if l.startswith("#EXTINF"):
            for j in range(i+1, len(lines)):
                cl = lines[j].strip()
                if cl and not cl.startswith("#"):
                    segs.append(cl)
                    break
    return segs

class StreamChecker:
    def __init__(self, m=None):
        self.start = datetime.now()
        self.blacklist = set()
        self.whitelist = set()
        self.whitelist_lines = []
        self.new_failed = set()
        self.manual = m or []

    def _check_ipv6(self):
        try:
            s = socket.socket(socket.AF_INET6)
            s.settimeout(1)
            res = s.connect_ex(('2001:4860:4860::8888',53))
            s.close()
            return res == 0
        except: return False

    def _load_blacklist(self):
        bl = set()
        try:
            with open("blacklist_auto.txt", encoding='utf-8') as f:
                for l in f:
                    l = l.strip()
                    if l and not l.startswith(('更新','#')):
                        u = l.split(',')[-1].split('$')[0].split('#')[0].strip()
                        if u: bl.add(u)
        except: pass
        return bl

    def _save_blacklist(self):
        if not self.new_failed: return
        try:
            lines = []
            with open("blacklist_auto.txt", encoding='utf-8') as f:
                lines = [x.rstrip('\n') for x in f]
            now = datetime.now(timezone.utc)+timedelta(hours=8)
            if not any(l.startswith("更新时间") for l in lines[:5]):
                lines = [f"更新时间,#genre#",f"{now.strftime('%Y%m%d %H:%M')},url","","blacklist,#genre#"]
            exist = set()
            for l in lines:
                if l and not l.startswith(('更新','#')):
                    u = l.split(',')[-1].strip()
                    if u: exist.add(u)
            for u in self.new_failed:
                if u not in exist: lines.append(u)
            with open("blacklist_auto.txt",'w',encoding='utf-8') as f:
                f.write('\n'.join(lines))
        except: pass

    def read_file(self, p, sp=False):
        try:
            with open(p, encoding='utf-8') as f: c = f.read()
            if sp: return [x.strip() for x in re.split(r'[\s\t\n]',c) if x.strip().startswith('http')]
            return [x.strip() for x in c.splitlines() if x.strip()]
        except: return []

    def check_http(self, u, t):
        s = time.perf_counter()
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname=False
            ctx.verify_mode=ssl.CERT_NONE
            req = urllib.request.Request(u, headers={"User-Agent":Config.USER_AGENT})
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=t) as r:
                code = r.getcode()
                ct = r.headers.get("Content-Type","")
                d = _read_first_chunk(r)
                e = round((time.perf_counter()-s)*1000,2)
                ok = 200<=code<400 or code in (301,302)
                if not ok: return False,e,str(code),None
                if is_html_ct(ct) or _looks_like_html(d): return False,e,f"{code}/html","timeout"
                if is_stream_like_ct(ct) and _looks_like_media(d): return True,e,str(code),"stream"
                if b"#EXTM3U" in d: return True,e,str(code),"playlist"
                return True,e,str(code),"unknown"
        except: return False,round((time.perf_counter()-s)*1000,2),"err","timeout"

    def _hls(self, u, t):
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname=False
            ctx.verify_mode=ssl.CERT_NONE
            req = urllib.request.Request(u, headers={"User-Agent":Config.USER_AGENT})
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=t) as r:
                c = r.read(65536).decode('utf-8','ignore')
            segs = parse_m3u8_segments(c)
            if not segs: return False
            for s in segs[:2]:
                su = urljoin(u,s)
                self.check_http(su,2)
            return True
        except: return False

    def check_url(self, u, w=False):
        t = Config.TIMEOUT_WHITELIST if w else Config.TIMEOUT_CHECK
        if url_matches_domain_blacklist(u): return False,0,"black","blacklist"
        if u.startswith(('http://','https://')):
            ok,e,c,k = self.check_http(u,t)
            if ok and k=="playlist": self._hls(u,3)
            return ok,e,c,k
        return True,0,"ok","stream"

    def fetch(self, us):
        res = []
        for u in us:
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname=False
                ctx.verify_mode=ssl.CERT_NONE
                req = urllib.request.Request(u, headers={"User-Agent":Config.USER_AGENT})
                with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=15) as f:
                    c = f.read().decode('utf-8','replace')
                if "#EXTM3U" in c[:200]:
                    n = ""
                    for l in c.splitlines():
                        if l.startswith("#EXTINF"): n = l.split(',')[-1] if ',' in l else ""
                        elif l.startswith(('http','rtmp')) and n:
                            res.append(f"{n.strip()},{l.strip()}")
                            n = ""
                else:
                    for l in c.splitlines():
                        l = l.strip()
                        if l and ',' in l and '://' in l: res.append(l)
            except: continue
        return res

    def run(self):
        self.blacklist = self._load_blacklist()
        lines = []
        lines += self.fetch(self.read_file("../urls.txt",True))
        lines += self.fetch(self.read_file(MY_URLS_FILE,True))
        for l in self.read_file("whitelist_manual.txt"):
            if not l.startswith('#'): lines.append(l)
        lines += self.manual
        
        check = []
        seen = set()
        for l in lines:
            r, k = clean_source_line(l)
            if not r: continue
            n, u = r
            if u in seen: continue
            seen.add(u)
            if u in self.blacklist: continue
            check.append((u,l))
        
        results = []
        with ThreadPoolExecutor(Config.MAX_WORKERS) as e:
            fs = {e.submit(self.check_url,u,u in self.whitelist):(u,l) for u,l in check}
            for f in as_completed(fs):
                u,l = fs[f]
                try: results.append((u,)+f.result())
                except: results.append((u,0,"err","timeout"))
        
        self._save_blacklist()
        now = datetime.now(timezone.utc)+timedelta(hours=8)
        with open("whitelist_respotime.txt",'w',encoding='utf-8') as f:
            f.write(f"更新时间,#genre#\n{now.strftime('%Y%m%d %H:%M')}\n\n")
            for u,e,c,k in sorted(results,key=lambda x:(0 if x[3]=="stream" else 1 if x[3]=="playlist" else 2,x[1])):
                f.write(f"{e},{u},{c},{k}\n")
        with open("whitelist_auto.txt",'w',encoding='utf-8') as f:
            f.write(f"更新时间,#genre#\n{now.strftime('%Y%m%d %H:%M')}\n\n")
            for u,e,c,k in sorted(results,key=lambda x:x[1]):
                if k not in ("timeout","blacklist"): f.write(f"自动,{u}\n")

# ===================== 主函数（先更新Token，再运行）=====================
if __name__ == "__main__":
    # 核心：先更新my_urls.txt
    token = get_token()
    update_file(token)
    # 运行原程序
    sc = StreamChecker()
    sc.run()
