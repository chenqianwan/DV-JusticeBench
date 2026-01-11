// 全局状态
let currentCases = [];
let currentResults = [];
let selectedCaseIds = []; // 改为数组支持多选
let currentTaskId = null;
let progressInterval = null;

// DOM加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    initTabs();
    initCaseForm();
    initAnalysis();
    loadCases();
    loadResults();
});

// 标签页切换
function initTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetTab = btn.getAttribute('data-tab');
            
            // 移除所有活动状态
            tabBtns.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            
            // 添加活动状态
            btn.classList.add('active');
            document.getElementById(`${targetTab}-tab`).classList.add('active');
        });
    });
}

// 案例表单
function initCaseForm() {
    const form = document.getElementById('case-form');
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const caseData = {
            title: document.getElementById('case-title').value,
            case_text: document.getElementById('case-text').value,
            judge_decision: document.getElementById('judge-decision').value,
            case_date: document.getElementById('case-date').value || new Date().toISOString().split('T')[0]
        };

        try {
            showLoading();
            const response = await fetch('/api/cases', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(caseData)
            });

            const data = await response.json();
            
            if (data.success) {
                showMessage('案例添加成功！', 'success');
                form.reset();
                loadCases();
            } else {
                showMessage('添加失败: ' + data.error, 'error');
            }
        } catch (error) {
            showMessage('请求失败: ' + error.message, 'error');
        } finally {
            hideLoading();
        }
    });

    // 刷新案例列表
    document.getElementById('refresh-cases').addEventListener('click', loadCases);
    
    // 下载导入模板
    document.getElementById('download-template').addEventListener('click', async () => {
        try {
            showLoading('正在生成模板...');
            const response = await fetch('/api/download_template', {
                method: 'GET'
            });

            if (response.ok) {
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = '案例导入模板.xlsx';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                showMessage('模板下载成功！', 'success');
            } else {
                const data = await response.json();
                showMessage('下载失败: ' + (data.error || '未知错误'), 'error');
            }
        } catch (error) {
            showMessage('请求失败: ' + error.message, 'error');
        } finally {
            hideLoading();
        }
    });

    // Excel文件选择
    const excelFileInput = document.getElementById('excel-file-input');
    const uploadExcelBtn = document.getElementById('upload-excel');
    const importStatus = document.getElementById('import-status');

    excelFileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            uploadExcelBtn.disabled = false;
            importStatus.style.display = 'block';
            importStatus.innerHTML = `<span style="color: #666;">已选择文件: ${file.name}</span>`;
        } else {
            uploadExcelBtn.disabled = true;
            importStatus.style.display = 'none';
        }
    });

    // 上传并导入Excel
    uploadExcelBtn.addEventListener('click', async () => {
        const file = excelFileInput.files[0];
        if (!file) {
            showMessage('请先选择Excel文件', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        try {
            showLoading('正在导入案例，请稍候...');
            importStatus.style.display = 'block';
            importStatus.innerHTML = '<span style="color: #667eea;">正在上传并解析文件...</span>';

            const response = await fetch('/api/import_cases', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (data.success) {
                let statusHtml = `<div style="color: #28a745; font-weight: 500;">`;
                statusHtml += `✅ 导入成功！<br>`;
                statusHtml += `成功导入: ${data.imported_count} 个案例<br>`;
                statusHtml += `总计: ${data.total_count} 个案例<br>`;
                if (data.invalid_count > 0) {
                    statusHtml += `<span style="color: #dc3545;">无效案例: ${data.invalid_count} 个</span><br>`;
                    if (data.invalid_cases && data.invalid_cases.length > 0) {
                        statusHtml += `<details style="margin-top: 10px;"><summary style="cursor: pointer; color: #dc3545;">查看无效案例详情</summary><ul style="margin-top: 5px;">`;
                        data.invalid_cases.forEach(invalid => {
                            statusHtml += `<li>第${invalid.row}行 "${invalid.title}": ${invalid.errors.join(', ')}</li>`;
                        });
                        statusHtml += `</ul></details>`;
                    }
                }
                statusHtml += `</div>`;
                importStatus.innerHTML = statusHtml;
                showMessage(`成功导入 ${data.imported_count} 个案例！`, 'success');
                
                // 清空文件选择
                excelFileInput.value = '';
                uploadExcelBtn.disabled = true;
                
                // 刷新案例列表
                loadCases();
            } else {
                let errorMsg = data.error || '导入失败';
                if (data.invalid_cases && data.invalid_cases.length > 0) {
                    errorMsg += '<br>无效案例详情：<ul>';
                    data.invalid_cases.forEach(invalid => {
                        errorMsg += `<li>第${invalid.row}行: ${invalid.errors.join(', ')}</li>`;
                    });
                    errorMsg += '</ul>';
                }
                importStatus.innerHTML = `<div style="color: #dc3545;">❌ ${errorMsg}</div>`;
                showMessage('导入失败: ' + data.error, 'error');
            }
        } catch (error) {
            importStatus.innerHTML = `<div style="color: #dc3545;">❌ 请求失败: ${error.message}</div>`;
            showMessage('请求失败: ' + error.message, 'error');
        } finally {
            hideLoading();
        }
    });
    
    // 导出案例
    document.getElementById('export-cases').addEventListener('click', async () => {
        try {
            showLoading();
            const response = await fetch('/api/export_cases', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            if (response.ok) {
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `cases_${new Date().toISOString().split('T')[0]}.xlsx`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                showMessage('案例导出成功！', 'success');
            } else {
                const data = await response.json();
                showMessage('导出失败: ' + data.error, 'error');
            }
        } catch (error) {
            showMessage('导出失败: ' + error.message, 'error');
        } finally {
            hideLoading();
        }
    });
}

// 加载案例列表
async function loadCases() {
    try {
        showLoading();
        const response = await fetch('/api/cases');
        const data = await response.json();
        
        if (data.success) {
            currentCases = data.cases;
            renderCases(data.cases);
            updateCaseSelect(data.cases);
        } else {
            showMessage('加载失败: ' + data.error, 'error');
        }
    } catch (error) {
        showMessage('请求失败: ' + error.message, 'error');
    } finally {
        hideLoading();
    }
}

// 渲染案例列表
function renderCases(cases) {
    const container = document.getElementById('cases-list');
    
    if (cases.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: #666; padding: 40px;">暂无案例，请添加新案例</p>';
        return;
    }

    container.innerHTML = cases.map(case_ => `
        <div class="case-card">
            <h3>${escapeHtml(case_.title)}</h3>
            <div class="meta">
                <span>日期: ${case_.case_date || '未设置'}</span> | 
                <span>创建时间: ${new Date(case_.created_at).toLocaleString('zh-CN')}</span>
            </div>
            <div class="content">${escapeHtml(case_.case_text.substring(0, 200))}${case_.case_text.length > 200 ? '...' : ''}</div>
            <div class="actions">
                <button class="btn btn-primary" onclick="selectCaseForAnalysis('${case_.id}')">选择分析</button>
                <button class="btn btn-danger" onclick="deleteCase('${case_.id}')">删除</button>
            </div>
        </div>
    `).join('');
}

// 更新案例选择复选框列表
function updateCaseSelect(cases) {
    const container = document.getElementById('case-checkbox-list');
    
    if (cases.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: #999; padding: 20px;">暂无案例，请先添加案例</p>';
        return;
    }
    
    container.innerHTML = cases.map(case_ => `
        <div class="case-checkbox-item" style="padding: 12px; margin-bottom: 8px; background: white; border-left: 3px solid #8b7355; cursor: pointer; transition: all 0.2s;">
            <label style="display: flex; align-items: center; cursor: pointer; margin: 0;">
                <input type="checkbox" value="${case_.id}" class="case-checkbox" style="margin-right: 12px; width: 18px; height: 18px; cursor: pointer;">
                <div style="flex: 1;">
                    <div style="font-weight: 600; color: #1a1a2e; margin-bottom: 4px;">${escapeHtml(case_.title)}</div>
                    <div style="font-size: 12px; color: #666;">${escapeHtml(case_.case_text.substring(0, 100))}${case_.case_text.length > 100 ? '...' : ''}</div>
                </div>
            </label>
        </div>
    `).join('');
    
    // 添加复选框事件监听
    const checkboxes = container.querySelectorAll('.case-checkbox');
    checkboxes.forEach(checkbox => {
        checkbox.addEventListener('change', updateSelectedCases);
    });
    
    // 添加点击整个区域也可以选择
    container.querySelectorAll('.case-checkbox-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.type !== 'checkbox') {
                const checkbox = item.querySelector('.case-checkbox');
                checkbox.checked = !checkbox.checked;
                updateSelectedCases();
            }
        });
    });
    
    // 全选按钮
    document.getElementById('select-all-cases').addEventListener('click', () => {
        checkboxes.forEach(cb => cb.checked = true);
        updateSelectedCases();
    });
    
    // 清空按钮
    document.getElementById('clear-all-cases').addEventListener('click', () => {
        checkboxes.forEach(cb => cb.checked = false);
        updateSelectedCases();
    });
}

// 更新选中的案例列表
function updateSelectedCases() {
    const checkboxes = document.querySelectorAll('.case-checkbox:checked');
    selectedCaseIds = Array.from(checkboxes).map(cb => cb.value);
    
    const countSpan = document.getElementById('selected-count');
    countSpan.textContent = `已选择: ${selectedCaseIds.length} 个案例`;
    countSpan.style.color = selectedCaseIds.length > 0 ? '#1a1a2e' : '#666';
    countSpan.style.fontWeight = selectedCaseIds.length > 0 ? '600' : '400';
}

// 选择案例进行分析
function selectCaseForAnalysis(caseId) {
    // 如果还没有选中，则选中；如果已选中，则取消选中
    const checkbox = document.querySelector(`.case-checkbox[value="${caseId}"]`);
    if (checkbox) {
        checkbox.checked = !checkbox.checked;
        updateSelectedCases();
    } else {
        // 如果复选框还没加载，先切换到分析标签页
        selectedCaseIds = [caseId];
    }
    
    // 切换到分析标签页
    document.querySelector('[data-tab="analysis"]').click();
    
    // 等待复选框加载后选中
    setTimeout(() => {
        const checkbox = document.querySelector(`.case-checkbox[value="${caseId}"]`);
        if (checkbox) {
            checkbox.checked = true;
            updateSelectedCases();
        }
    }, 100);
}

// 删除案例
async function deleteCase(caseId) {
    if (!confirm('确定要删除这个案例吗？')) {
        return;
    }

    try {
        showLoading();
        const response = await fetch(`/api/cases/${caseId}`, {
            method: 'DELETE'
        });

        const data = await response.json();
        
        if (data.success) {
            showMessage('案例删除成功！', 'success');
            loadCases();
        } else {
            showMessage('删除失败: ' + data.error, 'error');
        }
    } catch (error) {
        showMessage('请求失败: ' + error.message, 'error');
    } finally {
        hideLoading();
    }
}

// 初始化分析功能
function initAnalysis() {
    // 生成问题
    document.getElementById('generate-questions-btn').addEventListener('click', async () => {
        if (selectedCaseIds.length === 0) {
            showMessage('请先选择至少一个案例', 'error');
            return;
        }

        const numQuestionsPerCase = parseInt(document.getElementById('num-questions').value) || 10;

        try {
            if (selectedCaseIds.length === 1) {
                // 单个案例，使用原有API
                showLoading('正在生成测试问题，请稍候...');
                const response = await fetch('/api/generate_questions', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        case_id: selectedCaseIds[0],
                        num_questions: numQuestionsPerCase
                    })
                });

                const data = await response.json();
                
                if (data.success) {
                    renderQuestions(data.questions);
                    if (data.questions.length <= 5) {
                        showMessage(`成功生成${data.questions.length}个问题！`, 'success');
                    } else {
                        showMessage(`已生成${data.questions.length}个问题，已自动导出到Excel！`, 'success');
                    }
                } else {
                    showMessage('生成失败: ' + data.error, 'error');
                }
            } else {
                // 多个案例，每个案例生成指定数量的问题
                showLoading(`正在为${selectedCaseIds.length}个案例批量生成问题，请稍候...`);
                
                const response = await fetch('/api/generate_questions_batch', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        case_ids: selectedCaseIds,
                        num_questions_per_case: numQuestionsPerCase
                    })
                });

                const data = await response.json();
                
                if (data.success) {
                    const totalQuestions = data.all_questions.length;
                    // 保存问题和案例信息
                    currentQuestions = data.all_questions;
                    currentQuestionsByCase = data.questions_by_case || null;
                    
                    if (totalQuestions > 5) {
                        // 总问题数超过5个，自动导出
                        await exportQuestionsToExcel();
                        document.getElementById('questions-container').innerHTML = `
                            <div style="padding: 20px; background: #d4edda; border-left: 4px solid #28a745; border-radius: 4px; text-align: center;">
                                <strong style="color: #155724; font-size: 16px;">✅ 已为${selectedCaseIds.length}个案例生成${totalQuestions}个问题</strong>
                                <p style="margin: 12px 0 0 0; color: #155724; font-size: 14px;">
                                    问题数量较多，已自动导出到Excel文件，请查看下载的文件
                                </p>
                            </div>
                        `;
                        showMessage(`已生成${totalQuestions}个问题，已自动导出到Excel！`, 'success');
                    } else {
                        // 总问题数不超过5个，正常显示
                        renderQuestions(data.all_questions);
                        showMessage(`成功为${selectedCaseIds.length}个案例生成${totalQuestions}个问题！`, 'success');
                    }
                } else {
                    showMessage('生成失败: ' + data.error, 'error');
                }
            }
        } catch (error) {
            showMessage('请求失败: ' + error.message, 'error');
        } finally {
            hideLoading();
        }
    });

    // 执行分析
    document.getElementById('analyze-btn').addEventListener('click', async () => {
        if (selectedCaseIds.length === 0) {
            showMessage('请先选择至少一个案例', 'error');
            return;
        }

        const question = document.getElementById('analysis-question').value.trim();

        try {
            if (selectedCaseIds.length === 1) {
                // 单个案例分析
                showLoading('正在调用DeepSeek API进行分析，这可能需要30-90秒，请耐心等待...');
                const response = await fetch('/api/analyze', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        case_id: selectedCaseIds[0],
                        question: question
                    })
                });

                const data = await response.json();
                
                if (data.success) {
                    renderAnalysisResult(data.result);
                    showMessage('分析完成！', 'success');
                    loadResults();
                } else {
                    showMessage('分析失败: ' + data.error, 'error');
                }
            } else {
                // 多个案例分析 - 使用批量API（后台固定50并发）
                showLoading(`正在启动批量分析${selectedCaseIds.length}个案例...`);
                
                const response = await fetch('/api/analyze_batch', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        case_ids: selectedCaseIds,
                        question: question
                    })
                });

                const data = await response.json();
                
                if (data.success && data.task_id) {
                    // 保存任务ID
                    currentTaskId = data.task_id;
                    
                    // 显示进度容器
                    document.getElementById('batch-progress-container').style.display = 'block';
                    
                    // 隐藏加载提示
                    hideLoading();
                    
                    // 开始轮询进度
                    startProgressPolling(data.task_id);
                } else {
                    showMessage('批量分析启动失败: ' + (data.error || '未知错误'), 'error');
                    hideLoading();
                }
            }
        } catch (error) {
            showMessage('请求失败: ' + error.message, 'error');
        } finally {
            hideLoading();
        }
    });
}

// 存储完整问题列表（用于导出）
let currentQuestions = [];

// 渲染问题列表
function renderQuestions(questions) {
    const container = document.getElementById('questions-container');
    currentQuestions = questions; // 保存完整列表
    
    // 如果问题数超过5个，直接导出到Excel，不显示
    const MAX_DISPLAY_QUESTIONS = 5;
    if (questions.length > MAX_DISPLAY_QUESTIONS) {
        // 自动导出到Excel
        exportQuestionsToExcel();
        container.innerHTML = `
            <div style="padding: 20px; background: #d4edda; border-left: 4px solid #28a745; border-radius: 4px; text-align: center;">
                <strong style="color: #155724; font-size: 16px;">✅ 已生成${questions.length}个问题</strong>
                <p style="margin: 12px 0 0 0; color: #155724; font-size: 14px;">
                    问题数量较多，已自动导出到Excel文件，请查看下载的文件
                </p>
            </div>
        `;
        return;
    }
    
    // 问题数不超过5个，正常显示
    let html = questions.map((q, index) => `
        <div class="question-item" onclick="selectQuestion('${escapeHtml(q)}')">
            ${index + 1}. ${escapeHtml(q)}
        </div>
    `).join('');
    
    container.innerHTML = html;
}

// 选择问题
function selectQuestion(question) {
    document.getElementById('analysis-question').value = question;
}

// 渲染分析结果
function renderAnalysisResult(result) {
    const container = document.getElementById('analysis-result');
    const MAX_DISPLAY_LENGTH = 2000; // 最大显示长度（字符数）
    
    // 检查是否需要省略
    const aiDecisionFull = result.ai_decision || '';
    const judgeDecisionFull = result.judge_decision || '';
    const comparisonFull = result.comparison || '';
    
    const totalLength = aiDecisionFull.length + judgeDecisionFull.length + comparisonFull.length;
    const needsTruncation = totalLength > MAX_DISPLAY_LENGTH;
    
    // 截断显示
    const truncateText = (text, maxLen) => {
        if (!text || text.length <= maxLen) return text;
        return text.substring(0, maxLen) + '...';
    };
    
    // 计算各部分显示长度（按比例分配）
    const aiLen = aiDecisionFull.length;
    const judgeLen = judgeDecisionFull.length;
    const compLen = comparisonFull.length;
    const total = aiLen + judgeLen + compLen;
    
    let aiDisplay = aiDecisionFull;
    let judgeDisplay = judgeDecisionFull;
    let compDisplay = comparisonFull;
    
    if (needsTruncation && total > 0) {
        const aiMax = Math.floor((aiLen / total) * MAX_DISPLAY_LENGTH);
        const judgeMax = Math.floor((judgeLen / total) * MAX_DISPLAY_LENGTH);
        const compMax = MAX_DISPLAY_LENGTH - aiMax - judgeMax;
        
        aiDisplay = truncateText(aiDecisionFull, aiMax);
        judgeDisplay = truncateText(judgeDecisionFull, judgeMax);
        compDisplay = truncateText(comparisonFull, compMax);
    }
    
    container.innerHTML = `
        <div style="margin-bottom: 20px;">
            <h3>AI分析结果</h3>
            ${needsTruncation && aiDecisionFull.length > (aiDisplay.length - 3) ? `
                <div style="padding: 12px; background: #fff3cd; border-left: 4px solid #ffc107; margin-bottom: 15px; border-radius: 4px;">
                    <strong style="color: #856404;">⚠️ 内容较长，已省略部分显示</strong>
                    <p style="margin: 8px 0 0 0; color: #856404; font-size: 14px;">
                        完整内容已保存，请点击下方按钮导出到Excel查看完整分析结果
                    </p>
                </div>
            ` : ''}
            <div class="content">${escapeHtml(aiDisplay)}</div>
            ${needsTruncation && aiDecisionFull.length > (aiDisplay.length - 3) ? `
                <div style="margin-top: 10px; padding: 10px; background: #f8f9fa; border-radius: 4px; text-align: center;">
                    <button onclick="exportSingleResult(${JSON.stringify(result).replace(/"/g, '&quot;')})" 
                            class="btn btn-success" style="margin: 0;">
                        导出完整结果到Excel
                    </button>
                </div>
            ` : ''}
        </div>
        ${result.judge_decision ? `
            <div style="margin-top: 20px;">
                <h3>法官判决</h3>
                <div class="content">${escapeHtml(judgeDisplay)}</div>
            </div>
        ` : ''}
        ${result.comparison ? `
            <div style="margin-top: 20px;">
                <h3>差异对比</h3>
                <div class="content">${escapeHtml(compDisplay)}</div>
            </div>
        ` : ''}
    `;
}

// 导出单个分析结果
function exportSingleResult(result) {
    const results = [result];
    exportResultsToExcel(results);
}

// 存储问题信息（用于导出）
let currentQuestionsByCase = null;

// 导出问题到Excel
async function exportQuestionsToExcel() {
    if (!currentQuestions || currentQuestions.length === 0) {
        showMessage('没有可导出的问题', 'error');
        return;
    }

    try {
        showLoading('正在导出问题到Excel...');
        const response = await fetch('/api/export_questions', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                questions: currentQuestions,
                questions_by_case: currentQuestionsByCase
            })
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `generated_questions_${new Date().toISOString().split('T')[0]}.xlsx`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            showMessage('导出成功！', 'success');
        } else {
            const data = await response.json();
            showMessage('导出失败: ' + data.error, 'error');
        }
    } catch (error) {
        showMessage('导出失败: ' + error.message, 'error');
    } finally {
        hideLoading();
    }
}

// 导出分析结果到Excel（复用现有功能）
async function exportResultsToExcel(results) {
    try {
        showLoading('正在导出到Excel...');
        const response = await fetch('/api/export_excel', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                results: results
            })
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `analysis_results_${new Date().toISOString().split('T')[0]}.xlsx`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            showMessage('导出成功！', 'success');
        } else {
            const data = await response.json();
            showMessage('导出失败: ' + data.error, 'error');
        }
    } catch (error) {
        showMessage('导出失败: ' + error.message, 'error');
    } finally {
        hideLoading();
    }
}

// 渲染多个案例的分析结果
function renderMultipleAnalysisResults(results) {
    const container = document.getElementById('analysis-result');
    
    container.innerHTML = `
        <h3>批量分析结果（共${results.length}个案例）</h3>
        ${results.map((result, index) => `
            <div style="margin-bottom: 30px; padding: 20px; background: #fafafa; border-left: 4px solid #8b7355;">
                <h4 style="color: #1a1a2e; margin-bottom: 15px; font-size: 1.2em;">案例 ${index + 1}: ${escapeHtml(result.case_title)}</h4>
                <div style="margin-bottom: 15px;">
                    <strong>AI分析结果：</strong>
                    <div class="content" style="margin-top: 8px;">${escapeHtml(result.ai_decision)}</div>
                </div>
                ${result.judge_decision ? `
                    <div style="margin-bottom: 15px;">
                        <strong>法官判决：</strong>
                        <div class="content" style="margin-top: 8px;">${escapeHtml(result.judge_decision)}</div>
                    </div>
                ` : ''}
                ${result.comparison ? `
                    <div style="margin-bottom: 15px;">
                        <strong>差异对比：</strong>
                        <div class="content" style="margin-top: 8px;">${escapeHtml(result.comparison)}</div>
                    </div>
                ` : ''}
            </div>
        `).join('')}
    `;
}

// 加载分析结果
async function loadResults() {
    try {
        const response = await fetch('/api/results');
        const data = await response.json();
        
        if (data.success) {
            currentResults = data.results;
            renderStatistics(data.results);
            renderSimilarityChart(data.results);
            renderResults(data.results);
        } else {
            showMessage('加载失败: ' + data.error, 'error');
        }
    } catch (error) {
        showMessage('请求失败: ' + error.message, 'error');
    }
}

// 渲染统计信息
function renderStatistics(results) {
    const container = document.getElementById('statistics-container');
    
    if (!container) return;
    
    if (results.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: #666; padding: 20px;">暂无统计数据</p>';
        return;
    }
    
    const withJudgeDecision = results.filter(r => r.judge_decision && r.similarity_metrics);
    const avgSimilarity = withJudgeDecision.length > 0
        ? withJudgeDecision.reduce((sum, r) => sum + (r.similarity_metrics?.overall_similarity || 0), 0) / withJudgeDecision.length
        : 0;
    
    const avgKeywordSimilarity = withJudgeDecision.length > 0
        ? withJudgeDecision.reduce((sum, r) => sum + (r.similarity_metrics?.keyword_similarity || 0), 0) / withJudgeDecision.length
        : 0;
    
    const avgResultConsistency = withJudgeDecision.length > 0
        ? withJudgeDecision.reduce((sum, r) => sum + (r.similarity_metrics?.result_consistency || 0), 0) / withJudgeDecision.length
        : 0;
    
    container.innerHTML = `
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">${results.length}</div>
                <div class="stat-label">总分析数</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${withJudgeDecision.length}</div>
                <div class="stat-label">有法官判决</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${avgSimilarity.toFixed(1)}%</div>
                <div class="stat-label">平均相似度</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${avgKeywordSimilarity.toFixed(1)}%</div>
                <div class="stat-label">关键词相似度</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${avgResultConsistency.toFixed(1)}%</div>
                <div class="stat-label">结果一致性</div>
            </div>
        </div>
    `;
}

// 渲染相似度图表
let similarityChart = null;
function renderSimilarityChart(results) {
    const canvas = document.getElementById('similarity-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    
    const ctx = canvas.getContext('2d');
    const withMetrics = results.filter(r => r.similarity_metrics && r.similarity_metrics.has_judge_decision);
    
    if (withMetrics.length === 0) {
        const container = canvas.parentElement;
        if (container) {
            container.innerHTML = '<p style="text-align: center; color: #666; padding: 20px;">暂无相似度数据</p>';
        }
        return;
    }
    
    const labels = withMetrics.map((r, i) => `案例${i + 1}`);
    const overallData = withMetrics.map(r => r.similarity_metrics.overall_similarity || 0);
    const keywordData = withMetrics.map(r => r.similarity_metrics.keyword_similarity || 0);
    const resultData = withMetrics.map(r => r.similarity_metrics.result_consistency || 0);
    const legalData = withMetrics.map(r => r.similarity_metrics.legal_basis_similarity || 0);
    const reasoningData = withMetrics.map(r => r.similarity_metrics.reasoning_similarity || 0);
    
    if (similarityChart) {
        similarityChart.destroy();
    }
    
    similarityChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '整体相似度',
                    data: overallData,
                    backgroundColor: 'rgba(102, 126, 234, 0.6)',
                    borderColor: 'rgba(102, 126, 234, 1)',
                    borderWidth: 1
                },
                {
                    label: '关键词相似度',
                    data: keywordData,
                    backgroundColor: 'rgba(167, 139, 250, 0.6)',
                    borderColor: 'rgba(167, 139, 250, 1)',
                    borderWidth: 1
                },
                {
                    label: '结果一致性',
                    data: resultData,
                    backgroundColor: 'rgba(52, 211, 153, 0.6)',
                    borderColor: 'rgba(52, 211, 153, 1)',
                    borderWidth: 1
                },
                {
                    label: '法律依据相似度',
                    data: legalData,
                    backgroundColor: 'rgba(251, 146, 60, 0.6)',
                    borderColor: 'rgba(251, 146, 60, 1)',
                    borderWidth: 1
                },
                {
                    label: '推理相似度',
                    data: reasoningData,
                    backgroundColor: 'rgba(34, 211, 238, 0.6)',
                    borderColor: 'rgba(34, 211, 238, 1)',
                    borderWidth: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100,
                    ticks: {
                        callback: function(value) {
                            return value + '%';
                        }
                    }
                }
            },
            plugins: {
                legend: {
                    position: 'top',
                },
                title: {
                    display: true,
                    text: 'AI判决与法官判决相似度对比'
                }
            }
        }
    });
}

// 渲染结果列表
function renderResults(results) {
    const container = document.getElementById('results-list');
    
    if (results.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: #666; padding: 40px;">暂无分析结果</p>';
        return;
    }

    container.innerHTML = results.map((result, index) => {
        const metrics = result.similarity_metrics || {};
        const hasMetrics = metrics.has_judge_decision;
        
        return `
        <div class="result-card">
            <h3>${escapeHtml(result.case_title)}</h3>
            <div class="meta">生成时间: ${result.created_at}</div>
            ${hasMetrics ? `
                <div class="similarity-table">
                    <table class="metrics-table">
                        <thead>
                            <tr>
                                <th>指标</th>
                                <th>数值</th>
                                <th>可视化</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td>整体相似度</td>
                                <td>${metrics.overall_similarity || 0}%</td>
                                <td><div class="progress-bar"><div class="progress-fill" style="width: ${metrics.overall_similarity || 0}%"></div></div></td>
                            </tr>
                            <tr>
                                <td>关键词相似度</td>
                                <td>${metrics.keyword_similarity || 0}%</td>
                                <td><div class="progress-bar"><div class="progress-fill" style="width: ${metrics.keyword_similarity || 0}%"></div></div></td>
                            </tr>
                            <tr>
                                <td>结果一致性</td>
                                <td>${metrics.result_consistency || 0}%</td>
                                <td><div class="progress-bar"><div class="progress-fill" style="width: ${metrics.result_consistency || 0}%"></div></div></td>
                            </tr>
                            <tr>
                                <td>法律依据相似度</td>
                                <td>${metrics.legal_basis_similarity || 0}%</td>
                                <td><div class="progress-bar"><div class="progress-fill" style="width: ${metrics.legal_basis_similarity || 0}%"></div></div></td>
                            </tr>
                            <tr>
                                <td>推理相似度</td>
                                <td>${metrics.reasoning_similarity || 0}%</td>
                                <td><div class="progress-bar"><div class="progress-fill" style="width: ${metrics.reasoning_similarity || 0}%"></div></div></td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            ` : '<p style="color: #999; padding: 10px;">该案例暂无法官判决，无法计算相似度</p>'}
            ${result.question ? `
                <div class="field">
                    <div class="field-label">问题:</div>
                    <div class="field-value">${escapeHtml(result.question)}</div>
                </div>
            ` : ''}
            <div class="field">
                <div class="field-label">AI判决:</div>
                <div class="field-value">${escapeHtml(result.ai_decision)}</div>
            </div>
            ${result.judge_decision ? `
                <div class="field">
                    <div class="field-label">法官判决:</div>
                    <div class="field-value">${escapeHtml(result.judge_decision)}</div>
                </div>
            ` : ''}
            ${result.comparison ? `
                <div class="field">
                    <div class="field-label">差异对比:</div>
                    <div class="field-value">${escapeHtml(result.comparison)}</div>
                </div>
            ` : ''}
        </div>
    `;
    }).join('');
}

// 刷新结果
document.getElementById('refresh-results').addEventListener('click', loadResults);

// 导出结果
document.getElementById('export-results').addEventListener('click', async () => {
    if (currentResults.length === 0) {
        showMessage('没有可导出的结果', 'error');
        return;
    }

    try {
        showLoading();
        const response = await fetch('/api/export_excel', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                results: currentResults
            })
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `analysis_results_${new Date().toISOString().split('T')[0]}.xlsx`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            showMessage('导出成功！', 'success');
        } else {
            const data = await response.json();
            showMessage('导出失败: ' + data.error, 'error');
        }
    } catch (error) {
        showMessage('导出失败: ' + error.message, 'error');
    } finally {
        hideLoading();
    }
});

// 工具函数
function showLoading(message = '处理中...') {
    const loadingEl = document.getElementById('loading');
    const loadingText = loadingEl.querySelector('p');
    if (loadingText) {
        loadingText.textContent = message;
    }
    loadingEl.classList.remove('hidden');
}

function hideLoading() {
    document.getElementById('loading').classList.add('hidden');
}

function showMessage(text, type = 'success') {
    const messageEl = document.getElementById('message');
    messageEl.textContent = text;
    messageEl.className = `message ${type}`;
    messageEl.classList.remove('hidden');
    
    setTimeout(() => {
        messageEl.classList.add('hidden');
    }, 3000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 开始进度轮询
function startProgressPolling(taskId) {
    // 清除之前的轮询
    if (progressInterval) {
        clearInterval(progressInterval);
    }
    
    // 立即查询一次
    checkProgress(taskId);
    
    // 每1秒轮询一次
    progressInterval = setInterval(() => {
        checkProgress(taskId);
    }, 1000);
}

// 检查进度
async function checkProgress(taskId) {
    try {
        const response = await fetch(`/api/batch_progress/${taskId}`);
        const data = await response.json();
        
        if (data.success && data.progress) {
            updateProgressDisplay(data.progress);
            
            // 如果完成，停止轮询
            if (data.progress.status === 'completed' || data.progress.status === 'failed') {
                if (progressInterval) {
                    clearInterval(progressInterval);
                    progressInterval = null;
                }
                
                // 如果成功完成，显示结果
                if (data.progress.status === 'completed' && data.progress.results.length > 0) {
                    setTimeout(() => {
                        renderMultipleAnalysisResults(data.progress.results);
                        let message = `成功分析${data.progress.success}个案例！`;
                        if (data.progress.failed > 0) {
                            message += ` ${data.progress.failed}个案例分析失败。`;
                        }
                        showMessage(message, data.progress.failed > 0 ? 'error' : 'success');
                        loadResults();
                    }, 500);
                } else if (data.progress.status === 'failed') {
                    showMessage('批量分析失败: ' + (data.progress.error || '未知错误'), 'error');
                }
            }
        } else {
            console.error('获取进度失败:', data.error);
        }
    } catch (error) {
        console.error('查询进度时出错:', error);
    }
}

// 更新进度显示
function updateProgressDisplay(progress) {
    // 更新状态文本
    const statusText = document.getElementById('progress-status-text');
    if (progress.status === 'running') {
        statusText.textContent = '正在分析...';
        statusText.style.color = '#1a1a2e';
    } else if (progress.status === 'completed') {
        statusText.textContent = '分析完成';
        statusText.style.color = '#28a745';
    } else if (progress.status === 'failed') {
        statusText.textContent = '分析失败';
        statusText.style.color = '#dc3545';
    }
    
    // 更新百分比
    document.getElementById('progress-percentage').textContent = `${progress.percentage}%`;
    
    // 更新进度条
    document.getElementById('progress-bar').style.width = `${progress.percentage}%`;
    
    // 更新统计信息
    document.getElementById('progress-completed').textContent = progress.completed;
    document.getElementById('progress-total').textContent = progress.total;
    document.getElementById('progress-success').textContent = progress.success;
    document.getElementById('progress-failed').textContent = progress.failed;
    
    // 更新预计剩余时间
    const etaContainer = document.getElementById('progress-eta-container');
    const etaElement = document.getElementById('progress-eta');
    if (progress.estimated_remaining_seconds && progress.status === 'running') {
        const minutes = Math.floor(progress.estimated_remaining_seconds / 60);
        const seconds = progress.estimated_remaining_seconds % 60;
        etaElement.textContent = `${minutes}分${seconds}秒`;
        etaContainer.style.display = 'flex';
    } else {
        etaContainer.style.display = 'none';
    }
    
    // 更新错误列表
    if (progress.errors && progress.errors.length > 0) {
        const errorContainer = document.getElementById('progress-errors');
        const errorList = document.getElementById('error-list');
        const errorCount = document.getElementById('error-count');
        
        errorCount.textContent = progress.errors.length;
        errorContainer.style.display = 'block';
        
        errorList.innerHTML = progress.errors.map(err => `
            <li>
                <strong>${escapeHtml(err.case_title || err.case_id)}</strong>: 
                ${escapeHtml(err.error)}
            </li>
        `).join('');
    } else {
        document.getElementById('progress-errors').style.display = 'none';
    }
}

