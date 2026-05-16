#!/bin/bash
# 阅见 · 一键爬取 + 验证
# 用法:
#   ./crawl.sh                     # 全量爬取 + 验证
#   ./crawl.sh --days 2            # 保留最近2天
#   ./crawl.sh --browser           # 强制浏览器模式
#   ./crawl.sh --limit 5           # 只处理前5篇（测试用）

cd "$(dirname "$0")"

echo "==================================="
echo " 阅见 · 文章爬取 + 验证流水线"
echo "==================================="

# 步骤 1: 清理旧数据（可选）
if [ "$1" == "--clean" ]; then
    echo "🧹 清理旧数据..."
    rm -f articles_raw.json articles.json
    shift
fi

# 步骤 2: 爬取搜狗候选
echo ""
echo "📡 [1/2] 爬取搜狗微信搜索..."
node scraper.mjs "$@"

# 步骤 3: 如果候选人存在且 > 0，运行验证
if [ -f articles_raw.json ]; then
    COUNT=$(python3.10 -c "import json; print(len(json.load(open('articles_raw.json'))))" 2>/dev/null || echo "0")
    if [ "$COUNT" -gt 0 ]; then
        echo ""
        echo "🔍 [2/2] 验证真实作者 + 提取发布时间..."
        python3.10 resolve_and_verify.py "$@"
    fi
fi

echo ""
echo "==================================="
echo " ✅ 完成"
echo " 启动: python3.10 -m http.server 3000"
echo "==================================="
