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

# ==============================================
# 正则：从任意文本中提取所有 http(s) URL
# ==============================================
RE_ALL_URLS = re.compile(r'https?://[^\s,\'"<>}\])]+')

# ==============================================
# Token：通过Playwright模拟点击获取（强化反检测 + 手动回退）
# ==============================================
def get_taoiptv_token_by_playwright() -> Optional[str]:
    """
    使用 Playwright + playwright-stealth 模拟真实点击网页上的"获取Token"元素。
    增加反检测功能、更可靠的等待策略、以及手动输入回退机制。
    """
    try:
        logger.info("正在通过Playwright模拟点击获取TaoIPTV最新Token...")
        
        # 检查playwright是否可用
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright未安装，尝试安装...")
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "-q"], 
                             check=True, capture_output=True, timeout=120)
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], 
                             check=True, capture_output=True, timeout=300)
                from playwright.sync_api import sync_playwright
                logger.info("Playwright安装成功")
            except Exception as e:
                logger.warning(f"Playwright安装失败: {e}，使用备用方法...")
                return get_taoiptv_token_by_agent_browser()

        # 尝试安装 playwright-stealth
        try:
            import playwright_stealth
        except ImportError:
            logger.info("正在安装 playwright-stealth ...")
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright-stealth", "-q"], 
                         check=True, capture_output=True, timeout=60)
            import playwright_stealth
        
        with sync_playwright() as p:
            # 启动浏览器（headless模式），增加反检测参数
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-blink-features=AutomationControlled',  # 关键：隐藏自动化标志
                ]
            )
            context = browser.new_context(
                user_agent=Config.USER_AGENT,
                viewport={'width': 1920, 'height': 1080}
            )
            page = context.new_page()
            
            try:
                # 应用 stealth 伪装
                try:
                    stealth = playwright_stealth.Stealth()
                    stealth.apply_sync(page)
                    logger.info("  playwright-stealth 已应用")
                except Exception as e:
                    logger.warning(f"  应用 stealth 失败: {e}，继续执行...")

                # 授予剪贴板权限
                try:
                    context.grant_permissions(["clipboard-read", "clipboard-write"])
                except Exception:
                    pass

                # 访问网页，使用 'domcontentloaded' 避免超时
                logger.info("  步骤1: 打开 https://taoiptv.com ...")
                page.goto("https://taoiptv.com", wait_until="domcontentloaded", timeout=60000)
                
                # 等待页面关键元素出现
                logger.info("  步骤2: 等待页面关键元素加载...")
                try:
                    page.wait_for_selector("body", state="visible", timeout=15000)
                    page.wait_for_timeout(3000)
                except Exception:
                    logger.warning("  页面加载等待异常，继续尝试查找元素...")

                # 查找并点击"获取Token"元素
                logger.info("  步骤3: 查找'获取Token'元素...")
                selectors = [
                    "text=获取Token",
                    "text=获取 Token",
                    "text=Token",
                    "a:has-text('Token')",
                    "span:has-text('Token')",
                    "div:has-text('Token')",
                    "button:has-text('Token')",
                    "[onclick*='token']",
                    "[onclick*='Token']",
                ]
                
                clicked = False
                for selector in selectors:
                    try:
                        element = page.locator(selector).first
                        if element.is_visible(timeout=2000):
                            logger.info(f"  找到元素: {selector}")
                            # 尝试强制点击，避免元素被遮挡
                            try:
                                element.click(force=True, timeout=5000)
                            except Exception:
                                element.click(timeout=5000)
                            clicked = True
                            logger.info("  步骤4: 已点击'获取Token'元素")
                            break
                    except Exception:
                        continue
                
                if not clicked:
                    logger.info("  尝试通过JavaScript查找并点击...")
                    try:
                        elements = page.locator("*:has-text('Token')").all()
                        for elem in elements:
                            try:
                                text = elem.inner_text()
                                if "获取Token" in text or "获取 Token" in text:
                                    elem.click(force=True, timeout=5000)
                                    clicked = True
                                    logger.info("  通过JavaScript点击成功")
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        logger.warning(f"  JavaScript查找失败: {e}")

                if not clicked:
                    logger.info("  尝试通过 evaluate 方式查找...")
                    try:
                        page.evaluate("""
                            () => {
                                const elements = document.querySelectorAll('*');
                                for (const el of elements) {
                                    if (el.innerText && (el.innerText.includes('获取Token') || el.innerText.includes('获取 Token'))) {
                                        el.click();
                                        return true;
                                    }
                                }
                                return false;
                            }
                        """)
                        clicked = True
                        logger.info("  通过 evaluate 点击成功")
                    except Exception as e:
                        logger.warning(f"  evaluate 点击失败: {e}")
                
                if clicked:
                    page.wait_for_timeout(3000)
                    
                    logger.info("  步骤5: 从页面获取Token...")
                    
                    # 排除已知的无效Token
                    known_invalid_tokens = ["8c78df7c7c0f4844"]
                    
                    # 方法1: 从页面内容中提取
                    page_content = page.content()
                    all_tokens = re.findall(r"[a-f0-9]{16}", page_content, re.I)
                    valid_tokens = [t for t in all_tokens if t.lower() not in [x.lower() for x in known_invalid_tokens]]
                    
                    if valid_tokens:
                        for token in valid_tokens:
                            if f"token={token}" in page_content.lower():
                                logger.info(f"✅ 成功从页面获取Token: {token}")
                                return token
                        logger.info(f"✅ 获取到Token: {valid_tokens[0]}")
                        return valid_tokens[0]
                    
                    # 方法2: 从localStorage获取
                    try:
                        storage = page.evaluate("JSON.stringify({...localStorage, ...sessionStorage})")
                        storage_dict = json.loads(storage)
                        for key, value in storage_dict.items():
                            if 'token' in key.lower() and len(str(value)) == 16:
                                if re.match(r'^[a-f0-9]{16}$', str(value), re.I):
                                    logger.info(f"✅ 成功从storage获取Token: {value}")
                                    return value
                    except Exception:
                        pass
                    
                    # 方法3: 从剪贴板获取
                    try:
                        clipboard_text = page.evaluate("navigator.clipboard.readText()")
                        if clipboard_text:
                            tokens = re.findall(r"[a-f0-9]{16}", clipboard_text, re.I)
                            if tokens:
                                logger.info(f"✅ 成功从剪贴板获取Token: {tokens[0]}")
                                return tokens[0]
                    except Exception:
                        pass
                    
                    logger.warning("  点击成功，但未能从页面获取有效Token")
                else:
                    logger.warning("  未找到'获取Token'元素，可能被Cloudflare验证拦截")
                
            except Exception as e:
                logger.error(f"  页面操作异常: {e}")
            finally:
                browser.close()
        
        # 如果Playwright失败，尝试备用方法
        return get_taoiptv_token_by_agent_browser()
        
    except Exception as e:
        logger.error(f"Playwright获取Token失败: {e}", exc_info=True)
        return get_taoiptv_token_by_agent_browser()


def get_taoiptv_token_by_agent_browser() -> Optional[str]:
    """
    使用 agent-browser CLI 模拟点击获取Token
    """
    try:
        logger.info("正在通过agent-browser模拟点击获取TaoIPTV最新Token...")
        
        # 步骤1: 打开网页
        logger.info("  步骤1: 打开 https://taoiptv.com ...")
        result = subprocess.run(
            ["agent-browser", "open", "https://taoiptv.com", "--timeout", "30000"],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # 等待页面加载
        time.sleep(3)
        
        # 步骤2: 获取页面快照
        logger.info("  步骤2: 获取页面元素快照...")
        result = subprocess.run(
            ["agent-browser", "snapshot", "-i", "--json"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # 步骤3: 点击"获取Token"元素
        logger.info("  步骤3: 查找并点击'获取Token'元素...")
        
        # 尝试多种方式点击
        click_commands = [
            ["agent-browser", "find", "text", "获取Token", "click"],
            ["agent-browser", "find", "text", "Token", "click"],
            ["agent-browser", "find", "text", "获取 Token", "click"],
        ]
        
        clicked = False
        for cmd in click_commands:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    clicked = True
                    logger.info(f"  点击成功: {' '.join(cmd[2:4])}")
                    break
            except Exception:
                continue
        
        if clicked:
            time.sleep(2)
            
            # 步骤4: 从剪贴板获取Token
            logger.info("  步骤4: 从剪贴板获取Token...")
            token = get_clipboard_content()
            
            if token and len(token) == 16 and re.match(r'^[a-f0-9]{16}$', token, re.I):
                logger.info(f"✅ 成功通过agent-browser获取Token: {token}")
                return token
        
        # 如果失败，尝试从页面获取
        return get_taoiptv_token_from_page()
        
    except FileNotFoundError:
        logger.warning("agent-browser未安装，使用备用方法...")
        return get_taoiptv_token_from_page()
    except Exception as e:
        logger.error(f"agent-browser获取Token失败: {e}")
        return get_taoiptv_token_from_page()
    finally:
        # 关闭浏览器
        try:
            subprocess.run(["agent-browser", "close"], capture_output=True, timeout=10)
        except Exception:
            pass


def get_taoiptv_token_from_page() -> Optional[str]:
    """
    备用方法：直接从网页HTML中提取Token
    注意：这个方法可能获取到无效Token，需要验证
    """
    try:
        logger.info("  使用备用方法从网页提取Token...")
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            "https://taoiptv.com",
            headers={
                "User-Agent": Config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        with urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx)
        ).open(req, timeout=15) as resp:
            if resp.getcode() != 200:
                logger.error(f"访问官网失败，状态码: {resp.getcode()}")
                return None
            html = resp.read().decode("utf-8", errors="ignore")
        
        # 排除已知的干扰Token
        known_invalid_tokens = [
            "8c78df7c7c0f4844",  # Cloudflare beacon token
        ]
        
        # 查找所有16位十六进制Token
        all_tokens = re.findall(r"[a-f0-9]{16}", html, re.I)
        
        # 过滤掉已知的无效Token
        valid_tokens = [t for t in all_tokens if t.lower() not in [x.lower() for x in known_invalid_tokens]]
        
        if valid_tokens:
            # 优先选择在特定上下文中的Token
            for token in valid_tokens:
                # 检查Token是否在token=参数附近
                if f"token={token}" in html.lower():
                    logger.info(f"✅ 找到URL参数中的Token: {token}")
                    return token
            
            # 否则返回第一个有效Token
            logger.info(f"✅ 从网页提取Token: {valid_tokens[0]}")
            return valid_tokens[0]
        
        logger.error("❌ 未在页面中匹配到有效Token")
        return None
    except Exception as e:
        logger.error(f"❌ 备用方法获取Token失败: {e}", exc_info=True)
        return None


def get_clipboard_content() -> Optional[str]:
    """
    获取剪贴板内容（跨平台支持）
    """
    try:
        # Linux: 使用 xclip 或 xsel
        if sys.platform.startswith('linux'):
            # 尝试 xclip
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
            
            # 尝试 xsel
            result = subprocess.run(
                ["xsel", "--clipboard", "--output"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
            
            # 尝试 wl-paste (Wayland)
            result = subprocess.run(
                ["wl-paste"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        
        # macOS: 使用 pbpaste
        elif sys.platform == 'darwin':
            result = subprocess.run(
                ["pbpaste"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        
        # Windows: 使用 PowerShell
        elif sys.platform == 'win32':
            result = subprocess.run(
                ["powershell", "-command", "Get-Clipboard"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        
        return None
    except Exception as e:
        logger.debug(f"获取剪贴板失败: {e}")
        return None


# ==============================================
# Token：获取一次，批量更新
# ==============================================
def get_taoiptv_token() -> Optional[str]:
    """优先使用Playwright模拟点击获取Token，失败则尝试手动输入"""
    # 先尝试 Playwright
    token = get_taoiptv_token_by_playwright()
    if token:
        return token
    
    # 尝试 agent-browser
    token = get_taoiptv_token_by_agent_browser()
    if token:
        return token
    
    # 尝试从页面提取
    token = get_taoiptv_token_from_page()
    if token:
        return token
    
    # 所有自动化方法都失败，提示手动输入
    logger.warning("⚠️ 所有自动化方法均失败，需要手动获取Token")
    print("\n" + "=" * 60)
    print("🔴 自动获取Token失败，请手动获取。")
    print("📋 操作步骤：")
    print("   1. 在浏览器中访问 https://taoiptv.com")
    print("   2. 完成人机验证（如有）")
    print("   3. 点击\"获取Token\"按钮")
    print("   4. 将复制到剪贴板的16位Token粘贴到下方")
    print("=" * 60)

    while True:
        manual_token = input("👉 请输入新的16位Token（直接回车将跳过）: ").strip()
        if not manual_token:
            logger.warning("用户未输入token，将使用现有地址继续")
            return None
        if len(manual_token) == 16 and re.match(r"^[a-f0-9]{16}$", manual_token, re.I):
            logger.info(f"✅ 已获取手动输入的Token: {manual_token}")
            return manual_token
        else:
            print("❌ Token格式不正确，必须为16位十六进制字符。")


def update_my_urls_all(token: str) -> bool:
    """更新Token + 删除旧备注 + 写新备注"""
    if not token or len(token) != 16:
        logger.error("❌ Token无效，跳过更新")
        return False
    file_path = FILE_PATHS["my_urls"]
    if not os.path.exists(file_path):
        logger.error(f"❌ my_urls.txt不存在: {file_path}")
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        count = len(re.findall(r"token=[a-f0-9]{16}", content, re.I))
        if count == 0:
            logger.info("✅ 文件中无需更新的Token")
            return False

        content = re.sub(r"token=[a-f0-9]{16}", f"token={token}", content, flags=re.I)

        # 删除旧备注行
        content = re.sub(r"^#\s*更新时间:.*$", "", content, flags=re.MULTILINE)
        content = re.sub(r"\n{2,}", "\n\n", content).strip() + "\n"

        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        header = f"# 更新时间: {bj.strftime('%Y-%m-%d %H:%M:%S')} | Token: {token}\n"
        content = header + content

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"✅ my_urls.txt更新成功！替换 {count} 个Token（{bj.strftime('%Y-%m-%d %H:%M:%S')}）")
        return True
    except Exception as e:
        logger.error(f"❌ 更新失败: {e}", exc_info=True)
        return False


# ==============================================
# 解析第一个远程源并保存到 assets/111.txt
# ==============================================
def fetch_and_save_first_source(token: str = None) -> bool:
    """
    解析 assets/my_urls.txt 文件里第一个远程源
    把网页的内容保存到 assets/111.txt 中
    并在 assets/111.txt 备注保存时间
    每次修改 assets/111.txt 之前，需要清空原内容
    """
    try:
        file_path = FILE_PATHS["my_urls"]
        if not os.path.exists(file_path):
            logger.error(f"❌ my_urls.txt不存在: {file_path}")
            return False
        
        # 读取 my_urls.txt 获取第一个远程源URL
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip() and not l.strip().startswith("#")]
        
        if not lines:
            logger.error("❌ my_urls.txt 中没有有效的URL")
            return False
        
        # 获取第一个URL
        first_url = None
        for line in lines:
            # 提取URL（可能是纯URL，也可能包含其他内容）
            urls = RE_ALL_URLS.findall(line)
            if urls:
                first_url = urls[0]
                break
        
        if not first_url:
            logger.error("❌ my_urls.txt 中没有找到有效的URL")
            return False
        
        # 如果提供了新Token，更新URL中的Token
        if token and "token=" in first_url:
            first_url = re.sub(r"token=[a-f0-9]{16}", f"token={token}", first_url, flags=re.I)
        
        logger.info(f"正在解析第一个远程源: {first_url}")
        
        # 对URL做安全编码（与fetch_remote保持一致）
        safe_url = quote(unquote(first_url), safe=":/?&=#%")
        
        # 获取远程源内容
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            safe_url,
            headers={"User-Agent": Config.USER_AGENT}
        )
        
        with urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx)
        ).open(req, timeout=Config.TIMEOUT_FETCH) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        
        # 检查是否获取成功（不是认证失败页面）
        if "认证token参数不正确" in content or "Authentication Failed" in content:
            logger.error("❌ Token无效，获取第一个远程源失败")
            # 即使失败也写入错误信息到111.txt
            output_path = FILE_PATHS["first_source"]
            bj = datetime.now(timezone.utc) + timedelta(hours=8)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"# 保存时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 来源: {first_url}\n")
                f.write(f"# ❌ 错误: 服务器认证失败，Token无效或已过期\n")
                f.write(f"# 原始返回:\n")
                f.write("# " + "\n# ".join(content.splitlines()[:10]) + "\n")
            return False
        
        # 清空原内容并写入新内容
        output_path = FILE_PATHS["first_source"]
        
        # 添加保存时间备注
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        time_header = f"# 保存时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}\n"
        time_header += f"# 来源: {first_url}\n\n"
        
        # 写入文件（清空原内容）
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(time_header)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        
        logger.info(f"✅ 第一个远程源内容已保存到: {output_path}")
        logger.info(f"   保存时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 解析第一个远程源失败: {e}", exc_info=True)
        # 即使异常也尝试写入错误信息
        try:
            output_path = FILE_PATHS["first_source"]
            bj = datetime.now(timezone.utc) + timedelta(hours=8)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"# 保存时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# ❌ 错误: 获取远程源失败\n")
                f.write(f"# 异常信息: {str(e)}\n")
        except Exception:
            pass
        return False


# ==============================================
# Git 提交推送
# ==============================================
def git_commit_push():
    try:
        logger.info("正在同步到GitHub仓库...")
        os.chdir(PROJECT_ROOT)
        subprocess.run(["git", "config", "--global", "user.name", "IPTV-Auto-Bot"],
                       check=True, capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@noreply.github.com"],
                       check=True, capture_output=True)
        status = subprocess.run(["git", "status", "--porcelain"],
                                capture_output=True, text=True).stdout.strip()
        if not status:
            logger.info("✅ 无文件变更，无需提交")
            return True
        subprocess.run(["git", "add", "assets/my_urls.txt", "assets/111.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Auto update TaoIPTV token and first source"],
                       check=True, capture_output=True)
        gh_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        if gh_token and repo:
            push_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
            subprocess.run(["git", "push", push_url, "HEAD"], check=True, capture_output=True)
        else:
            subprocess.run(["git", "push"], check=True, capture_output=True)
        logger.info("✅ 已同步到GitHub仓库！")
        return True
    except subprocess.CalledProcessError as e:
        hint = ""
        try:
            hint = f" [ACTIONS={os.getenv('GITHUB_ACTIONS','?')} REPO={os.getenv('GITHUB_REPOSITORY','?')}]"
        except Exception:
            pass
        logger.warning(f"Git推送失败:{hint} {e.stderr.decode('utf-8','ignore') if e.stderr else ''}")
        return False
    except Exception as e:
        logger.warning(f"Git异常: {e}")
        return False

# ==============================================
# 域名黑名单
# ==============================================
DOMAIN_BLACKLIST: Set[str] = {
    "iptv.catvod.com", "dd.ddzb.fun", "goodiptv.club", "jiaojirentv.top",
    "alist.xicp.fun", "rihou.cc", "php.jdshipin.com",
    "t.freetv.fun", "stream1.freetv.fun", "hlsztemgsplive.miguvideo", "stream2.freetv.fun",
}

def url_matches_domain_blacklist(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in DOMAIN_BLACKLIST)
    except Exception:
        return False

# ==============================================
# 点播/图片过滤
# ==============================================
VOD_DOMAINS: Set[str] = {
    "kwimgs.com", "kuaishou.com", "ixigua.com", "douyin.com",
    "tiktokcdn.com", "bdstatic.com", "byteimg.com",
}
VOD_EXTS: Set[str] = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".rmvb"}
IMG_EXTS: Set[str] = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}

def is_vod_or_image_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in VOD_DOMAINS):
            return True
        path = urlparse(url).path.lower()
        return path.endswith(tuple(VOD_EXTS)) or path.endswith(tuple(IMG_EXTS))
    except Exception:
        return False

# ==============================================
# 行格式清洗（用于"组名,URL"格式的逐行处理）
# ==============================================
CLEAN_OK = "ok"
CLEAN_NO_FORMAT = "no_format"
CLEAN_EMPTY_NAME = "empty_name"
CLEAN_BAD_URL = "bad_url"
CLEAN_DOMAIN_BL = "domain_blacklist"
CLEAN_VOD = "vod_filtered"

def clean_source_line(line: str) -> Tuple[Optional[Tuple[str, str]], str]:
    if not line:
        return None, CLEAN_NO_FORMAT
    line = line.replace("\r", "").replace("\n", " ").strip()
    if "," not in line or "://" not in line:
        return None, CLEAN_NO_FORMAT
    idx = line.find("://")
    if idx < 1:
        return None, CLEAN_BAD_URL
    prefix = line[: idx - 1]
    pos = prefix.rfind(",")
    if pos < 0:
        return None, CLEAN_NO_FORMAT
    name = re.sub(r"\s{2,}", " ", prefix[:pos].strip())
    if not name:
        return None, CLEAN_EMPTY_NAME
    rest = line[pos + 1:].strip()
    url = rest.split(",")[0].strip().split("$")[0].split("#")[0].strip()
    if not url or "://" not in url:
        return None, CLEAN_BAD_URL
    if url_matches_domain_blacklist(url):
        return None, CLEAN_DOMAIN_BL
    if is_vod_or_image_url(url):
        return None, CLEAN_VOD
    return (name, url), CLEAN_OK

# ==============================================
# 媒体类型判定
# ==============================================
STREAM_CTS = [
    "video/mp2t", "video/mp4", "video/x-flv", "application/vnd.apple.mpegurl",
    "application/octet-stream", "application/x-mpegURL",
]
def is_stream_ct(ct):
    return any(p in ct.lower() for p in STREAM_CTS) if ct else False
def is_html_ct(ct):
    return "text/html" in ct.lower() if ct else False
def _read_chunk(resp, n=4096):
    try:
        return resp.read(n)
    except Exception:
        return b""
def _looks_media(d):
    if not d:
        return False
    return (d[:3] == b"FLV" or (len(d) >= 8 and d[4:8] == b"ftyp")
            or d[:3] == b"ID3" or (len(d) >= 188 and d[0] == 0x47))
def _looks_html(d):
    if not d:
        return False
    d = d.lstrip(b"\xef\xbb\xbf").lstrip()
    return d[:5].lower().startswith((b"<!doc", b"<html"))

# ==============================================
# StreamChecker
# ==============================================
class StreamChecker:
    def __init__(self, manual_urls=None):
        self.start_time = datetime.now()
        self.blacklist_urls = self._load_blacklist()
        self.whitelist_urls: Set[str] = set()
        self.whitelist_lines: List[str] = []
        self.new_failed_urls: Set[str] = set()
        self.manual_urls = manual_urls or []
        self.clean_stats: Dict[str, int] = {
            CLEAN_NO_FORMAT: 0, CLEAN_EMPTY_NAME: 0, CLEAN_BAD_URL: 0,
            CLEAN_DOMAIN_BL: 0, CLEAN_VOD: 0,
        }

    def _load_blacklist(self):
        bl = set()
        try:
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith(("更新时间", "#")):
                            continue
                        url = line.split(",")[-1].split("$")[0].split("#")[0].strip()
                        if "://" in url:
                            bl.add(url)
                logger.info(f"加载URL黑名单: {len(bl)} 条")
        except Exception as e:
            logger.error(f"加载黑名单失败: {e}")
        return bl

    def _save_blacklist(self):
        if not self.new_failed_urls:
            return
        try:
            existing, has_h = [], False
            if os.path.exists(FILE_PATHS["blacklist_auto"]):
                with open(FILE_PATHS["blacklist_auto"], "r", encoding="utf-8") as f:
                    existing = [l.rstrip("\n") for l in f]
                for l in existing[:5]:
                    if l.startswith("更新时间"):
                        has_h = True
                        break
            parts = []
            if not has_h:
                bj = datetime.now(timezone.utc) + timedelta(hours=8)
                parts += [
                    "更新时间,#genre#",
                    f"{bj.strftime('%Y%m%d %H:%M')},url",
                    "",
                    "blacklist,#genre#",
                ]
            seen = set()
            for l in existing:
                if l and not l.startswith(("更新时间", "#")):
                    u = l.split(",")[-1].strip()
                    if u:
                        seen.add(u)
                        parts.append(l)
            for u in self.new_failed_urls:
                if u not in seen:
                    parts.append(u)
            with open(FILE_PATHS["blacklist_auto"], "w", encoding="utf-8") as f:
                f.write("\n".join(parts))
            logger.info(f"黑名单更新，新增{len(self.new_failed_urls)}条")
        except Exception as e:
            logger.error(f"保存黑名单失败: {e}")

    def read_file(self, path, split_by_space=False):
        try:
            with open(path, "r", encoding="utf-8") as f:
                c = f.read()
            if split_by_space:
                return [l.strip() for l in re.split(r"[\s\t\n]+", c)
                        if l.strip().startswith("http")]
            return [l.strip() for l in c.splitlines() if l.strip()]
        except Exception as e:
            logger.warning(f"读取失败 {path}: {e}")
            return []

    def check_http(self, url, timeout):
        s = time.perf_counter()
        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(url, headers={"User-Agent": Config.USER_AGENT})
            with urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=ctx)
            ).open(req, timeout=timeout) as r:
                code = r.getcode()
                ct = r.headers.get("Content-Type", "")
                data = _read_chunk(r)
                ms = round((time.perf_counter() - s) * 1000, 2)
                ok = 200 <= code < 400 or code in (301, 302)
                if not ok:
                    return False, ms, str(code), None
                if is_html_ct(ct) or _looks_html(data):
                    return False, ms, f"{code}/html", "timeout"
                if is_stream_ct(ct) and _looks_media(data):
                    return True, ms, str(code), "stream"
                if b"#EXTM3U" in data:
                    return True, ms, str(code), "playlist"
                return True, ms, str(code), "unknown"
        except Exception as e:
            ms = round((time.perf_counter() - s) * 1000, 2)
            return False, ms, str(e), "timeout"

    def check_url(self, url, is_whitelist=False):
        t = Config.TIMEOUT_WHITELIST if is_whitelist else Config.TIMEOUT_CHECK
        if url_matches_domain_blacklist(url):
            return False, 0, "blacklist", "blacklist"
        if url.startswith(("http://", "https://")):
            return self.check_http(url, t)
        return True, 0, "ok", "stream"

    # =============================================================
    # 【关键修复】fetch_remote
    # - M3U：支持任意非 # 开头的 URL 行（含相对路径）；优先用 group-title
    # - 非 M3U：正则提取 + 逗号/空格 拆解"单行多 URL"；对少量结果打印诊断
    # =============================================================
    def fetch_remote(self, urls):
        all_lines = []
        for raw_url in urls:
            try:
                safe_url = quote(unquote(raw_url), safe=":/?&=#%")
            except Exception:
                safe_url = raw_url
            try:
                ctx = ssl._create_unverified_context()
                req = urllib.request.Request(safe_url, headers={"User-Agent": Config.USER_AGENT})
                with urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=ctx)
                ).open(req, timeout=15) as r:
                    c = r.read().decode("utf-8", errors="replace")

                before = len(all_lines)

                # 检查认证失败
                if "Authentication Failed" in c[:500] or "认证token参数不正确" in c[:500]:
                    logger.warning(f"  ✗ {raw_url[:90]} → 认证失败，token已过期或无效")
                    continue

                # ---------- 分支：M3U ----------
                if "#EXTM3U" in c[:200]:
                    name = ""
                    for l in c.splitlines():
                        l = l.strip()
                        if not l:
                            continue
                        # 组名提取：优先 group-title
                        if l.startswith("#EXTINF"):
                            m_group = re.search(r'group-title\s*=\s*["\']?([^"\',]+)', l)
                            if m_group:
                                name = m_group.group(1).strip()
                            else:
                                name = l.split(",")[-1].strip() if "," in l else ""
                        elif not l.startswith("#"):
                            # 只要不是 # 开头，就当作 URL 行
                            url_candidate = l.strip().split("#")[0].strip().split("$")[0].strip()
                            if not url_candidate:
                                name = ""
                                continue
                            # 相对路径补全
                            if not re.match(r'https?://', url_candidate, re.I):
                                url_candidate = urljoin(raw_url, url_candidate)
                            # 黑名单/点播/图片过滤
                            if url_matches_domain_blacklist(url_candidate):
                                name = ""
                                continue
                            if is_vod_or_image_url(url_candidate):
                                name = ""
                                continue
                            group = name if name else "订阅源"
                            res, _ = clean_source_line(f"{group},{url_candidate}")
                            if res:
                                all_lines.append(f"{res[0]},{res[1]}")
                            else:
                                all_lines.append(f"{group},{url_candidate}")
                            name = ""
                else:
                    # ---------- 分支：非 M3U ----------
                    # 策略1：正则提取所有 http(s) URL
                    raw_urls_found = RE_ALL_URLS.findall(c)
                    for u in raw_urls_found:
                        u = u.strip()
                        if not u:
                            continue
                        u = u.rstrip(".,;:!?)")
                        if is_vod_or_image_url(u):
                            continue
                        if url_matches_domain_blacklist(u):
                            continue
                        all_lines.append(f"订阅源,{u}")

                    # 策略2：如果正则没提取到任何 URL，尝试逐行清洗（兼容"组名,URL"格式）
                    if len(all_lines) == before:
                        for l in c.splitlines():
                            l = l.strip()
                            if not l or l.startswith("#"):
                                continue
                            res, _ = clean_source_line(l)
                            if res:
                                all_lines.append(f"{res[0]},{res[1]}")

                    # 策略3：对"单行多 URL（逗号分隔）"做一次拆解兜底
                    # 如果一整行里出现多个 http，按逗号拆开再逐条入队
                    if len(all_lines) == before:
                        for l in c.splitlines():
                            l = l.strip()
                            if not l or l.startswith("#"):
                                continue
                            if l.count("http") <= 1:
                                continue
                            # 优先用逗号拆
                            parts = l.split(",")
                            for part in parts:
                                part = part.strip()
                                if re.match(r'https?://', part, re.I):
                                    part = part.rstrip(".,;:!?)")
                                    if is_vod_or_image_url(part):
                                        continue
                                    if url_matches_domain_blacklist(part):
                                        continue
                                    all_lines.append(f"订阅源,{part}")

                got = len(all_lines) - before

                # 为 taoiptv 入口增加"少量结果"的诊断，便于后续排查
                if got > 1:
                    logger.info(f"  ✓ {raw_url[:90]} → {got} 个源")
                elif got == 1:
                    diag = c[:500].replace("\n", "\\n").replace("\r", "")
                    logger.warning(f"  ⚠ {raw_url[:90]} → 仅 {got} 个源 | 内容诊断: {diag}")
                else:
                    preview = c[:200].replace("\n", "\\n")
                    logger.warning(f"  ✗ {raw_url[:90]} → 0 个源 | 内容: {preview}")

            except Exception as e:
                logger.error(f"  ✗ {raw_url[:90]} → 异常: {e}")

        return all_lines

    def load_whitelist(self):
        for line in self.read_file(FILE_PATHS["whitelist_manual"]):
            if line.startswith("#"):
                continue
            res, _ = clean_source_line(line)
            if res:
                self.whitelist_urls.add(res[1])
                self.whitelist_lines.append(line)
        logger.info(f"手动白名单: {len(self.whitelist_urls)} 个频道")

    def prepare_lines(self, lines):
        to_check, seen = [], set()
        for line in lines:
            res, reason = clean_source_line(line)
            if not res:
                self.clean_stats[reason] = self.clean_stats.get(reason, 0) + 1
                continue
            _, url = res
            if url in seen:
                continue
            seen.add(url)
            if url in self.blacklist_urls:
                continue
            to_check.append((url, line))
        logger.info(f"待检测 {len(to_check)} 条")
        return to_check, []

    def run(self):
        logger.info("===== 开始流媒体检测 =====")
        self.load_whitelist()
        lines = []

        urls = self.read_file(FILE_PATHS["urls"], split_by_space=True)
        if urls:
            logger.info(f"开始拉取 urls.txt 中的 {len(urls)} 个节点")
            fetched = self.fetch_remote(urls)
            logger.info(f"urls.txt 完成：{len(urls)} 个节点 → {len(fetched)} 个源")
            lines.extend(fetched)
        else:
            logger.warning("未找到 urls.txt")

        my_urls = self.read_file(FILE_PATHS["my_urls"], split_by_space=True)
        if my_urls:
            logger.info(f"开始拉取 my_urls.txt 中的 {len(my_urls)} 个节点")
            fetched = self.fetch_remote(my_urls)
            logger.info(f"my_urls.txt 完成：{len(my_urls)} 个节点 → {len(fetched)} 个源")
            lines.extend(fetched)
        else:
            logger.warning("未找到 my_urls.txt")

        lines.extend(self.whitelist_lines)
        lines.extend(self.manual_urls)
        to_check, _ = self.prepare_lines(lines)

        results = []
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as pool:
            fmap = {
                pool.submit(self.check_url, u, u in self.whitelist_urls): u
                for u, _ in to_check
            }
            for fut in as_completed(fmap):
                url = fmap[fut]
                try:
                    s, ms, code, kind = fut.result()
                    results.append((url, ms, code, kind))
                    if not s and url not in self.whitelist_urls:
                        self.new_failed_urls.add(url)
                except Exception as e:
                    logger.error(f"检测异常 {url}: {e}")
                    self.new_failed_urls.add(url)
        self._save_blacklist()

        results.sort(key=lambda x: (
            {"stream": 0, "playlist": 1, "unknown": 2}.get(x[3], 3), x[1]
        ))
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        with open(FILE_PATHS["whitelist_respotime"], "w", encoding="utf-8") as f:
            f.write(f"更新时间,#genre#\n{bj.strftime('%Y%m%d %H:%M')}\n\n")
            for url, ms, code, kind in results:
                f.write(f"{ms},{url},{code},{kind}\n")
        with open(FILE_PATHS["whitelist_auto"], "w", encoding="utf-8") as f:
            f.write(f"更新时间,#genre#\n{bj.strftime('%Y%m%d %H:%M')}\n\n")
            for url, _, _, kind in results:
                if kind not in ("timeout", "blacklist"):
                    f.write(f"自动,{url}\n")

        total = len(results)
        stream = sum(1 for *_, k in results if k == "stream")
        playlist = sum(1 for *_, k in results if k == "playlist")
        unknown = sum(1 for *_, k in results if k == "unknown")
        timeout = sum(1 for *_, k in results if k == "timeout")
        elapsed = (datetime.now() - self.start_time).seconds
        logger.info(
            f"===== 检测完成 | 总计:{total} | 流:{stream} | 列表:{playlist} | "
            f"未知:{unknown} | 超时:{timeout} | 耗时:{elapsed}s ====="
        )

# ==============================================
# 主函数
# ==============================================
def main():
    try:
        logger.info("===== 开始执行Token自动更新 =====")
        
        # 步骤1: 获取Token（优先Playwright模拟点击，失败则引导手动输入）
        token = get_taoiptv_token()
        
        # 步骤2: 更新my_urls.txt中的Token（如果有新Token）
        if token:
            update_my_urls_all(token)
        
        # 步骤3: 解析第一个远程源并保存到assets/111.txt
        # 传入token确保使用最新Token
        fetch_and_save_first_source(token)
        
        # 步骤4: 同步到GitHub仓库
        git_commit_push()
        
        # 步骤5: 执行WhiteList BlackList检测
        checker = StreamChecker()
        checker.run()
        
        logger.info("===== 全部流程执行完成 =====")
    except KeyboardInterrupt:
        logger.warning("⚠️ 用户中断操作")
        sys.exit(0)
    except Exception as e:
        logger.error(f"主程序异常: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
