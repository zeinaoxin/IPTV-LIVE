#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直播源响应时间检测工具（优化版 v4）
针对线上 live.txt 实测反馈的 7 大问题全面重构：
1) 修复：catvod 等坏域名仍然大量出现（增强域名黑名单 + 输出阶段拦截）
2) 修复：URL 被自动换行截断（输出时强制单行 + 去内部换行）
3) 修复：点播 MP4/图片混入直播列表（增加更多点播域名/后缀/图片路径过滤）
4) 修复：双逗号“频道名,,URL”与尾部垃圾字段（只取最后一个逗号分割，并截断 URL 后多余逗号）
5) 改进：单频道多源无优先级（保留 media_kind/stream/playlist/unknown/timeout 供主程序排序，本脚本保证输出格式一致）
6) 修复：no_host 等异常字段暴露（清洗阶段去除 URL 后的 ,no_host,- 等附加信息）
7) 保留：HLS 二次验证、全链路 URL 去重、自动域名黑名单累积
"""

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

# ===================== 文件路径 =====================
def get_file_paths():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    return {
        "urls": os.path.join(parent_dir, 'urls.txt'),
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
    USER_AGENT_URL = "okhttp/3.14.9"
    TIMEOUT_FETCH = 6
    TIMEOUT_CHECK = 3.0
    TIMEOUT_WHITELIST = 4.5
    TIMEOUT_CONNECT = 1.5
    TIMEOUT_READ = 2.0
    MAX_WORKERS = 30
    FIRST_CHUNK_BYTES = 4096
    MIN_FIRST_CHUNK_FOR_STREAM = 256
    HLS_SAMPLE_SEGMENTS = 2
    HLS_SEGMENT_TIMEOUT = 2.5
    # 同一频道最多保留多少源（本脚本暂不做频道名聚合，留给 main.py；此处仅做 URL 去重）
    MAX_SOURCES_PER_CHANNEL = 50  # 防止极端情况

# ===================== 域名黑名单（更强） =====================
DOMAIN_BLACKLIST: Set[str] = set()

def _init_domain_blacklist():
    """
    初始化域名黑名单：
    1) 硬编码已知坏域名
    2) 从 blacklist_auto.txt 自动补充域名（累积效果）
    """
    global DOMAIN_BLACKLIST
    hardcoded = {
        "iptv.catvod.com",
        # 其他反馈较多的坏域名（可根据后续反馈继续追加）
        "dd.ddzb.fun",
        "goodiptv.club",
        "jiaojirentv.top",
        "alist.xicp.fun",
        "rihou.cc",
        "php.jdshipin.com",
    }

    auto_domains: Set[str] = set()
    try:
        if os.path.exists(FILE_PATHS["blacklist_auto"]):
            with open(FILE_PATHS["blacklist_auto"], 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith(('更新时间', 'blacklist', '#')):
                        continue
                    url = line.split(',')[-1].strip() if ',' in line else line
                    try:
                        host = urlparse(url).hostname
                        if host:
                            auto_domains.add(host.lower())
                    except Exception:
                        pass
    except Exception:
        pass

    DOMAIN_BLACKLIST = hardcoded | auto_domains
    logger.info(
        f"域名黑名单: 硬编码 {len(hardcoded)} + 自动补充 {len(auto_domains)} = {len(DOMAIN_BLACKLIST)} 个域名"
    )

_init_domain_blacklist()

def url_matches_domain_blacklist(url: str) -> bool:
    """精确匹配或后缀匹配"""
    try:
        host = urlparse(url).hostname or ""
        if not host:
            return False
        host_lower = host.lower()
        for d in DOMAIN_BLACKLIST:
            d_lower = d.lower()
            if host_lower == d_lower or host_lower.endswith("." + d_lower):
                return True
    except Exception:
        pass
    return False

# ===================== 点播/图片 过滤 =====================
VOD_DOMAINS: Set[str] = {
    "kwimgs.com",
    "kuaishou.com",
    "ixigua.com",
    "douyin.com",
    "tiktokcdn.com",
    "bdstatic.com",        # 百度点播/短视频
    "byteimg.com",         # 字节图片
    "a.kwimgs.com",        # 快手资源
}
VOD_EXTENSIONS: Set[str] = {
    ".mp4", ".mkv", ".avi", ".wmv", ".mov", ".rmvb",  # 不含 .flv（可能是直播）
}

IMAGE_EXTENSIONS: Set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
}

def is_vod_or_image_url(url: str) -> bool:
    """判断是否为点播地址/图片（单文件视频 / 图片 / 已知点播平台）"""
    try:
        host = (urlparse(url).hostname or "").lower()
        for vd in VOD_DOMAINS:
            if host == vd or host.endswith("." + vd):
                return True
        path = urlparse(url).path.lower()
        # 图片路径直接过滤
        for ext in IMAGE_EXTENSIONS:
            if path.endswith(ext):
                return True
        # 视频（排除 .flv）
        for ext in VOD_EXTENSIONS:
            if path.endswith(ext):
                return True
    except Exception:
        pass
    return False

# ===================== 行格式清洗 =====================
# 清洗原因常量
CLEAN_OK = "ok"
CLEAN_NO_FORMAT = "no_format"       # 无逗号或无协议
CLEAN_EMPTY_NAME = "empty_name"
CLEAN_BAD_URL = "bad_url"
CLEAN_DOMAIN_BL = "domain_blacklist"
CLEAN_VOD = "vod_filtered"

def clean_source_line(line: str) -> Tuple[Optional[Tuple[str, str]], str]:
    """
    清洗单行源数据。
    - 去内部换行、去多余空格
    - 解决“双逗号”、“尾部垃圾字段（,no_host,- / ,0.47,1920x1080 等）”
    - 域名黑名单/点播过滤
    返回 ((频道名, url), reason)
    """
    if not line:
        return None, CLEAN_NO_FORMAT

    # 去内部换行与多余空白
    line = line.replace('\r', '').replace('\n', ' ').strip()
    if not line:
        return None, CLEAN_NO_FORMAT

    if ',' not in line or '://' not in line:
        return None, CLEAN_NO_FORMAT

    # 定位 URL 起始位置
    proto_idx = line.find('://')
    if proto_idx < 1:
        return None, CLEAN_BAD_URL

    # URL 前面的部分（可能包含频道名和多余逗号）
    prefix = line[:proto_idx - 1]  # 去掉 "http" 或 "https"

    # 取 prefix 中最后一个逗号作为分隔符（避免双逗号）
    comma_pos = prefix.rfind(',')
    if comma_pos < 0:
        return None, CLEAN_NO_FORMAT

    name = prefix[:comma_pos].strip()
    # 去除频道名中的多余空格
    name = re.sub(r'\s{2,}', ' ', name).strip()
    if not name:
        return None, CLEAN_EMPTY_NAME

    # URL 及其后可能的多余字段（延迟、分辨率、no_host 等）
    rest = line[comma_pos + 1:].strip()

    # 只取第一个逗号前的内容作为 URL（逗号不是合法 URL 字符）
    url = rest.split(',')[0].strip() if ',' in rest else rest

    # 清洗 URL
    url = url.split('$')[0].strip().split('#')[0].strip()
    if not url or '://' not in url:
        return None, CLEAN_BAD_URL

    # 域名黑名单
    if url_matches_domain_blacklist(url):
        return None, CLEAN_DOMAIN_BL

    # 点播/图片过滤
    if is_vod_or_image_url(url):
        return None, CLEAN_VOD

    return (name, url), CLEAN_OK

# ===================== 媒体类型判定 =====================
STREAM_LIKE_CT = [
    "video/mp2t", "video/mp4", "video/x-flv", "video/fmp4",
    "application/octet-stream",
    "application/vnd.apple.mpegurl", "application/x-mpegURL",
    "application/dash+xml",
    "audio/mpegurl", "audio/mpeg", "audio/aac", "audio/x-aac",
    "text/xml", "text/plain",
]

def is_stream_like_ct(ct: str) -> bool:
    if not ct:
        return False
    ct_lower = ct.lower()
    return any(p in ct_lower for p in STREAM_LIKE_CT)

def is_html_ct(ct: str) -> bool:
    if not ct:
        return False
    return "text/html" in ct.lower()

def _read_first_chunk(resp, max_bytes=4096):
    try:
        chunk = resp.read(max_bytes)
        return chunk if chunk else b""
    except Exception:
        return b""

def _looks_like_media(data: bytes) -> bool:
    """检查首包是否像媒体容器（TS/FLV/FMP4/ID3）"""
    if not data:
        return False
    if data[:3] == b"FLV":
        return True
    if len(data) >= 8 and data[:4] == b"\x00\x00\x00" and data[4:8] in (b"ftyp", b"ftyp"):
        return True
    if data[:3] == b"ID3":
        return True
    if len(data) >= 188 and data[0] == 0x47:
        return True
    # MP4 box: 00 00 00 xx 66 74 79 70
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return True
    return False

def _looks_like_html(data: bytes) -> bool:
    """检查首包是否像 HTML 页面 / JSON 鉴权响应"""
    if not data:
        return False
    d = data.lstrip(b'\xef\xbb\xbf').lstrip()
    if len(d) < 5:
        return False
    head = d[:20].lower()
    if head.startswith(b"<!doc") or head.startswith(b"<html") or head.startswith(b"<head"):
        return True
    # JSON 鉴权页
    if d[0:1] == b"{" and (b'"code"' in d[:500] or b'"error"' in d[:500] or b'"msg"' in d[:500]):
        return True
    # 纯文本错误页
    if len(d) < 200 and (b"403" in d[:50] or b"404" in d[:50] or b"forbidden" in d[:100].lower()):
        return True
    return False

# ===================== HLS 解析 =====================
def parse_m3u8_segments(content: str) -> List[str]:
    """从 M3U8 索引中提取分片路径列表"""
    lines = content.splitlines()
    segments: List[str] = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            for j in range(i + 1, len(lines)):
                l = lines[j].strip()
                if not l or l.startswith("#"):
                    continue
                segments.append(l)
                break
        elif line.startswith("#EXT-X-ENDLIST"):
            break
    return segments

# ===================== StreamChecker =====================
class StreamChecker:
    def __init__(self, manual_urls=None):
        self.start_time = datetime.now()
        self.ipv6_available = self._check_ipv6()
        self.blacklist_urls = self._load_blacklist()
        self.whitelist_urls: Set[str] = set()
        self.whitelist_lines: List[str] = []
        self.new_failed_urls: Set[str] = set()
        self.manual_urls = manual_urls or []
        # 清洗统计
        self.clean_stats: Dict[str, int] = {
            CLEAN_NO_FORMAT: 0,
            CLEAN_EMPTY_NAME: 0,
            CLEAN_BAD_URL: 0,
            CLEAN_DOMAIN_BL: 0,
            CLEAN_VOD: 0,
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

    # ---------- 黑名单 ----------
    def _load_blacklist(self) -> Set[str]:
        blacklist: Set[str] = set()
        try:
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith(('更新时间', 'blacklist', '#')):
                            continue
                        url = line.split(',')[-1].strip() if ',' in line else line
                        url = url.split('$')[0].split('#')[0].strip()
                        if '://' in url:
                            blacklist.add(url)
            logger.info(f"加载 URL 黑名单: {len(blacklist)} 条")
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
                if (line
                        and not line.startswith('更新时间')
                        and not line.startswith('blacklist')
                        and line.strip()):
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

    # ---------- 文件读取 ----------
    def read_file(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip()]
        except Exception:
            return []

    # ---------- SSL ----------
    def _ssl_ctx(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    # ---------- HTTP 探测 ----------
    def check_http(self, url: str, timeout: float):
        """
        返回 (success, elapsed_ms, code_or_reason, media_kind)
        - text/html 或首包像 HTML → 直接判定不可用
        """
        start = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": Config.USER_AGENT,
                "Connection": "close",
            }, method="GET")
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=self._ssl_ctx())
            )
            with opener.open(req, timeout=timeout) as resp:
                code = resp.getcode()
                ct = resp.headers.get("Content-Type") or ""
                data = _read_first_chunk(resp, Config.FIRST_CHUNK_BYTES)
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                success = (200 <= code < 400) or code in (301, 302)
                if not success:
                    return (False, elapsed, str(code), None)

                # ★ text/html → 网页/鉴权页，直接判定不可用
                if is_html_ct(ct):
                    return (False, elapsed, f"{code}/html", "timeout")

                # ★ 首包像 HTML（即使 CT 不对）→ 也不可用
                if _looks_like_html(data):
                    return (False, elapsed, f"{code}/html_body", "timeout")

                # 二进制媒体流
                if is_stream_like_ct(ct) and not ct.lower().startswith("text/"):
                    if _looks_like_media(data) and len(data) >= Config.MIN_FIRST_CHUNK_FOR_STREAM:
                        return (True, elapsed, str(code), "stream")
                    # 能连通但首包不够/无特征 → 可疑
                    return (True, elapsed, str(code), "unknown")

                # 文本类 → 播放列表
                if ct.lower().startswith("text/") or ct.lower().startswith("application/xml"):
                    if b"#EXTM3U" in data or b"#EXTINF" in data or b"#EXT-X-" in data:
                        return (True, elapsed, str(code), "playlist")
                    return (True, elapsed, str(code), "unknown")

                # 纯二进制无 CT
                if _looks_like_media(data):
                    if len(data) >= Config.MIN_FIRST_CHUNK_FOR_STREAM:
                        return (True, elapsed, str(code), "stream")
                    return (True, elapsed, str(code), "unknown")

                return (True, elapsed, str(code), "unknown")

        except urllib.error.HTTPError as e:
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            code = getattr(e, "code", None) or 0
            if code in (301, 302):
                return (True, elapsed, str(code), None)
            return (False, elapsed, str(code), None)
        except Exception as e:
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            return (False, elapsed, str(e) or "unknown", "timeout")

    # ---------- HLS 二次验证 ----------
    def _hls_probe_segment(self, seg_url: str, timeout: float) -> bool:
        try:
            req = urllib.request.Request(seg_url, headers={
                "User-Agent": Config.USER_AGENT,
                "Connection": "close",
            }, method="GET")
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=self._ssl_ctx())
            )
            with opener.open(req, timeout=timeout) as resp:
                code = resp.getcode()
                if not (200 <= code < 400 or code in (301, 302)):
                    return False
                data = _read_first_chunk(resp, 2048)
                if _looks_like_media(data):
                    return True
                # 能读到足够数据也认为分片存在
                return len(data) >= 64
        except Exception:
            return False

    def _hls_validate(self, playlist_url: str, timeout: float) -> bool:
        """对 HLS 索引做二次验证：抽样检查分片是否真实存在"""
        try:
            req = urllib.request.Request(playlist_url, headers={
                "User-Agent": Config.USER_AGENT,
                "Connection": "close",
            }, method="GET")
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=self._ssl_ctx())
            )
            with opener.open(req, timeout=timeout) as resp:
                code = resp.getcode()
                if not (200 <= code < 400 or code in (301, 302)):
                    return False
                content = resp.read(64 * 1024).decode("utf-8", errors="replace")
                segments = parse_m3u8_segments(content)
                if not segments:
                    return False
                # 相对路径转绝对路径
                abs_segs = [
                    urljoin(playlist_url, s) if not s.startswith("http") else s
                    for s in segments
                ]
                # 抽样：首 + 尾 + 中
                samples = [abs_segs[0]]
                if len(abs_segs) > 1:
                    samples.append(abs_segs[-1])
                if len(abs_segs) > 2:
                    samples.append(abs_segs[len(abs_segs) // 2])
                samples = list(dict.fromkeys(samples))[:Config.HLS_SAMPLE_SEGMENTS]
                ok = sum(1 for s in samples if self._hls_probe_segment(s, Config.HLS_SEGMENT_TIMEOUT))
                return ok > 0
        except Exception:
            return False

    # ---------- RTMP/RTSP ----------
    def check_rtmp_rtsp(self, url, timeout):
        start = time.perf_counter()
        try:
            parsed = urlparse(url)
            if not parsed.hostname:
                return False, 0
            port = parsed.port or (1935 if url.startswith('rtmp') else 554)
            ips: List[Tuple[str, int]] = []
            try:
                addrs = socket.getaddrinfo(
                    parsed.hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM
                )
                ips = [(a[4][0], a[0]) for a in addrs[:2]]
            except Exception:
                pass
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
                    else:
                        return True, round((time.perf_counter() - start) * 1000, 2)
                except Exception:
                    continue
                finally:
                    if s:
                        s.close()
            return False, round((time.perf_counter() - start) * 1000, 2)
        except Exception:
            return False, round((time.perf_counter() - start) * 1000, 2)

    # ---------- 统一 URL 检测 ----------
    def check_url(self, url: str, is_whitelist=False):
        """返回 (success, elapsed_ms, code_or_reason, media_kind)"""
        start = time.perf_counter()
        try:
            u = quote(unquote(url), safe=':/?&=#')
            t = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK

            # 域名黑名单
            if url_matches_domain_blacklist(u):
                return (False, 0, "domain_blacklist", "blacklist")

            if u.startswith(('http://', 'https://')):
                succ, elapsed, code_or_reason, kind = self.check_http(u, t)

                # HLS 二次验证
                if succ and kind == "playlist":
                    try:
                        with urllib.request.urlopen(
                            urllib.request.Request(u, headers={
                                "User-Agent": Config.USER_AGENT,
                                "Connection": "close",
                            }, method="GET"),
                            timeout=t,
                        ) as r:
                            sample = r.read(4096)
                            if b"#EXTM3U" in sample and (b"#EXT-X-" in sample or b"#EXTINF" in sample):
                                if not self._hls_validate(u, Config.HLS_SEGMENT_TIMEOUT + 1):
                                    # 索引能访问但分片全挂 → 降级
                                    return (True, elapsed, code_or_reason, "unknown")
                    except Exception:
                        pass

                return (succ, elapsed, code_or_reason, kind)

            elif u.startswith(('rtmp://', 'rtsp://')):
                ok, ms = self.check_rtmp_rtsp(u, t)
                return (ok, ms, None if ok else "rtmp/rtsp_fail", "stream" if ok else "timeout")

            else:
                parsed = urlparse(u)
                if not parsed.hostname:
                    return (False, 0, "no_host", None)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(Config.TIMEOUT_CONNECT)
                s.connect((parsed.hostname, parsed.port or 80))
                s.close()
                return (True, round((time.perf_counter() - start) * 1000, 2), "tcp_ok", None)

        except Exception as e:
            return (False, 0, str(e), "timeout")

    # ---------- 远程源拉取（带清洗） ----------
    def fetch_remote(self, urls):
        all_lines: List[str] = []
        for url in urls:
            try:
                req = urllib.request.Request(
                    quote(unquote(url), safe=':/?&=#'),
                    headers={"User-Agent": Config.USER_AGENT_URL}
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
        """解析 M3U 格式（带清洗）"""
        lines: List[str] = []
        name = ""
        for l in content.split('\n'):
            l = l.strip()
            if l.startswith("#EXTINF"):
                m = re.search(r',(.+)$', l)
                if m:
                    name = m.group(1).strip()
            elif l.startswith(('http://', 'https://', 'rtmp://', 'rtsp://')) and name:
                result, reason = clean_source_line(f"{name},{l}")
                if result:
                    lines.append(f"{result[0]},{result[1]}")
                else:
                    self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
                name = ""
        return lines

    def _parse_text(self, content):
        """解析 DIYP / 纯文本格式（带清洗）"""
        lines: List[str] = []
        for l in content.split('\n'):
            l = l.strip()
            if not l or l.startswith('#'):
                continue
            # 跳过 genre 标记行
            if l.endswith(',#genre#'):
                continue
            result, reason = clean_source_line(l)
            if result:
                lines.append(f"{result[0]},{result[1]}")
            else:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
        return lines

    # ---------- 白名单 ----------
    def load_whitelist(self):
        for line in self.read_file(FILE_PATHS["whitelist_manual"]):
            if line.startswith('#'):
                continue
            result, reason = clean_source_line(line)
            if result:
                name, url = result
                self.whitelist_urls.add(url)
                self.whitelist_lines.append(f"{name},{url}")
            else:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
        logger.info(f"手动白名单: {len(self.whitelist_urls)} 个频道")

    # ---------- 任务分组 ----------
    def prepare_lines(self, lines):
        to_check: List[Tuple[str, str]] = []     # (url, "name,url")
        pre_fail: List[str] = []
        skip = 0
        seen_urls: Set[str] = set()

        for line in lines:
            result, reason = clean_source_line(line)
            if not result:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
                continue

            name, url = result

            # URL 级别去重
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if url in self.blacklist_urls and url not in self.whitelist_urls:
                pre_fail.append(f"{name},{url}")
                skip += 1
            else:
                to_check.append((url, f"{name},{url}"))

        logger.info(
            f"待检测 {len(to_check)} 条，"
            f"跳过 {skip} 条（URL黑名单）"
        )
        # 打印清洗统计
        stats_parts = []
        for k, v in self.clean_stats.items():
            if v > 0:
                stats_parts.append(f"{k}={v}")
        if stats_parts:
            logger.info(f"格式清洗统计: {', '.join(stats_parts)}")

        return to_check, pre_fail

    # ---------- 输出 ----------
    def _ensure_single_line(text: str) -> str:
        """确保单行输出（去掉可能的内部换行）"""
        return text.replace('\r', '').replace('\n', ' ').strip()

    def save_respotime(self, items: List[Tuple[str, float, str, str]]):
        try:
            bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
            with open(FILE_PATHS["whitelist_respotime"], 'w', encoding='utf-8') as f:
                f.write("白名单测速,#genre#\n")
                f.write("更新时间,#genre#\n")
                f.write(f"{bj_time.strftime('%Y%m%d %H:%M')},url,耗时ms,状态码/备注,媒体类型\n\n")
                for url, elapsed, code_or_reason, kind in items:
                    url_single = self._ensure_single_line(url)
                    line = f"{elapsed},{url_single},{code_or_reason or '-'},{kind or '-'}"
                    f.write(line + "\n")
            logger.info(f"测速结果 → {FILE_PATHS['whitelist_respotime']} ({len(items)} 条)")
        except Exception as e:
            logger.error(f"保存测速结果失败: {e}")

    def save_whitelist_auto(self, items: List[Tuple[str, float, str, str]]):
        """
        同时输出 whitelist_auto.txt（清洗后的可用源列表）。
        格式: url（仅保留成功且非 timeout/blacklist 的条目）
        """
        try:
            bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
            with open(FILE_PATHS["whitelist_auto"], 'w', encoding='utf-8') as f:
                f.write(f"更新时间,#genre#\n")
                f.write(f"{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
                for url, elapsed, code_or_reason, kind in items:
                    if kind not in ("timeout", "blacklist"):
                        url_single = self._ensure_single_line(url)
                        f.write(url_single + "\n")
            count = sum(1 for _, _, _, k in items if k not in ("timeout", "blacklist"))
            logger.info(f"自动白名单 → {FILE_PATHS['whitelist_auto']} ({count} 条)")
        except Exception as e:
            logger.error(f"保存自动白名单失败: {e}")

    # ---------- 主流程 ----------
    def run(self):
        logger.info(f"===== 程序开始: {self.start_time.strftime('%Y%m%d %H:%M:%S')} =====")
        self.load_whitelist()

        # 收集所有频道行
        lines: List[str] = []
        urls = self.read_file(FILE_PATHS["urls"])
        if urls:
            remote_lines = self.fetch_remote(urls)
            lines.extend(remote_lines)
        lines.extend(self.whitelist_lines)
        for url in self.manual_urls:
            lines.append(url)

        # 分组（含清洗 + 去重）
        to_check, pre_fail = self.prepare_lines(lines)

        # 并发测速
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

        # 更新黑名单
        self._save_blacklist()

        # 排序：stream > playlist > unknown > timeout > blacklist
        def sort_key(item):
            _, elapsed, _, kind = item
            order = {"stream": 0, "playlist": 1, "unknown": 2}.get(kind, 3)
            return (order, elapsed)

        results_sorted = sorted(results, key=sort_key)

        # 写盘（强制单行输出）
        self.save_respotime(results_sorted)
        self.save_whitelist_auto(results_sorted)

        # 统计
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


# ===================== CLI =====================
def main():
    try:
        manual_urls: List[str] = []
        if not sys.stdin.isatty():
            for chunk in sys.stdin:
                chunk = chunk.strip()
                if not chunk:
                    continue
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
