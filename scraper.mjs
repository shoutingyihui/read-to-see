#!/usr/bin/env node
/**
 * 阅见 · 混合爬取引擎
 * ======================
 * 策略: HTTP 请求搜狗微信搜索 → 获取候选文章
 * 然后调用 resolve_and_verify.py 验证真实作者+URL+发布时间
 *
 * 步骤:
 *   1. node scraper.mjs          → 爬取 sogou 搜索，输出 articles_raw.json
 *   2. python3.10 resolve_and_verify.py  → 验证并输出 clean articles.json
 *
 * 也可以一步到位:
 *   node scraper.mjs && python3.10 resolve_and_verify.py
 *
 * 参数:
 *   --days N   只保留最近 N 天
 *   --browser  强制使用 CamoFox 浏览器模式（HTTP 失败时自动降级）
 *   --limit N  只处理前 N 个候选（测试用）
 */

import { readFileSync, writeFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const RAW_PATH = resolve(__dirname, 'articles_raw.json');
const CAMOFOX_URL = 'http://localhost:9377';

// ===== 57 个关注公众号 =====
// 【修改此列表即可调整关注范围】
const DEFAULT_ACCOUNTS = [
  '语言即世界language is world',
  'DeepSeek',
  '贵州大学化学与化工学院',
  '贵州大学美术学院',
  '贵大体育',
  '政治经济学评论',
  '伙伴神在线',
  '贵州大学本科招生办公室',
  '宁都红色马拉松',
  '中国大学生在线',
  '贵大心理',
  '香港中文大学深圳',
  '国家自然科学基金委员会',
  '贵州大学医院',
  '贵州大学',
  '世界哲学杂志',
  '雨梨设计',
  'Planet',
  '星球数据派',
  '贵大青年',
  '光明日报',
  '央视财经',
  '人民日报',
  '中国科学报',
  '群学书院',
  '三近斋杂记',
  '落日间',
  '区域国别智库',
  '半月谈',
  '江西省教育厅',
  '宁都县第二中学',
  '高中学习会',
  '小南新',
  '贵阳交响乐团',
  '黑神话',
  '贵州大学音乐学院',
  'CPEER',
  '宁都共青团',
  '绿色农药全国重点实验室 GPL',
  '宁都县宁师中学',
  '陕西省社会科学院',
  '贵大后勤管理处',
  '超算互联网',
  '人文杂志',
  '数字生命卡兹克',
  '贵州大学出版社',
  '宁都中学',
  '北大哲学人',
  '保研岛',
  '贵大学工',
  '哲学研究',
  '网络MIDI音乐制作资源库',
  '武汉大学',
  '创青春',
  'Guiyangwow',
  '贵大化院学工之家',
  '江西省教育考试院',
];

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36';

// ===== 工具函数 =====
function log(level, msg, data) {
  const ts = new Date().toLocaleTimeString('zh-CN');
  const prefix = { info: '✅', warn: '⚠️', error: '❌', debug: '🔍' }[level] || 'ℹ️';
  console.log(`${prefix} [${ts}] ${msg}`, data ? JSON.stringify(data) : '');
}

function stripHtml(html) {
  return html
    .replace(/<[^>]+>/g, '')
    .replace(/&ldquo;/g, '"').replace(/&rdquo;/g, '"')
    .replace(/&mdash;/g, '—').replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ').replace(/&hellip;/g, '…')
    .replace(/&#\d+;/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// ===== 解析搜狗搜索结果 HTML =====
function parseSogouHtml(html) {
  const articles = [];
  const blocks = html.split(/class="txt-box"/);

  for (let i = 1; i < blocks.length; i++) {
    const block = blocks[i];

    // 标题
    const titleMatch = block.match(/<h3[^>]*>[\s\S]*?<a[^>]*>([\s\S]*?)<\/a>/);
    if (!titleMatch) continue;
    const title = stripHtml(titleMatch[1]);
    if (!title) continue;

    // 来源公众号（sogou 标记，后续会被 resolve_and_verify.py 覆盖）
    const acctMatch = block.match(/class="all-time-y2"[^>]*>([^<]+)</);
    const sogouAuthor = acctMatch ? acctMatch[1].trim() : '';

    // 摘要
    const summaryMatch = block.match(/<p[^>]*>([\s\S]*?)<\/p>/);
    const summary = summaryMatch ? stripHtml(summaryMatch[1]).slice(0, 300) : '';

    articles.push({
      title,
      author: sogouAuthor || '未知',
      summary,
    });
  }

  return articles;
}

// ===== HTTP 模式爬取 =====
async function scrapeViaHttp(account, pages = 2) {
  const allArticles = [];

  for (let page = 1; page <= pages; page++) {
    const url = `https://weixin.sogou.com/weixin?type=2&query=${encodeURIComponent(account)}&ie=utf8&page=${page}`;

    try {
      const res = await fetch(url, {
        headers: {
          'User-Agent': USER_AGENT,
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
          'Referer': 'https://weixin.sogou.com/',
        },
        signal: AbortSignal.timeout(15000),
      });

      if (!res.ok) {
        log('warn', `HTTP ${res.status} for "${account}" page ${page}`);
        break;
      }

      const html = await res.text();

      // 检查是否被反爬
      if (html.includes('用户您好，您的访问过于频繁') || html.includes('antispider')) {
        log('warn', `"${account}" 触发反爬限制`);
        break;
      }

      const articles = parseSogouHtml(html);
      if (articles.length === 0) {
        log('debug', `"${account}" page ${page} 无结果`);
        break;
      }

      allArticles.push(...articles);
      log('info', `"${account}" page ${page}: 获取 ${articles.length} 篇`);

      if (page < pages) await new Promise(r => setTimeout(r, 1500 + Math.random() * 1000));
    } catch (err) {
      log('warn', `HTTP 失败 "${account}" page ${page}: ${err.message}`);
      break;
    }
  }

  return allArticles;
}

// ===== CamoFox 浏览器模式爬取 =====
async function scrapeViaBrowser(account, pages = 2) {
  log('info', `"${account}" 降级到浏览器模式...`);
  const allArticles = [];
  let tabId = null;

  try {
    const tabRes = await fetch(`${CAMOFOX_URL}/tabs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userId: 'wenhui-scraper', url: 'about:blank' }),
    });
    const tab = await tabRes.json();
    tabId = tab.tabId;

    for (let page = 1; page <= pages; page++) {
      const searchUrl = `https://weixin.sogou.com/weixin?type=2&query=${encodeURIComponent(account)}&ie=utf8&page=${page}`;

      await fetch(`${CAMOFOX_URL}/tabs/${tabId}/navigate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userId: 'wenhui-scraper', url: searchUrl }),
      });

      await new Promise(r => setTimeout(r, 3000));

      const htmlRes = await fetch(`${CAMOFOX_URL}/tabs/${tabId}/snapshot?userId=wenhui-scraper&format=html`);
      const htmlData = await htmlRes.json();

      if (htmlData.html) {
        const articles = parseSogouHtml(htmlData.html);
        allArticles.push(...articles);
        log('info', `"${account}" browser page ${page}: 获取 ${articles.length} 篇`);
      }

      if (page < pages) await new Promise(r => setTimeout(r, 2000));
    }
  } catch (err) {
    log('error', `浏览器模式失败 "${account}": ${err.message}`);
  } finally {
    if (tabId) {
      await fetch(`${CAMOFOX_URL}/tabs/${tabId}?userId=wenhui-scraper`, { method: 'DELETE' }).catch(() => {});
    }
  }

  return allArticles;
}

// ===== 混合策略爬取 =====
async function scrapeAccount(account, { forceBrowser = false, pages = 2 } = {}) {
  if (forceBrowser) return scrapeViaBrowser(account, pages);

  const httpResult = await scrapeViaHttp(account, pages);
  if (httpResult.length > 0) return httpResult;

  log('info', `"${account}" HTTP 无结果，降级到浏览器...`);
  return scrapeViaBrowser(account, pages);
}

// ===== 主流程 =====
async function main() {
  const args = process.argv.slice(2);
  const forceBrowser = args.includes('--browser');
  const daysIndex = args.indexOf('--days');
  const keepDays = daysIndex >= 0 ? parseInt(args[daysIndex + 1]) || 7 : null;
  const limitIndex = args.indexOf('--limit');
  const limit = limitIndex >= 0 ? parseInt(args[limitIndex + 1]) || null : null;
  const accounts = args.filter(a => !a.startsWith('--') && a !== String(keepDays) && a !== String(limit));

  const targetAccounts = accounts.length > 0 ? accounts : DEFAULT_ACCOUNTS;

  log('info', '阅见爬取引擎启动', {
    mode: forceBrowser ? 'browser' : 'hybrid',
    accounts: targetAccounts.length,
  });

  const allArticles = [];

  for (const account of targetAccounts) {
    const articles = await scrapeAccount(account, { forceBrowser });
    allArticles.push(...articles);
    log('info', `"${account}" 共 ${articles.length} 篇候选`);
    await new Promise(r => setTimeout(r, 1500 + Math.random() * 1000));
  }

  // 去重（按标题去重）
  const seen = new Set();
  const unique = [];
  for (const a of allArticles) {
    // 全文模糊去重：取标题前 20 字作为 key
    const key = a.title.slice(0, 20);
    if (!seen.has(key)) {
      seen.add(key);
      unique.push(a);
    }
  }

  log('info', `爬取完成: ${allArticles.length} 原始 → ${unique.length} 去重后`);

  // 限制候选数量
  let final = unique;
  if (limit) final = final.slice(0, limit);

  // 保存原始候选数据（后续由 resolve_and_verify.py 处理）
  writeFileSync(RAW_PATH, JSON.stringify(final, null, 2), 'utf-8');
  log('info', `候选数据已保存: ${RAW_PATH} (${final.length} 篇)`);

  // 启动验证流水线
  log('info', '启动 resolve_and_verify.py...');
  const { execSync } = await import('child_process');
  try {
    const pyArgs = ['python3.10', resolve(__dirname, 'resolve_and_verify.py')];
    if (limit) pyArgs.push('--limit', String(limit));
    // 如果需要保留最近 N 天，传给 Python 脚本（略）
    execSync(pyArgs.join(' '), { cwd: __dirname, stdio: 'inherit', timeout: 600000 });
  } catch (e) {
    log('warn', '验证阶段部分失败', { error: e.message });
  }
}

main().catch(err => {
  log('error', `爬取失败: ${err.message}`);
  process.exit(1);
});
