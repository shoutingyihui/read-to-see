#!/usr/bin/env python3
import json
from datetime import datetime

data = json.load(open('/home/shouting/wenhui-reader/articles.json'))
content = open('/home/shouting/wenhui-reader/articles.json').read()
print(f'文件大小: {len(content)} 字节')
print(f'文章总数: {len(data)}')

# Count articles by date
today = datetime.now().strftime('%Y-%m-%d')
count_today = sum(1 for d in data if d.get('pubDate','').startswith(today))
print(f'今日日期: {today}')
print(f'今日新增文章: {count_today}')

# Show first 3 articles
print('最新文章预览:')
for a in data[:5]:
    title = a.get('title', '?')[:60]
    pub = a.get('pubDate', '?')[:10]
    print(f'  [{pub}] {title}')
