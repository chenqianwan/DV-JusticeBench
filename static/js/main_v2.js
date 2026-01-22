/**
 * DV-JusticeBench 法律AI研究平台 - V2版本
 * 4步骤可视化流程前端逻辑
 */

// ==================== 全局状态管理 ====================
const state = {
    sessionId: null,
    currentStep: 1,
    originalText: '',
    maskedText: '',
    questions: [],
    model: 'deepseek',
    answers: [],
    evaluations: [],
    radarChart: null  // Chart.js实例
};

// ==================== 工具函数 ====================
function showLoading(elementId) {
    const element = document.getElementById(elementId);
    if (element) element.classList.remove('hidden');
}

function hideLoading(elementId) {
    const element = document.getElementById(elementId);
    if (element) element.classList.add('hidden');
}

function showError(message) {
    alert('Error: ' + message);
}

function showSuccess(message) {
    console.log('Success: ' + message);
}

// ==================== 步骤切换 ====================
function goToStep(step) {
    // 更新状态
    state.currentStep = step;
    
    // 隐藏所有步骤容器
    document.querySelectorAll('.step-container').forEach(container => {
        container.classList.remove('visible');
        container.classList.add('hidden');
    });
    
    // 显示当前步骤
    const currentContainer = document.getElementById(`step${step}`);
    if (currentContainer) {
        currentContainer.classList.remove('hidden');
        currentContainer.classList.add('visible');
    }
    
    // 更新步骤指示器
    document.querySelectorAll('.stepper .step').forEach((stepEl, index) => {
        stepEl.classList.remove('active', 'completed');
        if (index + 1 < step) {
            stepEl.classList.add('completed');
        } else if (index + 1 === step) {
            stepEl.classList.add('active');
        }
    });
    
    // 步骤3特殊处理：重新启用按钮
    if (step === 3 && state.questions.length > 0) {
        const modelSelect = document.getElementById('modelSelect');
        const generateBtn = document.getElementById('generateAllAnswers');
        if (modelSelect) modelSelect.disabled = false;
        if (generateBtn) generateBtn.disabled = false;
    }
    
    // 滚动到顶部
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ==================== 步骤1: 文件上传和脱敏 ====================
function initStep1() {
    const fileInput = document.getElementById('fileInput');
    const uploadArea = document.getElementById('uploadArea');
    
    // 文件选择
    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            uploadFile(file);
        }
    });
    
    // 拖拽上传
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('drag-over');
    });
    
    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('drag-over');
    });
    
    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file) {
            uploadFile(file);
        }
    });
    
    // 开始脱敏按钮
    document.getElementById('startMaskBtn').addEventListener('click', startMasking);
    
    // 编辑脱敏文本按钮
    document.getElementById('editMaskedBtn').addEventListener('click', toggleEditMasked);
    
    // 下一步按钮
    document.getElementById('nextToQuestions').addEventListener('click', () => {
        generateQuestions();
    });
}

async function uploadFile(file) {
    if (!file.name.toLowerCase().endsWith('.docx')) {
        showError('Only .docx files are supported');
        return;
    }
    
    showLoading('loadingMask');
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const response = await fetch('/api/v2/upload', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.success) {
            state.sessionId = data.session_id;
            state.originalText = data.text;
            
            // Show uploaded file info
            document.getElementById('uploadedFileName').textContent = file.name;
            document.getElementById('uploadedFileInfo').classList.remove('hidden');
            
            // Hide upload area
            document.getElementById('uploadArea').style.display = 'none';
            
            // Show masking mode selection
            document.getElementById('maskModeSection').classList.remove('hidden');
            
            showSuccess(`✅ File "${file.name}" uploaded successfully! (${(file.size / 1024).toFixed(1)} KB)`);
        } else {
            showError(data.error || 'Upload failed');
        }
    } catch (error) {
        showError('Upload failed: ' + error.message);
    } finally {
        hideLoading('loadingMask');
    }
}

async function startMasking() {
    const mode = document.querySelector('input[name="maskMode"]:checked').value;
    const btn = document.getElementById('startMaskBtn');
    
    // Disable button
    btn.disabled = true;
    
    // Show progress with mode-specific messages
    if (mode === 'fast') {
        btn.textContent = '⚡ Processing... (~5 sec)';
        showLoadingWithMessage('loadingMask', 'Applying regex-based masking...');
    } else {
        btn.textContent = '🔍 Processing... (~45 sec)';
        showLoadingWithMessage('loadingMask', 'AI is analyzing sensitive information...');
    }
    
    try {
        const response = await fetch('/api/v2/mask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                mode: mode
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            state.maskedText = data.masked_text;
            
            // Show comparison
            document.getElementById('originalText').textContent = state.originalText;
            document.getElementById('maskedText').textContent = state.maskedText;
            document.getElementById('compareSection').classList.remove('hidden');
            
            // Always allow editing (both Fast and Review modes)
            document.getElementById('editMaskedBtn').classList.remove('hidden');
            
            showSuccess('✅ Step 1 Complete: Privacy masking finished! Ready to generate questions.');
            
            // Mark step as completed
            document.querySelector('.step[data-step="1"]').classList.add('completed');
        } else {
            showError(data.error || 'Masking failed');
        }
    } catch (error) {
        showError('Masking failed: ' + error.message);
    } finally {
        hideLoading('loadingMask');
        btn.disabled = false;
        btn.textContent = 'Start Masking';
    }
}

function showLoadingWithMessage(loadingId, message) {
    const loadingEl = document.getElementById(loadingId);
    if (loadingEl) {
        loadingEl.classList.remove('hidden');
        // Update loading message if there's a paragraph
        const loadingText = loadingEl.querySelector('p');
        if (loadingText) {
            loadingText.textContent = message;
        }
    }
}

function toggleEditMasked() {
    const maskedTextEl = document.getElementById('maskedText');
    const btn = document.getElementById('editMaskedBtn');
    
    if (maskedTextEl.getAttribute('contenteditable') === 'true') {
        // Save edits
        maskedTextEl.setAttribute('contenteditable', 'false');
        btn.textContent = 'Edit';
        state.maskedText = maskedTextEl.textContent;
        showSuccess('Edits saved');
    } else {
        // Start editing
        maskedTextEl.setAttribute('contenteditable', 'true');
        maskedTextEl.focus();
        btn.textContent = 'Save';
    }
}

// ==================== 步骤2: 问题生成 ====================
function initStep2() {
    document.getElementById('addQuestionBtn').addEventListener('click', addQuestion);
    document.getElementById('backToMask').addEventListener('click', () => goToStep(1));
    document.getElementById('nextToAnalysis').addEventListener('click', () => {
        if (state.questions.length === 0) {
            showError('Please generate or add at least one question');
            return;
        }
        goToStep(3);
        initAnswersProgress();
    });
}

async function generateQuestions() {
    showLoading('loadingQuestions');
    goToStep(2);
    
    // Disable all relevant buttons during generation
    const nextBtn = document.getElementById('nextToQuestions');
    const addQuestionBtn = document.getElementById('addQuestionBtn');
    const nextToAnalysisBtn = document.getElementById('nextToAnalysis');
    const modelSelect = document.getElementById('modelSelect');
    const generateBtn = document.getElementById('generateAllAnswers');
    
    if (nextBtn) nextBtn.disabled = true;
    if (addQuestionBtn) addQuestionBtn.disabled = true;
    if (nextToAnalysisBtn) nextToAnalysisBtn.disabled = true;
    if (modelSelect) modelSelect.disabled = true;
    if (generateBtn) generateBtn.disabled = true;
    
    try {
        const response = await fetch('/api/v2/generate_questions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            // Limit to maximum 10 questions
            state.questions = data.questions.slice(0, 10);
            
            if (data.questions.length > 10) {
                console.log(`Limited questions from ${data.questions.length} to 10`);
            }
            
            renderQuestions();
            showSuccess(`✅ Step 2 Complete: ${state.questions.length} questions generated successfully! Ready for AI analysis.`);
            
            // Mark step as completed
            document.querySelector('.step[data-step="2"]').classList.add('completed');
        } else {
            showError(data.error || 'Question generation failed');
        }
    } catch (error) {
        showError('Question generation failed: ' + error.message);
    } finally {
        hideLoading('loadingQuestions');
        // Re-enable step 2 controls
        if (nextBtn) nextBtn.disabled = false;
        if (addQuestionBtn) addQuestionBtn.disabled = false;
        if (nextToAnalysisBtn) nextToAnalysisBtn.disabled = false;
        // Don't re-enable step 3 controls yet - user needs to navigate there first
    }
}

function renderQuestions() {
    const container = document.getElementById('questionsList');
    container.innerHTML = '';
    
    state.questions.forEach((question, index) => {
        const questionItem = document.createElement('div');
        questionItem.className = 'question-item';
        questionItem.innerHTML = `
            <div class="question-number">${index + 1}</div>
            <div class="question-text">
                <input type="text" value="${question}" data-index="${index}" onchange="updateQuestion(${index}, this.value)">
            </div>
            <div class="question-actions">
                <button class="icon-btn" onclick="deleteQuestion(${index})" title="删除">🗑️</button>
            </div>
        `;
        container.appendChild(questionItem);
    });
}

function addQuestion() {
    if (state.questions.length >= 10) {
        showError('Maximum 10 questions allowed');
        return;
    }
    
    const newQuestion = prompt('Enter new question:');
    if (newQuestion && newQuestion.trim()) {
        state.questions.push(newQuestion.trim());
        renderQuestions();
        updateQuestionsOnServer();
        
        if (state.questions.length >= 10) {
            showSuccess('Maximum question limit reached (10/10)');
        }
    }
}

function updateQuestion(index, value) {
    state.questions[index] = value;
    updateQuestionsOnServer();
}

function deleteQuestion(index) {
    if (confirm('Are you sure you want to delete this question?')) {
        state.questions.splice(index, 1);
        renderQuestions();
        updateQuestionsOnServer();
    }
}

async function updateQuestionsOnServer() {
    try {
        await fetch('/api/v2/update_questions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                questions: state.questions
            })
        });
    } catch (error) {
        console.error('更新问题失败:', error);
    }
}

// ==================== 步骤3: AI答案生成 ====================
function initStep3() {
    document.getElementById('generateAllAnswers').addEventListener('click', generateAllAnswers);
    document.getElementById('backToQuestions').addEventListener('click', () => goToStep(2));
    document.getElementById('nextToResults').addEventListener('click', () => {
        evaluateAllAnswers();
    });
}

function initAnswersProgress() {
    const container = document.getElementById('answersProgress');
    container.innerHTML = '';
    
    state.questions.forEach((question, index) => {
        const card = document.createElement('div');
        card.className = 'answer-card';
        card.id = `answer-card-${index}`;
        card.innerHTML = `
            <div class="answer-header">
                <div class="answer-question">Question ${index + 1}: ${question}</div>
                <span class="answer-status pending" id="status-${index}">Pending</span>
            </div>
            <div class="answer-content hidden" id="answer-${index}"></div>
        `;
        container.appendChild(card);
    });
}

async function generateAllAnswers() {
    const modelSelect = document.getElementById('modelSelect');
    const generateBtn = document.getElementById('generateAllAnswers');
    
    state.model = modelSelect.value;
    
    // Disable controls
    modelSelect.disabled = true;
    generateBtn.disabled = true;
    generateBtn.textContent = 'Generating...';
    
    // 判断是否使用thinking模式
    const useThinking = state.model === 'deepseek-thinking';
    const actualModel = useThinking ? 'deepseek' : state.model;
    
    // 并行生成所有答案（最多10个）
    const promises = [];
    const maxQuestions = Math.min(state.questions.length, 10);
    
    console.log(`Generating ${maxQuestions} answers in parallel...`);
    
    for (let i = 0; i < maxQuestions; i++) {
        promises.push(generateSingleAnswer(i, actualModel, useThinking));
    }
    
    // 等待所有答案生成完成
    try {
        await Promise.all(promises);
        console.log('All answers generated successfully');
        
        showSuccess(`✅ Step 3 Complete: All ${maxQuestions} answers generated! Starting evaluation...`);
        
        // Mark step as completed
        document.querySelector('.step[data-step="3"]').classList.add('completed');
        
        // 自动开始评分
        await evaluateAllAnswers();
        
        // 启用下一步按钮
        document.getElementById('nextToResults').disabled = false;
    } catch (error) {
        console.error('Error in parallel generation:', error);
        showError('Some answers failed to generate. Please check the results.');
    } finally {
        modelSelect.disabled = false;
        generateBtn.disabled = false;
        generateBtn.textContent = 'Generate All Answers';
    }
}

async function generateSingleAnswer(index, model, useThinking) {
    const statusEl = document.getElementById(`status-${index}`);
    const answerEl = document.getElementById(`answer-${index}`);
    
    // Update status to generating
    statusEl.textContent = 'Generating...';
    statusEl.className = 'answer-status generating';
    
    try {
        const response = await fetch('/api/v2/generate_answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                question_index: index,
                model: model,
                use_thinking: useThinking
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            // Save answer
            while (state.answers.length <= index) {
                state.answers.push(null);
            }
            state.answers[index] = {
                question: state.questions[index],
                answer: data.answer,
                reasoning: data.reasoning
            };
            
            // Display answer
            answerEl.textContent = data.answer;
            answerEl.classList.remove('hidden');
            
            // Update status to completed
            statusEl.textContent = 'Completed';
            statusEl.className = 'answer-status completed';
        } else {
            statusEl.textContent = 'Failed';
            statusEl.className = 'answer-status pending';
            showError(`Question ${index + 1} generation failed: ${data.error}`);
        }
    } catch (error) {
        statusEl.textContent = 'Failed';
        statusEl.className = 'answer-status pending';
        showError(`Question ${index + 1} generation failed: ${error.message}`);
    }
}

// ==================== 步骤4: 评分结果 ====================
function initStep4() {
    document.getElementById('backToAnalysis').addEventListener('click', () => goToStep(3));
    document.getElementById('restartBtn').addEventListener('click', () => {
        if (confirm('Are you sure you want to restart? Current progress will be lost.')) {
            location.reload();
        }
    });
    document.getElementById('downloadExcelBtn').addEventListener('click', downloadExcelReport);
}

async function evaluateAllAnswers() {
    goToStep(4);
    
    // Show loading overlay with animation
    showEvaluationLoading();
    
    // 初始化评估数组
    state.evaluations = [];
    
    try {
        // 并行评分所有答案
        const promises = [];
        for (let i = 0; i < state.answers.length; i++) {
            promises.push(evaluateSingleAnswer(i));
        }
        
        await Promise.all(promises);
        
        // 渲染结果
        renderResults();
        
        showSuccess(`✅ Step 4 Complete: All ${state.answers.length} answers evaluated! Results are ready.`);
        
        // Mark step as completed
        document.querySelector('.step[data-step="4"]').classList.add('completed');
    } catch (error) {
        showError('Evaluation failed: ' + error.message);
    } finally {
        hideEvaluationLoading();
    }
}

async function evaluateSingleAnswer(index) {
    updateEvaluationProgress(index + 1, state.answers.length);
    
    try {
        const response = await fetch('/api/v2/evaluate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                question_index: index,
                judge_decision: ''  // 实际应该提供法官判决
            })
        });
        
        const data = await response.json();
        
        console.log(`=== 评估结果 ${index + 1} ===`);
        console.log('完整数据:', data);
        console.log('evaluation对象:', data.evaluation);
        console.log('total_score:', data.evaluation?.total_score);
        console.log('scores:', data.evaluation?.scores);
        console.log('==================');
        
        if (data.success) {
            state.evaluations[index] = data.evaluation;
        } else {
            console.error(`Evaluation ${index + 1} failed: ${data.error}`);
            state.evaluations[index] = {
                total_score: 0,
                scores: {},
                errors: {}
            };
        }
    } catch (error) {
        console.error(`Evaluation ${index + 1} failed: ${error.message}`);
        state.evaluations[index] = {
            total_score: 0,
            scores: {},
            errors: {}
        };
    }
}

function showEvaluationLoading() {
    const totalQuestions = state.answers.length;
    const estimatedTime = Math.ceil(totalQuestions * 10); // Estimate 10 seconds per question
    
    const overlay = document.createElement('div');
    overlay.id = 'evaluation-loading-overlay';
    overlay.innerHTML = `
        <div class="evaluation-loading-content">
            <div class="loading-spinner-large"></div>
            <h3>Evaluating Answers...</h3>
            <p id="evaluation-progress">Processing: 0 / ${totalQuestions}</p>
            <div class="progress-bar-container">
                <div class="progress-bar" id="evaluation-progress-bar"></div>
            </div>
            <p class="time-estimate-loading">⏱️ Estimated time: ${estimatedTime}-${estimatedTime + 30} seconds</p>
            <p class="loading-hint">Please wait, this process requires AI evaluation and may take a few minutes...</p>
        </div>
    `;
    document.body.appendChild(overlay);
}

function hideEvaluationLoading() {
    const overlay = document.getElementById('evaluation-loading-overlay');
    if (overlay) {
        overlay.remove();
    }
}

function updateEvaluationProgress(current, total) {
    const progressText = document.getElementById('evaluation-progress');
    const progressBar = document.getElementById('evaluation-progress-bar');
    
    if (progressText) {
        progressText.textContent = `Processing: ${current} / ${total}`;
    }
    
    if (progressBar) {
        const percentage = (current / total) * 100;
        progressBar.style.width = `${percentage}%`;
    }
}

function renderResults() {
    if (state.evaluations.length === 0) {
        return;
    }
    
    console.log('=== renderResults 调试 ===');
    console.log('evaluations数量:', state.evaluations.length);
    console.log('第一个evaluation:', state.evaluations[0]);
    
    // 计算总分和平均分
    let totalScore = 0;
    let totalDim1 = 0, totalDim2 = 0, totalDim3 = 0, totalDim4 = 0, totalDim5 = 0;
    let totalMajor = 0, totalObvious = 0, totalMinor = 0, totalAbandoned = 0;
    
    state.evaluations.forEach((evaluation, idx) => {
        console.log(`评估 ${idx + 1}:`, evaluation);
        const scores = evaluation.scores || {};
        console.log(`评估 ${idx + 1} scores:`, scores);
        
        totalDim1 += scores['规范依据相关性'] || 0;
        totalDim2 += scores['涵摄链条对齐度'] || 0;
        totalDim3 += scores['价值衡量与同理心对齐度'] || 0;
        totalDim4 += scores['关键事实与争点覆盖度'] || 0;
        totalDim5 += scores['裁判结论与救济配置一致性'] || 0;
        
        totalScore += (evaluation.total_score || 0);
        console.log(`评估 ${idx + 1} total_score:`, evaluation.total_score, '累计:', totalScore);
        
        const errors = evaluation.errors || {};
        totalMajor += errors.major_errors || 0;
        totalObvious += errors.obvious_errors || 0;
        totalMinor += errors.minor_errors || 0;
        totalAbandoned += errors.abandoned_law_citations || 0;
    });
    
    const count = state.evaluations.length;
    const avgScore = (totalScore / count).toFixed(2);
    const avgDim1 = (totalDim1 / count).toFixed(2);
    const avgDim2 = (totalDim2 / count).toFixed(2);
    const avgDim3 = (totalDim3 / count).toFixed(2);
    const avgDim4 = (totalDim4 / count).toFixed(2);
    const avgDim5 = (totalDim5 / count).toFixed(2);
    
    // 显示总分
    document.getElementById('totalScore').textContent = `${avgScore} / 20`;
    
    // 显示等级
    const grade = getGrade(avgScore);
    document.getElementById('scoreGrade').textContent = grade;
    
    // 显示维度分数
    document.getElementById('dim1Score').textContent = `${avgDim1} / 4`;
    document.getElementById('dim2Score').textContent = `${avgDim2} / 4`;
    document.getElementById('dim3Score').textContent = `${avgDim3} / 4`;
    document.getElementById('dim4Score').textContent = `${avgDim4} / 4`;
    document.getElementById('dim5Score').textContent = `${avgDim5} / 4`;
    
    // 显示错误统计
    document.getElementById('majorErrors').textContent = totalMajor;
    document.getElementById('obviousErrors').textContent = totalObvious;
    document.getElementById('minorErrors').textContent = totalMinor;
    document.getElementById('abandonedLaws').textContent = totalAbandoned;
    
    // 渲染雷达图
    renderRadarChart([
        parseFloat(avgDim1),
        parseFloat(avgDim2),
        parseFloat(avgDim3),
        parseFloat(avgDim4),
        parseFloat(avgDim5)
    ]);
    
    // 渲染详细结果
    renderDetailedResults();
}

function getGrade(score) {
    const s = parseFloat(score);
    if (s >= 16) return 'Highly Reliable (Professionally Usable)';
    if (s >= 11) return 'Basically Reliable (Requires Review)';
    if (s >= 6) return 'Reference Only (Not for Direct Use)';
    return 'Unreliable/Unusable';
}

function renderRadarChart(scores) {
    const ctx = document.getElementById('radarChart').getContext('2d');
    
    // Destroy old chart
    if (state.radarChart) {
        state.radarChart.destroy();
    }
    
    // Create new chart
    state.radarChart = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: [
                'Normative Basis',
                'Subsumption Chain',
                'Value & Empathy',
                'Key Facts Coverage',
                'Outcome Consistency'
            ],
            datasets: [{
                label: 'Average Score',
                data: scores,
                borderColor: 'rgb(33, 150, 243)',
                backgroundColor: 'rgba(33, 150, 243, 0.2)',
                borderWidth: 2,
                pointBackgroundColor: 'rgb(33, 150, 243)',
                pointBorderColor: '#fff',
                pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: 'rgb(33, 150, 243)'
            }]
        },
        options: {
            scales: {
                r: {
                    min: 0,
                    max: 4,
                    ticks: {
                        stepSize: 1
                    },
                    pointLabels: {
                        font: {
                            size: 12
                        }
                    }
                }
            },
            plugins: {
                legend: {
                    display: false
                }
            }
        }
    });
}

function renderDetailedResults() {
    const container = document.getElementById('detailedResultsList');
    container.innerHTML = '';
    
    state.evaluations.forEach((evaluation, index) => {
        const scores = evaluation.scores || {};
        const totalScore = evaluation.total_score || 0;
        
        const resultItem = document.createElement('div');
        resultItem.className = 'result-item';
        
        const header = document.createElement('div');
        header.className = 'result-header';
        header.innerHTML = `
            <div class="result-title">Question ${index + 1}: ${state.questions[index]}</div>
            <div class="result-score">${totalScore.toFixed(2)} / 20</div>
        `;
        header.onclick = () => toggleResultContent(index);
        
        const content = document.createElement('div');
        content.className = 'result-content';
        content.id = `result-content-${index}`;
        content.innerHTML = `
            <div class="result-section">
                <h4>AI Answer</h4>
                <p>${state.answers[index]?.answer || 'No answer'}</p>
            </div>
            <div class="result-section">
                <h4>Score Details</h4>
                <p>Normative Basis Relevance: ${scores['规范依据相关性'] || 0} / 4</p>
                <p>Subsumption Chain Alignment: ${scores['涵摄链条对齐度'] || 0} / 4</p>
                <p>Value & Empathy Alignment: ${scores['价值衡量与同理心对齐度'] || 0} / 4</p>
                <p>Key Facts Coverage: ${scores['关键事实与争点覆盖度'] || 0} / 4</p>
                <p>Outcome Consistency: ${scores['裁判结论与救济配置一致性'] || 0} / 4</p>
            </div>
            <div class="result-section">
                <h4>Scoring Rationale</h4>
                <p>${evaluation.rationale || 'No rationale provided'}</p>
            </div>
        `;
        
        resultItem.appendChild(header);
        resultItem.appendChild(content);
        container.appendChild(resultItem);
    });
}

function toggleResultContent(index) {
    const content = document.getElementById(`result-content-${index}`);
    content.classList.toggle('expanded');
}

// ==================== Excel Download ====================
async function downloadExcelReport() {
    const btn = document.getElementById('downloadExcelBtn');
    const originalText = btn.textContent;
    
    btn.disabled = true;
    btn.textContent = 'Generating...';
    
    try {
        const response = await fetch('/api/v2/export_excel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId
            })
        });
        
        if (!response.ok) {
            throw new Error('Export failed');
        }
        
        // Get filename from headers or use default
        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = 'evaluation_results.xlsx';
        if (contentDisposition) {
            const filenameMatch = contentDisposition.match(/filename="?(.+)"?/i);
            if (filenameMatch) {
                filename = filenameMatch[1];
            }
        }
        
        // Download file
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        showSuccess('✅ Excel report downloaded successfully!');
    } catch (error) {
        showError('Export failed: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// ==================== Page Initialization ====================
document.addEventListener('DOMContentLoaded', () => {
    initStep1();
    initStep2();
    initStep3();
    initStep4();
    
    console.log('DV-JusticeBench V2 Loaded');
});
