# ==============================================
# 主函数（优化：强制执行解析与文件保存逻辑）
# ==============================================
def main():
    try:
        logger.info("===== 开始执行Token自动更新 =====")
        
        # 1. 尝试获取并更新Token（无论成败，都将记录）
        token = get_taoiptv_token()
        token_updated = False
        if token:
            token_updated = update_my_urls_all(token)
            if token_updated:
                logger.info("✅ Token已成功更新并应用到my_urls.txt")
            else:
                logger.warning("⚠️ 获取到Token但更新文件失败，将使用旧Token继续")
        else:
            logger.warning("⚠️ 无法获取新Token，将使用my_urls.txt中的现有地址继续")
        
        # 2. 解析第一个远程源并保存到111.txt（无论Token是否成功）
        logger.info("开始处理第一个远程源...")
        first_url = get_first_my_url()
        out_path = FILE_PATHS["first_parse"]
        bj = datetime.now(timezone.utc) + timedelta(hours=8)
        timestamp = bj.strftime('%Y-%m-%d %H:%M:%S')
        
        if first_url:
            logger.info(f"找到第一个远程源: {first_url[:90]}...")
            # 获取远程内容
            content = fetch_url_content(first_url)
            
            # 构建文件内容（无论是否获取成功都写入）
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(f"# 解析时间: {timestamp}\n")
                f.write(f"# 来源地址: {first_url}\n")
                if token_updated:
                    f.write(f"# Token状态: 已更新 (新Token: {token})\n")
                else:
                    f.write(f"# Token状态: 未更新 (使用文件中的现有Token)\n")
                f.write("\n")
                if content:
                    f.write(content)
                    logger.info(f"✅ 已将第一个远程源解析内容保存到: {out_path}")
                    # 统计内容行数
                    line_count = len([l for l in content.splitlines() if l.strip()])
                    logger.info(f"共 {line_count} 条记录")
                else:
                    f.write(f"# 错误: 无法获取远程源内容 (尝试时间: {timestamp})\n")
                    logger.warning(f"⚠️ 未能获取远程源内容，已将错误信息写入 {out_path}")
        else:
            logger.warning("⚠️ my_urls.txt 中未找到有效URL")
            # 即使没有有效URL，也创建带时间戳的提示文件
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(f"# 解析时间: {timestamp}\n")
                f.write(f"# 错误: my_urls.txt 中未找到有效的远程源URL\n")
                if token_updated:
                    f.write(f"# Token状态: 已更新\n")
                else:
                    f.write(f"# Token状态: 未更新\n")
            logger.warning(f"已在 {out_path} 中写入错误信息")

        # 3. 仅当Token成功更新时才执行Git提交推送
        if token_updated:
            git_commit_push()
        else:
            logger.info("Token未更新，跳过Git推送（避免无效提交）")
        
        # 4. 继续执行后续流媒体检测
        checker = StreamChecker()
        checker.run()
        logger.info("===== 全部流程执行完成 =====")
        
    except Exception as e:
        logger.error(f"主程序异常: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
