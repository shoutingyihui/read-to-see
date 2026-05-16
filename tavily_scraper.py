#!/usr/bin/env python3
"""
阅见 · 混合文章爬取引擎 (Tavily 为主)
====================================
策略：
  1. 优先使用 Tavily Search API（每天 ≤20 次调用）
  2. 对搜不到微信文章的账号，降级到 CamoFox 浏览器 + 搜狗微信搜索

用法：
  python3 tavily_scraper.py              # 正常运行
  python3 tavily_scraper.py --dry-run     # 仅预览
  python3 tavily_scraper.py --force-all   # 忽略限额，更新所有
"""
import json, os, sys, time, re
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_PATH = os.path.join(SCRIPT_DIR, 'articles.json')
STATE_PATH = os.path.join(SCRIPT_DIR, '.tavily_state.json')
RAW_PATH = os.path.join(SCRIPT_DIR, 'articles_raw.json')
MAX_TAVILY_CALLS = 20
RESULTS_PER_CALL = 5
CAMOFOX_URL = 'http://localhost:9377'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

ALL_ACCOUNTS = [
    '语言即世界language is world', 'DeepSeek',
    '贵州大学化学与化工学院', '贵州大学美术学院', '贵大体育',
    '政治经济学评论', '伙伴神在线', '贵州大学本科招生办公室',
    '宁都红色马拉松', '中国大学生在线', '贵大心理',
    '香港中文大学深圳', '国家自然科学基金委员会', '贵州大学医院',
    '贵州大学', '世界哲学杂志', '雨梨设计', 'Planet', '星球数据派',
    '贵大青年', '光明日报', '央视财经', '人民日报', '中国科学报',
    '群学书院', '三近斋杂记', '落日间', '区域国别智库', '半月谈',
    '江西省教育厅', '宁都县第二中学', '高中学习会', '小南新',
    '贵阳交响乐团', '黑神话', '贵州大学音乐学院', 'CPEER',
    '宁都共青团', '绿色农药全国重点实验室 GPL', '宁都县宁师中学',
    '陕西省社会科学院', '贵大后勤管理处', '超算互联网', '人文杂志',
    '数字生命卡兹克', '贵州大学出版社', '宁都中学', '北大哲学人',
    '保研岛', '贵大学工', '哲学研究', '网络MIDI音乐制作资源库',
    '武汉大学', '创青春', 'Guiyangwow', '贵大化院学工之家',
    '江西省教育考试院',
]


def log(msg, emoji='📡'):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'{emoji} [{ts}] {msg}')


def get_api_key():
    key = os.environ.get('TAVILY_API_KEY')
    if key:
        return key
    for p in [
        os.path.join(SCRIPT_DIR, '.env'),
        os.path.expanduser('~/.hermes/.env'),
    ]:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('TAVILY_API_KEY='):
                        return line.split('=', 1)[1].strip().strip("'\"")
    return None


# ── Tavily Search ──

def tavily_search(api_key, query, max_results=5):
    url = 'https://api.tavily.com/search'
    payload = json.dumps({
        'api_key': api_key,
        'query': query,
        'search_depth': 'basic',
        'max_results': max_results,
        'include_answer': False,
    }).encode('utf-8')
    req = Request(url, data=payload, headers={
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    })
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return data.get('results', [])
    except Exception as e:
        log(f'Tavily API 请求失败: {e}', '❌')
        return None


def parse_tavily_results(results, account_name):
    """从 Tavily 结果中提取文章（优先微信链接）"""
    articles = []
    seen_urls = set()
    for r in results:
        url = r.get('url', '').strip()
        title = (r.get('title', '') or '').strip()
        if not title or not url:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        content = r.get('content', '') or ''
        summary = content[:200].strip()
        pub_date = r.get('published_date', '') or datetime.now(
            timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M:%S+08:00')
        articles.append({
            'title': title,
            'author': account_name,
            'pubDate': pub_date,
            'summary': summary,
            'url': url,
            'category': '',
        })
    return articles


# ── CamoFox 降级爬取（搜狗微信搜索） ──

def strip_html(html):
    return re.sub(r'<[^>]+>', '', html).replace('&nbsp;', ' ').strip()


def parse_sogou_html(html):
    articles = []
    blocks = html.split('class="txt-box"')
    for block in blocks[1:]:
        title_match = re.search(r'<h3[^>]*>[\s\S]*?<a[^>]*>([\s\S]*?)</a>', block)
        if not title_match:
            continue
        title = strip_html(title_match.group(1))
        if not title:
            continue
        acct_match = re.search(r'class="all-time-y2"[^>]*>([^<]+)<', block)
        sogou_author = acct_match.group(1).strip() if acct_match else ''
        summary_match = re.search(r'<p[^>]*>([\s\S]*?)</p>', block)
        summary = strip_html(summary_match.group(1))[:300] if summary_match else ''
        articles.append({'title': title, 'author': sogou_author or '未知', 'summary': summary})
    return articles


async def scrape_via_sogou_browser(account, pages=1):
    """使用 CamoFox 浏览器爬取搜狗微信"""
    import asyncio
    articles = []
    tab_id = None
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # 创建标签页（CamoFox API: /tabs/open 不需要 sessionKey）
            async with session.post(f'{CAMOFOX_URL}/tabs/open', json={
                'userId': 'wenhui-scraper', 'url': 'about:blank'
            }) as resp:
                tab = await resp.json()
                tab_id = tab.get('tabId')
            if not tab_id:
                return articles
            for page_num in range(1, pages + 1):
                search_url = f'https://weixin.sogou.com/weixin?type=2&query={account}&ie=utf8&page={page_num}'
                async with session.post(f'{CAMOFOX_URL}/tabs/{tab_id}/navigate', json={
                    'userId': 'wenhui-scraper', 'url': search_url
                }) as _:
                    pass
                await asyncio.sleep(3)
                async with session.get(
                    f'{CAMOFOX_URL}/tabs/{tab_id}/snapshot?userId=wenhui-scraper&format=html'
                ) as resp:
                    html_data = await resp.json()
                if html_data.get('html'):
                    page_articles = parse_sogou_html(html_data['html'])
                    articles.extend(page_articles)
                    log(f'  CamoFox("{account}" p{page_num}): {len(page_articles)} 篇', '🌐')
                if page_num < pages:
                    await asyncio.sleep(2)
    except Exception as e:
        log(f'CamoFox 失败 "{account}": {e}', '⚠️')
    finally:
        if tab_id:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    await session.delete(f'{CAMOFOX_URL}/tabs/{tab_id}?userId=wenhui-scraper')
            except:
                pass
    return articles


def deduplicate(new_articles, existing_articles):
    existing_keys = {a['title'][:20] for a in existing_articles}
    result = []
    for a in new_articles:
        key = a['title'][:20]
        if key not in existing_keys:
            existing_keys.add(key)
            result.append(a)
    return result


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_articles():
    if os.path.exists(ARTICLES_PATH):
        with open(ARTICLES_PATH) as f:
            return json.load(f)
    return []


def save_articles(articles):
    with open(ARTICLES_PATH, 'w', encoding='utf-8') as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)


def get_priority_accounts(existing, state, max_count=20):
    """按优先级排序：无数据的账号优先，其次按「上次更新时间」升序"""
    now = datetime.now().isoformat()
    last_article_date = {}
    for a in existing:
        author = a['author']
        try:
            d = a['pubDate']
            if author not in last_article_date or d > last_article_date[author]:
                last_article_date[author] = d
        except:
            pass
    with_data = [a for a in ALL_ACCOUNTS if a in last_article_date]
    without_data = [a for a in ALL_ACCOUNTS if a not in last_article_date]

    with_data.sort(key=lambda a: (state.get(a, '2000-01-01'), last_article_date.get(a, '2000-01-01')))
    return (without_data + with_data)[:max_count]


def main():
    dry_run = '--dry-run' in sys.argv
    force_all = '--force-all' in sys.argv

    api_key = get_api_key()
    if not api_key:
        log('未找到 TAVILY_API_KEY', '❌')
        sys.exit(1)

    existing = load_articles()
    state = load_state()
    log(f'已有 {len(existing)} 篇文章 / {len(state)} 个账号有状态记录')

    # ═══ 阶段 1：确定目标账号 ═══
    to_update = ALL_ACCOUNTS if force_all else get_priority_accounts(existing, state, MAX_TAVILY_CALLS)
    log(f'本次计划更新 {len(to_update)} 个公众号')

    if dry_run:
        for acc in to_update:
            last = state.get(acc, '从未更新')
            n = len([a for a in existing if a['author'] == acc])
            log(f'  {acc} (上次: {last[:10]}, 已有 {n} 篇)', '🔍')
        log(f'将消耗 {len(to_update)} 次 Tavily API 调用')
        return

    # ═══ 阶段 2：Tavily 搜索 ═══
    total_new = 0
    calls_made = 0
    sogou_fallback_accounts = []

    for account in to_update:
        query = f'{account} 微信文章 site:mp.weixin.qq.com'
        log(f'🔍 Tavily 搜索 "{account}"...')

        results = tavily_search(api_key, query, RESULTS_PER_CALL)
        calls_made += 1

        if results is None:
            log(f'  ❌ "{account}" 搜索失败', '⚠️')
            sogou_fallback_accounts.append(account)
            state[account] = datetime.now().isoformat()
            continue

        # 过滤出微信文章
        wx_articles = parse_tavily_results(results, account)
        wx_only = [a for a in wx_articles if 'mp.weixin.qq.com' in a['url']]

        if not wx_only:
            log(f'  📭 "{account}" Tavily 未找到微信文章', '📭')
            sogou_fallback_accounts.append(account)
        else:
            new_unique = deduplicate(wx_only, existing)
            if new_unique:
                existing.extend(new_unique)
                total_new += len(new_unique)
                log(f'  ✓ 新增 {len(new_unique)} 篇', '📖')
            else:
                log(f'  ✓ 无新文章', '📖')

        state[account] = datetime.now().isoformat()
        if calls_made < len(to_update):
            time.sleep(0.5)

    # ═══ 阶段 3：CamoFox 降级（搜狗） ═══
    if sogou_fallback_accounts:
        log(f'\n🌙 降级到 CamoFox 浏览器模式，处理 {len(sogou_fallback_accounts)} 个搜不到的账号...', '🔮')
        import asyncio
        for account in sogou_fallback_accounts:
            try:
                sogou_articles = asyncio.run(scrape_via_sogou_browser(account, pages=1))
                if sogou_articles:
                    for a in sogou_articles:
                        a['author'] = account  # 用已知的账号名覆盖
                    new_unique = deduplicate(sogou_articles, existing)
                    if new_unique:
                        # 保存原始数据给 resolve_and_verify 处理 URL
                        raw_path = os.path.join(SCRIPT_DIR, f'articles_raw.json')
                        # 合并到原始候选数据
                        try:
                            with open(raw_path) as f:
                                raw_data = json.load(f)
                        except:
                            raw_data = []
                        raw_data.extend(new_unique)
                        with open(raw_path, 'w') as f:
                            json.dump(raw_data, f, indent=2, ensure_ascii=False)
                        log(f'  CamoFox ✓ "{account}": {len(new_unique)} 篇候选 → articles_raw.json', '🌐')
                        total_new += len(new_unique)
                    else:
                        log(f'  CamoFox "{account}": 无新文章', '📭')
                else:
                    log(f'  CamoFox "{account}": 无结果', '📭')
            except Exception as e:
                log(f'  CamoFox "{account}" 异常: {e}', '❌')
            time.sleep(1)

        # 如果有原始候选数据，尝试用 resolve_and_verify.py 验证
        raw_path = os.path.join(SCRIPT_DIR, 'articles_raw.json')
        resolve_path = os.path.join(SCRIPT_DIR, 'resolve_and_verify.py')
        if os.path.exists(raw_path) and os.path.exists(resolve_path):
            log('调用 resolve_and_verify.py 验证候选文章...', '🔮')
            import subprocess
            try:
                subprocess.run(
                    ['python3.10', resolve_path, '--resume'],
                    cwd=SCRIPT_DIR,
                    timeout=600,
                    capture_output=True,
                    text=True,
                )
                # 重新加载合并后的数据
                reloaded = load_articles()
                if len(reloaded) > len(existing):
                    log(f'resolve_and_verify 验证完成，共 {len(reloaded)} 篇', '✅')
                    existing = reloaded
            except Exception as e:
                log(f'resolve_and_verify 执行失败: {e}', '⚠️')

    # ═══ 阶段 4：保存 ═══
    if total_new > 0:
        existing.sort(key=lambda a: a.get('pubDate', ''), reverse=True)
        save_articles(existing)
        log(f'\n🎉 保存完成！共 {len(existing)} 篇（本次新增 {total_new} 篇）')
    else:
        log(f'\n📭 无新增文章，现有 {len(existing)} 篇')

    save_state(state)
    log(f'📊 本次 Tavily API 调用: {calls_made} 次')


if __name__ == '__main__':
    main()
