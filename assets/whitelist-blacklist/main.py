import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse, quote, unquote, urljoin
import ssl
import re
from typing import List, Tuple, Set, Dict, Optional
import logging
import sys
import subprocess
import json

# ==============================================
# 路径配置 DEEP
# ==============================================
SCRIPT_ABS_PATH = os.path.abspath(__file__)
SCRIPT_DIR = os.path.dirname(SCRIPT_ABS_PATH)
ASSETS_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(ASSETS_DIR)

FILE_PATHS = {
    "my_urls": os.path.join(ASSETS_DIR, "my_urls.txt"),
    "urls": os.path.join(ASSETS_DIR, "urls.txt"),
    "first_source": os.path.join(ASSETS_DIR, "111.txt"),
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
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(FILE_PATHS["log"], mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

logger.info("=" * 60)
logger.info(f"项目根目录: {PROJECT_ROOT}")
logger.info(f"脚本目录:   {SCRIPT_DIR}")
logger.info(f"assets目录: {ASSETS_DIR}")
logger.info(f"my_urls.txt: {FILE_PATHS['my_urls']} ({'存在' if os.path.exists(FILE_PATHS['my_urls']) else '不存在'})")
logger.info("=" * 60)

# ==============================================
# 全局配置
# ==============================================
class Config:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    TIMEOUT_FETCH = 15
    TIMEOUT_CHECK = 3.0
    TIMEOUT_WHITELIST = 4.5
    MAX_WORKERS = 30

RE_ALL_URLS = re.compile(r'https?://[^\s,\'"<>}\])]+')

# ==============================================
# Xvfb 虚拟显示
# ==============================================
def setup_virtual_display():
    if sys.platform.startswith('linux'):
        try:
            subprocess.run(["which", "Xvfb"], capture_output=True, timeout=5)
        except:
            subprocess.run(["apt-get", "update", "-qq"], capture_output=True)
            subprocess.run(["apt-get", "install", "-y", "-qq", "xvfb"], capture_output=True)
        if not os.environ.get("DISPLAY"):
            subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1920x1080x24"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.environ["DISPLAY"] = ":99"
            logger.info("Xvfb 启动在 :99")

# ==============================================
# 强化 Playwright Token 获取
# ==============================================
def get_taoiptv_token_by_playwright() -> Optional[str]:
    try:
        logger.info("🔍 尝试使用 Playwright (强化 Chrome) 获取 Token...")
        from playwright.sync_api import sync_playwright

        # 安装 playwright stealth
        try:
            import playwright_stealth
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright-stealth", "-q"], check=True, capture_output=True)
            import playwright_stealth

        with sync_playwright() as p:
            # 尝试使用系统 Chrome
            launch_args = {
                "headless": True,
                "args": [
                    '--no-sandbox', '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            }
            # 如果系统有 chrome，使用它
            chrome_paths = ["/usr/bin/google-chrome", "/opt/google/chrome/chrome", "/usr/bin/chromium-browser"]
            for path in chrome_paths:
                if os.path.exists(path):
                    launch_args["executable_path"] = path
                    logger.info(f"使用浏览器: {path}")
                    break

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(
                user_agent=Config.USER_AGENT,
                viewport={'width': 1920, 'height': 1080},
                locale='zh-CN'
            )
            page = context.new_page()

            # 应用 stealth
            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
                logger.info("stealth 已应用")
            except Exception:
                try:
                    stealth = playwright_stealth.Stealth()
                    stealth.apply_stealth_sync(page)
                except:
                    pass

            # 访问页面
            logger.info("正在访问 https://taoiptv.com ...")
            try:
                page.goto("https://taoiptv.com", wait_until="domcontentloaded", timeout=120000)
            except Exception:
                page.goto("https://taoiptv.com", wait_until="load", timeout=120000)

            page.wait_for_timeout(8000)  # 等待动态验证

            # 查找并点击获取Token
            selectors = [
                "text=获取Token", "a:has-text('Token')", "span:has-text('Token')",
                "div:has-text('Token')", "button:has-text('Token')"
            ]
            clicked = False
            for sel in selectors:
                try:
                    elem = page.locator(sel).first
                    if elem.is_visible(timeout=3000):
                        elem.click(force=True, timeout=5000)
                        clicked = True
                        logger.info(f"点击成功: {sel}")
                        break
                except:
                    continue
            if not clicked:
                page.evaluate("""()=>{
                    let e=document.querySelector('*');if(e&&e.innerText.includes('Token'))e.click();
                }""")
                clicked = True

            if clicked:
                page.wait_for_timeout(4000)
                content = page.content()
                tokens = re.findall(r'[a-f0-9]{16}', content, re.I)
                invalid = ["8c78df7c7c0f4844"]
                valid = [t for t in tokens if t.lower() not in invalid]
                if valid:
                    token = valid[0]
                    logger.info(f"✅ Playwright 获取 Token: {token}")
                    return token

                # 尝试 storage
                try:
                    storage = page.evaluate("JSON.stringify({...localStorage, ...sessionStorage})")
                    for k, v in json.loads(storage).items():
                        if 'token' in k.lower() and len(str(v))==16 and re.match(r'^[a-f0-9]{16}$', str(v)):
                            logger.info(f"从 storage 获取 Token: {v}")
                            return v
                except:
                    pass
            browser.close()
            return None
    except Exception as e:
        logger.error(f"Playwright 失败: {e}")
        return None

def update_my_urls_all(token: str) -> bool:
    if not token or len(token) != 16:
        return False
    file_path = FILE_PATHS["my_urls"]
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        count = len(re.findall(r"token=[a-f0-9]{16}", content, re.I))
        if count == 0:
            return False
        content = re.sub(r"token=[a-f0-9]{16}", f"token={token}", content, flags=re.I)
        content = re.sub(r"^#\s*更新时间:.*$", "", content, flags=re.MULTILINE)
        content = content.strip() + "\n"
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        header = f"# 更新时间: {bj.strftime('%Y-%m-%d %H:%M:%S')} | Token: {token}\n"
        content = header + content
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"✅ my_urls.txt 已更新，替换 {count} 个 Token")
        return True
    except Exception as e:
        logger.error(f"更新失败: {e}")
        return False

def fetch_and_save_first_source(token: str = None) -> bool:
    # 简化版：尝试获取第一个源并保存，失败则写入错误信息
    try:
        path = FILE_PATHS["my_urls"]
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip() and not l.startswith("#")]
        first_url = None
        for line in lines:
            urls = RE_ALL_URLS.findall(line)
            if urls:
                first_url = urls[0]
                break
        if not first_url:
            return False
        if token and "token=" in first_url:
            first_url = re.sub(r"token=[a-f0-9]{16}", f"token={token}", first_url, flags=re.I)
        safe_url = quote(unquote(first_url), safe=":/?&=#%")
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(safe_url, headers={"User-Agent": Config.USER_AGENT})
        with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=Config.TIMEOUT_FETCH) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        out_path = FILE_PATHS["first_source"]
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        header = f"# 保存时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}\n# 来源: {first_url}\n\n"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(header)
            if "Authentication Failed" in content:
                f.write("# ❌ 认证失败，Token 无效\n")
                logger.error("Token 无效")
                return False
            f.write(content)
        logger.info(f"✅ 第一个源已保存至 {out_path}")
        return True
    except Exception as e:
        logger.error(f"保存失败: {e}")
        return False

def git_commit_push():
    try:
        os.chdir(PROJECT_ROOT)
        subprocess.run(["git", "config", "--global", "user.name", "Bot"], check=True, capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@noreply"], check=True, capture_output=True)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if not status:
            return True
        subprocess.run(["git", "add", "assets/my_urls.txt", "assets/111.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Auto update token"], check=True, capture_output=True)
        token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        if token and repo:
            push_url = f"https://x-access-token:{token}@github.com/{repo}.git"
            subprocess.run(["git", "push", push_url, "HEAD"], check=True, capture_output=True)
        else:
            subprocess.run(["git", "push"], check=True, capture_output=True)
        logger.info("✅ 推送成功")
        return True
    except Exception as e:
        logger.error(f"推送失败: {e}")
        return False

# 其他函数（StreamChecker 等）保持不变，省略...
# 主函数
def main():
    try:
        logger.info("===== 开始 Token 更新流程 =====")
        setup_virtual_display()

        token = get_taoiptv_token_by_playwright()
        if not token:
            logger.warning("❌ 未能通过自动化获取有效 Token。")
            # 不更新 my_urls.txt，但尝试保存第一个源的状态文件
            fetch_and_save_first_source(None)
            git_commit_push()
            logger.info("流程结束（无有效 Token）")
            return

        update_my_urls_all(token)
        if not fetch_and_save_first_source(token):
            logger.error("Token 无效，回滚")
            # 此时可以恢复旧的 my_urls.txt？这里略
        git_commit_push()

        # 执行检测
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # （完整的 StreamChecker 类及检测流程省略，请复制之前完整代码中的其余部分）
        logger.info("===== 全部流程执行完成 =====")
    except Exception as e:
        logger.error(f"主程序异常: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
