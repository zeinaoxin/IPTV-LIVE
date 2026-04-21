#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直播源响应时间检测工具（优化版 v2）
- 引入“黑名单域名”机制，直接拦截不可用域名（如 iptv.catvod.com）。
- 对 HLS/M3U8 做二次验证：抓取分片 URL 并做轻量探测。
- 判断“真流”依据首包数据（TS/FLV/FMP4 等特征）和首包最小体积。
- 输出格式保持与上游兼容（whitelist_respotime.txt 前两列仍为 耗时ms,url）。
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
import io

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
    # UA
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    USER_AGENT_URL = "okhttp/3.14.9"

    # 超时（秒）
    TIMEOUT_FETCH = 5
    TIMEOUT_CHECK = 3.0        # 普通源检测超时
    TIMEOUT_WHITELIST = 4.5    # 白名单源给更长时间
    TIMEOUT_CONNECT = 1.5
    TIMEOUT_READ = 2.0

    # 并发
    MAX_WORKERS = 30

    # 探测参数
    FIRST_CHUNK_BYTES = 4096   # HTTP 探测时读取首包大小
    MIN_FIRST_CHUNK_FOR_STREAM = 256  # 判定为“真流”的最小首包字节（防假流）

    # HLS 二次验证：抽样分片数
    HLS_SAMPLE_SEGMENTS = 2    # 至少抽查 2 个分片（索引中靠前、靠后各 1 个）
    HLS_SEGMENT_TIMEOUT = 2.5  # 单个分片探测超时

# ===================== 结果常量 =====================
# result tuple: (success:bool, elapsed_ms:float, code_or_reason:str, media_kind:str|None)
SUCCESS = 0
ELAPSED_MS = 1
CODE_OR_REASON = 2
MEDIA_KIND = 3

# ===================== 域名黑名单（可自定义追加） =====================
# 注意：只保留域名部分，如 "iptv.catvod.com" 或 ".catvod.com"
DOMAIN_BLACKLIST: Set[str] = set()

def _init_domain_blacklist():
    """
    初始化域名黑名单。
    你可以直接在这里追加更多不可用域名，每行一个字符串。
    """
    global DOMAIN_BLACKLIST
    # 示例：将用户反馈的 iptv.catvod.com 加入域名黑名单
    DOMAIN_BLACKLIST = {
        # 用户反馈不可用的域名
        "iptv.catvod.com",
        # 你可以继续追加：
        # "bad-domain.example.com",
        # ".catvod.com",  # 会匹配所有 catvod.com 的子域名
    }

_init_domain_blacklist()

def url_matches_domain_blacklist(url: str) -> bool:
    """
    判断 URL 是否命中“域名黑名单”。
    - 精确匹配：host == 黑名单条目
    - 后缀匹配：host 以 “.” + 黑名单条目 结尾
    """
    try:
        host = urlparse(url).hostname or ""
        if not host:
            return False
        host_lower = host.lower()
        for d in DOMAIN_BLACKLIST:
            d_lower = d.lower().strip().lstrip(".")
            if host_lower == d_lower:
                return True
            if host_lower.endswith("." + d_lower):
                return True
    except Exception:
        pass
    return False

# ===================== 媒体类型判定辅助 =====================
STREAM_LIKE_CONTENT_TYPE_PARTS = [
    "video/mp2t",
    "video/mp4",
    "video/x-flv",
    "video/fmp4",
    "application/octet-stream",
    "application/vnd.apple.mpegurl",
    "application/x-mpegURL",
    "application/dash+xml",
    "audio/mpegurl",
    "audio/mpeg",
    "audio/aac",
    "audio/x-aac",
    "text/xml",          # 有时 HLS/M3U 会是 text/xml
    "text/plain",        # 部分源会返回纯文本分片列表
]

def is_stream_like_content_type(ct: str) -> bool:
    if not ct:
        return False
    ct_lower = ct.lower()
    return any(p in ct_lower for p in STREAM_LIKE_CONTENT_TYPE_PARTS)

def _read_first_chunk(resp, max_bytes: int = 4096) -> bytes:
    """尽量从流中读一小段用于判断类型（不消耗过多带宽）"""
    try:
        chunk = resp.read(max_bytes)
        return chunk if chunk else b""
    except Exception:
        return b""

def _first_bytes_looks_like_media(data: bytes) -> bool:
    """启发式：检查二进制是否像媒体/分片容器（TS/FLV/FMP4/ID3 等）"""
    if not data:
        return False
    if data[:3] == b"FLV":
        return True
    if data[:4] == b"\x00\x00\x00\x18\x66\x74\x79\x70":
        return True
    if data[:4] == b"\x00\x00\x00\x20\x66\x74\x79\x70":
        return True
    if data[:3] == b"ID3":
        return True
    if len(data) >= 188 and data[0] == 0x47:
        return True
    return False

# ===================== HLS/M3U8 简易解析 =====================
def parse_m3u8_index(content: str) -> List[str]:
    """
    从 M3U8 内容中提取分片 URL 列表（简单实现，覆盖绝大多数公开 HLS 源）。
    - 支持绝对路径与相对路径
    - 忽略加密信息、字幕流等
    """
    lines = content.splitlines()
    segments: List[str] = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            # 下一个非注释行大概率是分片路径
            for j in range(i + 1, len(lines)):
                l = lines[j].strip()
                if not l or l.startswith("#"):
                    continue
                segments.append(l)
                break
        elif line.startswith("#EXT-X-ENDLIST"):
            break
    return segments

# ===================== StreamChecker 主体 =====================
class StreamChecker:
    def __init__(self, manual_urls=None):
        self.start_time = datetime.now()
        self.ipv6_available = self._check_ipv6()
        self.blacklist_urls = self._load_blacklist()
        self.whitelist_urls: Set[str] = set()
        self.whitelist_lines: List[str] = []
        self.new_failed_urls: Set[str] = set()
        self.manual_urls = manual_urls or []

    # ---------- 网络能力检测 ----------
    def _check_ipv6(self):
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('2001:4860:4860::8888', 53))
            sock.close()
            return result == 0
        except Exception:
            return False

    # ---------- 黑名单读写 ----------
    def _load_blacklist(self) -> Set[str]:
        blacklist = set()
        try:
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('更新时间') or line.startswith('blacklist'):
                            continue
                        if ',' in line:
                            parts = line.split(',')
                            url = parts[-1].strip()
                        else:
                            url = line
                        if '://' in url:
                            blacklist.add(url)
            logger.info(f"加载黑名单: {len(blacklist)} 个链接")
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
                    existing_lines = [line.rstrip('\n') for line in f]
                for line in existing_lines[:3]:
                    if line.startswith('更新时间') or line.startswith('blacklist'):
                        has_header = True
            all_content = []
            if not has_header:
                bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
                version = f"{bj_time.strftime('%Y%m%d %H:%M')},url"
                all_content.extend(["更新时间,#genre#", version, "", "blacklist,#genre#"])

            existing_urls = set()
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
            logger.info(f"黑名单已更新: 新增 {len(self.new_failed_urls)} 个")
        except Exception as e:
            logger.error(f"保存黑名单失败: {e}")

    # ---------- 通用文件读取 ----------
    def read_file(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip()]
        except Exception:
            return []

    # ---------- SSL ----------
    def create_ssl_context(self):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    # ---------- HTTP：GET 轻量探测（读首包判断是否为媒体流） ----------
    def check_http(self, url: str, timeout: float):
        """
        返回 (success, elapsed_ms, code_or_reason, media_kind|None)
        - media_kind: "stream"/"playlist"/"unknown"/"timeout"
        - 对非 2xx 但可能是重定向（301/302）也视为成功（很多源是这样）
        - 若首包过小且无媒体特征，则视为可疑（大概率不可播放），media_kind 设为 "unknown"，由上层排序降权
        """
        start = time.perf_counter()
        try:
            headers = {
                "User-Agent": Config.USER_AGENT,
                "Connection": "close",
            }
            req = urllib.request.Request(url, headers=headers, method="GET")
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=self.create_ssl_context())
            )
            with opener.open(req, timeout=timeout) as resp:
                code = resp.getcode()
                ct = resp.headers.get("Content-Type") or ""
                data = _read_first_chunk(resp, Config.FIRST_CHUNK_BYTES)
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                success = (200 <= code < 400) or code in (301, 302)
                if not success:
                    return (False, elapsed, str(code), None)

                # 判断是否为媒体流/播放列表
                if is_stream_like_content_type(ct):
                    # 对二进制类媒体流，增加“首包最小体积”约束
                    if _first_bytes_looks_like_media(data) and len(data) >= Config.MIN_FIRST_CHUNK_FOR_STREAM:
                        return (True, elapsed, str(code), "stream")
                    else:
                        # 可能是假流或鉴权页
                        return (True, elapsed, str(code), "unknown")

                if ct.lower().startswith("text/") or ct.lower().startswith("application/xml"):
                    # 检查是否是 M3U/M3U8 播放列表
                    if b"#EXTM3U" in data or b"#EXTINF" in data or b"#EXT-X-" in data:
                        return (True, elapsed, str(code), "playlist")
                    # 其他文本也当播放列表/索引
                    return (True, elapsed, str(code), "playlist")

                # 纯二进制且未给 CT，启发式判断
                if _first_bytes_looks_like_media(data):
                    if len(data) >= Config.MIN_FIRST_CHUNK_FOR_STREAM:
                        return (True, elapsed, str(code), "stream")
                    else:
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
            reason = str(e) or "unknown"
            return (False, elapsed, reason, "timeout")

    # ---------- HLS/M3U8 二次验证 ----------
    def _hls_probe_segment(self, seg_url: str, timeout: float) -> bool:
        """
        对单个 HLS 分片做轻量探测：
        - 能连上且返回 2xx/3xx
        - 首包至少 1 字节（只要能读出数据就先认为是“分片存在”）
        - 优先判断媒体特征（0x47/FLV/FMP4/ID3），若无特征但能读到足够数据，也视为通过
        """
        try:
            start = time.perf_counter()
            headers = {
                "User-Agent": Config.USER_AGENT,
                "Connection": "close",
            }
            req = urllib.request.Request(seg_url, headers=headers, method="GET")
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=self.create_ssl_context())
            )
            with opener.open(req, timeout=timeout) as resp:
                code = resp.getcode()
                if not (200 <= code < 400 or code in (301, 302)):
                    return False
                data = _read_first_chunk(resp, 2048)  # 分片只需很小一段
                # 媒体特征
                if _first_bytes_looks_like_media(data):
                    return True
                # 能读到足够数据也认为分片存在（避免误杀）
                return len(data) >= 64
        except Exception:
            return False

    def _hls_validate_playlist(self, playlist_url: str, timeout: float) -> Tuple[bool, str]:
        """
        对 HLS 播放列表做“二次验证”：
        - 先 GET 下载索引（受限长度，避免巨大索引）
        - 解析出分片 URL 列表
        - 抽样若干分片（前/中/后），调用 _hls_probe_segment
        - 只要有一条分片通过，就认为该播放列表“可用”
        返回 (是否验证通过, 原因)
        """
        try:
            start = time.perf_counter()
            headers = {
                "User-Agent": Config.USER_AGENT,
                "Connection": "close",
            }
            req = urllib.request.Request(playlist_url, headers=headers, method="GET")
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=self.create_ssl_context())
            )
            # 限流：最多读 64KB 索引，避免抓巨大列表
            with opener.open(req, timeout=timeout) as resp:
                code = resp.getcode()
                if not (200 <= code < 400 or code in (301, 302)):
                    return False, f"索引状态码={code}"
                # 限制索引大小
                content = resp.read(64 * 1024).decode("utf-8", errors="replace")
                segments = parse_m3u8_index(content)
                if not segments:
                    return False, "索引中未找到分片路径"
                # 将相对路径转成绝对路径
                abs_segments = [
                    urljoin(playlist_url, s) if not s.startswith("http") else s
                    for s in segments
                ]
                # 抽样逻辑
                sample_urls: List[str] = []
                if len(abs_segments) == 1:
                    sample_urls = [abs_segments[0]]
                else:
                    # 取前 1、后 1
                    sample_urls.append(abs_segments[0])
                    if len(abs_segments) > 1:
                        sample_urls.append(abs_segments[-1])
                    # 若长度允许，再加 1 个中间位置
                    if len(abs_segments) > 2:
                        mid = len(abs_segments) // 2
                        sample_urls.append(abs_segments[mid])
                    # 去重
                    sample_urls = list(dict.fromkeys(sample_urls))
                # 逐个探测分片
                ok_count = 0
                for seg_url in sample_urls[:Config.HLS_SAMPLE_SEGMENTS]:
                    if self._hls_probe_segment(seg_url, Config.HLS_SEGMENT_TIMEOUT):
                        ok_count += 1
                if ok_count > 0:
                    return True, f"分片抽样通过({ok_count}/{len(sample_urls[:Config.HLS_SAMPLE_SEGMENTS])})"
                else:
                    return False, f"分片抽样全部失败(0/{len(sample_urls[:Config.HLS_SAMPLE_SEGMENTS])})"
        except Exception as e:
            return False, f"索引解析异常({e})"

    # ---------- RTMP/RTSP 探测（保持原有逻辑） ----------
    def check_rtmp_rtsp(self, url, timeout):
        start = time.perf_counter()
        try:
            parsed = urlparse(url)
            if not parsed.hostname:
                return False, 0
            port = parsed.port or (1935 if url.startswith('rtmp') else 554)
            ips = []
            try:
                addrs = socket.getaddrinfo(parsed.hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
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

    # ---------- 统一 URL 检测（带域名黑名单 + HLS 二次验证） ----------
    def check_url(self, url: str, is_whitelist=False):
        """
        返回 (success, elapsed_ms, code_or_reason, media_kind|None)
        - 新增：域名黑名单拦截；HLS 播放列表二次验证。
        """
        try:
            u = quote(unquote(url), safe=':/?&=#')
            t = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK

            # 1) 域名黑名单拦截
            if url_matches_domain_blacklist(u):
                return (False, 0, "domain_blacklist", "blacklist")

            if url.startswith(('http://', 'https://')):
                succ, elapsed, code_or_reason, kind = self.check_http(u, t)
                # 2) HLS 二次验证：若首次判定为 playlist 且成功，继续抽样分片
                if succ and kind == "playlist":
                    # 为了避免对非 HLS 的“假 M3U”误判，尽量只对典型 HLS 索引做二次验证
                    try:
                        with urllib.request.urlopen(
                            urllib.request.Request(
                                u,
                                headers={"User-Agent": Config.USER_AGENT, "Connection": "close"},
                                method="GET",
                            ),
                            timeout=t,
                        ) as r:
                            sample = r.read(4096)
                            # 只对明显是 HLS 索引的做二次验证
                            if b"#EXTM3U" in sample and (b"#EXT-X-" in sample or b"#EXTINF" in sample):
                                valid, reason = self._hls_validate_playlist(u, Config.HLS_SEGMENT_TIMEOUT + 1)
                                if not valid:
                                    logger.debug(f"HLS二次验证失败: {u} ({reason})")
                                    # 不视为“真可用”，降权
                                    return (True, elapsed, code_or_reason, "unknown")
                    except Exception:
                        pass
                return (succ, elapsed, code_or_reason, kind)
            elif url.startswith(('rtmp://', 'rtsp://')):
                ok, ms = self.check_rtmp_rtsp(u, t)
                kind = "stream" if ok else "timeout"
                return (ok, ms, None if ok else "rtmp/rtsp_fail", kind)
            else:
                start = time.perf_counter()
                parsed = urlparse(url)
                if not parsed.hostname:
                    return False, 0, "no_host", None
                p = parsed.port or 80
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(Config.TIMEOUT_CONNECT)
                s.connect((parsed.hostname, p))
                s.close()
                return True, round((time.perf_counter() - start) * 1000, 2), "tcp_ok", None
        except Exception as e:
            elapsed = round((time.perf_counter() - start) * 1000, 2) if 'start' in dir() else 0
            return (False, elapsed, str(e), "timeout")

    # ---------- 远程源拉取 ----------
    def fetch_remote(self, urls):
        all_lines = []
        for url in urls:
            try:
                req = urllib.request.Request(
                    quote(unquote(url), safe=':/?&=#'),
                    headers={"User-Agent": Config.USER_AGENT_URL}
                )
                with urllib.request.urlopen(req, timeout=Config.TIMEOUT_FETCH) as r:
                    c = r.read().decode('utf-8', 'replace')
                    if "#EXTM3U" in c:
                        lines = self.parse_m3u(c)
                    else:
                        lines = [l.strip() for l in c.split('\n') if l.strip() and '://' in l and ',' in l]
                    all_lines.extend(lines)
                    logger.info(f"获取 {url} → {len(lines)} 条")
            except Exception as e:
                logger.error(f"拉取失败 {url}: {e}")
        return all_lines

    # ---------- M3U 解析 ----------
    def parse_m3u(self, content):
        lines, name = [], ""
        for l in content.split('\n'):
            l = l.strip()
            if l.startswith("#EXTINF"):
                m = re.search(r',(.+)$', l)
                if m:
                    name = m.group(1).strip()
            elif l.startswith(('http://', 'https://', 'rtmp://', 'rtsp://')) and name:
                lines.append(f"{name},{l}")
                name = ""
        return lines

    # ---------- 白名单处理 ----------
    def load_whitelist(self):
        for line in self.read_file(FILE_PATHS["whitelist_manual"]):
            if ',' in line and '://' in line:
                n, u = line.split(',', 1)
                self.whitelist_urls.add(u.strip())
                self.whitelist_lines.append(line)
        logger.info(f"白名单: {len(self.whitelist_urls)} 个")

    # ---------- 任务分组 ----------
    def prepare_lines(self, lines):
        to_check, pre_fail, url2line = [], [], {}
        skip = 0
        for line in lines:
            if ',' not in line or '://' not in line:
                continue
            n, u = line.split(',', 1)
            u = u.strip().split('#')[0].split('$')[0]
            full = f"{n},{u}"
            url2line[u] = full

            # 优先使用域名黑名单拦截
            if url_matches_domain_blacklist(u):
                pre_fail.append(full)
                skip += 1
                continue

            if u in self.blacklist_urls and u not in self.whitelist_urls:
                pre_fail.append(full)
                skip += 1
            else:
                to_check.append((u, full))
        logger.info(f"待检测 {len(to_check)} 条，跳过黑名单（含域名黑名单） {skip} 条")
        return to_check, pre_fail, url2line

    # ---------- 输出写盘（保持与现有格式兼容） ----------
    def save_respotime(self, items: List[Tuple[str, float, str, str]]):
        """
        items: [(url, elapsed_ms, code_or_reason, media_kind|None), ...]
        文件头部字段兼容：根目录 main.py 仍然按前两列“耗时ms,url”读取。
        """
        try:
            bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
            with open(FILE_PATHS["whitelist_respotime"], 'w', encoding='utf-8') as f:
                f.write(f"白名单测速,#genre#\n")
                f.write(f"更新时间,#genre#\n")
                f.write(f"{bj_time.strftime('%Y%m%d %H:%M')},url,耗时ms,状态码/备注,媒体类型\n\n")

                for url, elapsed, code_or_reason, kind in items:
                    kind_str = kind or "-"
                    f.write(f"{elapsed},{url},{code_or_reason or '-'},{kind_str}\n")
            logger.info(f"白名单测速结果已写入: {FILE_PATHS['whitelist_respotime']} ({len(items)} 条)")
        except Exception as e:
            logger.error(f"保存白名单测速结果失败: {e}")

    # ---------- 主流程 ----------
    def run(self):
        logger.info(f"程序开始执行: {self.start_time.strftime('%Y%m%d %H:%M:%S')}")

        # 加载白名单
        self.load_whitelist()

        # 收集需要测速的行
        lines = []
        urls = self.read_file(FILE_PATHS["urls"])
        if urls:
            remote_lines = self.fetch_remote(urls)
            lines.extend(remote_lines)
        lines.extend(self.whitelist_lines)

        # 合并手动输入的URL
        for url in self.manual_urls:
            lines.append(url)

        # 任务分组（已含域名黑名单过滤）
        to_check, pre_fail, url2line = self.prepare_lines(lines)

        # 并发测速
        results = []
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

        # 排序：成功优先，其次“stream/playlist > unknown > timeout”，同级别按耗时升序
        def sort_key(item):
            url, elapsed, code_or_reason, kind = item
            if kind == "stream":
                kind_order = 0
            elif kind == "playlist":
                kind_order = 1
            elif kind == "unknown":
                kind_order = 2
            else:
                kind_order = 3
            succ = 0 if kind not in ("timeout",) else 1
            return (succ, kind_order, elapsed)

        results_sorted = sorted(results, key=sort_key)

        # 写盘（使用新版格式；根目录主程序只读第一、第二个逗号分隔字段，保持兼容）
        self.save_respotime(results_sorted)

        # 统计与耗时
        ok_cnt = sum(1 for _, _, _, k in results if k not in ("timeout",))
        stream_cnt = sum(1 for _, _, _, k in results if k == "stream")
        playlist_cnt = sum(1 for _, _, _, k in results if k == "playlist")
        unknown_cnt = sum(1 for _, _, _, k in results if k == "unknown")
        logger.info(
            f"检测完成: 总计 {len(results)} 条，"
            f"成功 {ok_cnt}（流 {stream_cnt}，列表 {playlist_cnt}，未知 {unknown_cnt}），"
            f"超时/失败 {len(results) - ok_cnt}"
        )
        logger.info(f"总耗时: {(datetime.now() - self.start_time).seconds}s")

# ===================== CLI =====================
def main():
    checker = None
    try:
        manual_urls = []
        if not sys.stdin.isatty():
            for chunk in sys.stdin:
                chunk = chunk.strip()
                if not chunk:
                    continue
                parts = re.split(r'[\s,]+', chunk)
                manual_urls.extend(p for p in parts if p.startswith(('http://', 'https://', 'rtmp://', 'rtsp://')))
        checker = StreamChecker(manual_urls=manual_urls)
        checker.run()
    except Exception as e:
        logger.error(f"主流程异常: {e}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
