#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IPTV 直播源聚合生成工具（配套优化版）
- 与优化版测速脚本完全配套，读取新版 whitelist_respotime.txt 的全部字段
- 按"可用性 > 媒体类型 > 响应速度"三级排序：stream > playlist > unknown > timeout/blacklist
- 黑名单 URL 最早过滤；手动白名单始终保留且置顶
- 同频道多源保留前 N 个最优源（可配）
- 频道名模糊去重（CCTV1/CCTV-1/CCTV 1 视为同一频道）
- 同时输出 result.txt（DIYP 格式）和 result.m3u（标准 M3U 格式）
- UTF-8 BOM 编码，兼容 DIYP/TVBox 等播放器
"""

import os
import re
import time
import urllib.request
import ssl
from datetime import datetime, timedelta, timezone
from collections import defaultdict, OrderedDict
from typing import List, Tuple, Dict, Set, Optional
import logging
import sys

# ===================== 路径配置 =====================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(CURRENT_DIR, 'assets', 'whitelist-blacklist')

PATHS = {
    "urls":                os.path.join(CURRENT_DIR, 'urls.txt'),
    "whitelist_respotime": os.path.join(ASSETS_DIR, 'whitelist_respotime.txt'),
    "whitelist_manual":    os.path.join(ASSETS_DIR, 'whitelist_manual.txt'),
    "whitelist_auto":      os.path.join(ASSETS_DIR, 'whitelist_auto.txt'),
    "blacklist_auto":      os.path.join(ASSETS_DIR, 'blacklist_auto.txt'),
    "result_txt":          os.path.join(CURRENT_DIR, 'result.txt'),
    "result_m3u":          os.path.join(CURRENT_DIR, 'result.m3u'),
    "log":                 os.path.join(ASSETS_DIR, 'log_main.txt'),
}

# ===================== 日志 =====================
os.makedirs(os.path.dirname(PATHS["log"]), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(PATHS["log"], mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# ===================== 配置 =====================
class Config:
    # 媒体类型优先级（数字越小越优先）
    MEDIA_KIND_PRIORITY = {
        "stream":   0,   # 真实媒体流（ts/fmp4/flv）
        "playlist": 1,   # 播放列表（m3u8/mpd，且通过了二次验证）
        "unknown":  2,   # 成功但类型不明或首包可疑
        "timeout":  99,  # 超时/失败
        "blacklist":100, # 域名黑名单拦截
    }
    # 超时阈值：响应时间超过此值的源降权
    TIMEOUT_THRESHOLD_MS = 3000
    # 每频道最多保留几个源
    MAX_SOURCES_PER_CHANNEL = 30
    # 是否启用频道名模糊去重
    FUZZY_DEDUP = True
    # 输出编码（utf-8-sig = UTF-8 with BOM）
    OUTPUT_ENCODING = "utf-8-sig"
    # 是否输出 M3U
    OUTPUT_M3U = True
    # 远程源拉取超时
    FETCH_TIMEOUT = 8
    # UA
    USER_AGENT = "okhttp/3.14.9"
    USER_AGENT_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ===================== 工具函数 =====================

def clean_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    url = url.split('$')[0].strip()
    url = url.split('#')[0].strip()
    return url


def normalize_name(name: str) -> str:
    """频道名标准化，用于模糊去重"""
    if not name:
        return ""
    n = name.strip()
    n = n.replace('０','0').replace('１','1').replace('２','2')
    n = n.replace('３','3').replace('４','4').replace('５','5')
    n = n.replace('６','6').replace('７','7').replace('８','8')
    n = n.replace('９','9')
    n = re.sub(r'[\s\-_]+', '', n)
    n = re.sub(r'[（(].*?[）)]', '', n)
    return n.lower()


def sort_key_for_channel(name: str) -> str:
    """频道排序 key：CCTV 靠前，卫视靠前，数字频道靠前"""
    n = name.strip()
    # 提取前缀数字排序（如 "1,CCTV1" → "0001"）
    m = re.match(r'^(\d+)', n)
    if m:
        return f"A{int(m.group(1)):04d}{n}"
    if re.match(r'^(CCTV|cctv)', n):
        return f"B{n}"
    if '卫视' in n or '高清' in n:
        return f"C{n}"
    return f"D{n}"


def read_lines(path: str) -> List[str]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"读取失败 {path}: {e}")
        return []


def make_ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ===================== 测速结果解析 =====================

class SpeedItem:
    """单条测速结果"""
    __slots__ = ('url', 'elapsed_ms', 'code', 'kind', 'priority')

    def __init__(self, url: str, elapsed_ms: float, code: str = "", kind: str = "unknown"):
        self.url = url
        self.elapsed_ms = elapsed_ms
        self.code = code or ""
        self.kind = kind or "unknown"
        # 综合优先级
        kind_p = Config.MEDIA_KIND_PRIORITY.get(self.kind, 50)
        time_penalty = 0 if self.elapsed_ms <= Config.TIMEOUT_THRESHOLD_MS else 10
        self.priority = kind_p + time_penalty

    def __repr__(self):
        return f"<{self.elapsed_ms:.0f}ms {self.kind} {self.url[:50]}>"


def parse_respotime() -> Dict[str, SpeedItem]:
    """
    解析 whitelist_respotime.txt，返回 url -> SpeedItem 映射。
    兼容旧格式（仅两列）和新格式（四列含媒体类型）。
    """
    items = {}
    lines = read_lines(PATHS["whitelist_respotime"])
    data_started = False

    for line in lines:
        if not data_started:
            if line and not line.startswith(('白名单', '更新', '#')):
                data_started = True
            else:
                continue
        if not line:
            continue

        parts = line.split(',')
        if len(parts) < 2:
            continue

        try:
            elapsed = float(parts[0].strip())
        except ValueError:
            continue

        url = clean_url(parts[1].strip())
        if not url or '://' not in url:
            continue

        code = parts[2].strip() if len(parts) >= 3 else ""
        kind = parts[3].strip() if len(parts) >= 4 else ""

        # 旧格式无 kind 字段，根据 code 推断
        if not kind:
            if code and code.isdigit():
                c = int(code)
                kind = "unknown" if 200 <= c < 400 else "timeout"
            elif "timeout" in code.lower() or "timed out" in code.lower():
                kind = "timeout"
            elif "blacklist" in code.lower():
                kind = "blacklist"
            else:
                kind = "unknown"

        items[url] = SpeedItem(url, elapsed, code, kind)

    logger.info(f"测速结果: {len(items)} 条 URL")
    return items


# ===================== 黑名单加载 =====================

def load_blacklist_urls() -> Set[str]:
    urls = set()
    for line in read_lines(PATHS["blacklist_auto"]):
        if line.startswith(('更新时间', 'blacklist', '#')):
            continue
        if ',' in line:
            url = clean_url(line.split(',')[-1].strip())
        else:
            url = clean_url(line)
        if url and '://' in url:
            urls.add(url)
    logger.info(f"黑名单 URL: {len(urls)} 个")
    return urls


# ===================== 手动白名单加载 =====================

def load_manual_whitelist() -> List[Tuple[str, str]]:
    """返回 [(频道名, url), ...]"""
    entries = []
    seen = set()
    for line in read_lines(PATHS["whitelist_manual"]):
        if line.startswith('#'):
            continue
        if ',' in line and '://' in line:
            idx = line.index(',')
            name = line[:idx].strip()
            url = clean_url(line[idx+1:])
            if url and url not in seen:
                seen.add(url)
                entries.append((name, url))
    logger.info(f"手动白名单: {len(entries)} 个频道")
    return entries


# ===================== 远程源拉取 =====================

def fetch_remote_sources(source_urls: List[str]) -> List[Tuple[str, str, str]]:
    """
    从远程 URL 拉取频道列表。
    返回: [(频道名, url, 分类), ...]
    分类可能为空字符串。
    """
    all_entries = []
    for src_url in source_urls[:30]:
        try:
            req = urllib.request.Request(
                src_url.strip(),
                headers={"User-Agent": Config.USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=Config.FETCH_TIMEOUT, context=make_ssl_ctx()) as resp:
                content = resp.read().decode('utf-8', 'replace')
                entries = parse_source_content(content)
                all_entries.extend(entries)
                logger.info(f"拉取 {src_url[:50]}... → {len(entries)} 条")
        except Exception as e:
            logger.warning(f"拉取失败 {src_url[:50]}... : {e}")
    return all_entries


def parse_source_content(content: str) -> List[Tuple[str, str, str]]:
    """
    解析源内容，支持：
    - 标准 M3U（#EXTM3U + #EXTINF）
    - DIYP 格式（分类,#genre# / 频道名,url）
    - 纯 "频道名,url" 列表
    返回: [(频道名, url, 分类), ...]
    """
    entries = []
    lines = content.split('\n')
    current_genre = ""
    name = ""

    is_m3u = "#EXTM3U" in content[:200]

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # ---- DIYP genre 标记 ----
        if line.endswith(',#genre#'):
            current_genre = line.split(',')[0].strip()
            continue

        # ---- M3U 格式 ----
        if is_m3u:
            if line.startswith('#EXTINF'):
                m = re.search(r',(.+)$', line)
                if m:
                    name = m.group(1).strip()
                # 尝试从 group-title 提取分类
                gm = re.search(r'group-title="([^"]*)"', line)
                if gm and gm.group(1).strip():
                    current_genre = gm.group(1).strip()
                continue
            if line.startswith('#'):
                continue
            if '://' in line and name:
                url = clean_url(line.split(',')[0] if ',' in line else line)
                if url:
                    entries.append((name, url, current_genre))
                name = ""
            continue

        # ---- DIYP / 纯文本格式 ----
        if ',' in line and '://' in line:
            idx = line.index(',')
            n = line[:idx].strip()
            u = clean_url(line[idx+1:])
            if u and n:
                entries.append((n, u, current_genre))

    return entries


# ===================== 频道聚合核心 =====================

class ChannelAggregator:

    def __init__(self):
        self.blacklist_urls: Set[str] = set()
        self.manual_whitelist: List[Tuple[str, str]] = []
        self.speed_map: Dict[str, SpeedItem] = {}       # url -> SpeedItem
        self.url_to_names: Dict[str, List[str]] = defaultdict(list)
        self.url_to_genre: Dict[str, str] = {}

    def load(self):
        logger.info("===== 加载数据 =====")
        self.blacklist_urls = load_blacklist_urls()
        self.manual_whitelist = load_manual_whitelist()
        self.speed_map = parse_respotime()

    def build_url_name_map(self, remote_entries: List[Tuple[str, str, str]]):
        """从远程源构建 url -> [names] 和 url -> genre 映射"""
        for name, url, genre in remote_entries:
            if url:
                self.url_to_names[url].append(name)
                if genre and url not in self.url_to_genre:
                    self.url_to_genre[url] = genre

        # 从手动白名单补充
        for name, url in self.manual_whitelist:
            if url:
                self.url_to_names[url].append(name)

        # 从自动白名单补充
        for line in read_lines(PATHS["whitelist_auto"]):
            if ',' in line and '://' in line:
                idx = line.index(',')
                n = line[:idx].strip()
                u = clean_url(line[idx+1:])
                if u:
                    self.url_to_names[u].append(n)

        logger.info(f"URL→名称映射: {len(self.url_to_names)} 个 URL")

    def _best_name_for_url(self, url: str) -> str:
        names = self.url_to_names.get(url, [])
        if names:
            # 取最短的名称（通常最简洁）
            return min(names, key=len)
        # 无名称映射时从 URL 生成
        try:
            path = urlparse(url).path
            fname = os.path.basename(path).split('.')[0]
            return fname if fname else url[:40]
        except Exception:
            return url[:40]

    def aggregate(self) -> Tuple[List[Tuple[str, str, str, int]], List[Tuple[str, str, str, int]]]:
        """
        聚合频道。
        返回:
          manual_result:  [(name, url, genre, priority), ...]  手动白名单（置顶）
          auto_result:    [(name, url, genre, priority), ...]  自动测速结果
        """
        logger.info("===== 开始聚合 =====")

        # ---- 1. 过滤黑名单 ----
        valid_speed = {}
        filtered = 0
        for url, item in self.speed_map.items():
            if url in self.blacklist_urls:
                filtered += 1
                continue
            if item.kind == "blacklist":
                filtered += 1
                continue
            valid_speed[url] = item
        logger.info(f"黑名单过滤: 移除 {filtered} 个")

        # ---- 2. 按优先级排序（stream > playlist > unknown > timeout） ----
        sorted_items = sorted(valid_speed.values(), key=lambda x: (x.priority, x.elapsed_ms))

        # ---- 3. 频道聚合：同名频道只保留前 N 个最优源 ----
        norm_to_best: Dict[str, List[Tuple[str, str, str, int]]] = defaultdict(list)
        used_norms: Set[str] = set()

        for item in sorted_items:
            name = self._best_name_for_url(item.url)
            genre = self.url_to_genre.get(item.url, "")

            if Config.FUZZY_DEDUP:
                norm = normalize_name(name)
            else:
                norm = name.lower()

            group = norm_to_best[norm]
            if len(group) < Config.MAX_SOURCES_PER_CHANNEL:
                group.append((name, item.url, genre, item.priority))

        # ---- 4. 组装自动结果 ----
        auto_result = []
        for norm, group in norm_to_best.items():
            for name, url, genre, pri in group:
                auto_result.append((name, url, genre, pri))

        # ---- 5. 组装手动白名单结果（置顶，不受去重限制） ----
        manual_result = []
        manual_urls_set = set()
        for name, url in self.manual_whitelist:
            if url in self.blacklist_urls:
                continue
            item = self.speed_map.get(url)
            if item:
                pri = item.priority
                genre = self.url_to_genre.get(url, "")
            else:
                pri = 0  # 手动白名单没有测速数据也给最高优先级
                genre = ""
            manual_result.append((name, url, genre, pri))
            manual_urls_set.add(url)

        # ---- 6. 自动结果中去掉已出现在手动白名单的 URL ----
        auto_result = [(n, u, g, p) for n, u, g, p in auto_result if u not in manual_urls_set]

        logger.info(f"手动白名单频道: {len(manual_result)} 个")
        logger.info(f"自动测速频道: {len(auto_result)} 个（去重后）")
        return manual_result, auto_result


# ===================== 输出 =====================

def build_txt(manual: List[Tuple[str,str,str,int]], auto: List[Tuple[str,str,str,int]]) -> str:
    """构建 result.txt 内容（DIYP 格式）"""
    lines = []

    # 收集所有分类及其频道
    genre_groups: Dict[str, List[str]] = OrderedDict()
    no_genre_lines = []

    for name, url, genre, _ in (manual + auto):
        line = f"{name},{url}"
        if genre:
            genre_groups.setdefault(genre, []).append(line)
        else:
            no_genre_lines.append(line)

    # 先输出有分类的
    for genre, chs in genre_groups.items():
        lines.append(f"{genre},#genre#")
        lines.extend(chs)
        lines.append("")

    # 再输出无分类的
    if no_genre_lines:
        lines.append("其他,#genre#")
        lines.extend(no_genre_lines)
        lines.append("")

    return '\n'.join(lines)


def build_m3u(manual: List[Tuple[str,str,str,int]], auto: List[Tuple[str,str,str,int]]) -> str:
    """构建 result.m3u 内容（标准 M3U 格式）"""
    lines = ["#EXTM3U"]

    for name, url, genre, _ in (manual + auto):
        group_attr = f' group-title="{genre}"' if genre else ""
        lines.append(f'#EXTINF:-1{group_attr},{name}')
        lines.append(url)

    return '\n'.join(lines) + '\n'


def write_file(path: str, content: str):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding=Config.OUTPUT_ENCODING) as f:
            f.write(content)
        logger.info(f"已写入: {path} ({len(content)} 字节)")
    except Exception as e:
        logger.error(f"写入失败 {path}: {e}")


# ===================== 主流程 =====================

def main():
    start_time = datetime.now()
    logger.info(f"===== 程序开始: {start_time.strftime('%Y%m%d %H:%M:%S')} =====")

    agg = ChannelAggregator()
    agg.load()

    # 拉取远程源
    source_urls = read_lines(PATHS["urls"])
    if source_urls:
        logger.info(f"远程源地址: {len(source_urls)} 个")
        remote_entries = fetch_remote_sources(source_urls)
        logger.info(f"远程源频道总计: {len(remote_entries)} 条")
    else:
        logger.warning("urls.txt 为空，仅使用已有白名单数据")
        remote_entries = []

    agg.build_url_name_map(remote_entries)
    manual_result, auto_result = agg.aggregate()

    # 构建输出
    txt_content = build_txt(manual_result, auto_result)
    write_file(PATHS["result_txt"], txt_content)

    if Config.OUTPUT_M3U:
        m3u_content = build_m3u(manual_result, auto_result)
        write_file(PATHS["result_m3u"], m3u_content)

    # 统计
    total = len(manual_result) + len(auto_result)
    stream_cnt = sum(1 for _, u, _, _ in (manual_result + auto_result)
                     if u in agg.speed_map and agg.speed_map[u].kind == "stream")
    playlist_cnt = sum(1 for _, u, _, _ in (manual_result + auto_result)
                       if u in agg.speed_map and agg.speed_map[u].kind == "playlist")

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"===== 完成: 共 {total} 个频道（流 {stream_cnt}，列表 {playlist_cnt}），耗时 {elapsed:.1f}s =====")


if __name__ == "__main__":
    main()
