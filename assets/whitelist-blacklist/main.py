import urllib.request
import os
import re
import ssl
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Set, Dict, Optional
import logging

# ==============================================
# 路径配置（100%正确）
# ==============================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)

MY_URLS_FILE = os.path.join(ASSETS_DIR, "my_urls.txt")
URLS_FILE = os.path.join(ASSETS_DIR, "urls.txt")
BLACKLIST_FILE = os.path.join(SCRIPT_DIR, "blacklist_auto.txt")
WHITELIST_MANUAL = os.path.join(SCRIPT_DIR, "whitelist_manual.txt")
WHITELIST_AUTO = os.path.join(SCRIPT_DIR, "whitelist_auto.txt")
WHITELIST_RESPOTIME = os.path.join(SCRIPT_DIR, "whitelist_respotime.txt")
LOG_FILE = os.path.join(SCRIPT_DIR, "log.txt")

# ==============================================
# 日志配置
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

logger.info("="*60)
logger.info("🚀 启动自动检测（永不卡死优化版）")
logger.info(f"my_urls.txt 存在: {os.path.exists(MY_URLS_FILE)}")
logger.info("="*60)

# ==============================================
# 【防卡死核心配置】调低并发 + 强制超时
# ==============================================
MAX_WORKERS = 5          # 绝对不卡
CHECK_TIMEOUT = 4        # 4秒强制断连
FETCH_TIMEOUT = 8        # 远程拉取超时

# ==============================================
# 1. 获取Token
# ==============================================
def get_token() -> Optional[str]:
    try:
        ctx = ssl._create_unverified_context()
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request("https://www.taoiptv.com", headers=headers)
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        token_match = re.search(r'[a-f0-9]{16}', html, re.I)
        if token_match:
            token = token_match.group(0)
            logger.info(f"✅ Token获取成功: {token}")
            return token
        logger.error("❌ Token未找到")
        return None
    except:
        logger.error("❌ Token获取失败")
        return None

# ==============================================
# 2. 更新 my_urls.txt（强制写入 + 强制校验）
# ==============================================
def update_my_urls(token: str) -> bool:
    if not token or len(token) != 16:
        return False
    if not os.path.exists(MY_URLS_FILE):
        logger.error("❌ my_urls.txt 不存在")
        return False
    try:
        with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
            original = f.read()
        old_tokens = re.findall(r'token=[a-f0-9]{16}', original, re.I)
        if not old_tokens:
            logger.info("✅ 无需更新Token")
            return True

        new_content = re.sub(r'token=[a-f0-9]{16}', f'token={token}', original, flags=re.I)

        with open(MY_URLS_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())

        with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
            verify = f.read()
        if token in verify:
            logger.info(f"✅ my_urls.txt 更新成功，替换 {len(old_tokens)} 个")
            return True
        else:
            logger.error("❌ 文件写入校验失败")
            return False
    except:
        logger.error("❌ my_urls.txt 更新失败")
        return False

# ==============================================
# 3. Git推送（轻量化，不卡死）
# ==============================================
def git_push():
    try:
        os.chdir(PROJECT_ROOT)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
        if not status:
            logger.info("✅ 无变更，无需推送")
            return True

        subprocess.run(["git", "add", "assets/my_urls.txt"], check=False)
        subprocess.run(["git", "commit", "-m", "Auto update token"], check=False)

        github_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        ref = os.getenv("GITHUB_REF_NAME", "main")
        if github_token and repo:
            push_url = f"https://x-access-token:{github_token}@github.com/{repo}.git"
            subprocess.run(["git", "push", push_url, f"HEAD:{ref}"], check=False, timeout=15)
        else:
            subprocess.run(["git", "push"], check=False, timeout=15)

        logger.info("✅ Git推送完成")
        return True
    except:
        logger.warning("⚠️ Git推送跳过（不影响主流程）")
        return False

# ==============================================
# 4. 检测链接（防卡死核心）
# ==============================================
def check_url(url: str) -> Tuple[bool, float, str]:
    start = time.perf_counter()
    try:
        ctx = ssl._create_unverified_context()
        headers = {"User-Agent": "Mozilla/5.0", "Connection": "close"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=CHECK_TIMEOUT) as resp:
            code = resp.getcode()
            elapsed = round((time.perf_counter() - start)*1000, 2)
            return 200 <= code < 400, elapsed, str(code)
    except:
        elapsed = round((time.perf_counter() - start)*1000, 2)
        return False, elapsed, "timeout"

# ==============================================
# 5. 主流程（永不卡死）
# ==============================================
def main():
    try:
        # ========== Token 更新（必须成功）==========
        token = get_token()
        if not token or not update_my_urls(token):
            logger.error("❌ Token更新失败，退出")
            sys.exit(1)
        git_push()

        # ========== 读取直播源 ==========
        my_urls_streams: List[Tuple[str, str]] = []
        if os.path.exists(MY_URLS_FILE):
            with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if ',' in line and '://' in line:
                        parts = line.split(',', 1)
                        name, url = parts[0].strip(), parts[1].strip()
                        my_urls_streams.append((name, url))
        logger.info(f"📥 my_urls.txt 读取: {len(my_urls_streams)} 个")

        # ========== 读取远程列表 ==========
        urls_streams: List[Tuple[str, str]] = []
        if os.path.exists(URLS_FILE):
            with open(URLS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('http'):
                        try:
                            ctx = ssl._create_unverified_context()
                            req = urllib.request.Request(line, headers={"User-Agent": "Mozilla/5.0"})
                            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=FETCH_TIMEOUT) as resp:
                                content = resp.read().decode('utf-8', errors='ignore')
                            for l in content.splitlines():
                                if ',' in l and '://' in l:
                                    p = l.split(',', 1)
                                    n, u = p[0].strip(), p[1].strip()
                                    urls_streams.append((n, u))
                        except:
                            continue
        logger.info(f"📥 urls.txt 读取: {len(urls_streams)} 个")

        # ========== 去重 ==========
        all_streams = my_urls_streams + urls_streams
        seen = set()
        unique = []
        source_map = {}
        for name, url in all_streams:
            if url not in seen:
                seen.add(url)
                unique.append((name, url))
                source_map[url] = "my_urls" if (name,url) in my_urls_streams else "urls"

        logger.info(f"🔍 去重后总计: {len(unique)} 个（开始检测）")

        # ========== 并发检测（防卡死）==========
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_map = {executor.submit(check_url, url): (name, url) for name, url in unique}
            for future in as_completed(future_map):
                name, url = future_map[future]
                try:
                    ok, ms, stat = future.result()
                    results.append((name, url, ok, ms, stat))
                except:
                    results.append((name, url, False, 0, "error"))

        # ========== 统计（分文件显示）==========
        my_total = sum(1 for _, u, _, _, _ in results if source_map.get(u) == "my_urls")
        my_ok = sum(1 for _, u, ok, _, _ in results if source_map.get(u) == "my_urls" and ok)
        ur_total = sum(1 for _, u, _, _, _ in results if source_map.get(u) == "urls")
        ur_ok = sum(1 for _, u, ok, _, _ in results if source_map.get(u) == "urls" and ok)

        logger.info("="*60)
        logger.info(f"📊 最终统计")
        logger.info(f"my_urls.txt：总 {my_total} ｜ 有效 {my_ok}")
        logger.info(f"urls.txt   ：总 {ur_total} ｜ 有效 {ur_ok}")
        logger.info("="*60)

        # ========== 输出文件 ==========
        sorted_res = sorted(results, key=lambda x: (not x[2], x[3]))
        with open(WHITELIST_AUTO, 'w', encoding='utf-8') as f:
            f.write(f"更新时间,#genre#\n{datetime.now(timezone.utc)+timedelta(hours=8):%Y%m%d %H:%M}\n\n")
            for name, url, ok, _, _ in sorted_res:
                if ok:
                    f.write(f"{name},{url}\n")

        with open(WHITELIST_RESPOTIME, 'w', encoding='utf-8') as f:
            f.write(f"更新时间,#genre#\n{datetime.now(timezone.utc)+timedelta(hours=8):%Y%m%d %H:%M}\n\n")
            f.write("频道名,地址,耗时(ms),状态,有效\n")
            for name, url, ok, ms, stat in sorted_res:
                f.write(f"{name},{url},{ms},{stat},{'✅' if ok else '❌'}\n")

        logger.info("✅ 全部流程完成！")

    except Exception as e:
        logger.error(f"💥 主程序异常: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
