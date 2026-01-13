#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试Claude API
"""
import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(__file__))

try:
    from utils.unified_model_api import UnifiedModelAPI
    from utils.ai_api import UnifiedAIAPI
    print("✓ 导入成功")
    
    # 测试1: 直接创建UnifiedModelAPI实例（Claude模型）
    print("\n测试1: 创建UnifiedModelAPI实例（Claude模型）...")
    claude_api = UnifiedModelAPI(model='claude-opus-4-20250514')
    print(f"✓ UnifiedModelAPI创建成功，模型: {claude_api.model}")
    print(f"  API URL: {claude_api.api_url}")
    print(f"  是Claude: {claude_api.is_claude}")
    print(f"  API Key: {'已设置' if claude_api.api_key else '未设置（需要在.env中配置ANTHROPIC_API_KEY）'}")
    
    # 测试2: 通过UnifiedAIAPI创建
    print("\n测试2: 通过UnifiedAIAPI创建...")
    unified_api = UnifiedAIAPI(provider='claude', model='claude-opus-4-20250514')
    print(f"✓ UnifiedAIAPI创建成功，提供商: {unified_api.api_name}")
    
    # 测试3: 测试analyze_case方法（不实际调用API，只检查方法存在）
    print("\n测试3: 检查方法...")
    if hasattr(claude_api, 'analyze_case'):
        print("✓ analyze_case方法存在")
    if hasattr(claude_api, 'generate_questions'):
        print("✓ generate_questions方法存在")
    
    print("\n" + "=" * 80)
    print("✓ 所有测试通过！")
    print("=" * 80)
    print("\n注意：要实际调用API，需要在.env文件中设置ANTHROPIC_API_KEY")
    print("然后运行: python process_cases.py --model claude --num_cases 1")
    
except Exception as e:
    print(f"✗ 错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
