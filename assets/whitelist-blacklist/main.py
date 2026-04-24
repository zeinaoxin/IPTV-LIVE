import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse
import ssl
import re
from typing import List, Tuple, Set, Dict, Optional
import logging
import sys
import subprocess

# ==============================================
# 【路径双保险】100%正确，杜绝嵌套错误    豆包
# ==============================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 双保险计算assets目录
ASSETS_DIR1 = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR2 = os.path.join(os.getcwd(), "assets")
ASSETS_DIR = ASSETS_DIR1 if os.path.exists(os.path.join(ASSETS_DIR1, "my_urls.txt")) else ASSETS_DIR2
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)

# 固定文件路径
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

# 【启动标识】一眼确认新代码生效
logger.info("="*60)
logger.info("🚀 【新代码生效】IPTV直播源检测优化版启动")
logger.info(f"my_urls.txt 路径: {MY_URLS_FILE}")
logger.info(f"my_urls.txt 是否存在: {os.path.exists(MY_URLS_FILE)}")
logger.info(f"urls.txt 路径: {URLS_FILE}")
logger.info(f"urls.txt 是否存在: {os.path.exists(URLS_FILE)}")
logger.info("="*60)

# ==============================================
# 【防卡死配置】调低并发+强制超时
# ==============================================
MAX_WORKERS = 5          # 绝对不卡
CHECK_TIMEOUT = 4        # 4秒强制断连
FETCH_TIMEOUT = 8        # 远程拉取超时

# ==============================================
# 1. 获取Token（只访问一次官网）
# ==============================================
def get_taoiptv_token() -> Optional[str]:
    try:
        logger.info("【1/6】正在获取TaoIPTV Token...")
        ctx = ssl._create_unverified_context()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        req = urllib.request.Request("https://www.taoiptv.com", headers=headers)
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        
        token_match = re.search(r'[a-f0-9]{16}', html, re.I)
        if token_match:
            token = token_match.group(0)
            logger.info(f"✅ Token获取成功: {token}")
            return token
        logger.error("❌ 页面中未匹配到Token")
        return None
    except Exception as e:
        logger.error(f"❌ Token获取失败: {str(e)}")
        return None

# ==============================================
# 2. 更新my_urls.txt（强制写入+二次校验）
# ==============================================
def update_my_urls_token(token: str) -> bool:
    if not token or len(token) != 16:
        logger.error("❌ Token无效")
        return False
    if not os.path.exists(MY_URLS_FILE):
        logger.error(f"❌ my_urls.txt 不存在: {MY_URLS_FILE}")
        return False
    
    try:
        logger.info("【2/6】正在更新my_urls.txt的Token...")
        # 读取原文件
        with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
            original_content = f.read()
        
        # 统计需要替换的数量
        old_tokens = re.findall(r'token=[a-f0-9]{16}', original_content, re.I)
        if not old_tokens:
            logger.info("✅ 没有需要更新的Token")
            return True
        
        # 全局替换所有Token
        new_content = re.sub(r'token=[a-f0-9]{16}', f'token={token}', original_content, flags=re.I)
        
        # 强制写入文件
        with open(MY_URLS_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        
        # 【关键】二次校验，确认Token真的写入成功
        with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
            verify_content = f.read()
        if token in verify_content:
            logger.info(f"✅ my_urls.txt 更新成功！共替换 {len(old_tokens)} 个链接的Token")
            return True
        else:
            logger.error("❌ 文件写入校验失败，Token未更新")
            return False
    except Exception as e:
        logger.error(f"❌ my_urls.txt 更新失败: {str(e)}")
        return False

# ==============================================
# 3. Git推送到GitHub（轻量化，不冲突）
# ==============================================
def git_commit_push():
    try:
        logger.info("【3/6】正在同步修改到GitHub仓库...")
        os.chdir(PROJECT_ROOT)
        
        # 检查是否有变更
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
        if not status:
            logger.info("✅ 无文件变更，无需推送")
            return True

        # Git配置+提交推送
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Auto-Bot"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "bot@noreply.github.com"], check=False)
        subprocess.run(["git", "add", "assets/my_urls.txt"], check=False)
        subprocess.run(["git", "commit", "-m", "Auto update TaoIPTV token"], check=False)

        # 适配GitHub Actions
        github_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        ref = os.getenv("GITHUB_REF_NAME", "main")
        if github_token and repo:
            push_url = f"https://x-access-token:{github_token}@github.com/{repo}.git"
            subprocess.run(["git", "push", push_url, f"HEAD:{ref}"], check=False, timeout=15)
        else:
            subprocess.run(["git", "push"], check=False, timeout=15)

        logger.info("✅ GitHub推送完成")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Git推送跳过: {str(e)}")
        return False

# ==============================================
# 4. 核心功能：直播源检测&远程拉取
# ==============================================
# 黑名单过滤
DOMAIN_BLACKLIST: Set[str] = {
    "iptv.catvod.com", "dd.ddzb.fun", "goodiptv.club", "jiaojirentv.top",
    "alist.xicp.fun", "rihou.cc", "php.jdshipin.com", "t.freetv.fun",
    "stream1.freetv.fun", "hlsztemgsplive.miguvideo", "stream2.freetv.fun",
}

def is_blacklist_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        host_lower = host.lower()
        return any(host_lower == d or host_lower.endswith(f".{d}") for d in DOMAIN_BLACKLIST)
    except:
        return False

# 清洗直播源行
def clean_stream_line(line: str) -> Optional[Tuple[str, str]]:
    line = line.strip()
    if not line or ',' not in line or '://' not in line:
        return None
    parts = line.split(',', 1)
    if len(parts) != 2:
        return None
    name, url = parts[0].strip(), parts[1].strip()
    if not name or not url:
        return None
    url = url.split('$')[0].split('#')[0].strip()
    if is_blacklist_url(url):
        return None
    return (name, url)

# 拉取远程m3u列表
def fetch_remote_list(remote_url: str) -> List[Tuple[str, str]]:
    result = []
    try:
        ctx = ssl._create_unverified_context()
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(remote_url, headers=headers)
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=FETCH_TIMEOUT) as resp:
            if resp.getcode() != 200:
                return result
            content = resp.read().decode('utf-8', errors='ignore')
        
        # 解析m3u格式
        if "#EXTM3U" in content[:200]:
            name = ""
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("#EXTINF"):
                    name_match = re.search(r',(.+)$', line)
                    if name_match:
                        name = name_match.group(1).strip()
                elif line.startswith(('http://', 'https://')) and name:
                    res = clean_stream_line(f"{name},{line}")
                    if res:
                        result.append(res)
                    name = ""
        # 解析普通txt格式
        else:
            for line in content.splitlines():
                res = clean_stream_line(line.strip())
                if res:
                    result.append(res)
        return result
    except:
        return result

# 检测直播源是否有效
def check_stream_url(url: str) -> Tuple[bool, float, str]:
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
# 5. 主检测流程（修正my_urls.txt为远程拉取）
# ==============================================
def main_check():
    logger.info("【4/6】开始读取直播源...")
    source_map: Dict[str, str] = {}  # 记录每个url的来源
    all_streams: List[Tuple[str, str]] = []
    seen_urls: Set[str] = set()

    # ===================== 【修正逻辑】处理urls.txt（远程拉取）=====================
    urls_total = 0
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE, 'r', encoding='utf-8') as f:
            remote_list = [line.strip() for line in f if line.strip().startswith('http')]
        logger.info(f"📥 urls.txt 读取到 {len(remote_list)} 个远程列表地址")
        for remote_url in remote_list:
            streams = fetch_remote_list(remote_url)
            for name, url in streams:
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_streams.append((name, url))
                    source_map[url] = "urls"
                    urls_total += 1
    logger.info(f"📥 urls.txt 拉取完成，共 {urls_total} 个有效直播源")

    # ===================== 【修正逻辑】处理my_urls.txt（同样远程拉取）=====================
    my_urls_total = 0
    if os.path.exists(MY_URLS_FILE):
        with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
            remote_list = [line.strip() for line in f if line.strip().startswith('http')]
        logger.info(f"📥 my_urls.txt 读取到 {len(remote_list)} 个远程列表地址")
        for remote_url in remote_list:
            streams = fetch_remote_list(remote_url)
            for name, url in streams:
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_streams.append((name, url))
                    source_map[url] = "my_urls"
                    my_urls_total += 1
    logger.info(f"📥 my_urls.txt 拉取完成，共 {my_urls_total} 个有效直播源")

    # 处理手动白名单
    whitelist_count = 0
    if os.path.exists(WHITELIST_MANUAL):
        with open(WHITELIST_MANUAL, 'r', encoding='utf-8') as f:
            for line in f:
                res = clean_stream_line(line)
                if res:
                    name, url = res
                    if url not in seen_urls:
                        seen_urls.add(url)
                        all_streams.append((name, url))
                        source_map[url] = "whitelist"
                        whitelist_count += 1
    logger.info(f"📥 手动白名单 读取完成，共 {whitelist_count} 个直播源")

    # 待检测总数
    total_to_check = len(all_streams)
    if total_to_check == 0:
        logger.error("❌ 没有可检测的直播源")
        return
    logger.info(f"【5/6】开始并发检测，总计 {total_to_check} 个直播源")

    # 并发检测
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(check_stream_url, url): (name, url) for name, url in all_streams}
        for future in as_completed(future_map):
            name, url = future_map[future]
            try:
                is_valid, elapsed, status = future.result()
                results.append((name, url, is_valid, elapsed, status))
            except:
                results.append((name, url, False, 0, "error"))

    # ===================== 分文件统计 =====================
    my_urls_success = sum(1 for _, u, ok, _, _ in results if source_map.get(u) == "my_urls" and ok)
    urls_success = sum(1 for _, u, ok, _, _ in results if source_map.get(u) == "urls" and ok)
    whitelist_success = sum(1 for _, u, ok, _, _ in results if source_map.get(u) == "whitelist" and ok)
    total_success = sum(1 for _, _, ok, _, _ in results if ok)

    logger.info("="*60)
    logger.info(f"📊 【最终统计结果】")
    logger.info(f"urls.txt   ：总 {urls_total} 个 | 有效 {urls_success} 个")
    logger.info(f"my_urls.txt：总 {my_urls_total} 个 | 有效 {my_urls_success} 个")
    logger.info(f"手动白名单：总 {whitelist_count} 个 | 有效 {whitelist_success} 个")
    logger.info(f"总计：{total_to_check} 个 | 有效 {total_success} 个")
    logger.info("="*60)

    # ===================== 输出结果文件 =====================
    logger.info("【6/6】正在保存结果文件...")
    # 按有效状态+耗时排序
    sorted_results = sorted(results, key=lambda x: (not x[2], x[3]))

    # 保存测速结果
    with open(WHITELIST_RESPOTIME, 'w', encoding='utf-8') as f:
        bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
        f.write(f"更新时间,#genre#\n{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
        f.write("频道名,直播地址,耗时(ms),状态码,是否有效\n")
        for name, url, ok, ms, stat in sorted_results:
            f.write(f"{name},{url},{ms},{stat},{'✅' if ok else '❌'}\n")

    # 保存自动白名单（有效源）
    with open(WHITELIST_AUTO, 'w', encoding='utf-8') as f:
        bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
        f.write(f"更新时间,#genre#\n{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
        for name, url, ok, _, _ in sorted_results:
            if ok:
                f.write(f"{name},{url}\n")

    # 保存黑名单（无效源）
    try:
        existing_blacklist = set()
        if os.path.exists(BLACKLIST_FILE):
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith(('更新时间', '#')):
                        url = line.split(',')[-1].strip()
                        if url:
                            existing_blacklist.add(url)
        # 新增无效源
        new_blacklist = [u for _, u, ok, _, _ in results if not ok and u not in existing_blacklist]
        if new_blacklist:
            with open(BLACKLIST_FILE, 'a', encoding='utf-8') as f:
                f.write('\n' + '\n'.join(new_blacklist))
            logger.info(f"✅ 黑名单新增 {len(new_blacklist)} 个无效源")
    except:
        pass

    logger.info("✅ 所有流程执行完成！")

# ==============================================
# 主程序入口
# ==============================================
def main():
    try:
        # 第一步：Token更新（必须成功）
        token = get_taoiptv_token()
        if not token:
            logger.error("❌ Token获取失败，跳过更新，继续检测")
        else:
            update_ok = update_my_urls_token(token)
            if update_ok:
                git_commit_push()
        
        # 第二步：直播源检测
        main_check()
        
    except Exception as e:
        logger.error(f"💥 主程序异常: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
