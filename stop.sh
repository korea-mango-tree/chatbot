#!/bin/bash

echo "========================================="
echo "  RAG Chatbot Server Stopping..."
echo "========================================="

cd "$(dirname "$0")"

# Docker 컨테이너 종료
docker-compose down
echo ""
echo "  ✓ 서버 종료 완료"
echo "========================================="
