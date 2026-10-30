#!/usr/bin/env python3
"""固定检索条件抓取：仅按互斥裁判日期区间分片，并按案号全局去重。"""

import argparse
import atexit
from datetime import date, datetime, timedelta
import json
import math
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import crawl
from playwright.sync_api import sync_playwright

SITE_RESULT_LIMIT = 600


def close_page_safely(page):
    """Close a crawler-owned tab on normal exit, interruption, or safety stop."""
    try:
        if page is not None and not page.is_closed():
            page.close()
    except Exception:
        pass


def load_existing(path):
    doc_ids = set()
    case_nos = set()
    if not path.exists():
        return doc_ids, case_nos
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if row.get("doc_id"):
                doc_ids.add(row["doc_id"])
            case_no = (row.get("case_no") or "").strip()
            if case_no:
                case_nos.add(case_no)
    return doc_ids, case_nos


def wait_for_fixed_conditions(page, keyword, start_date, end_date, timeout=40):
    expected = (
        f"全文：{keyword}",
        f"裁判日期：{start_date}TO{end_date}",
    )
    last_state = None
    stable_ticks = 0
    for _ in range(timeout):
        time.sleep(1)
        crawl.wait_if_blocked(page)
        body = re.sub(r"\s+", "", page.inner_text("body"))
        if not all(text in body for text in expected):
            stable_ticks = 0
            last_state = None
            continue
        state = (page.evaluate(crawl.TOTAL_JS), page.evaluate(crawl.FIRST_DOC_JS))
        if state[0] < 0 or (state[0] > 0 and not state[1]):
            continue
        stable_ticks = stable_ticks + 1 if state == last_state else 1
        last_state = state
        if stable_ticks >= 3:
            return
    raise RuntimeError("固定检索条件在限定时间内未稳定生效")


def set_page_size(page):
    try:
        page.select_option("select.pageSizeSelect", crawl.PAGE_SIZE)
        for _ in range(20):
            time.sleep(1)
            if len(page.evaluate(crawl.LIST_JS)) > 5:
                break
    except Exception:
        print("[!] 每页条数设置失败，按网站当前设置继续")


def open_fixed_search(ctx, keyword, start_date, end_date, document_type):
    url = (
        crawl.SEARCH_URL.format(quote(keyword))
        + f"&cprqStart={quote(start_date)}&cprqEnd={quote(end_date)}"
    )
    page = ctx.new_page()
    # Register immediately: an interruption while the initial list is still
    # loading must not leave a half-open tab behind.
    atexit.register(close_page_safely, page)
    page.goto(url, wait_until="domcontentloaded")
    crawl.wait_if_blocked(page)
    wait_for_fixed_conditions(page, keyword, start_date, end_date)
    crawl.apply_document_type(page, document_type)
    return page


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def crawl_date_range(
    ctx,
    detail,
    stream,
    args,
    start_date,
    end_date,
    done_ids,
    done_case_nos,
    stats,
    pause_before=False,
):
    """Recursively split an over-limit date range, otherwise crawl it completely."""
    if stats["stop"]:
        return
    if pause_before:
        seconds = random.uniform(args.query_pause_min, args.query_pause_max)
        print(f"[i] 切换日期分片前冷却 {seconds / 60:.1f} 分钟")
        time.sleep(seconds)

    page = None
    try:
        page = open_fixed_search(
            ctx,
            args.keyword,
            start_date.isoformat(),
            end_date.isoformat(),
            args.document_type,
        )
        stats["queries"] += 1
        total = page.evaluate(crawl.TOTAL_JS)
        print(
            f"[日期查询 {stats['queries']}] {start_date} TO {end_date}："
            f"共 {total} 篇"
        )

        if total > SITE_RESULT_LIMIT:
            if start_date >= end_date:
                raise RuntimeError(
                    f"单日 {start_date} 仍有 {total} 篇，超过网站 {SITE_RESULT_LIMIT} 条上限"
                )
            midpoint = start_date + timedelta(days=(end_date - start_date).days // 2)
            right_start = midpoint + timedelta(days=1)
            print(
                f"  超过 {SITE_RESULT_LIMIT} 条，拆为 "
                f"{start_date} TO {midpoint} 和 {right_start} TO {end_date}"
            )
            close_page_safely(page)
            page = None
            crawl_date_range(
                ctx, detail, stream, args, start_date, midpoint,
                done_ids, done_case_nos, stats, pause_before=True,
            )
            crawl_date_range(
                ctx, detail, stream, args, right_start, end_date,
                done_ids, done_case_nos, stats, pause_before=True,
            )
            return

        stats["shards"] += 1
        shard_no = stats["shards"]
        if total == 0:
            print(f"[分片 {shard_no}] {start_date} TO {end_date}：0 篇，跳过")
            return

        set_page_size(page)
        total_pages = max(1, math.ceil(total / int(crawl.PAGE_SIZE)))
        print(
            f"=== [分片 {shard_no}] {start_date} TO {end_date}："
            f"{total} 篇 / {total_pages} 页 ==="
        )

        for page_no in range(1, total_pages + 1):
            batch = page.evaluate(crawl.LIST_JS)
            print(
                f"[分片 {shard_no} 列表 {page_no}/{total_pages}] 本页 {len(batch)} 条；"
                f"累计新增 {stats['added']}，重复 {stats['duplicate']}，"
                f"过滤 {stats['filtered']}，失败 {stats['failed']}"
            )
            if not batch:
                print("[!] 当前页没有解析到文书，保留已落盘数据并结束本分片")
                break

            for item in batch:
                list_meta = crawl.parse_meta(item["block"], item["title"])
                list_case_type = crawl.classify_case(
                    list_meta["category"], item["title"]
                )
                if list_case_type not in {"民事", "刑事"}:
                    stats["filtered"] += 1
                    continue
                if item["doc_id"] in done_ids:
                    stats["duplicate"] += 1
                    continue
                listed_case_no = (list_meta.get("case_no") or "").strip()
                if listed_case_no and listed_case_no in done_case_nos:
                    stats["duplicate"] += 1
                    done_ids.add(item["doc_id"])
                    continue

                try:
                    result = crawl.fetch_doc(detail, item["url"])
                except Exception as error:
                    if isinstance(error, crawl.SafetyVerificationRequired):
                        raise
                    if isinstance(error, crawl.BrowserDisconnected) or crawl.is_browser_disconnected(error):
                        raise crawl.BrowserDisconnected from error
                    print(f"  [!] {item['title'][:30]} 抓取失败：{error}")
                    stats["failed"] += 1
                    crawl.nap(args.min_delay, args.max_delay)
                    continue
                if not result["text"]:
                    print(f"  [!] {item['title'][:30]} 正文未渲染，重跑时再试")
                    stats["failed"] += 1
                    crawl.nap(args.min_delay, args.max_delay)
                    continue

                meta, title = crawl.refine(
                    list_meta, item["title"], result["text"]
                )
                case_type, matched_rule = crawl.assess_relevance(
                    meta, title, result["text"], args.keyword
                )
                if case_type not in {"民事", "刑事"}:
                    stats["filtered"] += 1
                    crawl.nap(args.min_delay, args.max_delay)
                    continue

                case_no = (meta.get("case_no") or "").strip()
                if case_no and case_no in done_case_nos:
                    stats["duplicate"] += 1
                    done_ids.add(item["doc_id"])
                    crawl.nap(args.min_delay, args.max_delay)
                    continue

                record = {
                    "case_id": crawl.stable_case_id(item["doc_id"]),
                    "doc_id": item["doc_id"],
                    "source_database": "中国裁判文书网",
                    "title": title,
                    "url": item["url"],
                    **meta,
                    "cause": crawl.infer_cause(meta, title, case_type),
                    "province": crawl.extract_province(
                        meta["court"], meta["case_no"], result["text"]
                    ),
                    "year": crawl.extract_year(meta["date"], meta["case_no"], ""),
                    "case_type": case_type,
                    "document_type": args.document_type,
                    "relevance_rule": matched_rule,
                    "search_date_start": start_date.isoformat(),
                    "search_date_end": end_date.isoformat(),
                    "content": result["text"],
                    "content_selector": result["selector"],
                    "list_block": item["block"],
                    "keyword": args.keyword,
                    "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
                done_ids.add(item["doc_id"])
                if case_no:
                    done_case_nos.add(case_no)
                stats["added"] += 1
                print(
                    f"  [+{stats['added']}] {title[:40]}"
                    f"（{case_no or '案号空缺'}）"
                )
                crawl.nap(args.min_delay, args.max_delay)

                if args.max_new and stats["added"] >= args.max_new:
                    print(f"[i] 已达到本轮新增上限 {args.max_new} 条，安全停止")
                    stats["stop"] = True
                    break
                if args.batch_size and stats["added"] % args.batch_size == 0:
                    seconds = random.uniform(
                        args.batch_pause_min, args.batch_pause_max
                    )
                    print(
                        f"[i] 已新增 {stats['added']} 条，"
                        f"冷却 {seconds / 60:.1f} 分钟"
                    )
                    time.sleep(seconds)

            if stats["stop"] or page_no >= total_pages:
                break
            previous_first = batch[0]["url"]
            if not crawl.goto_next_page(page, previous_first):
                print(
                    f"[!] 分片 {start_date} TO {end_date} 在第 {page_no} 页后"
                    "无法翻页；保留现有结果，重跑时续取"
                )
                break
            crawl.nap(args.min_delay, args.max_delay)
    finally:
        close_page_safely(page)


def run(args):
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    done_ids, done_case_nos = load_existing(output)
    print(
        f"已有 {len(done_ids)} 条记录、{len(done_case_nos)} 个非空案号；"
        "本轮仅拆分裁判日期，并按 doc_id 和案号全局去重"
    )
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")

    stats = {
        "added": 0,
        "duplicate": 0,
        "filtered": 0,
        "failed": 0,
        "queries": 0,
        "shards": 0,
        "stop": False,
    }
    with sync_playwright() as pw:
        _, ctx = crawl.connect(pw)
        detail = ctx.new_page()
        atexit.register(close_page_safely, detail)
        try:
            with output.open("a", encoding="utf-8") as stream:
                crawl_date_range(
                    ctx,
                    detail,
                    stream,
                    args,
                    start_date,
                    end_date,
                    done_ids,
                    done_case_nos,
                    stats,
                )
        finally:
            close_page_safely(detail)

    print(
        f"完成：日期查询 {stats['queries']} 次，完整分片 {stats['shards']} 个；"
        f"新增 {stats['added']}，重复 {stats['duplicate']}，"
        f"过滤 {stats['filtered']}，失败 {stats['failed']} → {output}"
    )


def main():
    parser = argparse.ArgumentParser(description="裁判文书网固定条件、日期自动分片抓取")
    parser.add_argument("--keyword", default="家庭暴力")
    parser.add_argument("--start-date", default="2023-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--document-type", default="判决书")
    parser.add_argument(
        "--out", default="data/wenshu_raw/家庭暴力_判决书_2023-2025.jsonl"
    )
    parser.add_argument("--min-delay", type=float, default=12.0)
    parser.add_argument("--max-delay", type=float, default=20.0)
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--batch-pause-min", type=float, default=600.0)
    parser.add_argument("--batch-pause-max", type=float, default=900.0)
    parser.add_argument("--query-pause-min", type=float, default=120.0)
    parser.add_argument("--query-pause-max", type=float, default=240.0)
    parser.add_argument("--max-new", type=int, default=0)
    args = parser.parse_args()
    if args.min_delay > args.max_delay:
        parser.error("--min-delay 不能大于 --max-delay")
    if args.batch_pause_min > args.batch_pause_max:
        parser.error("--batch-pause-min 不能大于 --batch-pause-max")
    if args.query_pause_min > args.query_pause_max:
        parser.error("--query-pause-min 不能大于 --query-pause-max")
    try:
        run(args)
    except crawl.SafetyVerificationRequired as error:
        print(f"\n[!] {error}；已安全停止，不自动重试")
        raise SystemExit(3)
    except crawl.BrowserDisconnected:
        print("\n[!] Chrome 已关闭；已经落盘的数据保留，可直接重跑")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
