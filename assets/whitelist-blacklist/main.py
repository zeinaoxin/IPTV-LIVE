import urllib.request
from urllib.parse import quote, unquote
import re
import os
from datetime import datetime, timedelta, timezone
import opencc
import time
import subprocess
import ssl
import hashlib
from concurrent.futures import ThreadPoolExecutor

# ===================== 全局核心配置 =====================
# 指定按TXT文件内顺序排列的分类，其余自动字典序排序，按需增删
ORDERED_CHANNEL_TYPES = ["央视频道", "卫视频道", "港澳台", "电影频道", "电视剧频道", "埋堆堆", "咪咕直播"]
# 频道名称清理字符集
REMOVAL_LIST = [
    "「IPV4」", "「IPV6」", "[ipv6]", "[ipv4]", "_电信", "电信", "（HD）", "[超清]", "高清", "超清", "-HD", "(HK)", "AKtv", "@", "IPV6", "🎞️", "🎦", " ", "[BD]", "[VGA]", "[HD]", "[SD]", "(1080p)", "(720p)", "(480p)", "HD", "｜"
]
# 网络请求配置 - 优化超时时间，优先速度
USER_AGENT = "PostmanRuntime-ApipostRuntime/1.1.0"
URL_FETCH_TIMEOUT = 5  # 减少超时时间（更快响应）
RESPONSE_TIME_THRESHOLD = 300  # 降低响应时间阈值（仅保留300ms内的源，优先快速源）
SINGLE_CHANNEL_MAX_COUNT = 10  # 减少每个频道的源数量（优先选最快的10个）
# M3U相关配置
TVG_URL = "https://ghfast.top/https://github.com/CCSH/IPTV/raw/refs/heads/main/e.xml.gz"
LOGO_URL_TPL = "https://ghfast.top/https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/logo/{}.png"

# ===================== Live Update 新增：域名/后缀拦截（优化速度） =====================
# 坏域名（整域拦截，不区分子域名）- 优先快速过滤
BLOCK_DOMAINS = {
    "iptv.catvod.com", "dd.ddzb.fun", "goodiptv.club", "jiaojirentv.top", "alist.xicp.fun", "rihou.cc",
    "php.jdshipin.com", "t.freetv.fun", "stream1.freetv.fun", "stream2.freetv.fun", "example.com"  # 示例新增
}
# 点播/图片类域名（拦截但不一定整域）- 优先快速过滤
VOD_DOMAINS = {
    "kwimgs.com", "kuaishou.com", "ixigua.com", "douyin.com", "tiktokcdn.com", "bdstatic.com",
    "byteimg.com", "txmov2.a.kwimgs.com", "alimov2.a.kwimgs.com", "p6-dy.byteimg.com", "example.com"  # 示例新增
}
# 点播/图片后缀（不含.flv，.flv可能是直播推流）
VOD_EXTENSIONS = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".rmvb"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}

def is_domain_blocked(url: str) -> bool:
    """整域拦截：iptv.catvod.com 等 - 优化速度，使用集合快速查找"""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        return host in BLOCK_DOMAINS or any(host.endswith("." + d) for d in BLOCK_DOMAINS)
    except Exception:
        return False

def is_vod_or_image_url(url: str) -> bool:
    """判断是否为点播文件或图片（非直播流）- 优化速度"""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        path = (urlparse(url).path or "").lower()
        # 域名匹配 - 使用集合快速查找
        if host in VOD_DOMAINS or any(host.endswith("." + d) for d in VOD_DOMAINS):
            return True
        # 图片后缀
        if any(path.endswith(ext) for ext in IMAGE_EXTENSIONS):
            return True
        # 点播后缀（排除.flv）
        if any(path.endswith(ext) for ext in VOD_EXTENSIONS):
            return True
    except Exception:
        pass
    return False

# ===================== 代理配置（模拟本地网络） =====================
def setup_proxy():
    """配置Cloudflare WARP代理，模拟本地网络"""
    try:
        # 安装WARP（如果未安装）
        subprocess.run(["sudo", "apt-get", "update"], check=True)
        subprocess.run(["sudo", "apt-get", "install", "-y", "cloudflare-warp"], check=True)
        # 注册并连接WARP
        subprocess.run(["sudo", "warp-cli", "--accept-tos", "registration", "new"], check=True)
        subprocess.run(["sudo", "warp-cli", "--accept-tos", "connect"], check=True)
        # 等待代理连接成功（10秒）
        time.sleep(10)
        # 验证代理是否生效（可选）
        import urllib.request
        resp = urllib.request.urlopen("https://ipinfo.io/country", timeout=5)
        country = resp.read().decode("utf-8")
        print(f"[PROXY] 代理已连接，当前国家: {country}")
    except Exception as e:
        print(f"[PROXY] 代理配置失败: {e}")
        # 若代理失败，继续运行（不中断）

# ===================== 通用工具函数 =====================
def get_project_dirs() -> dict:
    script_abspath = os.path.abspath(__file__)
    root_dir = os.path.dirname(script_abspath)
    return {
        "root": root_dir,
        "blacklist_auto": os.path.join(root_dir, "assets/whitelist-blacklist/blacklist_auto.txt"),
        "whitelist_respotime": os.path.join(root_dir, "assets/whitelist-blacklist/whitelist_respotime.txt"),
        "blacklist_manual": os.path.join(root_dir, "assets/whitelist-blacklist/blacklist_manual.txt"),
        "whitelist_manual": os.path.join(root_dir, "assets/whitelist-blacklist/whitelist_manual.txt"),
        "corrections_name": os.path.join(root_dir, "assets/corrections_name.txt"),
        "urls": os.path.join(root_dir, "assets/urls.txt"),
        "main_channel": os.path.join(root_dir, "主频道"),
        "local_channel": os.path.join(root_dir, "地方台"),
    }

def read_txt(file_path: str, strip: bool = True, skip_empty: bool = True) -> list:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if strip:
            lines = [line.strip() for line in lines]
        if skip_empty:
            lines = [line for line in lines if line]
        return lines
    except FileNotFoundError:
        print(f"[ERROR] 文件未找到: {file_path}")
        return []
    except Exception as e:
        print(f"[ERROR] 读取文件 {file_path} 失败: {str(e)}")
        return []

def write_txt(file_path: str, data: list or str) -> None:
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if isinstance(data, list):
            data = '\n'.join([str(line) for line in data])
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(data)
        print(f"[SUCCESS] 文件写入成功: {os.path.basename(file_path)}")
    except Exception as e:
        print(f"[ERROR] 写入文件 {file_path} 失败: {str(e)}")

def safe_quote_url(url: str) -> str:
    try:
        unquoted = unquote(url)
        return quote(unquoted, safe=':/?&=')
    except Exception:
        return url

def traditional_to_simplified(text: str) -> str:
    if not hasattr(traditional_to_simplified, "converter"):
        traditional_to_simplified.converter = opencc.OpenCC('t2s')
    return traditional_to_simplified.converter.convert(text) if text else ""

# ===================== 黑名单/纠错字典处理 =====================
def load_blacklist(blacklist_auto_path: str, blacklist_manual_path: str) -> set:
    def _extract_black_urls(file_path):
        lines = read_txt(file_path)
        urls = []
        for line in lines:
            if "," in line:
                url = line.split(',')[1].strip()
                if url:
                    urls.append(url)
        return urls
    auto_urls = _extract_black_urls(blacklist_auto_path)
    manual_urls = _extract_black_urls(blacklist_manual_path)
    combined = set(auto_urls + manual_urls)
    print(f"[INFO] 合并黑名单URL数: {len(combined)}")
    return combined

def load_corrections(corrections_path: str) -> dict:
    corrections = {}
    lines = read_txt(corrections_path)
    for line in lines:
        if not line or "," not in line:
            continue
        parts = line.split(',')
        correct_name = parts[0].strip()
        for wrong_name in parts[1:]:
            wrong_name = wrong_name.strip()
            if wrong_name:
                corrections[wrong_name] = correct_name
    print(f"[INFO] 加载频道纠错规则数: {len(corrections)}")
    return corrections

# ===================== 频道名称/URL处理 =====================
def clean_channel_name(name: str) -> str:
    if not name:
        return ""
    for item in REMOVAL_LIST:
        name = name.replace(item, "")
    name = traditional_to_simplified(name)
    name = name.strip()
    return name

def clean_channel_url(url: str) -> str:
    """统一清理URL：去多余前后缀/标记符，保证后续比对一致性"""
    url = url.strip()
    # 去除 $ 后的附加参数
    url = url.split('$')[0].strip()
    # 去除 # 后的附加信息
    url = url.split('#')[0].strip()
    return url

# ===================== 频道字典加载 =====================
def load_channel_dictionaries(main_dir: str, local_dir: str) -> tuple:
    main_channels = {
        "央视频道": "央视频道.txt", "卫视频道": "卫视频道.txt", "港澳台": "港澳台.txt",
        "电影频道": "电影频道.txt", "电视剧频道": "电视剧频道.txt", "埋堆堆": "埋堆堆.txt",
        "咪咕直播": "咪咕直播.txt", "天津频道": "天津频道.txt", "新疆频道": "新疆频道.txt"
    }
    main_dict = {}
    for chn_type, filename in main_channels.items():
        file_path = os.path.join(main_dir, filename)
        lines = read_txt(file_path)
        main_dict[chn_type] = lines
        print(f"[INFO] 加载主频道 {chn_type}: {len(lines)} 个")
    local_channels = {
        "儿童频道": "儿童频道.txt", "国际台": "国际台.txt", "纪录片": "纪录片.txt",
        "戏曲频道": "戏曲频道.txt", "上海频道": "上海频道.txt", "湖南频道": "湖南频道.txt",
        "湖北频道": "湖北频道.txt", "广东频道": "广东频道.txt", "浙江频道": "浙江频道.txt",
        "山东频道": "山东频道.txt", "江苏频道": "江苏频道.txt", "安徽频道": "安徽频道.txt",
        "海南频道": "海南频道.txt", "内蒙频道": "内蒙频道.txt", "辽宁频道": "辽宁频道.txt",
        "陕西频道": "陕西频道.txt", "山西频道": "山西频道.txt", "云南频道": "云南频道.txt",
        "北京频道": "北京频道.txt", "重庆频道": "重庆频道.txt", "福建频道": "福建频道.txt",
        "甘肃频道": "甘肃频道.txt", "广西频道": "广西频道.txt", "贵州频道": "贵州频道.txt",
        "河北频道": "河北频道.txt", "河南频道": "河南频道.txt", "黑龙江频道": "黑龙江频道.txt",
        "吉林频道": "吉林频道.txt", "江西频道": "江西频道.txt", "宁夏频道": "宁夏频道.txt",
        "青海频道": "青海频道.txt", "四川频道": "四川频道.txt", "天津频道": "天津频道.txt",
        "新疆频道": "新疆频道.txt", "春晚": "春晚.txt", "直播中国": "直播中国.txt",
        "MTV": "MTV.txt", "收音机频道": "收音机频道.txt"
    }
    local_dict = {}
    for chn_type, filename in local_channels.items():
        file_path = os.path.join(local_dir, filename)
        lines = read_txt(file_path)
        local_dict[chn_type] = lines
        print(f"[INFO] 加载地方台 {chn_type}: {len(lines)} 个")
    return main_dict, local_dict

# ===================== 频道分类核心 =====================
class ChannelClassifier:
    def __init__(self, main_dict: dict, local_dict: dict, blacklist: set):
        self.main_dict = main_dict
        self.local_dict = local_dict
        self.blacklist = blacklist
        self.channel_data = {}
        self.other_lines = []
        self.other_urls = set()
        self.all_urls = {}  # key: chn_type, value: set of urls
        # 全局单频道限流计数器
        self.single_chn_count = {}  # key: 频道名, value: 已添加源数量
        # 存储源的速度信息，用于排序
        self.source_speeds = {}  # key: url, value: response_time

        # 初始化分类数据
        for chn_type in list(main_dict.keys()) + list(local_dict.keys()):
            self.channel_data[chn_type] = []
            self.all_urls[chn_type] = set()

    def check_url_exist(self, chn_type: str, url: str) -> bool:
        if url in self.all_urls.get(chn_type, set()) or "127.0.0.1" in url:
            return True
        return False

    def is_single_chn_limit(self, channel_name: str) -> bool:
        if SINGLE_CHANNEL_MAX_COUNT == -1:
            return False
        current_count = self.single_chn_count.get(channel_name, 0)
        return current_count >= SINGLE_CHANNEL_MAX_COUNT

    def add_channel_line(self, chn_type: str, line: str, url: str, response_time: float = None):
        self.channel_data[chn_type].append(line)
        self.all_urls[chn_type].add(url)
        channel_name = line.split(',')[0].strip()
        self.single_chn_count[channel_name] = self.single_chn_count.get(channel_name, 0) + 1
        if response_time is not None:
            self.source_speeds[url] = response_time

    def add_other_line(self, line: str, url: str):
        if url not in self.other_urls and url not in self.blacklist:
            self.other_urls.add(url)
            self.other_lines.append(line)

    # Live Update 新增：三重拦截（黑名单文件 + 坏域名 + 点播/图片）
    def should_skip(self, channel_url: str) -> bool:
        """返回True表示该URL应该被拦截，不写入 live.txt"""
        if not channel_url:
            return True
        # 1) 黑名单文件
        if channel_url in self.blacklist:
            return True
        # 2) 坏域名
        if is_domain_blocked(channel_url):
            return True
        # 3) 点播/图片
        if is_vod_or_image_url(channel_url):
            return True
        return False

    def classify(self, channel_name: str, channel_url: str, line: str, response_time: float = None):
        # 先判断：拦截/空URL/单频道达上限 → 跳过
        if self.should_skip(channel_url) or not channel_url or self.is_single_chn_limit(channel_name):
            return
        for chn_type, chn_names in self.main_dict.items():
            if channel_name in chn_names and not self.check_url_exist(chn_type, channel_url):
                self.add_channel_line(chn_type, line, channel_url, response_time)
                return
        for chn_type, chn_names in self.local_dict.items():
            if channel_name in chn_names and not self.check_url_exist(chn_type, channel_url):
                self.add_channel_line(chn_type, line, channel_url, response_time)
                return
        self.add_other_line(line, channel_url)

    def get_channel_data(self, chn_type: str) -> list:
        return self.channel_data.get(chn_type, [])

    def get_all_other(self) -> list:
        return self.other_lines

    def sort_sources_by_speed(self, sources: list, threshold: float) -> list:
        """按响应速度排序源，优先快速源，且响应时间不超过阈值"""
        def speed_key(source):
            url = source.split(',')[1].strip()
            speed = self.source_speeds.get(url, float('inf'))
            return speed if speed <= threshold else float('inf')  # 超过阈值的排在后面
        return sorted(sources, key=speed_key)

# ===================== 数据处理与生成 =====================
def is_m3u_content(text: str) -> bool:
    if not text:
        return False
    first_line = text.strip().splitlines()[0].strip()
    return first_line.startswith("#EXTM3U")

def convert_m3u_to_txt(m3u_content: str) -> list:
    lines = [line.strip() for line in m3u_content.splitlines() if line.strip()]
    result = []
    channel_name = ""
    for line in lines:
        if line.startswith("#EXTINF"):
            # 提取频道名
            m = re.search(r',(.+)$', line)
            if m:
                channel_name = m.group(1).strip()
        elif not line.startswith("#"):
            if '://' in line and channel_name:
                result.append(f"{channel_name},{line}")
            channel_name = ""
    return result

def process_single_line(line: str, classifier: ChannelClassifier, corrections: dict):
    if not line or ',' not in line:
        return
    # 兼容部分"双逗号"情况，取最后一个逗号作为分隔
    idx = line.rfind(',')
    channel_name_raw = line[:idx].strip()
    channel_url_raw = line[idx+1:].strip()
    if not channel_url_raw or '://' not in channel_url_raw:
        return
    # 清理URL（去 $、# 等附加信息，统一格式）
    channel_url = clean_channel_url(channel_url_raw)
    # 频道名纠错与清理
    channel_name = clean_channel_name(channel_name_raw)
    if corrections and channel_name in corrections:
        channel_name = corrections[channel_name]
    # 触发分类（内部会做三重拦截：黑名单、坏域名、点播/图片）
    classifier.classify(channel_name, channel_url, f"{channel_name},{channel_url}")

def get_cache_key(url: str) -> str:
    """生成URL的缓存键（MD5哈希）"""
    return hashlib.md5(url.encode('utf-8')).hexdigest()

def process_remote_url(url: str, classifier: ChannelClassifier, corrections: dict):
    """处理远程URL，增加缓存机制，避免重复请求"""
    cache_key = get_cache_key(url)
    cache_file = f"cache/{cache_key}.txt"
    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=URL_FETCH_TIMEOUT) as resp:
                content = resp.read().decode('utf-8', errors='replace')
            os.makedirs("cache", exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"[ERROR] 远程源处理失败: {url} - {str(e)}")
            return
    if is_m3u_content(content):
        lines = convert_m3u_to_txt(content)
    else:
        lines = [l.strip() for l in content.splitlines() if l.strip() and ',' in l and '://' in l]
    for line in lines:
        process_single_line(line, classifier, corrections)
    print(f"[INFO] 远程源处理完成: {url} ({len(lines)} 条)")

def process_remote_urls(urls: list, classifier: ChannelClassifier, corrections: dict):
    """并行处理远程URL，提高效率"""
    with ThreadPoolExecutor(max_workers=5) as executor:  # 根据网络情况调整线程数
        executor.map(lambda url: process_remote_url(url, classifier, corrections), urls)

def sort_channel_data(channel_data: list, chn_type: str, chn_names_list: list or None, classifier: ChannelClassifier, threshold: float) -> list:
    if not chn_names_list:
        return channel_data
    name_to_order = {name: idx for idx, name in enumerate(chn_names_list)}
    # 先按频道顺序排序，再按速度排序（过滤超过阈值的源）
    sorted_data = sorted(channel_data, key=lambda line: name_to_order.get(line.split(',')[0].strip(), 999))
    result = []
    current_channel = None
    current_sources = []
    for line in sorted_data:
        channel_name = line.split(',')[0].strip()
        if channel_name != current_channel:
            if current_sources:
                result.extend(classifier.sort_sources_by_speed(current_sources, threshold))
            current_channel = channel_name
            current_sources = [line]
        else:
            current_sources.append(line)
    if current_sources:
        result.extend(classifier.sort_sources_by_speed(current_sources, threshold))
    return result

def generate_live_text(classifier: ChannelClassifier, main_dict: dict) -> tuple:
    formatted_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d %H:%M")
    version = f"{formatted_time},http://ottrrs.hl.chinamobile.com/PLTV/88888888/224/3221226537/index.m3u8"
    header = ["更新时间,#genre#", version, '\n']
    # 生成lite精简版
    lite_lines = header.copy()
    lite_sort_types = [
        "央视频道", "卫视频道", "港澳台", "电影频道", "电视剧频道", "综艺频道", "NewTV", "iHOT",
        "体育频道", "咪咕直播", "埋堆堆", "音乐频道", "游戏频道", "解说频道"
    ]
    for chn_type in lite_sort_types:
        chn_data = classifier.get_channel_data(chn_type)
        sorted_data = sort_channel_data(chn_data, chn_type, main_dict.get(chn_type, []), classifier, RESPONSE_TIME_THRESHOLD)
        lite_lines += [f"{chn_type},#genre#"] + sorted_data + ['\n']
    lite_lines = lite_lines[:-1] if lite_lines and lite_lines[-1] == '\n' else lite_lines
    # 补全剩余生成full版
    full_lines = lite_lines.copy() + ['\n']
    full_other_types = [
        "儿童频道", "国际台", "纪录片", "戏曲频道", "上海频道", "湖南频道", "湖北频道", "广东频道",
        "浙江频道", "山东频道", "江苏频道", "安徽频道", "海南频道", "内蒙频道", "辽宁频道", "陕西频道",
        "山西频道", "云南频道", "北京频道", "重庆频道", "福建频道", "甘肃频道", "广西频道", "贵州频道",
        "河北频道", "河南频道", "黑龙江频道", "吉林频道", "江西频道", "宁夏频道", "青海频道", "四川频道",
        "天津频道", "新疆频道", "春晚", "直播中国", "MTV", "收音机频道"
    ]
    for chn_type in full_other_types:
        chn_data = classifier.get_channel_data(chn_type)
        sort_list = main_dict.get(chn_type, []) or classifier.local_dict.get(chn_type, [])
        sorted_data = sort_channel_data(chn_data, chn_type, sort_list, classifier, RESPONSE_TIME_THRESHOLD)
        full_lines += [f"{chn_type},#genre#"] + sorted_data + ['\n']
    full_lines = full_lines[:-1] if full_lines and full_lines[-1] == '\n' else full_lines
    return full_lines, lite_lines

def make_m3u(txt_file: str, m3u_file: str, tvg_url: str, logo_tpl: str, threshold: float):
    """生成M3U文件，仅包含响应时间在阈值内的优质源"""
    try:
        if not os.path.exists(txt_file):
            print(f"[ERROR] M3U源文件不存在: {txt_file}")
            return
        m3u_content = f"#EXTM3U x-tvg-url=\"{tvg_url}\"\n"
        lines = read_txt(txt_file, strip=True, skip_empty=True)
        group_name = ""
        for line in lines:
            if "," not in line:
                continue
            parts = line.split(',', 1)
            if len(parts) != 2:
                continue
            if "#genre#" in parts[1]:
                group_name = parts[0].strip()
                continue
            channel_name, channel_url = parts[0].strip(), parts[1].strip()
            if not channel_url or "://" not in channel_url:
                continue
            # 检查响应时间是否在阈值内
            url = channel_url.split(',')[1].strip() if ',' in channel_url else channel_url
            resp_time = classifier.source_speeds.get(url, float('inf'))
            if resp_time > threshold:
                continue  # 跳过慢的源
            logo_url = logo_tpl.format(channel_name)
            m3u_content += (
                f"#EXTINF:-1 tvg-name=\"{channel_name}\" tvg-logo=\"{logo_url}\" group-title=\"{group_name}\",{channel_name}\n"
                f"{channel_url}\n"
            )
        write_txt(m3u_file, m3u_content)
    except Exception as e:
        print(f"[ERROR] 生成M3U失败 {m3u_file}: {str(e)}")

# ===================== 主函数执行 =====================
if __name__ == "__main__":
    # 配置代理（模拟本地网络）
    setup_proxy()
    
    timestart = datetime.now()
    print(f"[START] 程序开始执行: {timestart.strftime('%Y%m%d %H:%M:%S')}")
    dirs = get_project_dirs()
    # 1) 加载黑名单（文件级）
    blacklist = load_blacklist(dirs["blacklist_auto"], dirs["blacklist_manual"])
    corrections = load_corrections(dirs["corrections_name"])
    main_dict, local_dict = load_channel_dictionaries(dirs["main_channel"], dirs["local_channel"])
    classifier = ChannelClassifier(main_dict, local_dict, blacklist)
    # 2) 处理手动白名单（拦截逻辑由 classify 内部统一执行）
    print(f"[PROCESS] 处理手动白名单")
    whitelist_manual = read_txt(dirs["whitelist_manual"])
    classifier.other_lines.append("白名单,#genre#")
    for line in whitelist_manual:
        process_single_line(line, classifier, corrections)
    # 3) 处理自动白名单（响应时间<RESPONSE_TIME_THRESHOLD ms）
    print(f"[PROCESS] 处理自动白名单（响应时间<{RESPONSE_TIME_THRESHOLD}ms）")
    whitelist_respotime = read_txt(dirs["whitelist_respotime"])
    classifier.other_lines.append("白名单测速,#genre#")
    for line in whitelist_respotime:
        if "#genre#" in line or "," not in line or "://" not in line:
            continue
        parts = line.split(",")
        try:
            time_str = parts[0].replace('ms', '').strip()
            resp_time = float(time_str) if time_str else float('inf')
        except (ValueError, IndexError, AttributeError):
            resp_time = float('inf')
        if resp_time < RESPONSE_TIME_THRESHOLD:
            # parts[1] 可能是 url，也可能包含额外的列（status、kind等），我们只取第一个含://的片段
            url_part = next((p for p in parts[1:] if '://' in p), None)
            if not url_part:
                continue
            # 若原始行包含频道名（如 "CCTV1,http://..."）则保留；否则用URL作为"无名源"
            channel_part = parts[1].split(',')[0].strip() if ',' in parts[1] else ""
            combined_line = f"{channel_part},{url_part}" if channel_part else f"_白名单测速,{url_part}"
            process_single_line(combined_line, classifier, corrections)
    # 4) 处理远程URL源（并行处理，提高效率）
    print(f"[PROCESS] 处理远程URL源")
    urls = read_txt(dirs["urls"])
    process_remote_urls(urls, classifier, corrections)
    # 5) 生成 live.txt / live_lite.txt / others.txt
    live_full, live_lite = generate_live_text(classifier, main_dict)
    live_full_path = os.path.join(dirs["root"], "live.txt")
    live_lite_path = os.path.join(dirs["root"], "live_lite.txt")
    others_path = os.path.join(dirs["root"], "others.txt")
    write_txt(live_full_path, live_full)
    write_txt(live_lite_path, live_lite)
    write_txt(others_path, classifier.get_all_other())
    # 6) 生成 M3U（仅包含优质源）
    print(f"[GENERATE] 生成M3U文件")
    make_m3u(live_full_path, os.path.join(dirs["root"], "live.m3u"), TVG_URL, LOGO_URL_TPL, RESPONSE_TIME_THRESHOLD)
    make_m3u(live_lite_path, os.path.join(dirs["root"], "live_lite.m3u"), TVG_URL, LOGO_URL_TPL, RESPONSE_TIME_THRESHOLD)
    # 7) 统计
    timeend = datetime.now()
    elapsed = timeend - timestart
    minutes, seconds = int(elapsed.total_seconds() // 60), int(elapsed.total_seconds() % 60)
    blacklist_count = len(blacklist)
    live_count = len(live_full)
    others_count = len(classifier.get_all_other())
    print("=" * 60)
    print(f"[END] 程序执行完成: {timeend.strftime('%Y%m%d %H:%M:%S')}")
    print(f"[STAT] 执行时间: {minutes} 分 {seconds} 秒")
    print(f"[STAT] live.txt行数: {live_count}")
    print(f"[STAT] others.txt行数: {others_count}")
    print("=" * 60)
