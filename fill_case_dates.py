"""
从案例内容中提取日期并补齐到Excel文件
"""
import pandas as pd
import re
from datetime import datetime
import os


def _chinese_to_arabic(char):
    """将中文数字转换为阿拉伯数字"""
    chinese_to_arabic_map = {
        '〇': '0', '零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
        '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'
    }
    return chinese_to_arabic_map.get(char, char)


def extract_date_from_text(text: str) -> str:
    """
    从文本中提取日期
    
    Args:
        text: 案例文本内容
        
    Returns:
        日期字符串，格式：YYYY-MM-DD，如果未找到则返回空字符串
    """
    if not text or pd.isna(text):
        return ""
    
    text = str(text)
    case_date = ""
    
    # 方法1: 尝试匹配中文数字日期（如：二〇二五年十月三十一日）
    chinese_date_pattern = r'[二三四五六七八九〇零一二三四五六七八九十]+年[一二三四五六七八九十]+月[一二三四五六七八九十]+[日号]'
    chinese_date_match = re.search(chinese_date_pattern, text)
    if chinese_date_match:
        chinese_date = chinese_date_match.group(0)
        # 转换中文数字为阿拉伯数字
        chinese_to_arabic_map = {
            '〇': '0', '零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
            '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'
        }
        
        # 提取年份
        year_match = re.search(r'([二三四五六七八九〇零一二三四五六七八九十]+)年', chinese_date)
        if year_match:
            year_chinese = year_match.group(1)
            year = ''.join([chinese_to_arabic_map.get(c, c) for c in year_chinese])
            if len(year) == 4:
                # 提取月份
                month_match = re.search(r'年([一二三四五六七八九十]+)月', chinese_date)
                if month_match:
                    month_chinese = month_match.group(1)
                    if month_chinese == '十':
                        month = '10'
                    elif month_chinese.startswith('十'):
                        month = '1' + ''.join([chinese_to_arabic_map.get(c, '') for c in month_chinese[1:]])
                    elif month_chinese == '十一':
                        month = '11'
                    elif month_chinese == '十二':
                        month = '12'
                    else:
                        month = chinese_to_arabic_map.get(month_chinese, '1')
                    
                    # 提取日期
                    day_match = re.search(r'月([一二三四五六七八九十]+)[日号]', chinese_date)
                    if day_match:
                        day_chinese = day_match.group(1)
                        if day_chinese == '十':
                            day = '10'
                        elif day_chinese.startswith('十') and len(day_chinese) > 1:
                            if day_chinese == '十一':
                                day = '11'
                            elif day_chinese == '十二':
                                day = '12'
                            elif day_chinese == '十三':
                                day = '13'
                            elif day_chinese == '十四':
                                day = '14'
                            elif day_chinese == '十五':
                                day = '15'
                            elif day_chinese == '十六':
                                day = '16'
                            elif day_chinese == '十七':
                                day = '17'
                            elif day_chinese == '十八':
                                day = '18'
                            elif day_chinese == '十九':
                                day = '19'
                            else:
                                day = '1' + chinese_to_arabic_map.get(day_chinese[-1], '0')
                        elif day_chinese.startswith('二') and len(day_chinese) > 1:
                            if day_chinese == '二十':
                                day = '20'
                            elif day_chinese == '二十一':
                                day = '21'
                            elif day_chinese == '二十二':
                                day = '22'
                            elif day_chinese == '二十三':
                                day = '23'
                            elif day_chinese == '二十四':
                                day = '24'
                            elif day_chinese == '二十五':
                                day = '25'
                            elif day_chinese == '二十六':
                                day = '26'
                            elif day_chinese == '二十七':
                                day = '27'
                            elif day_chinese == '二十八':
                                day = '28'
                            elif day_chinese == '二十九':
                                day = '29'
                            else:
                                day = '2' + chinese_to_arabic_map.get(day_chinese[-1], '0')
                        elif day_chinese.startswith('三') and len(day_chinese) > 1:
                            if day_chinese == '三十':
                                day = '30'
                            elif day_chinese == '三十一':
                                day = '31'
                            else:
                                day = '3' + chinese_to_arabic_map.get(day_chinese[-1], '0')
                        else:
                            day = chinese_to_arabic_map.get(day_chinese, '1')
                        
                        case_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    # 方法2: 如果中文日期未匹配，尝试匹配阿拉伯数字日期
    if not case_date:
        # 优先从文档末尾查找日期（通常是判决日期）
        # 处理年份和"年"之间可能有空格的情况（如"2023 年5月9日"）
        date_pattern = r'(\d{4})\s*年\s*(\d{1,2})月\s*(\d{1,2})日'
        # 从后往前搜索，找到最后一个匹配的日期
        matches = list(re.finditer(date_pattern, text))
        if matches:
            # 使用最后一个匹配（通常是判决日期）
            date_match = matches[-1]
            year, month, day = date_match.groups()
            case_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        
        # 如果还没找到，尝试更宽松的模式（包括斜杠和横杠分隔符）
        if not case_date:
            date_pattern2 = r'(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})[日]?'
            matches2 = list(re.finditer(date_pattern2, text))
            if matches2:
                date_match = matches2[-1]
                year, month, day = date_match.groups()
                case_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    # 方法3: 如果还是没找到，尝试从案例内容开头查找（可能是案件发生日期）
    if not case_date:
        # 查找开头的日期模式（如：2018年8月、2022年7月等，处理空格）
        date_pattern_start = r'(\d{4})\s*年\s*(\d{1,2})月'
        match_start = re.search(date_pattern_start, text[:500])  # 只查找前500字符
        if match_start:
            year, month = match_start.groups()
            # 如果没有具体日期，使用1日作为默认值
            case_date = f"{year}-{month.zfill(2)}-01"
    
    return case_date


def fill_case_dates(excel_file: str, output_file: str = None):
    """
    从案例内容中提取日期并补齐到Excel文件
    
    Args:
        excel_file: 输入的Excel文件路径
        output_file: 输出的Excel文件路径，如果不提供则覆盖原文件
    """
    print('=' * 80)
    print('从案例内容中提取日期并补齐')
    print('=' * 80)
    print()
    
    # 读取Excel文件
    if not os.path.exists(excel_file):
        print(f"错误：找不到文件 {excel_file}")
        return
    
    print(f"读取文件: {excel_file}")
    df = pd.read_excel(excel_file)
    print(f"总行数: {len(df)}")
    print()
    
    # 检查必要的列
    required_columns = ['案例内容', '案例日期']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"错误：缺少必要的列: {missing_columns}")
        return
    
    # 统计需要补齐的行数
    empty_dates = df['案例日期'].isna() | (df['案例日期'] == '')
    print(f"需要补齐日期的行数: {empty_dates.sum()}")
    print()
    
    # 提取日期并补齐
    filled_count = 0
    failed_count = 0
    
    print("开始提取日期...")
    print('-' * 80)
    
    for idx, row in df.iterrows():
        if empty_dates.iloc[idx]:
            case_content = row.get('案例内容', '')
            case_title = row.get('案例标题', '')
            
            # 尝试从案例内容中提取日期
            extracted_date = extract_date_from_text(case_content)
            
            if extracted_date:
                df.at[idx, '案例日期'] = extracted_date
                filled_count += 1
                print(f"✓ [{idx+1}] {case_title[:30]}... -> {extracted_date}")
            else:
                failed_count += 1
                print(f"✗ [{idx+1}] {case_title[:30]}... -> 未找到日期")
    
    print()
    print('-' * 80)
    print(f"完成！")
    print(f"  成功补齐: {filled_count} 个")
    print(f"  未找到日期: {failed_count} 个")
    print()
    
    # 保存文件
    if output_file is None:
        # 生成备份文件名
        base_name = os.path.splitext(excel_file)[0]
        ext = os.path.splitext(excel_file)[1]
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"{base_name}_日期补齐_{timestamp}{ext}"
    
    print(f"保存到: {output_file}")
    df.to_excel(output_file, index=False)
    print("✓ 保存完成！")
    print()
    
    # 显示统计信息
    print('=' * 80)
    print('统计信息:')
    print('=' * 80)
    total_rows = len(df)
    has_date = df['案例日期'].notna() & (df['案例日期'] != '')
    print(f"总行数: {total_rows}")
    print(f"已有日期: {has_date.sum()} 个")
    print(f"缺少日期: {(~has_date).sum()} 个")
    print(f"日期完整率: {has_date.sum() / total_rows * 100:.1f}%")


if __name__ == '__main__':
    excel_file = 'data/1.3号案例内容提取_v3.xlsx'
    fill_case_dates(excel_file)

