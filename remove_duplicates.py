#!/usr/bin/env python3
"""
删除重复的案例，保留最早的一个
"""
from utils.case_manager import case_manager
from datetime import datetime

def main():
    cases = case_manager.get_all_cases()
    
    # 按标题分组
    title_groups = {}
    for case in cases:
        title = case.get('title', '')
        if title not in title_groups:
            title_groups[title] = []
        title_groups[title].append(case)
    
    # 找出重复的
    duplicates_to_remove = []
    for title, case_list in title_groups.items():
        if len(case_list) > 1:
            # 按创建时间排序，保留最早的
            case_list.sort(key=lambda x: x.get('created_at', ''))
            
            # 保留第一个，删除其他的
            for case in case_list[1:]:
                duplicates_to_remove.append({
                    'id': case['id'],
                    'title': title,
                    'created_at': case.get('created_at', '')
                })
    
    if not duplicates_to_remove:
        print("未发现重复案例")
        return
    
    print(f"发现 {len(duplicates_to_remove)} 个重复案例需要删除：")
    print("=" * 60)
    
    for dup in duplicates_to_remove:
        print(f"删除: {dup['title'][:50]}...")
        print(f"  ID: {dup['id']}")
        print(f"  创建时间: {dup['created_at']}")
        
        # 删除案例
        success = case_manager.delete_case(dup['id'])
        if success:
            print(f"  ✓ 删除成功")
        else:
            print(f"  ✗ 删除失败")
        print()
    
    # 显示最终统计
    remaining_cases = case_manager.get_all_cases()
    print("=" * 60)
    print(f"删除完成！")
    print(f"  删除前: {len(cases)} 个案例")
    print(f"  删除后: {len(remaining_cases)} 个案例")
    print(f"  已删除: {len(duplicates_to_remove)} 个重复案例")

if __name__ == '__main__':
    main()


