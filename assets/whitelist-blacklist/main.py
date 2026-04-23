import urllib.request
import os
import re
import ssl
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, quote, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Set, Dict, Optional
import logging

# ==============================================
# 【100%正确】路径配置
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
# 日志配置（全量打印，方便排查）
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 启动路径校验
logger.info("="*60)
logger.info(f"【1/7】路径校验")
logger.info(f"项目根目录: {PROJECT_ROOT}")
logger.info(f"my_urls.txt路径: {MY_URLS_FILE}")
logger.info(f"my_urls.txt是否存在: {os.path.exists(MY_URLS_FILE)}")
logger.info("="*60)

# ==============================================
# 【核心1/4】只获取一次Token
# ==============================================
def get_token() -> Optional[str]:
    """获取TaoIPTV Token，只访问一次官网"""
    try:
        logger.info("【2/7】正在获取TaoIPTV Token...")
        ctx = ssl._create_unverified_context()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        req = urllib.request.Request("https://www.taoiptv.com", headers=headers)
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        
        token_match = re.search(r'[a-f0-9]{16}', html, re.I)
        if token_match:
            token = token_match.group(0)
            logger.info(f"✅ Token获取成功: {token}")
            return token
        logger.error("❌ 未匹配到Token")
        return None
    except Exception as e:
        logger.error(f"❌ Token获取失败: {str(e)}")
        return None

# ==============================================
# 【核心2/4】更新my_urls.txt，强制校验
# ==============================================
def update_my_urls(token: str) -> bool:
    """更新my_urls.txt里所有链接的Token，写入后强制校验"""
    if not token or len(token) != 16:
        logger.error("❌ Token无效")
        return False
    
    if not os.path.exists(MY_URLS_FILE):
        logger.error(f"❌ my_urls.txt不存在: {MY_URLS_FILE}")
        return False
    
    try:
        logger.info("【3/7】正在更新my_urls.txt...")
        
        # 1. 读取原文件
        with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
            original = f.read()
        
        # 2. 统计需要替换的数量
        old_tokens = re.findall(r'token=[a-f0-9]{16}', original, re.I)
        if not old_tokens:
            logger.info("✅ 没有需要更新的Token")
            return True
        
        # 3. 全局替换
        new_content = re.sub(r'token=[a-f0-9]{16}', f'token={token}', original, flags=re.I)
        
        # 4. 强制写入文件
        with open(MY_URLS_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        
        # 5. 【关键】读取文件校验，确认更新成功
        with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
            verify_content = f.read()
        
        if token in verify_content:
            logger.info(f"✅ my_urls.txt更新成功！替换了 {len(old_tokens)} 个链接的Token")
            return True
        else:
            logger.error("❌ 文件校验失败，Token未写入成功")
            return False
            
    except Exception as e:
        logger.error(f"❌ 更新my_urls.txt失败: {str(e)}", exc_info=True)
        return False

# ==============================================
# 【核心3/4】Git推送到GitHub，详细错误打印
# ==============================================
def git_push() -> bool:
    """将修改推送到GitHub仓库，打印详细错误"""
    try:
        logger.info("【4/7】正在同步修改到GitHub仓库...")
        os.chdir(PROJECT_ROOT)
        
        # Git配置
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Bot"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "bot@noreply.com"], check=False)
        
        # 检查变更
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
        if not status:
            logger.info("✅ 无文件变更，无需提交")
            return True
        
        # 添加、提交
        subprocess.run(["git", "add", "assets/my_urls.txt"], check=False)
        subprocess.run(["git", "commit", "-m", "Auto update TaoIPTV token"], check=False)
        
        # 推送（适配Actions）
        github_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        ref = os.getenv("GITHUB_REF_NAME", "main")
        
        if github_token and repo:
            push_url = f"https://x-access-token:{github_token}@github.com/{repo}.git"
            result = subprocess.run(
                ["git", "push", push_url, f"HEAD:{ref}"],
                capture_output=True,
                text=True
            )
        else:
            result = subprocess.run(["git", "push"], capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info("✅ GitHub仓库同步成功！")
            return True
        else:
            logger.error(f"❌ GitHub推送失败，详细错误: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Git操作异常: {str(e)}")
        return False

# ==============================================
# 【核心4/4】简化的直播源检测
# ==============================================
def check_url(url: str) -> Tuple[bool, float, str]:
    """检测直播源是否有效"""
    start = time.perf_counter()
    try:
        ctx = ssl._create_unverified_context()
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=5) as resp:
            code = resp.getcode()
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            return (200 <= code < 400 or code in (301, 302)), elapsed, str(code)
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return False, elapsed, str(e)

def main():
    try:
        # ===================== 第一步：Token更新（优先执行，确保成功）=====================
        token = get_token()
        if not token:
            logger.error("❌ Token获取失败，终止流程")
            sys.exit(1)
        
        update_ok = update_my_urls(token)
        if not update_ok:
            logger.error("❌ my_urls.txt更新失败，终止流程")
            sys.exit(1)
        
        # 只有更新成功才推送
        git_push()
        
        # ===================== 第二步：读取更新后的my_urls.txt，检测直播源=====================
        logger.info("【5/7】正在读取更新后的my_urls.txt...")
        
        # 读取my_urls.txt（更新后的文件）
        my_urls_streams: List[Tuple[str, str]] = []
        if os.path.exists(MY_URLS_FILE):
            with open(MY_URLS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or ',' not in line or '://' not in line:
                        continue
                    parts = line.split(',', 1)
                    name, url = parts[0].strip(), parts[1].strip()
                    if name and url:
                        my_urls_streams.append((name, url))
        
        logger.info(f"✅ 从my_urls.txt读取到 {len(my_urls_streams)} 个直播源")
        
        # 读取urls.txt（远程列表）
        urls_streams: List[Tuple[str, str]] = []
        if os.path.exists(URLS_FILE):
            with open(URLS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or not line.startswith('http'):
                        continue
                    # 简单拉取远程列表
                    try:
                        ctx = ssl._create_unverified_context()
                        req = urllib.request.Request(line, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=15) as resp:
                            content = resp.read().decode('utf-8', errors='ignore')
                        for l in content.splitlines():
                            l = l.strip()
                            if ',' in l and '://' in l:
                                parts = l.split(',', 1)
                                name, url = parts[0].strip(), parts[1].strip()
                                if name and url:
                                    urls_streams.append((name, url))
                    except:
                        continue
        
        logger.info(f"✅ 从urls.txt拉取到 {len(urls_streams)} 个直播源")
        
        # ===================== 第三步：并发检测 =====================
        logger.info("【6/7】正在并发检测直播源...")
        all_streams = my_urls_streams + urls_streams
        
        # 去重
        seen_urls = set()
        unique_streams: List[Tuple[str, str]] = []
        source_map: Dict[str, str] = {}  # 记录来源
        for name, url in all_streams:
            if url not in seen_urls:
                seen_urls.add(url)
                unique_streams.append((name, url))
                # 标记来源
                if any(url == u for _, u in my_urls_streams):
                    source_map[url] = "my_urls"
                else:
                    source_map[url] = "urls"
        
        logger.info(f"去重后待检测: {len(unique_streams)} 个")
        
        # 并发检测
        results: List[Tuple[str, str, bool, float, str]] = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            future_map = {executor.submit(check_url, url): (name, url) for name, url in unique_streams}
            for future in as_completed(future_map):
                name, url = future_map[future]
                try:
                    is_valid, elapsed, status = future.result()
                    results.append((name, url, is_valid, elapsed, status))
                except:
                    results.append((name, url, False, 0, "error"))
        
        # ===================== 第四步：统计结果 =====================
        logger.info("【7/7】正在统计结果...")
        
        # 分文件统计
        my_urls_total = sum(1 for _, url, _, _, _ in results if source_map.get(url) == "my_urls")
        my_urls_success = sum(1 for _, url, is_valid, _, _ in results if source_map.get(url) == "my_urls" and is_valid)
        urls_total = sum(1 for _, url, _, _, _ in results if source_map.get(url) == "urls")
        urls_success = sum(1 for _, url, is_valid, _, _ in results if source_map.get(url) == "urls" and is_valid)
        
        # 保存结果
        results_sorted = sorted(results, key=lambda x: (not x[2], x[3]))
        
        with open(WHITELIST_RESPOTIME, 'w', encoding='utf-8') as f:
            bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
            f.write(f"更新时间,#genre#\n{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
            f.write("频道名,直播地址,耗时(ms),状态,是否有效\n")
            for name, url, is_valid, elapsed, status in results_sorted:
                f.write(f"{name},{url},{elapsed},{status},{'✅' if is_valid else '❌'}\n")
        
        with open(WHITELIST_AUTO, 'w', encoding='utf-8') as f:
            bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
            f.write(f"更新时间,#genre#\n{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
            for name, url, is_valid, _, _ in results_sorted:
                if is_valid:
                    f.write(f"{name},{url}\n")
        
        # 打印统计结果
        logger.info("="*60)
        logger.info(f"===== 分文件统计结果 =====")
        logger.info(f"my_urls.txt: 总源数 {my_urls_total} 个，检测成功 {my_urls_success} 个")
        logger.info(f"urls.txt: 总源数 {urls_total} 个，检测成功 {urls_success} 个")
        logger.info("="*60)
        logger.info(f"===== 全部流程执行完成 =====")
        
    except Exception as e:
        logger.error(f"主程序异常: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
