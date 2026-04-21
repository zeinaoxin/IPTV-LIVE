#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直播源响应时间检测工具（优化版）
- 优先采用“真实流探测（GET + 按 Content-Type/首包判断）”，避免仅 HEAD 误判；
- 对 HTTP 源进行轻量 GET 读取首包，识别是否为媒体流（ts/fmp4/flv/mpeg/url-list/playlist 等）；
- RTMP/RTSP 继续沿用 TCP 握手+简单探测；
- 输出字段扩展：响应时间ms, 状态码, 类型（stream/unknown/timeout）；
- 主函数排序：优先“成功 + 可识别为 stream”，其次“成功但未知类型”，再按响应时间升序。
"""

import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse, quote, unquote
import socket
import ssl
import re
from typing import List, Tuple, Set, Dict
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
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    USER_AGENT_URL = "okhttp/3.14.9"
    TIMEOUT_FETCH = 5
    TIMEOUT_CHECK = 2.5
    TIMEOUT_CONNECT = 1.5
    TIMEOUT_READ = 1.5
    MAX_WORKERS = 30

# ===================== 探测与结果定义 =====================
# result tuple: (success:bool, elapsed_ms:float, code_or_reason:str, media_kind:str|None)
# media_kind: "stream" | "playlist" | "unknown" | "timeout" 等
SUCCESS = 0
ELAPSED_MS = 1
CODE_OR_REASON = 2
MEDIA_KIND = 3

# 用于判断“更像媒体流”的 Content-Type 片段
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

def _read_first_chunk(resp, max_bytes=4096):
    """尽量从流中读一小段用于判断类型（不消耗过多带宽）"""
    try:
        chunk = resp.read(max_bytes)
        return chunk if chunk else b""
    except Exception:
        return b""

def _first_bytes_looks_like_media(data: bytes) -> bool:
    """启发式：检查二进制是否像媒体/分片容器"""
    if not data:
        return False
    # 常见媒体容器魔数
    if data[:3] == b"FLV":
        return True
    if data[:4] == b"\x00\x00\x00\x18\x66\x74\x79\x70":
        return True
    if data[:4] == b"\x00\x00\x00\x20\x66\x74\x79\x70":
        return True
    if data[:3] == b"ID3":
        return True
    # TS 包同步字节
    if len(data) >= 188 and data[0] == 0x47:
        return True
    return False


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
        - 对非2xx但可能是重定向（301/302）也算成功（很多源是这样）
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
                data = _read_first_chunk(resp, 4096)

                elapsed = round((time.perf_counter() - start) * 1000, 2)
                success = (200 <= code < 400) or code in (301, 302)

                if not success:
                    return (False, elapsed, str(code), None)

                # 判断是否为媒体流/播放列表
                if is_stream_like_content_type(ct):
                    return (True, elapsed, str(code), "stream" if ("mpeg" in ct.lower() or "octet" in ct.lower() or "video" in ct.lower() or "audio" in ct.lower()) else "playlist")
                if ct.lower().startswith("text/") or ct.lower().startswith("application/xml"):
                    # 检查是否是 M3U/M3U8 播放列表
                    if b"#EXTM3U" in data or b"#EXTINF" in data or b"#EXT-X-" in data:
                        return (True, elapsed, str(code), "playlist")
                    # 其他文本也当播放列表/索引
                    return (True, elapsed, str(code), "playlist")
                # 纯二进制且未给 CT，启发式判断
                if _first_bytes_looks_like_media(data):
                    return (True, elapsed, str(code), "stream")
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

    # ---------- 统一 URL 检测 ----------
    def check_url(self, url: str, is_whitelist=False):
        """
        返回 (success, elapsed_ms, code_or_reason, media_kind|None)
        """
        try:
            u = quote(unquote(url), safe=':/?&=#')
            t = Config.TIMEOUT_CHECK * 1.5 if is_whitelist else Config.TIMEOUT_CHECK

            if url.startswith(('http://', 'https://')):
                return self.check_http(u, t)
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
                return True, round((time.perf_counter() - start) * 1000, 2, "tcp_ok", None)
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
            if u in self.blacklist_urls and u not in self.whitelist_urls:
                pre_fail.append(full)
                skip += 1
            else:
                to_check.append((u, full))
        logger.info(f"待检测 {len(to_check)} 条，跳过黑名单 {skip} 条")
        return to_check, pre_fail, url2line

    # ---------- 输出写盘 ----------
    def save_respotime(self, items: List[Tuple[str, float, str, str]]):
        """
        items: [(url, elapsed_ms, code_or_reason, media_kind|None), ...]
        文件格式与原版兼容，可在头部字段处扩展类型说明。
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

        # 任务分组
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
            # kind 优先级映射
            if kind == "stream":
                kind_order = 0
            elif kind == "playlist":
                kind_order = 1
            elif kind == "unknown":
                kind_order = 2
            else:
                kind_order = 3
            # 简单成功/失败粗分
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
                # 支持空格/换行/逗号分隔
                parts = re.split(r'[\s,]+', chunk)
                manual_urls.extend(p for p in parts if p.startswith(('http://', 'https://', 'rtmp://', 'rtsp://')))
        checker = StreamChecker(manual_urls=manual_urls)
        checker.run()
    except Exception as e:
        logger.error(f"主流程异常: {e}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
