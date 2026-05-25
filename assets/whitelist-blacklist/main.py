#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
白名单/黑名单自动生成脚本（纯标准库，零依赖）
功能：
1. 读取 assets/my_urls/ 下所有 .txt 文件
2. 解析并按 URL 去重
3. 多线程检测每个直播源的连通性
4. 可连通的源写入 whitelist_auto.txt / whitelist_manual.txt
5. 不可连通的源写入 blacklist_auto.txt
6. 自动 git commit & push
"""

import os
import re
import sys
import subprocess
import logging
from datetime import datetime, timezone, timedelta
from typing import Tuple, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from http.client import HTTPException
import socket
import ssl

# ======================== 路径配置 ========================
SCRIPT_ABS_PATH = os.path.abspath(__file__)
SCRIPT_DIR = os.path.dirname(SCRIPT_ABS_PATH)
ASSETS_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)

MY_URLS_DIR = os.path.join(ASSETS_DIR, "my_urls")

FILE_PATHS = {
    "whitelist_auto":   os.path.join(SCRIPT_DIR, "whitelist_auto.txt"),
    "whitelist_manual": os.path.join(SCRIPT_DIR, "whitelist_manual.txt"),
    "blacklist_auto":   os.path.join(SCRIPT_DIR, "blacklist_auto.txt"),
    "log":              os.path.join(SCRIPT_DIR, "log.txt"),
}

# ======================== 检测配置 ========================
CHECK_TIMEOUT     = 8     # 单个源请求超时（秒）
CHECK_CONCURRENCY = 50    # 最大并发线程数
CHECK_RETRIES     = 1     # 失败后重试次数（0=不重试，1=重试1次）

# ======================== 日志配置 ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ======================== URL 正则 ========================
RE_URL = re.compile(r"(https?://[^\s,'\"<>}$#]+)")

# ======================== 不验证 SSL 的上下文 ========================
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# ------------------------------------------------------------------ #
#                        解析 & 读取 & 去重                            #
# ------------------------------------------------------------------ #

def parse_line(line: str) -> Tuple[str, str]:
    """从一行文本中提取 (频道名, URL)"""
    match = RE_URL.search(line)
    if not match:
        return "", ""
    url = match.group(1)
    idx = line.find("://")
    if idx > 1:
        prefix = line[:idx - 1].strip()
        if "," in prefix:
            name = prefix.rsplit(",", 1)[0].strip()
            if name:
                return name, url
    return "本地", url


def read_and_dedup(dirpath: str) -> List[Tuple[str, str]]:
    """扫描目录下所有 .txt，解析并按 URL 去重，返回 [(name, url), ...]"""
    if not os.path.isdir(dirpath):
        logger.warning(f"目录不存在: {dirpath}")
        return []

    txt_files = sorted(f for f in os.listdir(dirpath) if f.lower().endswith(".txt"))
    if not txt_files:
        logger.warning(f"目录下无 .txt 文件: {dirpath}")
        return []

    logger.info(f"开始读取: {dirpath}，共 {len(txt_files)} 个文件")

    result: List[Tuple[str, str]] = []
    seen: set = set()
    total_raw = dup_count = 0

    for fn in txt_files:
        fpath = os.path.join(dirpath, fn)
        try:
            with open(fpath, "r", encoding="utf-8") as fp:
                for raw_line in fp:
                    raw_line = raw_line.strip()
                    if not raw_line or raw_line.startswith("#"):
                        continue
                    total_raw += 1
                    name, url = parse_line(raw_line)
                    if not url:
                        continue
                    if url in seen:
                        dup_count += 1
                        continue
                    seen.add(url)
                    result.append((name, url))
        except Exception as e:
            logger.error(f"读取失败 {fpath}: {e}")

    logger.info(
        f"读取完成: 原始 {total_raw} 行，去除重复 {dup_count} 行，保留 {len(result)} 条源"
    )
    return result


# ------------------------------------------------------------------ #
#                      多线程连通性检测                                 #
# ------------------------------------------------------------------ #

def _check_one(name: str, url: str) -> Tuple[str, str, bool]:
    """
    检测单个 URL 是否可达（纯标准库）
    返回

    策略：
      - .m3u8 / .m3u → GET，读前 1 KB 验证内容
      - 其它 URL      → 先 HEAD，失败再 GET 兜底
      - 状态码 < 400 即视为存活
    """
    is_m3u = url.lower().endswith((".m3u8", ".m3u"))
    methods = ["GET"] if is_m3u else ["HEAD", "GET"]

    for _ in range(1 + CHECK_RETRIES):
        for method in methods:
            try:
                req = Request(url, method=method)
                req.add_header(
                    "User-Agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
                )
                resp = urlopen(req, timeout=CHECK_TIMEOUT, context=SSL_CTX)
                status = resp.status

                if status < 400:
                    if is_m3u and method == "GET":
                        try:
                            chunk = resp.read(1024)
                            text = chunk.decode("utf-8", errors="ignore")
                            if "#EXTM3U" in text or "#EXTINF" in text:
                                resp.close()
                                return name, url, True
                            # 200 但不是合法 m3u8 内容，可能跳转页
                            resp.close()
                            continue
                        except Exception:
                            resp.close()
                            continue
                    resp.close()
                    return name, url, True
                resp.close()

            except HTTPError as e:
                # 某些服务器对 HEAD 返回 405，换 GET 重试
                if method == "HEAD" and e.code == 405:
                    continue
                # 其它 HTTP 错误直接判定失效
                break
            except (URLError, socket.timeout, HTTPException, OSError, Exception):
                continue

    return name, url, False


def check_all_urls(
    sources: List[Tuple[str, str]],
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """
    多线程并发检测所有直播源
    返回 (存活列表, 失效列表)
    """
    alive: List[Tuple[str, str]] = []
    dead:  List[Tuple[str, str]] = []
    total = len(sources)
    done = 0

    with ThreadPoolExecutor(max_workers=CHECK_CONCURRENCY) as pool:
        futures = {
            pool.submit(_check_one, name, url): (name, url)
            for name, url in sources
        }

        for future in as_completed(futures):
            name, url, is_alive = future.result()
            done += 1

            if is_alive:
                alive.append((name, url))
            else:
                dead.append((name, url))
                logger.info(f"  ❌ 失效: {name},{url}")

            if done % 50 == 0 or done == total:
                logger.info(
                    f"检测进度: {done}/{total} "
                    f"(存活 {len(alive)} / 失效 {len(dead)})"
                )

    logger.info(f"检测完成: 存活 {len(alive)} 条，失效 {len(dead)} 条")
    return alive, dead


# ------------------------------------------------------------------ #
#                         写入文件                                     #
# ------------------------------------------------------------------ #

def write_files(
    alive: List[Tuple[str, str]],
    dead:  List[Tuple[str, str]],
):
    bj = datetime.now(timezone.utc) + timedelta(hours=8)
    ts = bj.strftime("%Y%m%d %H:%M")
    header = f"更新时间,#genre#\n{ts}\n\n"

    # ---- 白名单 ----
    for key in ("whitelist_auto", "whitelist_manual"):
        with open(FILE_PATHS[key], "w", encoding="utf-8") as f:
            f.write(header)
            for name, url in alive:
                f.write(f"{name},{url}\n")

    # ---- 黑名单 ----
    with open(FILE_PATHS["blacklist_auto"], "w", encoding="utf-8") as f:
        f.write(header)
        for name, url in dead:
            f.write(f"{name},{url}\n")

    logger.info(f"写入完成: 白名单 {len(alive)} 条，黑名单 {len(dead)} 条")


# ------------------------------------------------------------------ #
#                      Git 提交 & 推送                                 #
# ------------------------------------------------------------------ #

def git_commit_push():
    try:
        logger.info("正在同步到GitHub仓库...")
        os.chdir(PROJECT_ROOT)

        subprocess.run(
            ["git", "config", "--global", "user.name", "IPTV-Auto-Bot"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "config", "--global", "user.email", "bot@noreply.github.com"],
            check=True, capture_output=True, text=True,
        )

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout.strip()

        if not status:
            logger.info("✅ 无文件变更，无需提交")
            return True

        subprocess.run(
            [
                "git", "add",
                FILE_PATHS["whitelist_auto"],
                FILE_PATHS["whitelist_manual"],
                FILE_PATHS["blacklist_auto"],
                FILE_PATHS["log"],
            ],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Auto update whitelist & blacklist (merge & dedup & check)"],
            check=True, capture_output=True, text=True,
        )

        gh_token = os.getenv("GITHUB_TOKEN")
        repo     = os.getenv("GITHUB_REPOSITORY")
        if gh_token and repo:
            push_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
            subprocess.run(
                ["git", "push", push_url, "HEAD"],
                check=True, capture_output=True, text=True,
            )
        else:
            subprocess.run(
                ["git", "push"],
                check=True, capture_output=True, text=True,
            )

        logger.info("✅ 已同步到GitHub仓库！")
        return True

    except subprocess.CalledProcessError as e:
        logger.warning(f"Git推送失败: {e.stderr if e.stderr else ''}")
        return False
    except Exception as e:
        logger.warning(f"Git异常: {e}")
        return False


# ------------------------------------------------------------------ #
#                           主流程                                     #
# ------------------------------------------------------------------ #

def main():
    start = datetime.now()
    logger.info("===== 开始执行（合并去重 + 连通性检测 + 黑白名单） =====")

    # 1. 读取并去重
    sources = read_and_dedup(MY_URLS_DIR)

    if not sources:
        logger.warning("未获取到任何有效源，跳过写入和提交")
        elapsed = (datetime.now() - start).seconds
        logger.info(f"===== 执行完成 | 共 0 条源 | 耗时 {elapsed}s =====")
        return

    # 2. 多线程连通性检测
    logger.info(
        f"开始连通性检测，共 {len(sources)} 条源 "
        f"（超时 {CHECK_TIMEOUT}s，并发 {CHECK_CONCURRENCY}，重试 {CHECK_RETRIES} 次）"
    )
    alive, dead = check_all_urls(sources)

    # 3. 写入白名单 & 黑名单
    write_files(alive, dead)

    # 4. Git 提交推送
    git_commit_push()

    elapsed = (datetime.now() - start).seconds
    logger.info(
        f"===== 执行完成 | 白名单 {len(alive)} 条 | 黑名单 {len(dead)} 条 | 耗时 {elapsed}s ====="
    )


if __name__ == "__main__":
    main()
