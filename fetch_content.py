#!/usr/bin/env python3
"""
阅见 · 全文内容提取器
======================
从 WeChat 文章 URL 提取正文纯文本，追加到 articles.json。

用法:
  python3 fetch_content.py              # 处理所有缺失 content 的文章
  python3 fetch_content.py --limit 5    # 只处理前 5 篇
  python3 fetch_content.py --resume     # 跳过已有 content 的

依赖: Python 标准库 (urllib + re)
"""

import json, os, re, sys, time, urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_PATH = os.path.join(SCRIPT_DIR, 'articles.json')

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/136.0.0.0 Safari/537.36')

# HTTP 请求超时（秒）
REQUEST_TIMEOUT = 30
# 请求间隔（秒），避免被微信限制
REQUEST_INTERVAL = 1.5


def log(msg, emoji='📖'):
    ts = time.strftime('%H:%M:%S')
    print(f'{emoji} [{ts}] {msg}')


def is_clean_url(url):
    """判断是否为可直接访问的 mp.weixin.qq.com/s/xxx 链接"""
    if not url:
        return False
    # 必须包含 mp.weixin.qq.com
    if 'mp.weixin.qq.com' not in url:
        return False
    # 不能是搜狗跳转链接
    if 'src=11' in url or 'timestamp=' in url:
        return False
    # 不能是小程序链接
    if '/miniprogram/' in url:
        return False
    return True


# ── WeChat 内容噪音过滤规则 ──
NOISE_PATTERNS = [
    # 微信 UI 元素
    r'向上滑动查看更多',
    r'预览时标签不可点',
    r'素材来源官方媒体/网络新闻',
    r'阅读全文',
    r'点击上方[^。]*关注',
    r'长按[^。]*关注',
    r'喜欢此内容的人还喜欢',
    r'分享[^。]*收藏[^。]*赞[^。]*在看',
    r'人[^。]*赞过[^。]*读过',
    r'写下你的留言',
    r'精选留言',
    r'作者已设置[^。]*关注',
    r'关注公众号[^。]*收看',
    # 特殊符号分割线
    r'----- END -----',
    r'[-—]{5,}',
    r'[▍|｜]\s*$',
    r'^\s*[▪▫•·]\s*',
]


def html_to_text(html):
    """
    将 WeChat 文章 HTML 正文转换为干净的纯文本。
    保留段落结构，过滤噪音。
    """
    text = html

    # 1. 移除 <script> 和 <style> 块
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)

    # 2. 将块级标签替换为段落分隔符（保留段落结构）
    # <p> → 双换行
    text = re.sub(r'</p>', '\n\n', text, flags=re.DOTALL)
    # <br> → 单换行
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.DOTALL)
    # </div> → 双换行
    text = re.sub(r'</div>', '\n\n', text, flags=re.DOTALL)
    # </section> → 双换行
    text = re.sub(r'</section>', '\n\n', text, flags=re.DOTALL)
    # </h[1-6]> → 双换行
    text = re.sub(r'</h[1-6]>', '\n\n', text, flags=re.DOTALL)
    # </li> → 换行
    text = re.sub(r'</li>', '\n', text, flags=re.DOTALL)
    # </blockquote> → 双换行
    text = re.sub(r'</blockquote>', '\n\n', text, flags=re.DOTALL)

    # 3. 移除所有剩余 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)

    # 4. 解码 HTML 实体
    text = text.replace('&nbsp;', ' ')
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)

    # 5. 规整空白：多换行 → 双换行，行首尾空格清除
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    # 6. 噪音过滤：逐行过滤并去掉过长的连续噪音
    clean_lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        # 跳过空行（保留段落间距）
        if not stripped:
            clean_lines.append('')
            continue
        # 检查噪音模式
        is_noise = False
        for pattern in NOISE_PATTERNS:
            if re.search(pattern, stripped):
                is_noise = True
                break
        if not is_noise:
            clean_lines.append(stripped)

    # 去除末尾多余空行
    while clean_lines and clean_lines[-1] == '':
        clean_lines.pop()

    return '\n\n'.join(
        p.strip() for p in '\n'.join(clean_lines).split('\n\n') if p.strip()
    )


def extract_js_content(html):
    """从 WeChat 页面 HTML 中提取 #js_content 的原始 HTML"""
    # 模式 1: 标准 <div id="js_content"...>...</div></div>
    m = re.search(
        r'<div[^>]*id="js_content"[^>]*>(.*?)</div>\s*</div>',
        html, re.DOTALL
    )
    if m:
        return m.group(1)

    # 模式 2: id="js_content" 不带 div
    m = re.search(
        r'id="js_content"[^>]*>(.*?)</div>',
        html, re.DOTALL
    )
    if m:
        return m.group(1)

    return None


def fetch_article_text(url):
    """
    从 WeChat 文章 URL 提取正文纯文本。
    返回 (text, error_msg)
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml',
            }
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            html = resp.read().decode('utf-8', errors='replace')

        content_html = extract_js_content(html)

        if not content_html:
            return None, '未找到 #js_content'

        text = html_to_text(content_html)

        if not text:
            return None, '正文为空'

        return text, None

    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code} {e.reason}'
    except urllib.error.URLError as e:
        return None, f'网络错误: {e.reason}'
    except Exception as e:
        return None, f'异常: {e}'


def main():
    args = sys.argv[1:]
    limit = None
    resume = False

    for i, arg in enumerate(args):
        if arg == '--limit' and i + 1 < len(args):
            limit = int(args[i + 1])
        elif arg == '--resume':
            resume = True

    # 加载文章
    if not os.path.exists(ARTICLES_PATH):
        log(f'文件不存在: {ARTICLES_PATH}', '❌')
        sys.exit(1)

    with open(ARTICLES_PATH, 'r', encoding='utf-8') as f:
        articles = json.load(f)

    log(f'总文章数: {len(articles)}')

    # 筛选：已有 content 的跳过
    candidates = []
    for a in articles:
        if a.get('content'):
            continue
        if not is_clean_url(a.get('url', '')):
            continue
        candidates.append(a)

    if not candidates:
        log('所有可处理的文章已有 content，无需提取', '✅')
        return

    if limit:
        candidates = candidates[:limit]

    log(f'待提取: {len(candidates)} 篇（全部 clean URL）')

    success = 0
    failed = 0
    skipped = 0
    updated_count = 0

    for idx, article in enumerate(candidates):
        url = article['url']
        title = article['title'][:50]
        author = article['author']

        print(f'\n[{idx+1}/{len(candidates)}] {title} ({author})')

        text, error = fetch_article_text(url)
        if error:
            print(f'  ❌ {error}')
            failed += 1
            continue

        if not text or len(text) < 50:
            print(f'  ⚠️ 正文过短 ({len(text)} 字)，跳过')
            skipped += 1
            continue

        # 更新 content
        article['content'] = text
        success += 1
        print(f'  ✅ {len(text)} 字')

        time.sleep(REQUEST_INTERVAL)

    # 保存
    if success > 0:
        with open(ARTICLES_PATH, 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        log(f'已保存至 {ARTICLES_PATH}')

    print(f'\n{"="*50}')
    print(f'  成功: {success}')
    print(f'  失败: {failed}')
    print(f'  跳过: {skipped}')
    print(f'{"="*50}')


if __name__ == '__main__':
    main()
