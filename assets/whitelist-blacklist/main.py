import os
import sys
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Set
import logging

# ==============================================
# 路径配置
# ==============================================
SCRIPT_ABS_PATH = os.path.abspath(__file__)
SCRIPT_DIR = os.path.dirname(SCRIPT_ABS_PATH)
ASSETS_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)
MY_URLS_DIR = os.path.join(ASSETS_DIR, "my_urls")

FILE_PATHS = {
    "whitelist_auto": os.path.join(SCRIPT_DIR, "whitelist_auto.txt"),
    "whitelist_manual": os.path.join(SCRIPT_DIR, "whitelist_manual.txt"),  # 修改：指向 whitelist_manual.txt
    "log": os.path.join(SCRIPT_DIR, "log.txt"),
}

# ==============================================
# 日志配置
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 匹配 URL 主体（去除 $分组 和 #频道名 等后缀，用于精准去重）
RE_URL = re.compile(r'(https?://[^\s,\'"<>}$#]+)')

# ==============================================
# 核心解析：提取 (组名, URL主体)
# ==============================================
def parse_line(line: str) -> Tuple[str, str]:
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

# ==============================================
# 读取目录并去重
# ==============================================
def read_and_dedup(dirpath: str) -> List[str]:
    if not os.path.isdir(dirpath):
        logger.warning(f"目录不存在: {dirpath}")
        return []
        
    txt_files = sorted([f for f in os.listdir(dirpath) if f.lower().endswith(".txt")])
    if not txt_files:
        logger.warning(f"目录下无 .txt 文件: {dirpath}")
        return []
        
    logger.info(f"开始读取: {dirpath}，共 {len(txt_files)} 个文件")
    result = []
    seen = set()
    total_raw = 0
    dup_count = 0
    
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
                    result.append(f"{name},{url}")
        except Exception as e:
            logger.error(f"读取失败 {fpath}: {e}")
            
    logger.info(f"读取完成: 原始 {total_raw} 行，去除重复 {dup_count} 行，保留 {len(result)} 条源")
    return result

# ==============================================
# 写入输出文件
# ==============================================
def write_files(lines: List[str]):
    bj = datetime.now(timezone.utc) + timedelta(hours=8)
    header = f"更新时间,#genre#\n{bj.strftime('%Y%m%d %H:%M')}\n\n"
    
    # 写入 whitelist_auto.txt
    with open(FILE_PATHS["whitelist_auto"], "w", encoding="utf-8") as f:
        f.write(header)
        f.writelines(f"{line}\n" for line in lines)
        
    # 写入 whitelist_manual.txt（修改：去掉 0, 和 ,ok，直接写入纯粹的 频道名,URL 格式）
    with open(FILE_PATHS["whitelist_manual"], "w", encoding="utf-8") as f:
        f.write(header)
        f.writelines(f"{line}\n" for line in lines)
        
    logger.info(f"写入完成: {len(lines)} 条源")

# ==============================================
# Git 提交推送
# ==============================================
def git_commit_push():
    try:
        logger.info("正在同步到GitHub仓库...")
        os.chdir(PROJECT_ROOT)
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Auto-Bot"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@noreply.github.com"], check=True, capture_output=True, text=True)
        
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if not status:
            logger.info("✅ 无文件变更，无需提交")
            return True
            
        # 修改：git add 指向新的 whitelist_manual 路径
        subprocess.run(
            ["git", "add", FILE_PATHS["whitelist_auto"], FILE_PATHS["whitelist_manual"], FILE_PATHS["log"]], 
            check=True, capture_output=True, text=True
        )
        subprocess.run(["git", "commit", "-m", "Auto update whitelist (merge & dedup)"], check=True, capture_output=True, text=True)
        
        gh_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        if gh_token and repo:
            push_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
            subprocess.run(["git", "push", push_url, "HEAD"], check=True, capture_output=True, text=True)
        else:
            subprocess.run(["git", "push"], check=True, capture_output=True, text=True)
            
        logger.info("✅ 已同步到GitHub仓库！")
        return True
    except subprocess.CalledProcessError as e:
        logger.warning(f"Git推送失败: {e.stderr if e.stderr else ''}")
        return False
    except Exception as e:
        logger.warning(f"Git异常: {e}")
        return False

# ==============================================
# 主函数
# ==============================================
def main():
    start_time = datetime.now()
    logger.info("===== 开始执行（纯合并去重，保留所有源） =====")
    lines = read_and_dedup(MY_URLS_DIR)
    if lines:
        write_files(lines)
        git_commit_push()
    else:
        logger.warning("未获取到任何有效源，跳过写入和提交")
    elapsed = (datetime.now() - start_time).seconds
    logger.info(f"===== 执行完成 | 共保留 {len(lines)} 条源 | 耗时 {elapsed}s =====")

if __name__ == "__main__":
    main()
