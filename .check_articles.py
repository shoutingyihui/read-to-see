#!/usr/bin/env python3
import json, os

path = '/home/shouting/wenhui-reader/articles.json'
backup_path = '/home/shouting/wenhui-reader/articles.json.daily_backup'

# Check main
if not os.path.exists(path):
    print("ERROR: articles.json does not exist!")
    exit(1)

size = os.path.getsize(path)
with open(path) as f:
    data = json.load(f)

if isinstance(data, list):
    count = len(data)
elif isinstance(data, dict):
    count = len(data.get('articles', []))
else:
    count = 0

print(f'articles.json: {size} bytes, {count} articles')

# Count unique authors
authors = set()
for item in data if isinstance(data, list) else (data.get('articles', []) if isinstance(data, dict) else []):
    if isinstance(item, dict) and 'author' in item:
        authors.add(item['author'])
print(f'Unique authors: {len(authors)}')

# Check backup
backup_size = os.path.getsize(backup_path)
print(f'Backup: {backup_size} bytes')
print(f'Delta: {size - backup_size} bytes (+{(size - backup_size) / backup_size * 100:.1f}%)')
