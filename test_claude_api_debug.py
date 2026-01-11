#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试Claude API调用，调试400错误
"""
import sys
import os
import json

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(__file__))

from utils.unified_model_api import UnifiedModelAPI

def test_claude_api():
    """测试Claude API调用"""
    print("=" * 80)
    print("测试Claude API调用")
    print("=" * 80)
    
    # 创建API实例
    api = UnifiedModelAPI(model='claude-opus-4-20250514')
    print(f"模型: {api.model}")
    print(f"API URL: {api.api_url}")
    print(f"API Key: {'已设置' if api.api_key else '未设置'}")
    print()
    
    # 测试简单的API调用
    print("测试1: 简单消息调用...")
    try:
        messages = [
            {"role": "system", "content": "你是一位专业的法律专家。"},
            {"role": "user", "content": "请简单介绍一下法律分析的基本步骤。"}
        ]
        
        print(f"发送请求...")
        print(f"  URL: {api.api_url}")
        print(f"  模型: {api.model}")
        print(f"  消息数: {len(messages)}")
        
        response = api._make_request(messages, temperature=0.7, max_tokens=200)
        
        if response:
            print("✓ API调用成功")
            if 'choices' in response and len(response['choices']) > 0:
                content = response['choices'][0]['message']['content']
                print(f"  响应长度: {len(content)} 字符")
                print(f"  响应预览: {content[:100]}...")
            else:
                print(f"  响应格式: {json.dumps(response, ensure_ascii=False, indent=2)[:500]}")
        else:
            print("✗ API调用返回None")
            
    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("=" * 80)

if __name__ == '__main__':
    test_claude_api()
