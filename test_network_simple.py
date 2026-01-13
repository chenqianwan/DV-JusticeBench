#!/usr/bin/env python3
"""
简单的网络测试脚本：测试API连接和响应时间
"""
import requests
import time
from config import OPENAI_API_KEY, OPENAI_API_URL

def test_api_connection():
    """测试API连接"""
    print("=" * 60)
    print("网络连接测试")
    print("=" * 60)
    
    # 1. 测试基本连接
    print("\n1. 测试基本网络连接...")
    try:
        response = requests.get("https://api3.xhub.chat", timeout=10)
        print(f"✓ 基本连接成功，状态码: {response.status_code}")
    except requests.exceptions.Timeout:
        print("✗ 连接超时（10秒）")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"✗ 连接错误: {str(e)}")
        return False
    except Exception as e:
        print(f"✗ 其他错误: {str(e)}")
        return False
    
    # 2. 测试简单API请求
    print("\n2. 测试简单API请求（短文本）...")
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {OPENAI_API_KEY}'
    }
    
    payload = {
        'model': 'gpt-4o',
        'messages': [
            {'role': 'user', 'content': '你好，请回复"测试成功"'}
        ],
        'max_tokens': 50
    }
    
    start_time = time.time()
    try:
        response = requests.post(
            OPENAI_API_URL,
            headers=headers,
            json=payload,
            timeout=30  # 30秒超时
        )
        elapsed_time = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
            print(f"✓ API请求成功")
            print(f"  响应时间: {elapsed_time:.2f}秒")
            print(f"  响应内容: {content[:100]}")
        else:
            print(f"✗ API请求失败，状态码: {response.status_code}")
            print(f"  响应内容: {response.text[:200]}")
            
    except requests.exceptions.Timeout:
        elapsed_time = time.time() - start_time
        print(f"✗ API请求超时（{elapsed_time:.2f}秒后超时）")
    except Exception as e:
        elapsed_time = time.time() - start_time
        print(f"✗ API请求错误: {str(e)}")
        print(f"  已用时间: {elapsed_time:.2f}秒")
    
    # 3. 测试GPT-o3模型（如果支持）
    print("\n3. 测试GPT-o3模型连接...")
    payload_o3 = {
        'model': 'o3-2025-04-16',
        'messages': [
            {'role': 'user', 'content': '你好'}
        ],
        'max_tokens': 50
    }
    
    start_time = time.time()
    try:
        response = requests.post(
            OPENAI_API_URL,
            headers=headers,
            json=payload_o3,
            timeout=60  # 60秒超时
        )
        elapsed_time = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            print(f"✓ GPT-o3 API请求成功")
            print(f"  响应时间: {elapsed_time:.2f}秒")
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0].get('message', {}).get('content', '')
                print(f"  响应内容: {content[:100]}")
        else:
            print(f"✗ GPT-o3 API请求失败，状态码: {response.status_code}")
            print(f"  响应内容: {response.text[:200]}")
            
    except requests.exceptions.Timeout:
        elapsed_time = time.time() - start_time
        print(f"✗ GPT-o3 API请求超时（{elapsed_time:.2f}秒后超时）")
    except Exception as e:
        elapsed_time = time.time() - start_time
        print(f"✗ GPT-o3 API请求错误: {str(e)}")
        print(f"  已用时间: {elapsed_time:.2f}秒")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == '__main__':
    test_api_connection()
