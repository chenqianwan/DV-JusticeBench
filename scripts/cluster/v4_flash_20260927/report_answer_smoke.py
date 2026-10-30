#!/usr/bin/env python3
"""Audit the frozen answer-only smoke receipts and render Markdown outputs."""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
from run_answer_smoke import canonical, classify

ROOT = Path(__file__).resolve().parents[3] / 'data/pkulaw_v4_answer_smoke_20260927'


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait900', action='store_true')
    args = parser.parse_args()
    if args.wait900:
        ROOT = ROOT.with_name(ROOT.name + '_wait900')
    inputs = json.loads((ROOT / 'input_tasks.json').read_text())
    manifest = json.loads((ROOT / 'input_manifest.json').read_text())
    results = ROOT / 'results'
    summary = json.loads((results / 'summary.json').read_text())
    identity = json.loads((results / 'run_identity.json').read_text())
    actual_hash = hashlib.sha256((ROOT / 'input_tasks.json').read_bytes()).hexdigest()
    assert actual_hash == identity['input_sha256'] == manifest['input_tasks_sha256']
    assert summary['scoring_performed'] is False and summary['paid_api_used'] is False
    tasks = {(t['split'], t['case_id'], t['question_id']): t for t in inputs['tasks']}
    rows = []
    for split in ('primary', 'secondary'):
        rows.extend(json.loads(line) for line in
                    (results / f'answers_deepseek_v4_flash_{split}.jsonl').read_text().splitlines())
    keys = [(r['split'], r['case_id'], r['question_id']) for r in rows]
    assert len(rows) == len(set(keys)) == 10 and set(keys) == set(tasks)
    counts, usage, attempts = Counter(), Counter(), 0
    for row in rows:
        key = (row['split'], row['case_id'], row['question_id'])
        task = tasks[key]
        for field in ('prompt_hash', 'source_case_text_hash', 'masked_case_text_hash', 'question_hash'):
            assert row[field] == task[field]
        assert row['question_text'] == task['question_text']
        assert row['requested_model'] == inputs['model']
        assert row['temperature'] == 0.3 and row['thinking'] is False
        directory = results / ('task_' + hashlib.sha256(canonical(list(key))).hexdigest()[:24])
        requests = sorted(directory.glob('attempt_*.request.json'))
        responses = sorted(directory.glob('attempt_*.response.json'))
        assert len(requests) == len(responses) == row['attempts']
        receipt_usage = Counter()
        receipts = []
        for request_path, response_path in zip(requests, responses):
            assert request_path.name.replace('.request.', '.response.') == response_path.name
            request = json.loads(request_path.read_text())['payload']
            assert request['messages'] == task['messages']
            assert request['model'] == inputs['model'] and request['temperature'] == 0.3
            assert request['chat_template_kwargs'] == {'thinking': False}
            assert request['max_tokens'] in (3000, 6000)
            receipt = json.loads(response_path.read_text())
            receipts.append(receipt)
            receipt_usage.update({k: v for k, v in (receipt.get('usage') or {}).items()
                                  if isinstance(v, (int, float)) and not isinstance(v, bool)})
        assert row['usage'] == dict(receipt_usage)
        last = receipts[-1]
        assert (row['status'], row['answer'], row['finish_reason']) == classify(last)
        if row['status'] == 'ok':
            assert last['raw_response']['model'] == row['response_model'] == inputs['model']
            assert last['raw_response']['id'] == row['response_id']
            assert last['usage'] == row['final_response_usage']
        if row['status'] == 'ok':
            assert row['answer'].strip() and row['finish_reason'] == 'stop'
        counts[row['status']] += 1
        usage.update(row['usage'])
        attempts += row['attempts']
    assert dict(counts) == summary['counts']
    assert dict(usage) == summary['usage_totals'] and attempts == summary['actual_attempts']
    assert summary['uncertain_request_intents'] == 0
    report = [
        '# DeepSeek V4 Flash：回答阶段冒烟结果', '',
        '只执行回答问题；不重新脱敏、出题、评分或互评。此次为两案冒烟，不是全集运行。', '',
        '| 分组 | 案件数 | 回答数 | 正常完成 | 其他状态 | 输入 token | 输出 token |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for split in ('primary', 'secondary'):
        group = [r for r in rows if r['split'] == split]
        ok = sum(r['status'] == 'ok' for r in group)
        report.append(f"| {split} | 1 | {len(group)} | {ok} | {len(group)-ok} | "
                      f"{sum(r['usage'].get('prompt_tokens', 0) for r in group):,} | "
                      f"{sum(r['usage'].get('completion_tokens', 0) for r in group):,} |")
    report += ['',
        f"运行耗时：{summary['current_session_walltime_s']:.1f} 秒；并发 2；"
        f"请求 {attempts} 次；总 token {usage.get('total_tokens', 0):,}（含重试）。", '',
        '| 分组 | 问题 ID | 状态 | 结束原因 | 请求次数 | 输出字符 | 输入 token | 输出 token | 请求累计耗时（秒） |',
        '|---|---|---|---|---:|---:|---:|---:|---:|']
    answers = ['# DeepSeek V4 Flash：两案回答原文', '',
               '以下为模型生成内容，未评分、未经专家核验。原题与回答未作修改。', '']
    for row in rows:
        report.append(f"| {row['split']} | {row['question_id']} | {row['status']} | "
                      f"{row['finish_reason']} | {row['attempts']} | {len(row['answer'])} | "
                      f"{row['usage'].get('prompt_tokens', 0):,} | {row['usage'].get('completion_tokens', 0):,} | "
                      f"{row['elapsed_s']:.1f} |")
        answers += [f"## {row['split']} / {row['question_id']}", '',
                    f"案例 ID：`{row['case_id']}`；状态：`{row['status']}`。", '',
                    '### 问题', '', row['question_text'], '', '### 模型回答', '', row['answer'], '', '---', '']
    report += ['', '## 固定条件', '',
        '- 数据：清洗交付集 primary 217 案、secondary 2,020 案；各按脱敏案情长度取中位位置 1 案，生成前冻结。',
        '- 模型：集群原版 DeepSeek-V4-Flash preview，服务别名 `deepseek_v4_flash`；非思考模式。不是原实验 V3.2。',
        '- 回答 prompt 与固定源代码 `analysis_messages` 完全一致；复用既有脱敏案情和问题，不发送裁判理由字段。',
        '- temperature=0.3；初始 max_tokens=3000，截断最多扩至6000一次；有限重试，逐次保存原始响应。',
        '- 仅使用已分配的 402816 作业内服务；未调用 xhub，不产生 xhub API 消费；现有 GPU 资源仍有占用成本。',
        '- `ok` 仅表示非空回答以 `stop` 正常结束，不代表法律内容正确，也不排除需人工判断的拒答文字。',
        '- 本轮未启动其余 2,235 案，最终评分继续暂停。', '']
    if args.wait900:
        earlier = json.loads((ROOT.with_name(ROOT.name.removesuffix('_wait900'))/'results/summary.json').read_text())
        report += ['## 等待时间调整', '',
                   f"初轮使用原 180 秒等待上限，{earlier['actual_attempts']} 次请求未能取得完整回答；"
                   '该轮已停止，全部回执另行保留。仅将客户端等待上限延长至 900 秒后重新运行，'
                   '题目、prompt、模型、温度、输出上限、并发均未改变。', '',
                   '本报告 token 和耗时表仅统计 900 秒恢复轮。初轮超时未返回 usage，'
                   '其服务端已生成 token 无法由这些回执确定，不能视为零。', '']
    (ROOT / 'RESULTS.md').write_text('\n'.join(report))
    (ROOT / 'ANSWERS.md').write_text('\n'.join(answers))
    print(json.dumps({'audit': 'passed', 'answers': len(rows), 'counts': dict(counts),
                      'usage': dict(usage), 'reports': ['RESULTS.md', 'ANSWERS.md']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
