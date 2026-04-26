import urllib.request
from urllib.parse import quote, unquote
import re
import os
from datetime import datetime, timedelta, timezone
import opencc
import time

# ===================== 全局核心配置 =====================
# 指定按TXT文件内顺序排列的分类，其余自动字典序排序，按需增删
ORDERED_CHANNEL_TYPES = ["央视频道", "卫视频道", "港澳台", "电影频道", "电视剧频道", "埋堆堆", "咪咕直播"]

# 频道名称清理字符集
REMOVAL_LIST = [
    "「IPV4」", "「IPV6」", "[ipv6]", "[ipv4]", "_电信", "电信", "（HD）", "[超清]", "高清", "超清",
    "-HD", "(HK)", "AKtv", "@", "IPV6", "🎞️", "🎦", " ", "[BD]", "[VGA]", "[HD]", "[SD]",
    "(1080p)", "(720p)", "(480p)", "HD", "｜"
]

# 网络请求配置
USER_AGENT = "PostmanRuntime-ApipostRuntime/1.1.0"
URL_FETCH_TIMEOUT = 5

# M3U相关配置
TVG_URL = "https://ghfast.top/https://github.com/CCSH/IPTV/raw/refs/heads/main/e.xml.gz"
LOGO_URL_TPL = "https://ghfast.top/https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/logo/{}.png"

# ===================== 通用工具函数 =====================
def get_project_dirs() -> dict:
    script_abspath = os.path.abspath(__file__)
    root_dir = os.path.dirname(script_abspath)
    return {
        "root": root_dir,
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

# ===================== 纠错字典处理 =====================
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
    return name.strip()

def clean_channel_url(url: str) -> str:
    """统一清理URL：去多余前后缀/标记符，保证后续比对一致性"""
    url = url.strip()
    url = url.split('$')[0].strip()
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
        "新疆频道": "新疆频道.txt"
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
    def __init__(self, main_dict: dict, local_dict: dict):
        self.main_dict = main_dict
        self.local_dict = local_dict
        self.channel_data = {}
        self.other_lines = []
        self.other_urls = set()
        self.all_urls = {}
        
        for chn_type in list(main_dict.keys()) + list(local_dict.keys()):
            self.channel_data[chn_type] = []
            self.all_urls[chn_type] = set()

    def check_url_exist(self, chn_type: str, url: str) -> bool:
        # 仅用于同分类下基础去重，避免同源写多遍
        if url in self.all_urls.get(chn_type, set()) or "127.0.0.1" in url:
            return True
        return False

    def add_channel_line(self, chn_type: str, line: str, url: str):
        self.channel_data[chn_type].append(line)
        self.all_urls[chn_type].add(url)

    def add_other_line(self, line: str, url: str):
        if url not in self.other_urls:
            self.other_urls.add(url)
            self.other_lines.append(line)

    def classify(self, channel_name: str, channel_url: str, line: str):
        if not channel_url:
            return
            
        for chn_type, chn_names in self.main_dict.items():
            if channel_name in chn_names and not self.check_url_exist(chn_type, channel_url):
                self.add_channel_line(chn_type, line, channel_url)
                return
                
        for chn_type, chn_names in self.local_dict.items():
            if channel_name in chn_names and not self.check_url_exist(chn_type, channel_url):
                self.add_channel_line(chn_type, line, channel_url)
                return
                
        self.add_other_line(line, channel_url)

    def get_channel_data(self, chn_type: str) -> list:
        return self.channel_data.get(chn_type, [])

    def get_all_other(self) -> list:
        return self.other_lines

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
    
    # ===== 新增黑名单功能：直接屏蔽含有这些字符的源 =====
    if "[" in line or "catvod.com" in line or "【" in line:
        return

    idx = line.rfind(',')
    channel_name_raw = line[:idx].strip()
    channel_url_raw = line[idx+1:].strip()
    if not channel_url_raw or '://' not in channel_url_raw:
        return
    
    channel_url = clean_channel_url(channel_url_raw)
    channel_name = clean_channel_name(channel_name_raw)
    
    if corrections and channel_name in corrections:
        channel_name = corrections[channel_name]
        
    classifier.classify(channel_name, channel_url, f"{channel_name},{channel_url}")

def process_remote_url(url: str, classifier: ChannelClassifier, corrections: dict):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=URL_FETCH_TIMEOUT) as resp:
            content = resp.read().decode('utf-8', errors='replace')
            if is_m3u_content(content):
                lines = convert_m3u_to_txt(content)
            else:
                lines = [l.strip() for l in content.splitlines() if l.strip() and ',' in l and '://' in l]
            for line in lines:
                process_single_line(line, classifier, corrections)
            print(f"[INFO] 远程源处理完成: {url} ({len(lines)} 条)")
    except Exception as e:
        print(f"[ERROR] 远程源处理失败: {url} - {str(e)}")

def sort_channel_data(channel_data: list, chn_type: str, chn_names_list: list or None, classifier: ChannelClassifier) -> list:
    """排序逻辑：1.按频道字典序；2.每个频道的源将 hl.chinamobile.com 强制置顶"""
    if not channel_data:
        return []
        
    if chn_names_list:
        name_to_order = {name: idx for idx, name in enumerate(chn_names_list)}
        sorted_data = sorted(channel_data, key=lambda line: name_to_order.get(line.split(',')[0].strip(), 999))
    else:
        sorted_data = channel_data

    result = []
    current_channel = None
    current_sources = []
    
    for line in sorted_data:
        channel_name = line.split(',')[0].strip()
        if channel_name != current_channel:
            if current_sources:
                mobile_sources = [s for s in current_sources if "hl.chinamobile.com" in s]
                other_sources = [s for s in current_sources if "hl.chinamobile.com" not in s]
                result.extend(mobile_sources)
                result.extend(other_sources)
            current_channel = channel_name
            current_sources = [line]
        else:
            current_sources.append(line)
            
    if current_sources:
        mobile_sources = [s for s in current_sources if "hl.chinamobile.com" in s]
        other_sources = [s for s in current_sources if "hl.chinamobile.com" not in s]
        result.extend(mobile_sources)
        result.extend(other_sources)
        
    return result

def generate_live_text(classifier: ChannelClassifier, main_dict: dict) -> tuple:
    formatted_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d %H:%M")
    version = f"{formatted_time},http://ottrrs.hl.chinamobile.com/PLTV/88888888/224/3221226537/index.m3u8"
    header = ["更新时间,#genre#", version, ""]

    lite_lines = header.copy()
    lite_sort_types = [
        "央视频道", "卫视频道", "港澳台", "电影频道", "电视剧频道", "综艺频道", "NewTV", "iHOT",
        "体育频道", "咪咕直播", "埋堆堆", "音乐频道", "游戏频道", "解说频道"
    ]
    
    for chn_type in lite_sort_types:
        chn_data = classifier.get_channel_data(chn_type)
        if not chn_data:  # 无内容分类直接跳过
            continue
        sorted_data = sort_channel_data(chn_data, chn_type, main_dict.get(chn_type, []), classifier)
        if sorted_data:
            lite_lines.append(f"{chn_type},#genre#")
            lite_lines.extend(sorted_data)
            lite_lines.append("")

    full_lines = lite_lines.copy()
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
        if not chn_data:  # 无内容分类直接跳过
            continue
        sorted_data = sort_channel_data(chn_data, chn_type, sort_list, classifier)
        if sorted_data:
            full_lines.append(f"{chn_type},#genre#")
            full_lines.extend(sorted_data)
            full_lines.append("")

    # 清理尾部可能产生的多余空行
    while full_lines and not full_lines[-1]:
        full_lines.pop()
    while lite_lines and not lite_lines[-1]:
        lite_lines.pop()

    return full_lines, lite_lines

def make_m3u(txt_file: str, m3u_file: str, tvg_url: str, logo_tpl: str):
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
    timestart = datetime.now()
    print(f"[START] 程序开始执行: {timestart.strftime('%Y%m%d %H:%M:%S')}")
    
    dirs = get_project_dirs()
    corrections = load_corrections(dirs["corrections_name"])
    main_dict, local_dict = load_channel_dictionaries(dirs["main_channel"], dirs["local_channel"])
    
    # 初始化分类器（不再传入黑名单）
    classifier = ChannelClassifier(main_dict, local_dict)

    # 1) 处理手动白名单
    print(f"[PROCESS] 处理手动白名单")
    whitelist_manual = read_txt(dirs["whitelist_manual"])
    classifier.other_lines.append("白名单,#genre#")
    for line in whitelist_manual:
        process_single_line(line, classifier, corrections)

    # 2) 处理自动白名单（不做筛选，直接全部加载）
    print(f"[PROCESS] 处理自动白名单")
    whitelist_respotime = read_txt(dirs["whitelist_respotime"])
    classifier.other_lines.append("白名单测速,#genre#")
    for line in whitelist_respotime:
        if "#genre#" in line or "," not in line or "://" not in line:
            continue
        parts = line.split(",")
        url_part = next((p for p in parts[1:] if '://' in p), None)
        if not url_part:
            continue
        channel_part = parts[1].split(',')[0].strip() if ',' in parts[1] else ""
        combined_line = f"{channel_part},{url_part}" if channel_part else f"_白名单测速,{url_part}"
        process_single_line(combined_line, classifier, corrections)

    # 3) 处理远程URL源
    print(f"[PROCESS] 处理远程URL源")
    urls = read_txt(dirs["urls"])
    for url in urls:
        if url.startswith("http"):
            process_remote_url(url, classifier, corrections)

    # 4) 生成文件
    live_full, live_lite = generate_live_text(classifier, main_dict)
    live_full_path = os.path.join(dirs["root"], "live.txt")
    live_lite_path = os.path.join(dirs["root"], "live_lite.txt")
    others_path = os.path.join(dirs["root"], "others.txt")
    
    write_txt(live_full_path, live_full)
    write_txt(live_lite_path, live_lite)
    write_txt(others_path, classifier.get_all_other())

    # 5) 生成 M3U
    print(f"[GENERATE] 生成M3U文件")
    make_m3u(live_full_path, os.path.join(dirs["root"], "live.m3u"), TVG_URL, LOGO_URL_TPL)
    make_m3u(live_lite_path, os.path.join(dirs["root"], "live_lite.m3u"), TVG_URL, LOGO_URL_TPL)

    # 6) 统计
    timeend = datetime.now()
    elapsed = timeend - timestart
    minutes, seconds = int(elapsed.total_seconds() // 60), int(elapsed.total_seconds() % 60)
    live_count = len(live_full)
    others_count = len(classifier.get_all_other())
    
    print("=" * 60)
    print(f"[END] 程序执行完成: {timeend.strftime('%Y%m%d %H:%M:%S')}")
    print(f"[STAT] 执行时间: {minutes} 分 {seconds} 秒")
    print(f"[STAT] live.txt行数: {live_count}")
    print(f"[STAT] others.txt行数: {others_count}")
    print("=" * 60)
