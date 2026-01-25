#!/usr/bin/env python3
"""
统一脱敏和问题生成脚本
步骤1: 统一对所有案例进行脱敏
步骤2: 统一生成问题
保存到文件，供所有模型使用
"""
import pandas as pd
import os
import json
from datetime import datetime
from utils.ai_api import UnifiedAIAPI
from utils.data_masking import DataMaskerAPI
from utils.process_cleanup import SafeThreadPoolExecutor
import concurrent.futures

def process_case_masking_questions(case_id, case, index, total):
    """处理单个案例的脱敏和问题生成"""
    print(f"[{index}/{total}] 处理案例: {case_id} - {case['title'][:40]}...", flush=True)
    
    try:
        # 1. 脱敏处理
        masker = DataMaskerAPI()
        case_dict = {
            'title': case['title'],
            'case_text': case.get('content', case.get('case_text', '')),
            'judge_decision': case.get('judge_decision', '')
        }
        
        masked_case = masker.mask_case_with_api(case_dict)
        masked_title = masked_case.get('title_masked', '')
        masked_content = masked_case.get('case_text_masked', '')
        masked_judge = masked_case.get('judge_decision_masked', '')
        
        # 2. 生成问题
        deepseek_api = UnifiedAIAPI(provider='deepseek')
        questions = deepseek_api.generate_questions(masked_content, num_questions=5)
        
        print(f"[{index}/{total}] ✓ 完成: {case_id}", flush=True)
        
        return {
            'case_id': case_id,
            'case_title': case['title'],
            'masked_title': masked_title,
            'masked_content': masked_content,
            'masked_judge': masked_judge,
            'questions': questions
        }
    except Exception as e:
        print(f"[{index}/{total}] ✗ 失败: {case_id} - {str(e)}", flush=True)
        return None

def main():
    # 20个案例列表：16个没有被拒绝的案例 + 4个新案例
    # 敏感案例（已排除）：case_20260103_155150_0, 1, 2, 3
    # 16个没有被拒绝的案例（从20个案例中去掉4个敏感案例）：
    case_ids_16 = [
        'case_20251230_134952_90', 'case_20251230_134952_91', 'case_20251230_134952_92',
        'case_20251230_134952_93', 'case_20251230_134952_94', 'case_20251230_134952_95',
        'case_20251230_134952_96', 'case_20251230_134952_97', 'case_20251230_134952_98',
        'case_20251230_134952_99',
        'case_20260103_155150_4', 'case_20260103_155150_5', 'case_20260103_155150_6',
        'case_20260103_155150_7', 'case_20260103_155150_105', 'case_20260103_155150_106'
    ]
    
    # 4个新案例（从最开始的案例列表中选择，之前5个案例测试用的是90-94）
    # 注意：如果这4个新案例已经在16个里面，需要替换掉16个中的某些案例
    # 这里假设用户想用这4个新案例替换掉16个中的某些案例，或者直接添加（如果不在16个中）
    # 用户可以根据实际情况修改这个列表
    new_case_ids = [
        'case_20251230_134952_95',  # 如果已经在16个中，会被去重
        'case_20251230_134952_96',
        'case_20251230_134952_97',
        'case_20251230_134952_98',
    ]
    
    # 合并为20个案例（去重）
    all_case_ids = list(set(case_ids_16 + new_case_ids))
    
    # 如果去重后不是20个，说明有重复，使用16个案例
    if len(all_case_ids) < 20:
        print(f"⚠️ 注意: 案例列表去重后有 {len(all_case_ids)} 个（少于20个）", flush=True)
        print(f"  16个案例: {len(case_ids_16)} 个", flush=True)
        print(f"  4个新案例: {len(new_case_ids)} 个", flush=True)
        print(f"  合并后: {len(all_case_ids)} 个", flush=True)
        print(f"  将使用合并后的 {len(all_case_ids)} 个案例", flush=True)
    elif len(all_case_ids) > 20:
        print(f"⚠️ 警告: 案例列表去重后有 {len(all_case_ids)} 个（多于20个）", flush=True)
        print(f"  将使用前20个案例", flush=True)
        all_case_ids = all_case_ids[:20]
    
    print("=" * 80)
    print("统一脱敏和问题生成脚本")
    print("=" * 80)
    print(f"将处理 {len(all_case_ids)} 个案例")
    print(f"已排除敏感案例: case_20260103_155150_0, 1, 2, 3")
    print("=" * 80)
    
    # 加载案例数据
    with open('data/cases/cases.json', 'r', encoding='utf-8') as f:
        all_cases = json.load(f)
    
    selected_cases = {}
    for case_id in all_case_ids:
        if case_id in all_cases:
            selected_cases[case_id] = all_cases[case_id]
        else:
            print(f"⚠️ 警告: 案例 {case_id} 不在 cases.json 中", flush=True)
    
    if len(selected_cases) != len(all_case_ids):
        print(f"⚠️ 警告: 只找到 {len(selected_cases)}/{len(all_case_ids)} 个案例", flush=True)
    
    print(f"\n开始处理 {len(selected_cases)} 个案例...\n")
    
    # 并发处理
    results = []
    with SafeThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(process_case_masking_questions, case_id, case, i+1, len(selected_cases)): case_id
            for i, (case_id, case) in enumerate(selected_cases.items())
        }
        
        for future in concurrent.futures.as_completed(futures):
            case_id = futures[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                print(f"✗ 案例 {case_id} 处理异常: {str(e)}", flush=True)
    
    # 保存结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/unified_masking_questions_{timestamp}.json'
    
    output_data = {}
    for result in results:
        output_data[result['case_id']] = {
            'case_title': result['case_title'],
            'masked_title': result['masked_title'],
            'masked_content': result['masked_content'],
            'masked_judge': result['masked_judge'],
            'questions': result['questions']
        }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print("\n" + "=" * 80)
    print("完成")
    print("=" * 80)
    print(f"成功处理: {len(results)}/{len(selected_cases)} 个案例")
    print(f"结果已保存到: {output_file}")
    print(f"\n使用方式:")
    print(f"  python3 process_cases.py --model <model> --use_unified_data {output_file} --case_ids {' '.join(all_case_ids)}")
    
    return output_file

if __name__ == '__main__':
    main()
