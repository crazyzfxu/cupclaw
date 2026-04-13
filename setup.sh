#!/bin/bash
# ClawChat 一键安装脚本

set -e

echo "正在安装 ClawChat..."

# 1. 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误：需要 Python 3，请先安装"
    exit 1
fi

# 2. 安装依赖
echo "安装 Python 依赖..."
pip3 install -r backend/requirements.txt

# 3. 配置
echo ""
echo "请配置 OpenClaw 连接："
read -p "OpenClaw 地址（如 http://localhost:18789）: " OPENCLAW_URL
read -p "OpenClaw Token: " OPENCLAW_TOKEN

# 写入 .env 文件
cat > backend/.env << EOF
OPENCLAW_URL=$OPENCLAW_URL
OPENCLAW_TOKEN=$OPENCLAW_TOKEN
EOF

echo ""
echo "配置完成！"
echo ""
echo "启动服务："
echo "  cd backend && python3 app.py"
echo ""
echo "然后用浏览器打开 http://localhost:3000 访问"
