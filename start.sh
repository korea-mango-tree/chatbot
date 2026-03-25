#!/bin/bash

echo "========================================="
echo "  RAG Chatbot Server Starting..."
echo "========================================="

cd "$(dirname "$0")"

# 1. Docker 컨테이너 실행 (DB + pgAdmin)
echo ""
echo "[1/3] Docker 컨테이너 시작..."
docker-compose up -d
if [ $? -ne 0 ]; then
    echo "ERROR: Docker 실행 실패. Docker Desktop이 실행 중인지 확인하세요."
    exit 1
fi
echo "  ✓ DB + pgAdmin 실행 완료"

# 2. DB 준비 대기
echo ""
echo "[2/3] DB 연결 대기 중..."
sleep 3
echo "  ✓ DB 준비 완료"

# 3. FastAPI 서버 실행
echo ""
echo "[3/3] FastAPI 서버 시작..."
echo ""
echo "========================================="
echo "  서버 실행 완료!"
echo "========================================="
echo ""
echo "  사용자 채팅:  http://localhost:8000"
echo "  관리자:       http://localhost:8000/admin"
echo "  슈퍼어드민:   http://localhost:8000/superadmin"
echo "  pgAdmin:      http://localhost:5050"
echo ""
echo "  종료: Ctrl+C"
echo "========================================="
echo ""

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
