#!/bin/bash
# 快速启动脚本

echo "==================================="
echo "法律AI研究平台 - 启动脚本"
echo "==================================="

# 检查Python版本
python3 --version

# 检查依赖是否安装
echo ""
echo "检查依赖..."
if ! python3 -c "import flask" 2>/dev/null; then
    echo "正在安装依赖..."
    pip3 install -r requirements.txt
else
    echo "依赖已安装"
fi

# 检查.env文件
if [ ! -f .env ]; then
    echo ""
    echo "警告: .env文件不存在，请创建并配置API密钥"
    echo "示例:"
    echo "DEEPSEEK_API_KEY=your_api_key"
    echo "SECRET_KEY=your_secret_key"
    echo "DEBUG=True"
fi

# 启动应用
echo ""
echo "启动Flask应用..."
echo "访问地址: http://localhost:5000"
echo ""
python3 app.py


