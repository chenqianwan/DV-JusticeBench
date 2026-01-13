#!/bin/bash
# 打包脚本

echo "==================================="
echo "法律AI研究平台 - 打包脚本"
echo "==================================="

# 检查PyInstaller是否安装
if ! python3 -c "import PyInstaller" 2>/dev/null; then
    echo "正在安装PyInstaller..."
    pip3 install pyinstaller
fi

# 清理之前的构建
echo "清理之前的构建文件..."
rm -rf build dist __pycache__

# 执行打包
echo "开始打包..."
pyinstaller 法律AI研究平台.spec

if [ $? -eq 0 ]; then
    echo ""
    echo "==================================="
    echo "✓ 打包成功！"
    echo "==================================="
    echo "可执行文件位置: dist/法律AI研究平台"
    echo ""
    echo "运行方式:"
    echo "  ./dist/法律AI研究平台/法律AI研究平台"
    echo ""
else
    echo ""
    echo "==================================="
    echo "✗ 打包失败"
    echo "==================================="
    exit 1
fi

