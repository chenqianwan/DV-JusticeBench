"""
Flask主应用
提供Web界面和API接口
"""
from flask import Flask, render_template, request, jsonify, send_file
import os
import json
import uuid
import threading
import concurrent.futures
from datetime import datetime
from threading import Lock
from utils.ai_api import ai_api
from utils.case_manager import case_manager
from utils.excel_export import excel_exporter
from utils.excel_import import ExcelImporter
from utils.similarity import similarity_calculator
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
from config import RESULTS_DIR, MAX_CONCURRENT_WORKERS
from werkzeug.utils import secure_filename
import tempfile

# 设置信号处理器，确保中断时正确清理
setup_signal_handlers()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')

# 存储分析结果（实际应用中应使用数据库）
analysis_results = []

# Excel导入器
excel_importer = ExcelImporter()

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {'xlsx', 'xls'}

# 全局进度跟踪字典
batch_progress = {}
progress_lock = Lock()


@app.route('/')
def index():
    """主页面"""
    return render_template('index.html')


@app.route('/api/cases', methods=['GET'])
def get_cases():
    """获取所有案例"""
    try:
        cases = case_manager.get_all_cases()
        return jsonify({'success': True, 'cases': cases})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cases', methods=['POST'])
def add_case():
    """添加新案例"""
    try:
        data = request.json
        case_id = case_manager.add_case(
            title=data.get('title', '未命名案例'),
            case_text=data.get('case_text', ''),
            judge_decision=data.get('judge_decision', ''),
            case_date=data.get('case_date', ''),
            metadata=data.get('metadata', {})
        )
        return jsonify({'success': True, 'case_id': case_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cases/<case_id>', methods=['GET'])
def get_case(case_id):
    """获取指定案例"""
    try:
        case = case_manager.get_case(case_id)
        if case:
            return jsonify({'success': True, 'case': case})
        else:
            return jsonify({'success': False, 'error': '案例不存在'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cases/<case_id>', methods=['DELETE'])
def delete_case(case_id):
    """删除案例"""
    try:
        success = case_manager.delete_case(case_id)
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': '案例不存在'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/generate_questions', methods=['POST'])
def generate_questions():
    """生成测试问题（单个案例）"""
    try:
        data = request.json
        case_id = data.get('case_id')
        num_questions = data.get('num_questions', 10)
        
        if not case_id:
            return jsonify({'success': False, 'error': '缺少案例ID'}), 400
        
        case = case_manager.get_case(case_id)
        if not case:
            return jsonify({'success': False, 'error': '案例不存在'}), 404
        
        case_text = case.get('case_text', '')
        if not case_text:
            return jsonify({'success': False, 'error': '案例内容为空'}), 400
        
        # 调用DeepSeek API生成问题
        questions = ai_api.generate_questions(case_text, num_questions)
        
        return jsonify({
            'success': True,
            'questions': questions,
            'case_id': case_id
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/generate_questions_batch', methods=['POST'])
def generate_questions_batch():
    """批量生成问题（多个案例，每个案例生成指定数量的问题）"""
    try:
        data = request.json
        case_ids = data.get('case_ids', [])
        num_questions_per_case = data.get('num_questions_per_case', 10)
        
        if not case_ids:
            return jsonify({'success': False, 'error': '缺少案例ID列表'}), 400
        
        if len(case_ids) == 0:
            return jsonify({'success': False, 'error': '案例ID列表为空'}), 400
        
        # 获取所有案例
        cases = []
        for case_id in case_ids:
            case = case_manager.get_case(case_id)
            if case:
                cases.append((case_id, case))
        
        if not cases:
            return jsonify({'success': False, 'error': '未找到有效的案例'}), 404
        
        # 为每个案例生成问题
        all_questions = []
        questions_by_case = []
        
        for case_id, case_data in cases:
            case_text = case_data.get('case_text', '')
            if not case_text:
                continue
            
            try:
                # 为每个案例生成指定数量的问题
                questions = deepseek_api.generate_questions(case_text, num_questions_per_case)
                
                # 为每个问题添加案例信息
                for q in questions:
                    all_questions.append(q)
                    questions_by_case.append({
                        'case_id': case_id,
                        'case_title': case_data.get('title', ''),
                        'question': q
                    })
            except Exception as e:
                print(f"为案例 {case_data.get('title', case_id)} 生成问题失败: {str(e)}")
                continue
        
        return jsonify({
            'success': True,
            'all_questions': all_questions,
            'questions_by_case': questions_by_case,
            'total_questions': len(all_questions),
            'total_cases': len(cases)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/analyze', methods=['POST'])
def analyze():
    """AI分析案例"""
    try:
        data = request.json
        case_id = data.get('case_id')
        question = data.get('question', '')
        
        if not case_id:
            return jsonify({'success': False, 'error': '缺少案例ID'}), 400
        
        case = case_manager.get_case(case_id)
        if not case:
            return jsonify({'success': False, 'error': '案例不存在'}), 404
        
        case_text = case.get('case_text', '')
        if not case_text:
            return jsonify({'success': False, 'error': '案例内容为空'}), 400
        
        # 调用DeepSeek API分析
        ai_decision = ai_api.analyze_case(case_text, question)
        
        # 获取法官判决
        judge_decision = case.get('judge_decision', '')
        
        # 如果有法官判决，进行对比分析和相似度计算
        comparison = ''
        similarity_metrics = {}
        if judge_decision:
            try:
                comparison = ai_api.compare_decisions(ai_decision, judge_decision)
            except Exception as e:
                comparison = f'对比分析失败: {str(e)}'
            
            # 计算相似度指标
            try:
                similarity_metrics = similarity_calculator.calculate_metrics(
                    ai_decision, judge_decision
                )
            except Exception as e:
                print(f"相似度计算失败: {str(e)}")
                similarity_metrics = {
                    'overall_similarity': 0.0,
                    'keyword_similarity': 0.0,
                    'result_consistency': 0.0,
                    'legal_basis_similarity': 0.0,
                    'reasoning_similarity': 0.0,
                    'has_judge_decision': True
                }
        else:
            similarity_metrics = {
                'overall_similarity': 0.0,
                'keyword_similarity': 0.0,
                'result_consistency': 0.0,
                'legal_basis_similarity': 0.0,
                'reasoning_similarity': 0.0,
                'has_judge_decision': False
            }
        
        # 保存分析结果
        result = {
            'case_id': case_id,
            'case_title': case.get('title', ''),
            'case_text': case_text,
            'question': question,
            'ai_decision': ai_decision,
            'judge_decision': judge_decision,
            'comparison': comparison,
            'similarity_metrics': similarity_metrics,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        analysis_results.append(result)
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/results', methods=['GET'])
def get_results():
    """获取所有分析结果"""
    try:
        return jsonify({'success': True, 'results': analysis_results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/analyze_batch', methods=['POST'])
def analyze_batch():
    """批量分析多个案例（使用线程池并发处理，固定50并发）"""
    try:
        data = request.json
        case_ids = data.get('case_ids', [])
        question = data.get('question', '')
        
        if not case_ids:
            return jsonify({'success': False, 'error': '缺少案例ID列表'}), 400
        
        if len(case_ids) == 0:
            return jsonify({'success': False, 'error': '案例ID列表为空'}), 400
        
        # 生成任务ID
        task_id = str(uuid.uuid4())
        
        # 获取所有案例
        cases = []
        for case_id in case_ids:
            case = case_manager.get_case(case_id)
            if case:
                cases.append((case_id, case))
        
        if not cases:
            return jsonify({'success': False, 'error': '未找到有效的案例'}), 404
        
        # 初始化进度
        with progress_lock:
            batch_progress[task_id] = {
                'total': len(cases),
                'completed': 0,
                'success': 0,
                'failed': 0,
                'status': 'running',  # running, completed, failed
                'results': [],
                'errors': [],
                'start_time': datetime.now().isoformat()
            }
        
        # 使用配置的并发数（固定50）
        max_workers = min(MAX_CONCURRENT_WORKERS, len(cases))  # 不超过案例数量
        
        print(f"[批量分析] 任务 {task_id} 开始处理 {len(cases)} 个案例，并发数: {max_workers}")
        
        # 定义单个案例的分析函数
        def analyze_single_case(case_id, case_data):
            """分析单个案例的辅助函数"""
            try:
                case_text = case_data.get('case_text', '')
                if not case_text:
                    return {'success': False, 'case_id': case_id, 'error': '案例内容为空'}
                
                # 调用DeepSeek API分析
                ai_decision = ai_api.analyze_case(case_text, question)
                
                # 获取法官判决
                judge_decision = case_data.get('judge_decision', '')
                
                # 如果有法官判决，进行对比分析和相似度计算
                comparison = ''
                similarity_metrics = {}
                if judge_decision:
                    try:
                        comparison = ai_api.compare_decisions(ai_decision, judge_decision)
                    except Exception as e:
                        comparison = f'对比分析失败: {str(e)}'
                    
                    # 计算相似度指标
                    try:
                        similarity_metrics = similarity_calculator.calculate_metrics(
                            ai_decision, judge_decision
                        )
                    except Exception as e:
                        print(f"相似度计算失败: {str(e)}")
                        similarity_metrics = {
                            'overall_similarity': 0.0,
                            'keyword_similarity': 0.0,
                            'result_consistency': 0.0,
                            'legal_basis_similarity': 0.0,
                            'reasoning_similarity': 0.0,
                            'has_judge_decision': True
                        }
                else:
                    similarity_metrics = {
                        'overall_similarity': 0.0,
                        'keyword_similarity': 0.0,
                        'result_consistency': 0.0,
                        'legal_basis_similarity': 0.0,
                        'reasoning_similarity': 0.0,
                        'has_judge_decision': False
                    }
                
                # 构建结果
                result = {
                    'case_id': case_id,
                    'case_title': case_data.get('title', ''),
                    'case_text': case_text,
                    'question': question,
                    'ai_decision': ai_decision,
                    'judge_decision': judge_decision,
                    'comparison': comparison,
                    'similarity_metrics': similarity_metrics,
                    'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                
                # 保存分析结果
                analysis_results.append(result)
                
                # 更新进度
                with progress_lock:
                    if task_id in batch_progress:
                        batch_progress[task_id]['completed'] += 1
                        batch_progress[task_id]['success'] += 1
                        batch_progress[task_id]['results'].append(result)
                
                return {'success': True, 'result': result}
                
            except Exception as e:
                # 更新失败计数
                with progress_lock:
                    if task_id in batch_progress:
                        batch_progress[task_id]['completed'] += 1
                        batch_progress[task_id]['failed'] += 1
                        batch_progress[task_id]['errors'].append({
                            'case_id': case_id,
                            'case_title': case_data.get('title', ''),
                            'error': str(e)
                        })
                
                return {'success': False, 'case_id': case_id, 'error': str(e)}
        
        # 使用线程池并发处理
        def run_analysis():
            """在后台线程中运行分析"""
            try:
                with SafeThreadPoolExecutor(max_workers=max_workers) as executor:
                    # 提交所有任务
                    future_to_case = {
                        executor.submit(analyze_single_case, case_id, case_data): (case_id, case_data)
                        for case_id, case_data in cases
                    }
                    
                    # 收集结果
                    for future in concurrent.futures.as_completed(future_to_case):
                        case_id, case_data = future_to_case[future]
                        try:
                            result = future.result()
                            # 进度已在 analyze_single_case 中更新
                        except Exception as e:
                            print(f"[批量分析] 异常: {case_data.get('title', case_id)} - {str(e)}")
                
                # 标记完成
                with progress_lock:
                    if task_id in batch_progress:
                        batch_progress[task_id]['status'] = 'completed'
                        batch_progress[task_id]['end_time'] = datetime.now().isoformat()
                        
                        # 按原始顺序排序结果
                        case_id_order = {case_id: idx for idx, (case_id, _) in enumerate(cases)}
                        batch_progress[task_id]['results'].sort(
                            key=lambda x: case_id_order.get(x['case_id'], 999)
                        )
                
                print(f"[批量分析] 任务 {task_id} 完成")
            except Exception as e:
                with progress_lock:
                    if task_id in batch_progress:
                        batch_progress[task_id]['status'] = 'failed'
                        batch_progress[task_id]['error'] = str(e)
                print(f"[批量分析] 任务 {task_id} 失败: {str(e)}")
        
        # 在后台线程中启动分析
        analysis_thread = threading.Thread(target=run_analysis, daemon=True)
        analysis_thread.start()
        
        # 立即返回任务ID
        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': '批量分析已启动'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/batch_progress/<task_id>', methods=['GET'])
def get_batch_progress(task_id):
    """获取批量分析进度"""
    try:
        with progress_lock:
            if task_id not in batch_progress:
                return jsonify({
                    'success': False,
                    'error': '任务不存在或已过期'
                }), 404
            
            progress = batch_progress[task_id].copy()
            
            # 计算百分比
            if progress['total'] > 0:
                progress['percentage'] = int((progress['completed'] / progress['total']) * 100)
            else:
                progress['percentage'] = 0
            
            # 计算预计剩余时间
            if progress['status'] == 'running' and progress['completed'] > 0:
                elapsed = (datetime.now() - datetime.fromisoformat(progress['start_time'])).total_seconds()
                if elapsed > 0:
                    avg_time_per_case = elapsed / progress['completed']
                    remaining_cases = progress['total'] - progress['completed']
                    estimated_remaining = avg_time_per_case * remaining_cases
                    progress['estimated_remaining_seconds'] = int(estimated_remaining)
                else:
                    progress['estimated_remaining_seconds'] = None
            else:
                progress['estimated_remaining_seconds'] = None
        
        return jsonify({
            'success': True,
            'progress': progress
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export_excel', methods=['POST'])
def export_excel():
    """导出分析结果到Excel"""
    try:
        data = request.json
        results = data.get('results', analysis_results)
        
        if not results:
            return jsonify({'success': False, 'error': '没有可导出的结果'}), 400
        
        # 生成Excel文件
        filepath = excel_exporter.export_analysis_results(results)
        
        # 返回文件
        return send_file(
            filepath,
            as_attachment=True,
            download_name=os.path.basename(filepath),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export_cases', methods=['POST'])
def export_cases():
    """导出案例摘要到Excel"""
    try:
        cases = case_manager.get_all_cases()
        
        if not cases:
            return jsonify({'success': False, 'error': '没有可导出的案例'}), 400
        
        # 生成Excel文件
        filepath = excel_exporter.export_case_summary(cases)
        
        # 返回文件
        return send_file(
            filepath,
            as_attachment=True,
            download_name=os.path.basename(filepath),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/api/download_template', methods=['GET'])
def download_template():
    """下载Excel导入模板"""
    try:
        # 生成模板文件
        filepath = excel_exporter.generate_import_template()
        
        # 返回文件
        return send_file(
            filepath,
            as_attachment=True,
            download_name='案例导入模板.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export_questions', methods=['POST'])
def export_questions():
    """导出生成的问题到Excel"""
    try:
        data = request.json
        questions = data.get('questions', [])
        questions_by_case = data.get('questions_by_case', None)
        
        if not questions and not questions_by_case:
            return jsonify({'success': False, 'error': '没有可导出的问题'}), 400
        
        # 生成Excel文件
        filepath = excel_exporter.export_questions(questions, questions_by_case)
        
        # 返回文件
        return send_file(
            filepath,
            as_attachment=True,
            download_name=os.path.basename(filepath),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/import_cases', methods=['POST'])
def import_cases():
    """批量导入案例（从Excel文件）"""
    try:
        # 检查文件是否存在
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '没有上传文件'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': '不支持的文件格式，请上传 .xlsx 或 .xls 文件'}), 400
        
        # 保存临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
            file.save(tmp_file.name)
            tmp_filepath = tmp_file.name
        
        try:
            # 解析Excel文件
            cases = excel_importer.parse_excel(tmp_filepath)
            
            if not cases:
                return jsonify({'success': False, 'error': 'Excel文件中没有找到有效的案例数据'}), 400
            
            # 验证数据
            validation = excel_importer.validate_cases(cases)
            valid_cases = validation['valid']
            invalid_cases = validation['invalid']
            
            if not valid_cases:
                return jsonify({
                    'success': False,
                    'error': '没有有效的案例数据',
                    'invalid_cases': invalid_cases
                }), 400
            
            # 批量导入有效案例
            imported_count = 0
            imported_cases = []
            for case in valid_cases:
                try:
                    case_id = case_manager.add_case(
                        title=case['title'],
                        case_text=case['case_text'],
                        judge_decision=case.get('judge_decision', ''),
                        case_date=case.get('case_date', ''),
                        metadata={}
                    )
                    imported_cases.append({
                        'case_id': case_id,
                        'title': case['title']
                    })
                    imported_count += 1
                except Exception as e:
                    print(f"导入案例失败: {str(e)}")
            
            return jsonify({
                'success': True,
                'imported_count': imported_count,
                'total_count': len(cases),
                'invalid_count': len(invalid_cases),
                'invalid_cases': invalid_cases,
                'imported_cases': imported_cases
            })
            
        finally:
            # 删除临时文件
            try:
                os.unlink(tmp_filepath)
            except:
                pass
                
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)

