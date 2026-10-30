#!/usr/bin/env python3
"""Summarize the evaluation-only pilot without changing legacy inclusion/scores."""
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import json
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_cross_family_judging_sample20_xhub import atomic_write_json, load_json, JUDGES
from utils.evaluator import AnswerEvaluator

RUN = ROOT / 'data/cross_judge_budget_pilot_20260908'
OUTPUT = ROOT / 'outputs/20260908_cross_judge_budget_pilot'
LABELS = list(JUDGES)


def main():
    manifest = load_json(RUN / 'manifest.json')
    summary = load_json(RUN / 'summary.json')
    market = load_json(RUN / 'market_snapshot.json')
    rates = market['public_default_group_prices_usd_per_million']
    ratings = [load_json(RUN / 'ratings' / (t['task_id'] + '.json')) for t in manifest['tasks']]
    receipts = [load_json(f) for f in sorted((RUN / 'http_receipts').glob('*.json'))]
    source_answers = {a['answer_id']:a for a in load_json(RUN/'source_answers.json')}
    class Replay:
        provider = 'deepseek'
        def __init__(self, scoring):
            self.scoring = scoring
        def analyze_case(self, case_text, question=None, use_thinking=False):
            return {'answer':self.scoring['content'], 'thinking':self.scoring.get('reasoning_content','')}
    replayed = 0
    for rating in ratings:
        if rating['status'] != 'success':
            continue
        answer = source_answers[rating['answer_id']]
        replay = AnswerEvaluator(api=Replay(rating['scoring']),flag_parser_version='legacy').evaluate_answer(
            answer['answer'],answer['masked_judgment'],answer['question'],answer['masked_case_text'])
        assert replay == rating['evaluation'], 'Stored evaluation differs from unchanged legacy evaluator'
        replayed += 1
    table = []
    for label in LABELS:
        mid = JUDGES[label]['model_id']
        group = [r for r in ratings if r['judge_label'] == label]
        calls = [r for r in receipts if r['request']['model'] == mid]
        totals = {name: sum((r.get('usage') or {}).get(name, 0) for r in calls)
                  for name in ['prompt_tokens','completion_tokens','total_tokens']}
        reasoning = sum(((r.get('usage') or {}).get('completion_tokens_details') or {}).get('reasoning_tokens', 0) for r in calls)
        success = sum(r['status'] == 'success' for r in group)
        returned = sorted({str((r.get('response') or {}).get('model')) for r in calls if (r.get('response') or {}).get('model')})
        historical = sorted({str(r.get('historical_response_model')) for r in group if r.get('historical_response_model')})
        table.append({'label':label,'model_id':mid,'planned_ratings':len(group),'success':success,
                      'failed':len(group)-success,'http_attempts':len(calls),
                      'http_error_count':sum(r.get('http_status') != 200 for r in calls),
                      **totals,'reasoning_tokens_subset':reasoning,'response_models':returned,
                      'historical_response_models':historical,
                      'pilot_estimated_usd':sum(r['list_estimated_usd'] for r in calls),
                      'input_price_usd_per_million':rates[mid][0],
                      'output_price_usd_per_million':rates[mid][1]})

    # Calibration with the larger 40-case historical token pool reduces reliance
    # on the small length-targeted pilot when choosing a conservative sample size.
    history = load_json(ROOT / 'data/experiment1_cross_judge_sample40_xhub_20260822/combined_results.json')
    historical_repriced = defaultdict(float)
    for filename in history['rating_files'].values():
        record = load_json(Path(filename))
        if record['source'] != 'new_api':
            continue
        usage = (record.get('scoring') or {}).get('usage') or {}
        mid = record['judge_model_id']
        price = rates[mid]
        historical_repriced[mid] += (usage.get('prompt_tokens',0)*price[0] + usage.get('completion_tokens',0)*price[1])/1e6
    pilot_non_ds = sum(r['pilot_estimated_usd'] for r in table if r['model_id'] != 'deepseek-v3.2')
    # 15 answers contain one of five questions on three cases.
    pilot_per_full_case = pilot_non_ds / 3 * 5
    hist_per_full_case = sum(historical_repriced.values())/40
    conservative_case_estimate = max(pilot_per_full_case, hist_per_full_case)
    pilot_spent = summary.get('observed_key_debit_usd')
    budget_pilot = max(pilot_spent or 0, summary['all_http_list_estimate_usd'])
    projections = []
    for n in [40,60,80,108]:
        base = n * conservative_case_estimate
        reserve = .2 * base
        projections.append({'cases':n,'answers':n*25,'new_ratings':n*60,'reused_ds_ratings':n*15,
                            'base_usd':base,'contingency_usd':reserve,
                            'including_pilot_and_contingency_usd':base+reserve+budget_pilot})

    # Actual HTTP messages must match the previously frozen expected text.
    expected = {t['messages_sha256'] for t in manifest['tasks']}
    actual = [hashlib.sha256(json.dumps(r['request']['messages'], ensure_ascii=False, sort_keys=True).encode()).hexdigest() for r in receipts]
    assert set(actual) <= expected
    assert all(r['request']['temperature'] == .3 for r in receipts)
    assert all(r['request']['max_tokens'] in [3000,6000] for r in receipts)
    result = {'summary':summary,'models':table,'current_price_historical_40case_usd':dict(historical_repriced),
              'cost_per_full_case_from_pilot':pilot_per_full_case,
              'cost_per_full_case_from_historical_tokens_at_current_price':hist_per_full_case,
              'conservative_estimate_per_full_case':conservative_case_estimate,
              'projections':projections,'all_request_text_matches_manifest':True,
              'successful_ratings_replayed_with_identical_legacy_evaluation':replayed,
              'score_policy':'Original legacy scores/statuses unchanged; no new quality filtering applied.'}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OUTPUT/'token成本与模型核查.json', result)

    lines = ['# 108 案主实验：最终评分互评小测试与 $100 预算', '',
             f"测试完成时间：{summary['finished_at']}。仅执行最终评价。", '',
             '## 1. 评分协议', '',
             '| 项目 | 本次执行 |', '|---|---|',
             '| 评分 prompt、五维量表 | 直接复用原 `AnswerEvaluator`；历史 prompt 哈希一致 |',
             '| 阈值、错误解析、扣分 | 原 `legacy` 实现，无修改 |',
             '| 输入 | 复用同一问题、同一既有回答、同一脱敏案情及参考判决；实际请求逐字核对通过 |',
             '| 参数 | temperature=0.3；max_tokens=3000；DeepSeek thinking=true；其余保持原请求方式 |',
             '| 重试 | 沿用原 HTTP、空内容、截断和整评分任务重试；所有物理调用另行记录费用 |',
             '| 实验变量 | 评分模型；不重生成问题、答案或脱敏文本 |',
             '| 统计口径 | 保留旧分数及旧成功/失败判定；没有新增剔除规则 |', '',
             '## 2. 当前模型可用性与版本', '',
             '| 评分模型（请求 ID） | 成功 / 安排 | 旧回包模型 | 当前回包模型 |', '|---|---:|---|---|']
    for r in table:
        lines.append(f"| `{r['model_id']}` | {r['success']}/{r['planned_ratings']} | {', '.join(r['historical_response_models'])} | {', '.join(r['response_models']) or '无成功回包'} |")
    lines += ['', '四个请求 ID 均在当前 xhub 目录中。相同别名能够复用调用协议，但不能证明 xhub 上游权重/快照未变。', '',
              '## 3. 实测 token 与费用', '',
              '样本：从已有 40 案互评记录中，按评分输入长度 P20/P50/P90 选择 3 个不同案件的问题，每题复用 5 种模型回答，共 15 份回答、45 次计划评分。此次 DeepSeek 也重新评分以核查版本；正式扩样可复用符合条件的旧 DeepSeek 评分。', '',
              '| 评分模型 | HTTP 次数 | 输入 tokens | 输出 tokens | 其中 reasoning | 全部调用标价估算 |',
              '|---|---:|---:|---:|---:|---:|']
    for r in table:
        lines.append(f"| {r['label']} | {r['http_attempts']} | {r['prompt_tokens']:,} | {r['completion_tokens']:,} | {r['reasoning_tokens_subset']:,} | ${r['pilot_estimated_usd']:.4f} |")
    lines += [f"| 合计 | {len(receipts)} | {sum(r['prompt_tokens'] for r in table):,} | {sum(r['completion_tokens'] for r in table):,} | {sum(r['reasoning_tokens_subset'] for r in table):,} | ${summary['all_http_list_estimate_usd']:.4f} |", '',
              f"本次 API Key 额度实际扣减：{'暂不可得' if pilot_spent is None else f'${pilot_spent:.4f}'}。reasoning 已包含在输出 tokens 中，不重复计费。标价估算不扣缓存折扣；Key 的批次差值可能受并行使用同一 Key 的调用影响。", '',
              '| 模型 | 当前输入价 / 百万 tokens | 当前输出价 / 百万 tokens |', '|---|---:|---:|']
    for r in table:
        lines.append(f"| {r['label']} | ${r['input_price_usd_per_million']:.4f} | ${r['output_price_usd_per_million']:.4f} |")
    lines += ['', '价格取本次 xhub `/api/pricing` 的 default 组公开倍率和 `/api/status` 的 quota_per_unit，最终以实际额度扣减为准。', '',
              '## 4. $100 抽样预算', '',
              '每案保留原来的 5 题和 5 种回答，交由 3 个不同家族评分。每案共 75 份互评：60 次新 API 评分 + 15 份可复用 DeepSeek 评分。', '',
              f"成本取“小测试外推”和“历史 40 案 token 按当前单价重算”中的较高值：每案约 ${conservative_case_estimate:.4f}；再预留 20% 的重试/长度浮动，并计入本次小测试。", '',
              '| 抽样案件 | 既有回答 | 新 API 评分 | 复用 DS 评分 | 新评分估算 | 含小测试及20%余量 |',
              '|---|---:|---:|---:|---:|---:|']
    for r in projections:
        lines.append(f"| {r['cases']} | {r['answers']:,} | {r['new_ratings']:,} | {r['reused_ds_ratings']:,} | ${r['base_usd']:.2f} | ${r['including_pilot_and_contingency_usd']:.2f} |")
    lines += ['', '正式样本按案由分层、固定随机种子，预先冻结 case_id；不能按模型得分或新评分是否成功来选样。$100 是累计上限，不是必须花完的金额。上述数额不含专家人工复核费用。', '',
              '已有 40 案互评（2,400 次新评分 + 600 次旧 DS 评分）和 250 份专家复核待标注表可供历史对照；不能把它们当作本次新完成的结果。', '',
              '### 输入可复用范围与推荐规模', '',
              '| 108 案主实验中的数据部分 | 当前能够确认的原始输入 |', '|---|---|',
              '| 后续新增20案 + 剩余68案 | 共88案，均保存25份回答及5套按回答条件脱敏的案情/参考判决，可严格复用 |',
              '| 已做互评40案 | 全部包含于上述88案；其余48案尚未做该互评 |',
              '| 最初20案 | 本地找到历史Excel/TXT，但未确认齐备的同口径raw_results和逐回答对应脱敏输入；不能重新脱敏冒充原输入 |', '',
              f"建议先在可严格复用的88案中按案由分层抽60案，作为108案主实验的互评补充；母体范围和缺少原始输入的20案必须如实说明。按较保守外推，60案新评分约${projections[1]['base_usd']:.2f}，包含20%余量及本次小测试约${projections[1]['including_pilot_and_contingency_usd']:.2f}，低于$100。正式样本尚未冻结、尚未开跑。", '',
              f"若选择复用旧40案评分再新增40案，合计覆盖80案，较保守的增量标价约${projections[0]['base_usd']:.2f}，加20%余量及本次小测试约${projections[0]['including_pilot_and_contingency_usd']:.2f}；需要分开记录8月与9月评分批次，不能假定上游快照不变。", '',
              '## 5. 调用问题与执行状态', '',
              'GPT-4o 部分请求返回上游 content_filter；原请求不改写，按既有重试行为执行并保留失败。测试曾因余额查询过密触发 429，已改为批次边界核算额度，逐调用记录 token；原失败尝试已归档，成功结果复用。', '',
              '本次交付为模型核查、小测试原始记录和预算；尚未启动上述正式抽样批次。', '',
              '复现入口：`scripts/pilot_cross_judging_budget_xhub.py`；只读冻结为默认模式，`--execute --pilot-budget-usd 5 --workers 3` 执行小测试。',
              '原始产物：`data/cross_judge_budget_pilot_20260908/` 下的 manifest、source_answers、ratings、http_receipts、prior_attempts、budget_ledger 和 summary。', '']
    path = OUTPUT/'108案_最终互评小测试与100美元预算.md'
    path.write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'report':str(path),'models':table,'budget':projections},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
