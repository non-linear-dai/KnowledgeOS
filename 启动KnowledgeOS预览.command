#!/bin/bash
# 在访达中双击本文件即可启动 KnowledgeOS 本地预览（后端 + Studio），完成后自动打开浏览器。
# 首次运行会下载所需的 Python / Node.js 与依赖，约 5 分钟；关闭此窗口即停止预览。
cd "$(dirname "$0")" || exit 1
bash scripts/preview.sh
code=$?
if [ $code -ne 0 ]; then
  echo
  echo "启动未完成（退出码 $code）。日志位于 runtime/preview-logs/。按回车键关闭窗口。"
  read -r _
fi
