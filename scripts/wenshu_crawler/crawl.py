#!/usr/bin/env python3
"""裁判文书网爬虫：通过 CDP 接管已登录的 Chrome，全文检索关键词并抓取文书全文。

用法见同目录 README.md，需先用 launch_chrome.sh 启动带调试端口的 Chrome。
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

CDP_URL = "http://127.0.0.1:9222"
# s21 是全文检索参数，直接带关键词跳转比模拟点搜索框可靠
SEARCH_URL = "https://wenshu.court.gov.cn/website/wenshu/181217BMTKHNT2W0/index.html?s21={}"
PAGE_SIZE = "15"  # 列表页支持 5 / 10 / 15

# 触发瑞数验证页时页面上会出现的字样
BLOCK_HINTS = (
    "访问验证",
    "安全验证",
    "滑动验证",
    "请点击继续",
    "wzws",
    "账号存在违规行为",
    "账号进行封禁",
)


class BrowserDisconnected(RuntimeError):
    """用户关闭被接管的 Chrome 时终止本轮任务，并保留已经落盘的数据。"""


class SafetyVerificationRequired(RuntimeError):
    """网站要求安全验证时立即停机，避免自动重试继续累积风控。"""

LIST_JS = """
() => {
  const seen = new Set(), out = [];
  document.querySelectorAll('a[href*="docId="]').forEach(a => {
    const m = a.href.match(/docId=([^&#]+)/);
    if (!m || seen.has(m[1])) return;
    seen.add(m[1]);
    // 向上回溯到承载整条记录（法院/案号/日期）的容器，
    // 一旦父容器吞进第二个 docId 链接就说明回溯过头了
    let box = a, best = a;
    for (let i = 0; i < 8 && box.parentElement; i++) {
      box = box.parentElement;
      if (box.querySelectorAll('a[href*="docId="]').length > 1) break;
      best = box;
      if (/\\d{4}-\\d{2}-\\d{2}/.test(box.innerText || '')) break;
    }
    out.push({
      doc_id: m[1],
      title: a.innerText.trim(),
      url: a.href,
      block: (best.innerText || '').trim(),
    });
  });
  return out;
}
"""

# 正文区渲染慢，页面框架（公告栏、导航）会先出现，靠特征词把它和判决书正文区分开
DOC_JS = """
() => {
  const isDoc = t => t.length > 200 && /人民法院|仲裁委员会/.test(t)
                     && !t.includes('欢迎您') && !t.includes('点击了解更多');
  for (const s of ['#DivContent', '.PDF_pox', '#contentTxt', '.content_qw']) {
    const el = document.querySelector(s);
    const t = el ? (el.innerText || '').trim() : '';
    if (isDoc(t)) return {selector: s, text: t};
  }
  // 兜底：取符合特征的最内层文本块
  let best = null;
  for (const d of document.querySelectorAll('div, article')) {
    const t = (d.innerText || '').trim();
    if (isDoc(t) && t.length > 400 && (!best || t.length < best.text.length)) {
      best = {selector: 'auto', text: t};
    }
  }
  return best || {selector: 'none', text: ''};  // 空串表示还没渲染出来
}
"""

FIRST_DOC_JS = """() => {
  const a = document.querySelector('a[href*="docId="]');
  return a ? a.href : '';
}"""

TOTAL_JS = """() => {
  for (const e of document.querySelectorAll('div, span')) {
    const t = (e.innerText || '').trim();
    const m = t.match(/共检索到\\s*(\\d+)\\s*篇/);
    if (m && t.length < 60) return parseInt(m[1]);
  }
  return -1;
}"""

# 左侧筛选面板某一组的选项及条数，如「地域及法院」→ [['河北省', 134], ...]
GROUP_JS = """
(name) => {
  const out = [];
  document.querySelectorAll('div.itemhead').forEach(h => {
    if (!(h.innerText || '').includes(name)) return;
    h.parentElement.querySelectorAll('a.jstree-anchor').forEach(a => {
      const m = (a.innerText || '').trim().match(/^(.+?)\\((\\d+)\\)$/);
      if (m) out.push([m[1], parseInt(m[2])]);
    });
  });
  return out;
}
"""

SHARD_LIMIT = 600  # 官网单次检索只返回前 600 条，超过就得再切片

RE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
RE_CAUSE = re.compile(r"[\u4e00-\u9fa5]{2,12}纠纷")
RE_CASE_YEAR = re.compile(r"[（(](\d{4})[）)]")

CRIMINAL_TERMS = (
    "故意伤害", "故意杀人", "虐待", "遗弃", "强奸", "非法拘禁",
    "暴力干涉婚姻自由", "过失致人死亡", "寻衅滋事",
)
PROTECTION_TERMS = ("人身安全保护令", "人身保护令", "人格权保护禁令", "民保令")

PROVINCE_NAMES = (
    "北京市", "天津市", "上海市", "重庆市", "河北省", "河南省", "云南省",
    "辽宁省", "黑龙江省", "湖南省", "安徽省", "山东省", "新疆维吾尔自治区",
    "江苏省", "浙江省", "江西省", "湖北省", "广西壮族自治区", "甘肃省",
    "山西省", "内蒙古自治区", "陕西省", "吉林省", "福建省", "贵州省",
    "广东省", "青海省", "西藏自治区", "四川省", "宁夏回族自治区", "海南省",
)
CASE_NO_PROVINCES = {
    "京": "北京市", "津": "天津市", "沪": "上海市", "渝": "重庆市",
    "冀": "河北省", "豫": "河南省", "云": "云南省", "辽": "辽宁省",
    "黑": "黑龙江省", "湘": "湖南省", "皖": "安徽省", "鲁": "山东省",
    "新": "新疆维吾尔自治区", "苏": "江苏省", "浙": "浙江省", "赣": "江西省",
    "鄂": "湖北省", "桂": "广西壮族自治区", "甘": "甘肃省", "陇": "甘肃省",
    "晋": "山西省", "内": "内蒙古自治区", "陕": "陕西省", "秦": "陕西省",
    "吉": "吉林省", "闽": "福建省", "贵": "贵州省", "黔": "贵州省",
    "粤": "广东省", "青": "青海省", "藏": "西藏自治区", "川": "四川省",
    "蜀": "四川省", "宁": "宁夏回族自治区", "琼": "海南省",
}


def nap(lo, hi):
    time.sleep(random.uniform(lo, hi))


def stable_case_id(doc_id):
    """Return a deterministic filesystem-safe ID while preserving doc_id separately."""
    digest = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:24]
    return f"wenshu_{digest}"


def is_browser_disconnected(error):
    message = str(error).lower()
    return any(
        hint in message
        for hint in (
            "target page, context or browser has been closed",
            "browser has been closed",
            "connection closed",
            "browser disconnected",
        )
    )


def wait_if_blocked(page, timeout=45):
    """等待正常页面；发现安全验证后立即停机，不在验证页上轮询。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            body = page.inner_text("body", timeout=5000)
        except Exception as error:
            if is_browser_disconnected(error):
                raise BrowserDisconnected from error
            body = ""
        if any(h in body for h in BLOCK_HINTS):
            raise SafetyVerificationRequired("检测到裁判文书网安全验证")
        if body and len(body) > 80:
            return True
        time.sleep(2)
    raise RuntimeError("页面在限定时间内未完成加载")


def wait_for_list(page, timeout=30):
    """列表是异步渲染的，等到出现第一条文书链接为止。"""
    for _ in range(timeout):
        if page.evaluate(FIRST_DOC_JS):
            return True
        time.sleep(1)
    return False


def wait_for_search_state(
    page,
    keyword,
    year=None,
    prov=None,
    case_type=None,
    timeout=35,
):
    """Wait until URL query filters are reflected in the selected-condition panel.

    The site first paints an initial/stale list and applies query parameters a few
    seconds later.  Seeing a document link alone therefore does not mean the
    requested year/province/case type has taken effect.
    """
    expected = [("全文", keyword)]
    if year:
        expected.append(("裁判年份", year))
    if prov:
        expected.append(("法院省份", prov))
    if case_type:
        expected.append(("案件类型", f"{case_type}案件"))

    last_state = None
    stable_ticks = 0
    missing = expected
    for _ in range(timeout):
        time.sleep(1)
        wait_if_blocked(page)
        body = re.sub(r"\s+", "", page.inner_text("body"))
        missing = [
            (name, value)
            for name, value in expected
            if f"{name}：{value}" not in body and f"{name}:{value}" not in body
        ]
        if missing:
            stable_ticks = 0
            last_state = None
            continue

        state = (page.evaluate(TOTAL_JS), page.evaluate(FIRST_DOC_JS))
        if state[0] < 0 or not state[1]:
            stable_ticks = 0
            last_state = None
            continue
        stable_ticks = stable_ticks + 1 if state == last_state else 1
        last_state = state
        if stable_ticks >= 3:
            return

    missing_text = "、".join(f"{name}：{value}" for name, value in missing)
    raise RuntimeError(
        f"检索条件在限定时间内未稳定生效：{missing_text or '结果列表仍在变化'}"
    )


def do_search(page, keyword, year=None, prov=None, tag=None, case_type=None):
    # The site's SPA can retain the previous query state inside one tab even when
    # the address bar already shows new parameters.  Use one fresh list tab per
    # query and close the previous crawler tab so stale state cannot bleed across
    # years or provinces.
    ctx = page.context
    fresh_page = ctx.new_page()
    if not page.is_closed():
        page.close()
    page = fresh_page

    url = SEARCH_URL.format(quote(keyword))
    if year:
        url += f"&s42={year}"  # s42 = 裁判年份
    if prov:
        url += f"&s33={quote(prov)}"  # s33 = 地域
    if tag:
        url += f"&s45={quote(tag)}"  # s45 = 关键字标签，比全文检索精准得多
    case_type_codes = {"民事": "03", "刑事": "02"}
    if case_type:
        url += f"&s8={case_type_codes[case_type]}"
    page.goto(url, wait_until="domcontentloaded")
    wait_if_blocked(page)

    if not wait_for_list(page):
        body = page.inner_text("body")
        if "登录" in body and "欢迎您" not in body:
            input("  [!] 看起来还没登录，请在 Chrome 里完成登录，然后回到这里按回车继续…")
            page.reload(wait_until="domcontentloaded")
            wait_if_blocked(page)
        else:
            # 站点是 SPA；只有同一路由切换查询参数而页面没有消费新参数时才补一次刷新。
            page.reload(wait_until="domcontentloaded")
            wait_if_blocked(page)
        if not wait_for_list(page):
            raise RuntimeError("列表页没渲染出文书链接，用 --inspect 导出页面看看")

    wait_for_search_state(page, keyword, year, prov, case_type)

    try:
        page.select_option("select.pageSizeSelect", PAGE_SIZE)
        for _ in range(20):
            time.sleep(1)
            if len(page.evaluate(LIST_JS)) > 5:
                break
    except Exception:
        print("  [!] 每页条数设置失败，按默认 5 条继续")
    return page


def goto_next_page(page, prev_first_url):
    """点下一页。列表是 SPA 局部刷新，靠首条 docId 变化判断是否翻成功。"""
    btn = None
    try:
        for sel in ["a.pageButton:has-text('下一页')", "a:has-text('下一页')"]:
            for el in page.query_selector_all(sel):
                if el.is_visible():
                    btn = el
                    break
            if btn:
                break
        if btn is None:
            return False
        btn.click()
    except Exception as e:
        if is_browser_disconnected(e):
            raise BrowserDisconnected from e
        # SPA 局部刷新会把翻页按钮换掉，句柄可能已失效
        print(f"  [!] 翻页失败：{e}")
        return False

    for _ in range(30):
        time.sleep(1)
        cur = page.evaluate(FIRST_DOC_JS)
        if cur and cur != prev_first_url:
            wait_if_blocked(page)
            return True
    return False


def click_filter(page, label, group_name=None):
    """Click a left-side filter and verify that the selected condition took effect."""
    loc = page.locator("a.jstree-anchor").filter(has_text=re.compile(rf"^{re.escape(label)}\("))
    if not loc.count():
        print(f"    [!] 筛选项「{label}」没找到")
        return False
    before = page.evaluate(FIRST_DOC_JS)
    before_total = page.evaluate(TOTAL_JS)
    option_text = loc.first.inner_text().strip()
    option_count_match = re.search(r"\(([\d,]+)\)\s*$", option_text)
    expected_total = (
        int(option_count_match.group(1).replace(",", ""))
        if option_count_match
        else None
    )
    loc.first.click()
    selected_ticks = 0
    for _ in range(35):
        time.sleep(1)
        if group_name:
            body = re.sub(r"\s+", "", page.inner_text("body"))
            selected = (
                f"{group_name}：{label}" in body
                or f"{group_name}:{label}" in body
            )
            if not selected:
                continue
            selected_ticks += 1
            current_total = page.evaluate(TOTAL_JS)
            current_first = page.evaluate(FIRST_DOC_JS)

            # 选中条件标签会先出现，列表和“共检索到”数字稍后才更新。
            # 筛选项括号里的数量就是该条件应有的结果数，优先以它为准，
            # 防止把“条件已显示、结果仍是旧列表”误判成筛选完成。
            if expected_total is not None and current_total == expected_total:
                wait_if_blocked(page)
                return True
            if expected_total is None and (
                current_total != before_total or current_first not in ("", before)
            ):
                wait_if_blocked(page)
                return True

            # 极少数情况下筛选前后数量和首条完全相同；条件连续稳定显示
            # 一段时间后才允许继续，避免永久等待。
            if selected_ticks >= 8 and current_total >= 0:
                print(
                    f"    [!] 「{group_name}：{label}」已选中，但结果状态未变化；"
                    f"当前共 {current_total} 条"
                )
                wait_if_blocked(page)
                return True
        elif page.evaluate(FIRST_DOC_JS) not in ("", before):
            return True
    return False


def apply_document_type(page, document_type):
    if not document_type:
        return
    if not click_filter(page, document_type, "文书类型"):
        raise RuntimeError(f"文书类型筛选未生效：{document_type}")


def plan_shards(page, keyword, years, case_types, tag, delay, document_type=None):
    """官网单次检索只给前 600 条，超限就逐级细分：整年 → 按省份 → 按文书类型。"""
    plan = []
    for case_type in case_types:
        for year in years:
            page = do_search(page, keyword, year, tag=tag, case_type=case_type)
            apply_document_type(page, document_type)
            total = page.evaluate(TOTAL_JS)
            print(f"  {year or '不限年份'}/{case_type}: 共 {total} 条")
            nap(*delay)
            if 0 <= total <= SHARD_LIMIT:
                plan.append({
                    "year": year,
                    "case_type": case_type,
                    "prov": None,
                    "doc_type": None,
                    "count": total,
                })
                continue

            provs = page.evaluate(GROUP_JS, "地域及法院")
            print(f"    超过 {SHARD_LIMIT} 条上限，按 {len(provs)} 个省份切片")
            for prov, cnt in provs:
                if cnt <= SHARD_LIMIT:
                    plan.append({
                        "year": year,
                        "case_type": case_type,
                        "prov": prov,
                        "doc_type": None,
                        "count": cnt,
                    })
                    continue
                page = do_search(
                    page,
                    keyword,
                    year,
                    prov,
                    tag=tag,
                    case_type=case_type,
                )
                apply_document_type(page, document_type)
                nap(*delay)
                if document_type:
                    print(
                        f"    [!] {year}/{case_type}/{prov}/{document_type} 有 {cnt} 条，"
                        f"只能拿到前 {SHARD_LIMIT} 条"
                    )
                    plan.append({
                        "year": year,
                        "case_type": case_type,
                        "prov": prov,
                        "doc_type": None,
                        "count": cnt,
                    })
                    continue
                subs = page.evaluate(GROUP_JS, "文书类型")
                print(f"    {prov} {cnt} 条仍超限，按文书类型再切成 {len(subs)} 片")
                for doc_type, c in subs:
                    if c > SHARD_LIMIT:
                        print(f"    [!] {prov}/{doc_type} 有 {c} 条，只能拿到前 {SHARD_LIMIT} 条")
                    plan.append({
                        "year": year,
                        "case_type": case_type,
                        "prov": prov,
                        "doc_type": doc_type,
                        "count": c,
                    })
    return plan, page


def open_shard(page, keyword, shard, tag, document_type=None):
    page = do_search(
        page,
        keyword,
        shard["year"],
        shard["prov"],
        tag=tag,
        case_type=shard["case_type"],
    )
    apply_document_type(page, document_type)
    if shard["doc_type"]:
        click_filter(page, shard["doc_type"], "文书类型")
    return page


def parse_meta(block, title):
    """列表条目里「法院 案号 日期」固定同处一行，按空白切分比正则可靠。"""
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    court = case_no = date = ""
    for line in lines:
        m = RE_DATE.search(line)
        if not m:
            continue
        date = m.group(0)
        for part in line.split():
            if part == date:
                continue
            if part.endswith(("法院", "法庭")):
                court = part
            elif part.endswith("号"):
                case_no = part
        break
    cause = RE_CAUSE.search(title)
    return {
        "category": lines[0] if lines else "",  # 列表页给的分类，如「民事再审」
        "court": court,
        "case_no": case_no,
        "date": date,
        "cause": cause.group(0) if cause else "",
    }


def classify_case(category, title):
    text = f"{category} {title}"
    if "刑事" in text:
        return "刑事"
    if "民事" in text or "保护令" in text:
        return "民事"
    return ""


def extract_year(date, case_no, fallback=""):
    if date and len(date) >= 4:
        return date[:4]
    match = RE_CASE_YEAR.search(case_no)
    return match.group(1) if match else fallback


def extract_province(court, case_no, content=""):
    for province in PROVINCE_NAMES:
        if province in court:
            return province
    # 地方法院名称经常不含省名，案号中的法院简称更稳定，如（2024）湘01民终。
    match = re.search(r"[（(]\d{4}[）)]([^0-9最高法])", case_no)
    if match and match.group(1) in CASE_NO_PROVINCES:
        return CASE_NO_PROVINCES[match.group(1)]
    # 最高法文书的案号不带地域简称，正文开头通常会写明原审法院所在省份。
    opening = content[:2000]
    for province in PROVINCE_NAMES:
        if province in opening:
            return province
    return ""


def infer_cause(meta, title, case_type):
    if meta["cause"]:
        return meta["cause"]
    for term in PROTECTION_TERMS:
        if term in title or term in meta["case_no"]:
            return "申请人身安全保护令"
    if case_type == "刑事":
        for term in CRIMINAL_TERMS:
            if term in title:
                return f"{term}罪" if not term.endswith("罪") else term
    return meta["category"]


def assess_relevance(meta, title, content, keyword):
    """全文命中指定词后，仅保留补实验要求的民事、刑事案件。"""
    case_type = classify_case(meta["category"], title)
    if not case_type:
        return "", "非民事或刑事案件"
    if keyword not in content:
        return "", f"正文没有“{keyword}”"
    for term in PROTECTION_TERMS:
        if term in title or term in meta["case_no"]:
            return case_type, f"全文命中：{keyword}；保护令：{term}"
    return case_type, f"全文命中：{keyword}"


def collect_list(page, pages, delay):
    items, seen = [], set()
    for i in range(1, pages + 1):
        batch = page.evaluate(LIST_JS)
        new = [it for it in batch if it["doc_id"] not in seen]
        for it in new:
            seen.add(it["doc_id"])
        items.extend(new)
        print(f"[列表] 第 {i}/{pages} 页：本页 {len(batch)} 条，新增 {len(new)} 条，累计 {len(items)} 条")

        if not batch:
            print("  [!] 本页没解析到任何文书链接，可能是页面结构变了，用 --inspect 看看")
            break
        if i == pages:
            break
        if not goto_next_page(page, batch[0]["url"]):
            print("  没有下一页了，列表阶段提前结束")
            break
        nap(*delay)
    return items


def fetch_doc(page, url, tries=2):
    for attempt in range(tries):
        page.goto(url, wait_until="domcontentloaded")
        wait_if_blocked(page)
        for _ in range(25):
            res = page.evaluate(DOC_JS)
            if res["text"]:
                return res
            time.sleep(1)
        if attempt + 1 < tries:
            print("    正文没渲染出来，重试一次")
            nap(3, 5)
    return {"selector": "none", "text": ""}


def refine(meta, title, content):
    """列表页有一类条目没有标题，元数据会被切错，用格式规整的正文头部补齐。

    正文前三行固定是：法院 / 文书类型 / 案号。
    """
    lines = [l.strip() for l in content.split("\n")[:4] if l.strip()]
    if not meta["court"].endswith(("法院", "法庭")) and lines and lines[0].endswith(("法院", "法庭")):
        meta["court"] = lines[0]
    if not meta["case_no"]:
        for l in lines[1:4]:
            if l.endswith("号") and len(l) < 40:
                meta["case_no"] = l
                break
    if not title.endswith(("书", "令", "其他")):
        doc_type = lines[1].replace(" ", "").replace("\u3000", "") if len(lines) > 1 else ""
        title = " ".join(x for x in (meta["court"], doc_type, meta["case_no"]) if x)
        meta["cause"] = ""  # 合成标题里没有案由信息
    return meta, title


def load_done(out_path):
    """已抓过的 doc_id，以及「标题+案号」——同一篇文书在站上可能挂着两个不同的 docId。"""
    ids, keys = set(), set()
    if not out_path.exists():
        return ids, keys
    with out_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            ids.add(r["doc_id"])
            keys.add((r["title"], r["case_no"]))
    return ids, keys


def connect(pw):
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
    except Exception as e:
        sys.exit(
            f"连不上 Chrome 调试端口 {CDP_URL}：{e}\n"
            "请先运行 ./launch_chrome.sh 启动带调试端口的 Chrome。"
        )
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, ctx


def run_inspect(keyword):
    with sync_playwright() as pw:
        _, ctx = connect(pw)
        page = ctx.new_page()
        page = do_search(page, keyword)
        Path("debug_list.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="debug_list.png", full_page=True)
        print("已导出 debug_list.html 和 debug_list.png")
        print("解析到的条目：", json.dumps(page.evaluate(LIST_JS)[:3], ensure_ascii=False, indent=2))


def run_crawl(
    keyword,
    years,
    case_types,
    pages,
    out_path,
    delay,
    tag,
    document_type=None,
    max_new=0,
    batch_size=40,
    batch_pause=(480, 900),
):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done_ids, done_keys = load_done(out_path)
    if done_ids:
        print(f"已有 {len(done_ids)} 条历史结果，将跳过")

    with sync_playwright() as pw:
        _, ctx = connect(pw)
        page = ctx.new_page()
        detail = ctx.new_page()

        print(
            f"=== 规划分片（关键字限定: {tag or '否，纯全文检索'}；"
            f"案件类型: {','.join(case_types)}；"
            f"文书类型: {document_type or '不限'}）==="
        )
        plan, page = plan_shards(
            page,
            keyword,
            years or [None],
            case_types,
            tag,
            delay,
            document_type,
        )
        total = sum(s["count"] for s in plan)
        print(f"共 {len(plan)} 个分片，预计 {total} 条\n")

        ok = dup = fail = filtered = 0
        limit_reached = False
        with out_path.open("a", encoding="utf-8") as f:
            for i, sh in enumerate(plan, 1):
                name = "/".join(
                    x
                    for x in (
                        sh["year"],
                        sh["case_type"],
                        sh["prov"],
                        sh["doc_type"],
                    )
                    if x
                )
                print(f"=== [分片 {i}/{len(plan)}] {name}（{sh['count']} 条）===")
                if not sh["count"]:
                    continue
                try:
                    page = open_shard(page, keyword, sh, tag, document_type)
                    # The site keeps a visible but unclickable "next" control on the
                    # final page. Bounding the loop by the shard's advertised count
                    # avoids a 30-second click timeout at the end of every shard.
                    shard_pages = min(
                        pages,
                        max(1, (int(sh["count"]) + int(PAGE_SIZE) - 1) // int(PAGE_SIZE)),
                    )
                    listed = collect_list(page, shard_pages, delay)
                except Exception as e:
                    if isinstance(e, SafetyVerificationRequired):
                        raise
                    if isinstance(e, BrowserDisconnected) or is_browser_disconnected(e):
                        raise BrowserDisconnected from e
                    # 单个分片挂掉不该毁掉整轮抓取，重跑时会补上
                    print(f"  [!] 分片 {name} 打开失败：{e}")
                    continue

                for it in listed:
                    if it["doc_id"] in done_ids:
                        dup += 1
                        continue
                    try:
                        res = fetch_doc(detail, it["url"])
                    except Exception as e:
                        if isinstance(e, SafetyVerificationRequired):
                            raise
                        if isinstance(e, BrowserDisconnected) or is_browser_disconnected(e):
                            raise BrowserDisconnected from e
                        print(f"  [!] {it['title'][:30]} 抓取失败：{e}")
                        fail += 1
                        nap(*delay)
                        continue
                    if not res["text"]:
                        print(f"  [!] {it['title'][:30]} 正文没渲染出来，重跑会自动重试")
                        fail += 1
                        nap(*delay)
                        continue

                    meta, title = refine(parse_meta(it["block"], it["title"]),
                                         it["title"], res["text"])
                    case_type, matched_rule = assess_relevance(
                        meta, title, res["text"], keyword
                    )
                    if not case_type:
                        filtered += 1
                        print(f"  [-] 过滤：{title[:34]}（{matched_rule}）")
                        nap(*delay)
                        continue
                    if case_type != sh["case_type"]:
                        filtered += 1
                        print(
                            f"  [-] 过滤：{title[:34]}（案件类型为 {case_type}，"
                            f"当前分片要求 {sh['case_type']}）"
                        )
                        nap(*delay)
                        continue
                    key = (title, meta["case_no"])
                    if key in done_keys:
                        dup += 1
                        nap(*delay)
                        continue
                    done_ids.add(it["doc_id"])
                    done_keys.add(key)

                    rec = {
                        "case_id": stable_case_id(it["doc_id"]),
                        "doc_id": it["doc_id"],
                        "source_database": "中国裁判文书网",
                        "title": title,
                        "url": it["url"],
                        **meta,
                        "cause": infer_cause(meta, title, case_type),
                        "province": extract_province(meta["court"], meta["case_no"], res["text"]),
                        "year": extract_year(meta["date"], meta["case_no"], sh["year"]),
                        "case_type": case_type,
                        "document_type": document_type or "",
                        "relevance_rule": matched_rule,
                        "content": res["text"],
                        "content_selector": res["selector"],
                        "list_block": it["block"],
                        "keyword": keyword,
                        "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    ok += 1
                    print(f"  [{ok}/{total}] {title[:38]} … 正文 {len(res['text'])} 字")
                    nap(*delay)
                    if max_new and ok >= max_new:
                        print(f"  [i] 已达到本轮新增上限 {max_new} 条，安全停止")
                        limit_reached = True
                        break
                    if batch_size and ok % batch_size == 0:
                        pause_seconds = random.uniform(*batch_pause)
                        print(
                            f"  [i] 已完成 {ok} 条，冷却 {pause_seconds / 60:.1f} 分钟后继续"
                        )
                        time.sleep(pause_seconds)
                if limit_reached:
                    break
                nap(*delay)
        detail.close()

    print(
        f"\n完成：新增 {ok} 条，过滤 {filtered} 条，"
        f"跳过重复 {dup} 条，失败 {fail} 条 → {out_path}"
    )


def main():
    ap = argparse.ArgumentParser(description="裁判文书网爬虫（接管已登录的 Chrome）")
    ap.add_argument("--keyword", default="家庭暴力", help="全文检索词，默认与实验口径一致")
    ap.add_argument("--years", default="2023,2024,2025", help="裁判年份，逗号分隔；传空则不限年份")
    ap.add_argument("--case-types", default="民事,刑事",
                    help="案件类型，逗号分隔；仅支持民事、刑事")
    ap.add_argument("--pages", type=int, default=40,
                    help="每个分片最多翻多少页（每页 15 条，官网单次检索上限 40 页 / 600 条）")
    ap.add_argument("--out", default="", help="输出 jsonl 路径，默认 data/<关键词>_<年份>.jsonl")
    ap.add_argument("--min-delay", type=float, default=8.0, help="每次请求最小间隔秒数")
    ap.add_argument("--max-delay", type=float, default=15.0, help="每次请求最大间隔秒数")
    ap.add_argument("--max-new", type=int, default=0,
                    help="本轮最多新增多少条；0 表示不限制")
    ap.add_argument("--batch-size", type=int, default=40,
                    help="每新增多少条进入一次冷却；0 表示不冷却")
    ap.add_argument("--batch-pause-min", type=float, default=480,
                    help="批次冷却最短秒数")
    ap.add_argument("--batch-pause-max", type=float, default=900,
                    help="批次冷却最长秒数")
    ap.add_argument("--keyword-tag", action="store_true",
                    help="额外加关键字标签限定；默认关闭，按全文检索口径抓取")
    ap.add_argument("--document-type", default="判决书",
                    help="文书类型筛选，默认仅判决书；传空则不限")
    ap.add_argument("--inspect", action="store_true", help="只导出搜索结果页结构，用于调选择器")
    args = ap.parse_args()

    if args.inspect:
        run_inspect(args.keyword)
        return

    years = [y.strip() for y in args.years.split(",") if y.strip()]
    case_types = [x.strip() for x in args.case_types.split(",") if x.strip()]
    unsupported_case_types = sorted(set(case_types) - {"民事", "刑事"})
    if unsupported_case_types:
        ap.error(f"不支持的案件类型：{','.join(unsupported_case_types)}")
    if args.min_delay > args.max_delay:
        ap.error("--min-delay 不能大于 --max-delay")
    if args.batch_pause_min > args.batch_pause_max:
        ap.error("--batch-pause-min 不能大于 --batch-pause-max")
    if args.out:
        out = Path(args.out)
    else:
        suffix = f"_{years[0]}-{years[-1]}" if len(years) > 1 else (f"_{years[0]}" if years else "")
        out = Path("data") / f"{args.keyword}{suffix}.jsonl"
    tag = args.keyword if args.keyword_tag else None
    try:
        run_crawl(
            args.keyword,
            years,
            case_types,
            args.pages,
            out,
            (args.min_delay, args.max_delay),
            tag,
            args.document_type.strip(),
            args.max_new,
            args.batch_size,
            (args.batch_pause_min, args.batch_pause_max),
        )
    except SafetyVerificationRequired as error:
        print(f"\n[!] {error}；已安全停止，未自动重试。")
        raise SystemExit(3)
    except BrowserDisconnected:
        print("\n[!] Chrome 已关闭，爬虫安全退出；已经写入的数据均已保留，可直接重跑续爬。")


if __name__ == "__main__":
    main()
