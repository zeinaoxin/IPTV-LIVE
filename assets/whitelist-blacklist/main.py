# ... 前面的代码保持不变 ...

# ==============================================
# 新增功能：保存第一个远程源到文件（强制保存）
# ==============================================
def save_first_remote_source():
    """读取my_urls.txt的第一个远程源，解析内容并保存到111.txt，并添加修改时间备注"""
    try:
        logger.info("开始处理第一个远程源...")
        
        # 读取my_urls.txt
        if not os.path.exists(FILE_PATHS["my_urls"]):
            logger.error("❌ my_urls.txt不存在")
            # 即使文件不存在，也创建空文件并备注时间
            output_path = FILE_PATHS["first_source"]
            bj = datetime.now(timezone.utc) + timedelta(hours=8)
            timestamp = bj.strftime('%Y-%m-%d %H:%M:%S')
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"# 修改时间: {timestamp}\n")
                f.write("# 错误: my_urls.txt 不存在\n")
            return False
            
        with open(FILE_PATHS["my_urls"], "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # 找到第一个非注释、非空的URL行
        first_url = None
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and "://" in line:
                first_url = line
                break
        
        if not first_url:
            logger.error("❌ my_urls.txt中没有找到有效的URL")
            # 创建空文件并备注时间
            output_path = FILE_PATHS["first_source"]
            bj = datetime.now(timezone.utc) + timedelta(hours=8)
            timestamp = bj.strftime('%Y-%m-%d %H:%M:%S')
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"# 修改时间: {timestamp}\n")
                f.write("# 警告: my_urls.txt 中无有效URL\n")
            return False
        
        logger.info(f"找到第一个远程源: {first_url[:90]}...")
        
        # 创建临时StreamChecker实例来解析这个URL
        checker = StreamChecker()
        
        # 解析这个URL
        parsed_lines = checker.fetch_remote([first_url])
        
        # 保存到111.txt，并添加修改时间备注
        output_path = FILE_PATHS["first_source"]
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        timestamp = bj.strftime('%Y-%m-%d %H:%M:%S')
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# 修改时间: {timestamp}\n")
            if not parsed_lines:
                f.write("# 警告: 解析结果为空\n")
            else:
                for line in parsed_lines:
                    f.write(line + "\n")
        
        logger.info(f"✅ 已将第一个远程源解析内容保存到: {output_path}")
        logger.info(f"   共 {len(parsed_lines)} 条记录")
        return True
        
    except Exception as e:
        logger.error(f"❌ 保存第一个远程源失败: {e}", exc_info=True)
        # 异常时也创建文件并备注时间
        try:
            output_path = FILE_PATHS["first_source"]
            bj = datetime.now(timezone.utc) + timedelta(hours=8)
            timestamp = bj.strftime('%Y-%m-%d %H:%M:%S')
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"# 修改时间: {timestamp}\n")
                f.write(f"# 异常: {str(e)}\n")
        except:
            pass
        return False

# ==============================================
# 主函数（修改执行逻辑）
# ==============================================
def main():
    try:
        logger.info("===== 开始执行Token自动更新 =====")
        token = get_taoiptv_token()
        
        # 无论是否获取到Token，都强制保存第一个远程源到111.txt
        save_first_remote_source()
        
        if token:
            updated = update_my_urls_all(token)
            # 同步到仓库（包括my_urls.txt和111.txt）
            git_commit_push([FILE_PATHS["my_urls"], FILE_PATHS["first_source"]])
        else:
            # 未获取到Token，仅同步111.txt（my_urls.txt可能未变化）
            logger.warning("⚠️ 未获取到Token，仅同步111.txt")
            git_commit_push([FILE_PATHS["first_source"]])
        
        checker = StreamChecker()
        checker.run()
        logger.info("===== 全部流程执行完成 =====")
    except Exception as e:
        logger.error(f"主程序异常: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
