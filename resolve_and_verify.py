#!/usr/bin/env python3.10
"""
阅见 · URL 解析 + 作者验证 + 时间提取
=========================================
核心逻辑：
  1. 读取 sogou 爬取的候选文章（含 sogou 搜索链接）
  2. 对每篇候选，用 CamoFox 浏览器导航到 sogou 搜索 → 获取真实 link?url= → 导航到 mp.weixin.qq.com
  3. 从真实页面提取 #js_name（公众号名称）和 #publish_time（发布时间）
  4. 仅保留 #js_name 在关注列表中的文章
  5. 输出干净的 articles.json

用法:
  python3.10 resolve_and_verify.py [--input articles_raw.json] [--output articles.json] [--limit N] [--resume]

依赖: CamoFox 浏览器须在 localhost:9377 运行
"""

import json, time, urllib.request, os, sys, urllib.parse, re, difflib

BASE = "http://localhost:9377"
H = {"Content-Type": "application/json"}

# ===== 57 个关注公众号（来源：用户提供的完整列表）=====
ACCOUNTS = [
    "语言即世界language is world",
    "DeepSeek",
    "贵州大学化学与化工学院",
    "贵州大学美术学院",
    "贵大体育",
    "政治经济学评论",
    "伙伴神在线",
    "贵州大学本科招生办公室",
    "宁都红色马拉松",
    "中国大学生在线",
    "贵大心理",
    "香港中文大学深圳",
    "国家自然科学基金委员会",
    "贵州大学医院",
    "贵州大学",
    "世界哲学杂志",
    "雨梨设计",
    "Planet",
    "星球数据派",
    "贵大青年",
    "光明日报",
    "央视财经",
    "人民日报",
    "中国科学报",
    "群学书院",
    "三近斋杂记",
    "落日间",
    "区域国别智库",
    "半月谈",
    "江西省教育厅",
    "宁都县第二中学",
    "高中学习会",
    "小南新",
    "贵阳交响乐团",
    "黑神话",
    "贵州大学音乐学院",
    "CPEER",
    "宁都共青团",
    "绿色农药全国重点实验室 GPL",
    "宁都县宁师中学",
    "陕西省社会科学院",
    "贵大后勤管理处",
    "超算互联网",
    "人文杂志",
    "数字生命卡兹克",
    "贵州大学出版社",
    "宁都中学",
    "北大哲学人",
    "保研岛",
    "贵大学工",
    "哲学研究",
    "网络MIDI音乐制作资源库",
    "武汉大学",
    "创青春",
    "Guiyangwow",
    "贵大化院学工之家",
    "江西省教育考试院",
]

ACCOUNT_SET = set(ACCOUNTS)


def api(method, path, data=None, timeout=45):
    """调用 CamoFox HTTP API"""
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(BASE + path, data=body, headers=H, method=method)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def evaluate_js(tab_id, expression, timeout=15):
    """在浏览器标签页中执行 JS 并返回结果"""
    try:
        r = api("POST", f"/tabs/{tab_id}/evaluate", {
            "userId": "resolver", "sessionKey": "batch",
            "expression": expression
        }, timeout=timeout)
        return r.get("result", "")
    except Exception as e:
        print(f"  ⚠️ JS eval error: {e}")
        return ""


def resolve_and_verify(sogou_title, max_attempts=2):
    """
    完整流程：
    1. 导航到 sogou 搜索该文章
    2. 从搜索结果中找到 link?url= 链接（优先选标题最匹配的）
    3. 导航到该链接 → 跟随重定向到 mp.weixin.qq.com
    4. 提取 #js_name（真实作者）、#activity-name（真实标题）和 #publish_time（真实发布时间）
    5. 对比真实标题与搜索标题，确认是同篇文章
    
    返回: {ok, real_author, real_title, real_time, real_url} 或 {ok: False}
    """
    tab_id = None
    
    for attempt in range(max_attempts):
        try:
            # 步骤 1: 搜索标题的前 50 个字
            query = sogou_title[:50]
            search_url = f"https://weixin.sogou.com/weixin?type=2&query={urllib.parse.quote(query)}&ie=utf8"
            
            tab = api("POST", "/tabs", {
                "userId": "resolver", "sessionKey": "batch",
                "url": search_url
            })
            tab_id = tab["tabId"]
            time.sleep(3)
            
            # 步骤 2: 在搜索结果中找到 link?url=，优先选标题最匹配的
            links_result = evaluate_js(tab_id, """
                (function(){
                    var links = document.querySelectorAll('h3 a');
                    var results = [];
                    for (var i = 0; i < links.length; i++) {
                        var href = links[i].href;
                        var text = links[i].textContent.trim();
                        if (href && href.indexOf('/link?url=') !== -1) {
                            results.push({href: href, text: text});
                        }
                    }
                    // 如果没有通过 h3 a 找到，尝试 .txt-box a
                    if (results.length === 0) {
                        var links2 = document.querySelectorAll('.txt-box a');
                        for (var i = 0; i < links2.length; i++) {
                            var href = links2[i].href;
                            var text = links2[i].textContent.trim();
                            if (href && href.indexOf('/link?url=') !== -1) {
                                results.push({href: href, text: text});
                            }
                        }
                    }
                    return JSON.stringify(results);
                })();
            """)
            
            if not links_result or links_result == "[]" or links_result == "no_link":
                print(f"  ⚠️ 未找到 link (attempt {attempt+1})")
                try:
                    api("DELETE", f"/tabs/{tab_id}?userId=resolver&sessionKey=batch")
                except:
                    pass
                tab_id = None
                time.sleep(2)
                continue
            
            # 解析搜索结果，按标题相似度排序
            import json as _json
            try:
                links = _json.loads(links_result)
            except:
                links = []
            
            if not links:
                print(f"  ⚠️ 搜索结果解析失败 (attempt {attempt+1})")
                continue
            
            # 按标题相似度排序：最匹配的排最前
            def link_score(l):
                return title_similarity(sogou_title, l.get("text", ""))
            
            links.sort(key=link_score, reverse=True)
            best_link = links[0]["href"]
            best_title = links[0].get("text", "")
            best_score = link_score(links[0])
            
            if best_score < 0.3:
                print(f"  ⚠️ 所有搜索结果标题相似度均低 (最高 {best_score:.2f}: '{best_title[:30]}')")
            
            # 步骤 3: 导航到最佳匹配的 link?url= → 跟随重定向
            evaluate_js(tab_id, f"location.href = '{best_link}';")
            time.sleep(5)
            
            # 等待重定向完成（最多 20 秒）
            real_url = ""
            for _ in range(10):
                snap = api("GET", f"/tabs/{tab_id}/snapshot?userId=resolver&sessionKey=try", timeout=10)
                real_url = snap.get("url", "")
                if "mp.weixin.qq.com" in real_url:
                    break
                if "antispider" in real_url:
                    print(f"  ❌ 触发 antispider")
                    return {"ok": False}
                time.sleep(2)
            
            if "mp.weixin.qq.com" not in real_url:
                print(f"  ⚠️ 未到达 mp.weixin.qq.com: {real_url[:80]}")
                return {"ok": False}
            
            # 额外等待页面渲染
            time.sleep(2)
            
            # 步骤 4: 提取真实作者
            real_author = evaluate_js(tab_id,
                'document.querySelector("#js_name")?.textContent?.trim() || "unknown"')
            
            # 步骤 4a: 提取真实文章标题（用于后续对比验证）
            real_title = evaluate_js(tab_id,
                'document.querySelector("#activity-name")?.textContent?.trim() || ""')
            
            # 步骤 4b: 提取真实发布时间
            real_time_raw = evaluate_js(tab_id,
                'document.querySelector("#publish_time")?.textContent?.trim() || "unknown"')
            
            # 解析中文时间 "2026年4月26日 21:09" → ISO
            real_time_iso = parse_chinese_time(real_time_raw)
            
            # 清理 URL（去掉 http:// 前缀等）
            clean_url = real_url
            if clean_url.startswith("http://"):
                clean_url = "https://" + clean_url[7:]
            
            return {
                "ok": True,
                "real_author": real_author,
                "real_title": real_title,
                "real_time_raw": real_time_raw,
                "real_time_iso": real_time_iso,
                "real_url": clean_url
            }
            
        except Exception as e:
            print(f"  ⚠️ 尝试 {attempt+1} 失败: {e}")
            if tab_id:
                try: api("DELETE", f"/tabs/{tab_id}?userId=resolver&sessionKey=batch")
                except: pass
                tab_id = None
            time.sleep(3)
    
    return {"ok": False}


def parse_chinese_time(time_str):
    """解析'2026年4月26日 21:09' → ISO 格式"""
    if not time_str or time_str == "unknown":
        return ""
    try:
        # 匹配 "2026年4月26日 21:09"
        m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})', time_str)
        if m:
            y, mo, d, h, mi = m.groups()
            dt = f"{y}-{int(mo):02d}-{int(d):02d}T{int(h):02d}:{mi}:00+08:00"
            return dt
        # 匹配 "2026-04-26 21:09"
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})\s*(\d{2}):(\d{2})', time_str)
        if m:
            y, mo, d, h, mi = m.groups()
            return f"{y}-{mo}-{d}T{h}:{mi}:00+08:00"
    except:
        pass
    return ""


def title_similarity(t1, t2):
    """
    比较两个标题的相似度。
    先取前 20 个字符做快速比对，再用 SequenceMatcher 精确计算。
    返回 0.0 ~ 1.0 的值。
    """
    if not t1 or not t2:
        return 0.0
    # 标准化：去掉首尾空格、特殊符号
    clean1 = t1.strip().rstrip('.。！!？?')
    clean2 = t2.strip().rstrip('.。！!？?')
    # 如果短标题包含在长标题中，给高分
    short, long_ = (clean1, clean2) if len(clean1) <= len(clean2) else (clean2, clean1)
    if len(short) >= 4 and short in long_:
        return 1.0
    return difflib.SequenceMatcher(None, clean1[:30], clean2[:30]).ratio()


def main():
    args = sys.argv[1:]
    
    # 解析参数
    input_path = "articles_raw.json"
    output_path = "articles.json"
    limit = None
    resume = False
    
    for i, arg in enumerate(args):
        if arg == "--input" and i + 1 < len(args):
            input_path = args[i + 1]
        elif arg == "--output" and i + 1 < len(args):
            output_path = args[i + 1]
        elif arg == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
        elif arg == "--resume":
            resume = True
    
    # 检查 CamoFox
    try:
        health = api("GET", "/health")
        if not health.get("browserConnected"):
            print("❌ CamoFox 浏览器未连接")
            return
        print("✅ CamoFox 已连接\n")
    except:
        print("❌ CamoFox 未运行")
        return
    
    # 加载候选文章
    if not os.path.exists(input_path):
        print(f"❌ 候选文件不存在: {input_path}")
        return
    
    with open(input_path, 'r') as f:
        candidates = json.load(f)
    
    print(f"📚 候选文章: {len(candidates)} 篇")
    
    # 如果已有输出文件且 resume，加载已有结果
    verified = []
    processed_titles = set()
    if resume and os.path.exists(output_path):
        try:
            with open(output_path, 'r') as f:
                verified = json.load(f)
            processed_titles = set(a.get("title", "") for a in verified)
            print(f"↩️ 已有 {len(verified)} 篇已验证，继续处理未验证的")
        except:
            pass
    
    # 过滤未处理的
    remaining = [a for a in candidates if a.get("title", "") not in processed_titles]
    if limit:
        remaining = remaining[:limit]
    
    print(f"🔍 待处理: {len(remaining)} 篇")
    
    if not remaining:
        print("✅ 全部已处理")
        # 按时间排序
        verified.sort(key=lambda a: a.get("pubDate", ""), reverse=True)
        with open(output_path, 'w') as f:
            json.dump(verified, f, ensure_ascii=False, indent=2)
        return
    
    accepted = 0
    rejected = 0
    failed = 0
    
    for idx, article in enumerate(remaining):
        title = article.get("title", "")
        sogou_author = article.get("author", "?")
        print(f"\n[{idx+1}/{len(remaining)}] {title[:40]}... (sogou标记: {sogou_author})")
        
        result = resolve_and_verify(title)
        
        if not result.get("ok"):
            print(f"  ❌ 解析失败")
            failed += 1
            continue
        
        real_author = result["real_author"]
        real_time = result["real_time_iso"]
        real_url = result["real_url"]
        
        # 验证真实作者是否在关注列表中
        if real_author not in ACCOUNT_SET:
            print(f"  ❌ 作者不符: '{real_author}' → 排除")
            rejected += 1
            continue

        # 验证真实文章标题与候选标题是否匹配（防搜狗搜到同名不同文章）
        real_title = result.get("real_title", "")
        if real_title:
            sim = title_similarity(title, real_title)
            if sim < 0.4:
                print(f"  ❌ 标题不匹配: '{real_title[:40]}...' (相似度 {sim:.2f}) → 排除")
                rejected += 1
                continue
            elif sim < 0.65:
                print(f"  ⚠️ 标题相似度较低: {sim:.2f}")
        else:
            print(f"  ⚠️ 无法提取真实标题，跳过标题验证")
        
        # 通过验证
        verified_article = {
            "title": title,
            "author": real_author,
            "pubDate": real_time or article.get("pubDate", ""),
            "summary": (article.get("summary", "") or "")[:300],
            "url": real_url,
            "category": article.get("category", ""),
        }
        verified.append(verified_article)
        accepted += 1
        print(f"  ✅ {real_author} | {result['real_time_raw']} | ✓")
        
        # 每 5 篇保存一次进度
        if (idx + 1) % 5 == 0:
            temp_sorted = sorted(verified, key=lambda a: a.get("pubDate", ""), reverse=True)
            with open(output_path, 'w') as f:
                json.dump(temp_sorted, f, ensure_ascii=False, indent=2)
            print(f"  💾 进度保存 ({idx+1}/{len(remaining)}, 已收录 {accepted})")
        
        # 请求间隔
        time.sleep(2)
    
    # 最终排序 + 保存
    verified.sort(key=lambda a: a.get("pubDate", ""), reverse=True)
    with open(output_path, 'w') as f:
        json.dump(verified, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"📊 结果汇总")
    print(f"  候选总数: {len(remaining)}")
    print(f"  ✅ 已收录: {accepted}")
    print(f"  ❌ 作者不符: {rejected}")
    print(f"  ⚠️ 解析失败: {failed}")
    print(f"  📁 总文章数: {len(verified)}")
    print(f"  💾 输出: {output_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
