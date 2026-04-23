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
import subprocess

# ==============================================
# 路径配置（100%适配你的项目结构）
# ==============================================
SCRIPT_ABS_PATH = os.path.abspath(__file__)
SCRIPT_DIR = os.path.dirname(SCRIPT_ABS_PATH)  # assets/whitelist-blacklist
ASSETS_DIR = os.path.dirname(SCRIPT_DIR)  # 正确的assets目录
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)  # 项目根目录

# 固定文件路径
FILE_PATHS = {
    "my_urls": os.path.join(ASSETS_DIR, "my_urls.txt"),
    "urls": os.path.join(ASSETS_DIR, "urls.txt"),
    "blacklist_auto": os.path.join(SCRIPT_DIR, "blacklist_auto.txt"),
    "whitelist_manual": os.path.join(SCRIPT_DIR, "whitelist_manual.txt"),
    "whitelist_auto": os.path.join(SCRIPT_DIR, "whitelist_auto.txt"),
    "whitelist_respotime": os.path.join(SCRIPT_DIR, "whitelist_respotime.txt"),
    "log": os.path.join(SCRIPT_DIR, "log.txt"),
}

# ==============================================
# 日志配置
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 启动路径校验（必打日志，确认环境）
logger.info("="*60)
logger.info(f"【路径校验】项目根目录: {PROJECT_ROOT}")
logger.info(f"【路径校验】脚本所在目录: {SCRIPT_DIR}")
logger.info(f"【路径校验】assets目录: {ASSETS_DIR}")
logger.info(f"【路径校验】my_urls.txt路径: {FILE_PATHS['my_urls']}")
logger.info(f"【路径校验】my_urls.txt是否存在: {os.path.exists(FILE_PATHS['my_urls'])}")
logger.info(f"【路径校验】urls.txt是否存在: {os.path.exists(FILE_PATHS['urls'])}")
logger.info("="*60)

# ==============================================
# 全局配置
# ==============================================
class Config:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    REQUEST_TIMEOUT = 20
    FETCH_MAX_RETRY = 2
    CHECK_TIMEOUT = 5
    MAX_WORKERS = 20  # 降低并发，适配Actions网络

# ==============================================
# 【修复】Token获取+文件更新+Git推送（解决网页看不到更新的问题）
# ==============================================
def get_taoiptv_token() -> Optional[str]:
    """获取TaoIPTV Token，带重试机制"""
    for retry in range(Config.FETCH_MAX_RETRY+1):
        try:
            logger.info(f"正在获取TaoIPTV最新Token（第{retry+1}次尝试）...")
            ctx = ssl._create_unverified_context()
            headers = {
                "User-Agent": Config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.taoiptv.com/"
            }
            req = urllib.request.Request("https://www.taoiptv.com", headers=headers, method="GET")
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=Config.REQUEST_TIMEOUT) as resp:
                if resp.getcode() != 200:
                    logger.warning(f"官网访问失败，状态码: {resp.getcode()}")
                    time.sleep(2)
                    continue
                html = resp.read().decode('utf-8', errors='ignore')
            
            token_match = re.search(r'[a-f0-9]{16}', html, re.I)
            if token_match:
                token = token_match.group(0)
                logger.info(f"✅ 成功获取Token: {token}")
                return token
            logger.warning("页面未匹配到Token，重试中...")
            time.sleep(2)
        except Exception as e:
            logger.warning(f"Token获取失败（第{retry+1}次）: {str(e)}")
            time.sleep(2)
    logger.error("❌ 所有Token获取尝试均失败")
    return None

def update_my_urls_all(token: str) -> bool:
    """批量更新my_urls.txt里所有链接的Token"""
    if not token or len(token) != 16:
        logger.error("❌ Token无效，跳过更新")
        return False
    
    file_path = FILE_PATHS["my_urls"]
    if not os.path.exists(file_path):
        logger.error(f"❌ my_urls.txt文件不存在: {file_path}")
        return False
    
    try:
        # 读取文件
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
        
        # 统计需要替换的数量
        old_token_list = re.findall(r'token=[a-f0-9]{16}', original_content, re.I)
        if not old_token_list:
            logger.info("✅ 文件中没有需要更新的Token，无需修改")
            return False
        
        # 全局替换所有Token
        new_content = re.sub(r'token=[a-f0-9]{16}', f'token={token}', original_content, flags=re.I)
        
        # 强制写入文件，刷新到磁盘
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        
        logger.info(f"✅ my_urls.txt更新成功！共替换 {len(old_token_list)} 个链接的Token")
        return True
    except Exception as e:
        logger.error(f"❌ 更新my_urls.txt失败: {str(e)}", exc_info=True)
        return False

# 【重点修复】Git推送逻辑，解决修改后网页看不到的问题
def git_commit_push():
    """修改后自动推送到GitHub仓库，打印详细错误，兼容Actions环境"""
    try:
        logger.info("正在同步修改到GitHub仓库...")
        # 切换到项目根目录
        os.chdir(PROJECT_ROOT)
        
        # Git基础配置
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Auto-Bot"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "bot@noreply.github.com"], check=False)
        
        # 先拉取最新代码，避免冲突
        subprocess.run(["git", "pull"], check=False, capture_output=True)
        
        # 检查是否有变更
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if not status:
            logger.info("✅ 无文件变更，无需提交")
            return True
        
        # 添加、提交
        subprocess.run(["git", "add", "assets/my_urls.txt"], check=False)
        commit_result = subprocess.run(["git", "commit", "-m", "Auto update TaoIPTV token"], capture_output=True, text=True)
        
        # 适配Actions自动推送
        github_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        ref = os.getenv("GITHUB_REF_NAME", "main")  # 适配默认分支
        
        if github_token and repo:
            push_url = f"https://x-access-token:{github_token}@github.com/{repo}.git"
            push_result = subprocess.run(
                ["git", "push", push_url, f"HEAD:{ref}"],
                capture_output=True,
                text=True
            )
        else:
            push_result = subprocess.run(["git", "push"], capture_output=True, text=True)
        
        # 打印推送结果，排查错误
        if push_result.returncode == 0:
            logger.info("✅ 已成功同步修改到GitHub仓库！")
            return True
        else:
            logger.error(f"❌ Git推送失败，详细错误: {push_result.stderr}")
            return False
    except Exception as e:
        logger.error(f"❌ Git操作异常: {str(e)}", exc_info=True)
        return False

# ==============================================
# 【修复】中文URL编码，解决ascii报错
# ==============================================
def encode_chinese_url(url: str) -> str:
    """自动编码URL里的中文，解决ascii编码报错"""
    try:
        parsed = urlparse(url)
        # 编码路径里的中文
        encoded_path = quote(parsed.path, safe='/')
        # 编码查询参数里的中文
        encoded_query = quote(parsed.query, safe='&=')
        # 重建URL
        return parsed._replace(path=encoded_path, query=encoded_query).geturl()
    except:
        return url

# ==============================================
# 基础功能函数（黑名单、格式清洗等）
# ==============================================
DOMAIN_BLACKLIST: Set[str] = {
    "iptv.catvod.com", "dd.ddzb.fun", "goodiptv.club", "jiaojirentv.top",
    "alist.xicp.fun", "rihou.cc", "php.jdshipin.com", "t.freetv.fun",
    "stream1.freetv.fun", "hlsztemgsplive.miguvideo", "stream2.freetv.fun",
}

def url_matches_domain_blacklist(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        host_lower = host.lower()
        for d in DOMAIN_BLACKLIST:
            if host_lower == d or host_lower.endswith(f".{d}"):
                return True
        return False
    except Exception:
        return False

CLEAN_OK = "ok"
CLEAN_NO_FORMAT = "no_format"
CLEAN_BAD_URL = "bad_url"
CLEAN_DOMAIN_BL = "domain_blacklist"

def clean_source_line(line: str) -> Optional[Tuple[str, str]]:
    """清洗直播源行，返回(频道名, 地址)，无效返回None"""
    line = line.strip()
    if not line or ',' not in line or '://' not in line:
        return None
    parts = line.split(',', 1)
    if len(parts) != 2:
        return None
    name, url = parts[0].strip(), parts[1].strip()
    if not name or not url:
        return None
    # 清理地址里的多余参数
    url = url.split('$')[0].split('#')[0].strip()
    if url_matches_domain_blacklist(url):
        return None
    return (name, url)

# ==============================================
# 【修复】远程源拉取，解决中文编码、404报错
# ==============================================
def fetch_remote_list(remote_url: str) -> List[Tuple[str, str]]:
    """拉取远程m3u/直播源列表，返回清洗后的(频道名,地址)列表"""
    result = []
    for retry in range(Config.FETCH_MAX_RETRY+1):
        try:
            # 编码中文URL
            encoded_url = encode_chinese_url(remote_url)
            ctx = ssl._create_unverified_context()
            headers = {"User-Agent": Config.USER_AGENT}
            req = urllib.request.Request(encoded_url, headers=headers, method="GET")
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=Config.REQUEST_TIMEOUT) as resp:
                if resp.getcode() != 200:
                    logger.warning(f"远程源访问失败 {remote_url[:60]}，状态码: {resp.getcode()}")
                    time.sleep(1)
                    continue
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
                        res = clean_source_line(f"{name},{line}")
                        if res:
                            result.append(res)
                        name = ""
            # 解析普通txt格式
            else:
                for line in content.splitlines():
                    res = clean_source_line(line.strip())
                    if res:
                        result.append(res)
            
            logger.info(f"✅ 拉取远程源成功 {remote_url[:60]}，共获取 {len(result)} 个源")
            return result
        except Exception as e:
            logger.warning(f"拉取远程源失败 {remote_url[:60]}（第{retry+1}次）: {str(e)}")
            time.sleep(1)
    return result

# ==============================================
# 直播源检测函数
# ==============================================
def check_stream_url(url: str) -> Tuple[bool, float, str]:
    """检测直播源是否有效，返回(是否有效, 耗时ms, 状态码/错误信息)"""
    start = time.perf_counter()
    try:
        ctx = ssl._create_unverified_context()
        headers = {"User-Agent": Config.USER_AGENT, "Connection": "close"}
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=Config.CHECK_TIMEOUT) as resp:
            code = resp.getcode()
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            # 判定有效状态码
            if 200 <= code < 400 or code in (301, 302):
                return True, elapsed, str(code)
            return False, elapsed, str(code)
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return False, elapsed, str(e)

# ==============================================
# 【重写】StreamChecker，修复my_urls.txt处理逻辑、统计逻辑
# ==============================================
class StreamChecker:
    def __init__(self):
        self.start_time = datetime.now()
        self.blacklist = self._load_blacklist()
        self.whitelist: Set[str] = set()
        self.source_map: Dict[str, str] = {}  # 记录每个url的来源：urls/my_urls/manual
        self.new_failed: Set[str] = set()
        
        # 分文件统计变量
        self.urls_total = 0  # urls.txt拉取的总有效源数
        self.my_urls_total = 0  # my_urls.txt的总有效源数
        self.urls_success = 0  # urls.txt检测成功数
        self.my_urls_success = 0  # my_urls.txt检测成功数

    def _load_blacklist(self):
        blacklist = set()
        try:
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith(('更新时间', '#')):
                            continue
                        url = line.split(',')[-1].split('$')[0].strip()
                        if '://' in url:
                            blacklist.add(url)
            logger.info(f"加载URL黑名单: {len(blacklist)} 条")
        except Exception as e:
            logger.warning(f"加载黑名单失败: {e}")
        return blacklist

    def _save_blacklist(self):
        if not self.new_failed:
            return
        try:
            existing_lines = []
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], 'r', encoding='utf-8') as f:
                    existing_lines = [l.rstrip('\n') for l in f]
            # 新增表头
            if not any(l.startswith('更新时间') for l in existing_lines[:5]):
                bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
                existing_lines = [
                    "更新时间,#genre#",
                    f"{bj_time.strftime('%Y%m%d %H:%M')},url",
                    "",
                    "blacklist,#genre#"
                ] + existing_lines
            # 去重新增
            existing_urls = set()
            for line in existing_lines:
                if line and not line.startswith(('更新时间', '#')):
                    url = line.split(',')[-1].strip()
                    if url:
                        existing_urls.add(url)
            for url in self.new_failed:
                if url not in existing_urls:
                    existing_lines.append(url)
            # 写入
            with open(FILE_PATHS["blacklist_auto"], 'w', encoding='utf-8') as f:
                f.write('\n'.join(existing_lines))
            logger.info(f"黑名单更新完成，新增{len(self.new_failed)}条")
        except Exception as e:
            logger.warning(f"保存黑名单失败: {e}")

    def _read_file_lines(self, file_path: str) -> List[str]:
        """读取文件所有行，过滤空行和注释"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return [l.strip() for l in f.read().splitlines() if l.strip() and not l.startswith('#')]
        except Exception as e:
            logger.warning(f"读取文件失败 {os.path.basename(file_path)}: {e}")
            return []

    def run(self):
        logger.info("===== 开始流媒体检测 =====")
        all_streams: List[Tuple[str, str]] = []
        seen_urls: Set[str] = set()

        # ===================== 【重点修复】处理urls.txt（远程列表地址）=====================
        urls_remote_list = self._read_file_lines(FILE_PATHS["urls"])
        if urls_remote_list:
            logger.info(f"开始拉取 urls.txt 中的 {len(urls_remote_list)} 个远程源列表")
            for remote_url in urls_remote_list:
                if not remote_url.startswith('http'):
                    continue
                # 拉取远程列表
                stream_list = fetch_remote_list(remote_url)
                for name, url in stream_list:
                    if url not in seen_urls and url not in self.blacklist:
                        seen_urls.add(url)
                        all_streams.append((name, url))
                        self.source_map[url] = "urls"  # 标记来源
            self.urls_total = len([u for u in self.source_map.values() if u == "urls"])
            logger.info(f"urls.txt 处理完成，共获取 {self.urls_total} 个有效源")

        # ===================== 【重点修复】处理my_urls.txt（自有直播源，直接读取）=====================
        my_urls_lines = self._read_file_lines(FILE_PATHS["my_urls"])
        if my_urls_lines:
            logger.info(f"开始处理 my_urls.txt 中的自有直播源")
            valid_count = 0
            for line in my_urls_lines:
                res = clean_source_line(line)
                if res:
                    name, url = res
                    if url not in seen_urls and url not in self.blacklist:
                        seen_urls.add(url)
                        all_streams.append((name, url))
                        self.source_map[url] = "my_urls"  # 标记来源
                        valid_count += 1
            self.my_urls_total = valid_count
            logger.info(f"my_urls.txt 处理完成，共获取 {self.my_urls_total} 个有效源")

        # 处理手动白名单
        whitelist_lines = self._read_file_lines(FILE_PATHS["whitelist_manual"])
        for line in whitelist_lines:
            res = clean_source_line(line)
            if res:
                name, url = res
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_streams.append((name, url))
                    self.whitelist.add(url)
                    self.source_map[url] = "manual"
        logger.info(f"手动白名单: {len(self.whitelist)} 个频道")

        # 待检测列表
        to_check = [url for _, url in all_streams]
        logger.info(f"待检测总源数: {len(to_check)} 条")

        # 并发检测
        results: List[Tuple[str, bool, float, str]] = []
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
            future_map = {executor.submit(check_stream_url, u): u for u in to_check}
            for future in as_completed(future_map):
                url = future_map[future]
                try:
                    is_valid, elapsed, status = future.result()
                    results.append((url, is_valid, elapsed, status))
                    # 非白名单的无效源加入黑名单
                    if not is_valid and url not in self.whitelist:
                        self.new_failed.add(url)
                except Exception as e:
                    logger.error(f"检测异常 {url}: {e}")
                    results.append((url, False, 0, "error"))
                    if url not in self.whitelist:
                        self.new_failed.add(url)

        # 保存黑名单
        self._save_blacklist()

        # 排序结果：有效源按耗时升序
        results_sorted = sorted(results, key=lambda x: (not x[1], x[2]))

        # ===================== 【修复】分文件统计成功数 =====================
        for url, is_valid, _, _ in results_sorted:
            source = self.source_map.get(url, "")
            if is_valid:
                if source == "urls":
                    self.urls_success += 1
                elif source == "my_urls":
                    self.my_urls_success += 1

        # 保存测速结果
        with open(FILE_PATHS["whitelist_respotime"], 'w', encoding='utf-8') as f:
            bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
            f.write(f"更新时间,#genre#\n{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
            f.write("耗时(ms),直播地址,状态,是否有效\n")
            for url, is_valid, elapsed, status in results_sorted:
                f.write(f"{elapsed},{url},{status},{'✅' if is_valid else '❌'}\n")
        
        # 保存自动白名单（有效源）
        with open(FILE_PATHS["whitelist_auto"], 'w', encoding='utf-8') as f:
            bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
            f.write(f"更新时间,#genre#\n{bj_time.strftime('%Y%m%d %H:%M')}\n\n")
            for url, is_valid, _, _ in results_sorted:
                if is_valid:
                    f.write(f"自动,{url}\n")
        
        # 分文件统计日志（重点优化）
        logger.info("="*60)
        logger.info(f"===== 分文件源统计结果 =====")
        logger.info(f"urls.txt: 总有效源数 {self.urls_total} 个，检测成功 {self.urls_success} 个")
        logger.info(f"my_urls.txt: 总有效源数 {self.my_urls_total} 个，检测成功 {self.my_urls_success} 个")
        logger.info("="*60)

        # 总统计日志
        total = len(results)
        valid_total = sum(1 for _, is_valid, _, _ in results if is_valid)
        invalid_total = total - valid_total
        elapsed_s = (datetime.now() - self.start_time).seconds
        logger.info(
            f"===== 检测完成 =====\n"
            f"  总计: {total} 条\n"
            f"  ✅ 有效源: {valid_total}\n"
            f"  ❌ 无效源: {invalid_total}\n"
            f"  耗时: {elapsed_s}s"
        )

# ==============================================
# 主程序执行
# ==============================================
def main():
    try:
        logger.info("===== 开始执行Token自动更新流程 =====")
        # 1. 获取Token
        token = get_taoiptv_token()
        # 2. 更新my_urls.txt
        update_success = False
        if token:
            update_success = update_my_urls_all(token)
        # 3. 推送到GitHub仓库（解决网页看不到更新的问题）
        if update_success:
            git_commit_push()
        # 4. 执行流媒体检测
        checker = StreamChecker()
        checker.run()
        logger.info("===== 全部流程执行完成 =====")
    except Exception as e:
        logger.error(f"主程序异常: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
