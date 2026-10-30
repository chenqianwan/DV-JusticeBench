#!/usr/bin/env python3
"""北大法宝司法案例爬虫。

连接已经人工登录的 Chrome CDP 会话，按案件类型和审结年份拆分检索，
逐条写入 JSONL。脚本不保存或读取登录凭据。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    sync_playwright,
)


CASE_URL = "https://www.pkulaw.com/case"
CASE_TYPE_CODES = {"刑事案件": "002", "民事案件": "003"}
DOCUMENT_TYPE_CODES = {"判决书": "001"}
CASE_NO_RE = re.compile(r"[（(]\s*\d{4}\s*[）)]\s*[^\s，。,；;]{1,80}?号")
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


class LoginRequired(RuntimeError):
    pass


def nap(min_delay: float, max_delay: float) -> None:
    time.sleep(random.uniform(min_delay, max_delay))


def normalize_case_no(value: object) -> str:
    return (
        "".join(str(value or "").split())
        .replace("（", "(")
        .replace("）", ")")
    )


def case_no_from_list(value: str) -> str:
    match = CASE_NO_RE.search(value or "")
    return normalize_case_no(match.group(0)) if match else ""


def load_done_keys(paths: Iterable[Path]) -> tuple[set[str], set[str], int]:
    done_ids: set[str] = set()
    done_case_nos: set[str] = set()
    rows = 0
    seen_paths: set[Path] = set()
    for raw_path in paths:
        path = raw_path.expanduser().resolve()
        if path in seen_paths or not path.exists():
            continue
        seen_paths.add(path)
        for line in path.open(encoding="utf-8"):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows += 1
            record_id = str(row.get("record_id") or row.get("doc_id") or "").strip()
            if record_id:
                done_ids.add(record_id)
            case_no = normalize_case_no(row.get("case_no"))
            if case_no:
                done_case_nos.add(case_no)
    return done_ids, done_case_nos, rows


def connect(browser: Browser) -> tuple[BrowserContext, Page]:
    if not browser.contexts:
        raise RuntimeError("CDP Chrome 没有可用的浏览器上下文")
    context = browser.contexts[0]
    for page in context.pages:
        if page.url.startswith(CASE_URL):
            return context, page
    page = context.new_page()
    page.goto(CASE_URL, wait_until="domcontentloaded", timeout=30_000)
    return context, page


def body_text(page: Page) -> str:
    return page.locator("body").inner_text(timeout=15_000)


def ensure_logged_in(page: Page) -> None:
    text = body_text(page)
    if "登录/注册" in text or "carsi" not in text.lower():
        raise LoginRequired("北大法宝登录态不存在，请在调试 Chrome 中重新登录")


def wait_for_crumb(page: Page, cluster_index: str, cluster_code: str) -> None:
    selector = (
        f'a.crumb-select-item[cluster_index="{cluster_index}"]'
        f'[cluster_code="{cluster_code}"]'
    )
    page.wait_for_selector(selector, timeout=20_000)


def expand_filter(page: Page, search_name: str) -> None:
    fold = page.locator(f'a.fold[searchname="{search_name}"]')
    if not fold.count():
        raise RuntimeError(f"找不到筛选分组：{search_name}")
    if fold.first.get_attribute("iscollapse") == "true":
        fold.first.evaluate("element => element.click()")
        page.wait_for_timeout(1_000)


def apply_filter(
    page: Page,
    *,
    search_name: str,
    port_id: str,
    cluster_index: str,
    cluster_code: str,
) -> None:
    condition = (
        f'a.crumb-select-item[cluster_index="{cluster_index}"]'
        f'[cluster_code="{cluster_code}"]'
    )
    if page.locator(condition).count():
        return
    for _ in range(4):
        expand_filter(page, search_name)
        target = page.locator(
            f'ul[portid="{port_id}"] a[cluster_code="{cluster_code}"]'
        )
        if not target.count():
            page.wait_for_timeout(1_000)
            continue
        # 页面偶尔残留透明的新手引导遮罩，DOM click 能稳定触发原生筛选事件。
        target.first.evaluate("element => element.click()")
        try:
            wait_for_crumb(page, cluster_index, cluster_code)
            page.wait_for_timeout(1_200)
            return
        except Exception:
            page.wait_for_timeout(800)
    raise RuntimeError(
        f"筛选未生效：{search_name}={cluster_code}，当前条件可能已变化"
    )


def reset_and_search(page: Page, keyword: str) -> None:
    page.goto(CASE_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector("#txtSearch", timeout=20_000)
    page.wait_for_timeout(1_500)
    ensure_logged_in(page)

    current_field = page.locator(".areajSelect li.current a")
    if not current_field.count() or current_field.first.get_attribute("areacode") != "Fulltext":
        page.locator('.areajSelect a[areacode="Fulltext"]').first.evaluate(
            "element => element.click()"
        )
    search = page.locator("#txtSearch")
    search.fill(keyword)
    search.press("Enter")
    page.wait_for_selector(
        'a.crumb-select-item[title="同篇精确匹配"]', timeout=30_000
    )
    page.wait_for_timeout(1_500)


def configure_group(
    page: Page,
    *,
    keyword: str,
    case_type: str,
    document_type: str,
    year: int,
    page_size: int,
) -> int:
    reset_and_search(page, keyword)
    apply_filter(
        page,
        search_name="CaseClass",
        port_id="CaseClassport",
        cluster_index="2",
        cluster_code=CASE_TYPE_CODES[case_type],
    )
    apply_filter(
        page,
        search_name="DocumentAttr",
        port_id="DocumentAttrport",
        cluster_index="7",
        cluster_code=DOCUMENT_TYPE_CODES[document_type],
    )
    apply_filter(
        page,
        search_name="LastInstanceDate",
        port_id="LastInstanceDateport",
        cluster_index="8",
        cluster_code=str(year),
    )

    total = page.locator("span.total strong")
    if not total.count():
        return 0
    total_count = int(re.sub(r"\D", "", total.first.inner_text()) or 0)

    if page_size in {10, 20, 50, 100} and total_count:
        expected = min(total_count, page_size)
        for _ in range(4):
            size = page.locator(f'dl[prop="TokenNum"] dd[filter_value="{page_size}"]')
            if not size.count():
                break
            size.first.evaluate("element => element.click()")
            for _ in range(30):
                page.wait_for_timeout(500)
                if len(result_items(page)) == expected:
                    return total_count
        actual = len(result_items(page))
        raise RuntimeError(
            f"每页 {page_size} 条未稳定生效：预期 {expected} 条，实际 {actual} 条"
        )
    return total_count


def result_items(page: Page) -> list[dict[str, str]]:
    return page.locator('input[name="recordList"]:visible').evaluate_all(
        """
        inputs => inputs.map(input => {
          const item = input.closest('div.item');
          const link = item && item.querySelector('h4 a[flink="true"]');
          const info = item && item.querySelector('.info');
          return {
            record_id: input.value || '',
            title: (link && link.textContent || input.title || '').trim(),
            href: link && link.getAttribute('href') || '',
            list_info: (info && info.innerText || '').trim(),
          };
        })
        """
    )


def labelled_value(lines: list[str], label: str) -> str:
    compact_label = re.sub(r"\s+", "", label)
    for index, raw in enumerate(lines):
        compact = re.sub(r"\s+", "", raw)
        if compact in {compact_label, compact_label + "：", compact_label + ":"}:
            if index + 1 < len(lines):
                return lines[index + 1].strip()
        for marker in (compact_label + "：", compact_label + ":"):
            if compact.startswith(marker):
                return compact[len(marker) :].strip()
    return ""


def extract_province(court: str, case_no: str, full_text: str) -> str:
    for province in PROVINCE_NAMES:
        if province in court:
            return province
    match = re.search(r"[（(]\d{4}[）)]([^0-9最高法])", case_no)
    if match and match.group(1) in CASE_NO_PROVINCES:
        return CASE_NO_PROVINCES[match.group(1)]
    for province in PROVINCE_NAMES:
        if province in full_text[:2_000]:
            return province
    return ""


def extract_detail(
    detail: Page,
    item: dict[str, str],
    *,
    keyword: str,
    case_type: str,
    document_type: str,
    year: int,
) -> dict[str, object]:
    url = urljoin("https://www.pkulaw.com", item["href"])
    detail.goto(url, wait_until="domcontentloaded", timeout=30_000)
    detail.wait_for_selector("#divFullText", timeout=30_000)
    for _ in range(10):
        full_text = detail.locator("#divFullText").inner_text(timeout=10_000).strip()
        if full_text:
            break
        detail.wait_for_timeout(1_000)
    else:
        full_text = ""

    main_text = detail.locator("#body1").inner_text(timeout=15_000)
    lines = [line.strip() for line in main_text.splitlines() if line.strip()]
    case_no = labelled_value(lines, "案号")
    court = labelled_value(lines, "审理法院")
    decision_date = labelled_value(lines, "审结日期")
    detail_case_type = labelled_value(lines, "案件类型")
    normalized_case_type = "刑事" if case_type.startswith("刑事") else "民事"
    return {
        "case_id": f"pkulaw_{item['record_id']}",
        "record_id": item["record_id"],
        "doc_id": item["record_id"],
        "source_database": "北大法宝",
        "title": item["title"],
        "url": detail.url,
        "case_no": case_no,
        "cause": labelled_value(lines, "案由"),
        "court": court,
        "decision_date": decision_date,
        "date": decision_date,
        "year": str(year),
        "province": extract_province(court, case_no, full_text),
        "case_category": detail_case_type,
        "category": detail_case_type,
        "case_type": normalized_case_type,
        "procedure": labelled_value(lines, "审理程序"),
        "document_type": labelled_value(lines, "文书类型") or document_type,
        "judges": labelled_value(lines, "审理法官"),
        "public_type": labelled_value(lines, "公开类型"),
        "lawyers": labelled_value(lines, "代理律师/律所"),
        "keywords": labelled_value(lines, "权责关键词"),
        "penalty": labelled_value(lines, "刑罚"),
        "full_text": full_text,
        "content": full_text,
        "content_selector": "#divFullText",
        "full_text_chars": len(full_text),
        "list_info": item["list_info"],
        "list_block": item["list_info"],
        "search_keyword": keyword,
        "keyword": keyword,
        "search_case_type": case_type,
        "search_document_type": document_type,
        "search_year": year,
        "relevance_rule": "北大法宝全文精确匹配",
        "crawled_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def next_page(page: Page, next_index: int, first_id: str, page_size: int) -> bool:
    expected_ordinal = (next_index - 1) * page_size + 1
    # 北大法宝列表接口实测偶尔需要 6 分钟以上。只触发一次翻页并耐心等待，
    # 避免重复点击中断仍在完成的请求，也降低账号侧的访问压力。
    # 详情页抓取后列表页可能处于后台；先切回列表页，避免 Chrome 节流。
    page.bring_to_front()
    page.wait_for_timeout(1_000)
    link = page.locator(f'.pagination a[pageindex="{next_index}"]')
    if not link.count():
        link = page.locator('.pagination li:not(.disabled) a:has-text("下一页")')
    if not link.count():
        return False
    link.first.evaluate("element => element.click()")
    deadline = time.monotonic() + 1_800
    next_report = time.monotonic() + 300
    while time.monotonic() < deadline:
        page.wait_for_timeout(5_000)
        active = page.locator(
            f'.pagination li.active a[pageindex="{next_index}"]'
        )
        first = page.locator('input[name="recordList"]:visible').first
        ordinal = page.locator('div.item:visible label[name="sortNum"]').first
        if active.count() and first.count() and ordinal.count():
            ordinal_value = int(re.sub(r"\D", "", ordinal.inner_text()) or 0)
            current_id = first.get_attribute("value") or ""
            if ordinal_value == expected_ordinal and current_id != first_id:
                return True
        if time.monotonic() >= next_report:
            print(f"  [...] 第 {next_index} 页仍在加载，继续等待，不重复点击")
            next_report += 300
    raise RuntimeError(f"翻到第 {next_index} 页后 1800 秒内列表仍未刷新")


def parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def crawl(args: argparse.Namespace) -> None:
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    dedupe_paths = [output]
    if args.dedupe_jsonl:
        dedupe_paths.extend(Path(value) for value in args.dedupe_jsonl)
    else:
        dedupe_paths.extend(sorted(Path("data/wenshu_raw").glob("*.jsonl")))
    done_ids, done_case_nos, source_rows = load_done_keys(dedupe_paths)
    print(f"输出：{output}")
    print(
        f"去重源共 {source_rows} 行；已有 {len(done_ids)} 个记录ID、"
        f"{len(done_case_nos)} 个非空案号"
    )

    case_types = parse_csv(args.case_types)
    years = [int(value) for value in parse_csv(args.years)]
    for case_type in case_types:
        if case_type not in CASE_TYPE_CODES:
            raise ValueError(f"不支持的案件类型：{case_type}")

    added = 0
    skipped_case_no = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        context, list_page = connect(browser)
        detail: Page | None = None
        try:
            with output.open("a", encoding="utf-8") as stream:
                for case_type in case_types:
                    for year in years:
                        total = configure_group(
                            list_page,
                            keyword=args.keyword,
                            case_type=case_type,
                            document_type=args.document_type,
                            year=year,
                            page_size=args.page_size,
                        )
                        total_pages = max(1, math.ceil(total / args.page_size)) if total else 0
                        print(
                            f"[{case_type} / {year} / {args.document_type}] "
                            f"共 {total} 条，约 {total_pages} 页"
                        )
                        if args.max_pages:
                            total_pages = min(total_pages, args.max_pages)

                        for page_no in range(1, total_pages + 1):
                            batch = result_items(list_page)
                            if not batch:
                                raise RuntimeError(
                                    f"{case_type}/{year} 第 {page_no} 页未解析到列表"
                                )
                            print(
                                f"  第 {page_no}/{total_pages} 页，"
                                f"列表 {len(batch)} 条，累计新增 {added}"
                            )
                            first_id = batch[0]["record_id"]
                            for item in batch:
                                if item["record_id"] in done_ids:
                                    continue
                                listed_case_no = case_no_from_list(item["list_info"])
                                if listed_case_no and listed_case_no in done_case_nos:
                                    skipped_case_no += 1
                                    done_ids.add(item["record_id"])
                                    continue
                                if detail is None:
                                    detail = context.new_page()
                                row = extract_detail(
                                    detail,
                                    item,
                                    keyword=args.keyword,
                                    case_type=case_type,
                                    document_type=args.document_type,
                                    year=year,
                                )
                                case_no = normalize_case_no(row.get("case_no"))
                                if case_no and case_no in done_case_nos:
                                    skipped_case_no += 1
                                    done_ids.add(item["record_id"])
                                    print(
                                        f"    [案号重复] {row['title']} {row['case_no']}，跳过"
                                    )
                                    nap(args.min_delay, args.max_delay)
                                    continue
                                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                                stream.flush()
                                done_ids.add(item["record_id"])
                                if case_no:
                                    done_case_nos.add(case_no)
                                added += 1
                                print(
                                    f"    [+{added}] {row['title']} "
                                    f"{row['case_no']}，正文 {row['full_text_chars']} 字"
                                )
                                if args.max_records and added >= args.max_records:
                                    print(
                                        f"达到 --max-records，安全停止；"
                                        f"累计按案号跳过 {skipped_case_no} 条"
                                    )
                                    return
                                nap(args.min_delay, args.max_delay)

                            # 详情页处理完成后立即关闭，只保留列表页进行翻页。
                            # 这既避免标签积累，也防止列表页在后台被节流。
                            if detail is not None:
                                detail.close()
                                detail = None
                            list_page.bring_to_front()

                            if page_no < total_pages:
                                if not next_page(
                                    list_page,
                                    page_no + 1,
                                    first_id,
                                    args.page_size,
                                ):
                                    raise RuntimeError(
                                        f"{case_type}/{year} 找不到第 {page_no + 1} 页"
                                    )
                                nap(args.min_delay, args.max_delay)
        finally:
            if detail is not None and not detail.is_closed():
                detail.close()
            browser.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="北大法宝司法案例可恢复爬虫")
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument("--keyword", default="家庭暴力")
    parser.add_argument("--case-types", default="刑事案件,民事案件")
    parser.add_argument("--years", default="2023,2024,2025")
    parser.add_argument("--document-type", default="判决书")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument(
        "--output",
        default="data/pkulaw_raw/pkulaw_family_violence_2023_2025.jsonl",
    )
    parser.add_argument("--min-delay", type=float, default=8.0)
    parser.add_argument("--max-delay", type=float, default=15.0)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument(
        "--dedupe-jsonl",
        action="append",
        default=[],
        help="额外案号去重源，可重复指定；默认读取 data/wenshu_raw/*.jsonl",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.min_delay > args.max_delay:
        parser.error("--min-delay 不能大于 --max-delay")
    if args.page_size not in {10, 20, 50, 100}:
        parser.error("--page-size 只能是 10、20、50 或 100")
    crawl(args)


if __name__ == "__main__":
    main()
