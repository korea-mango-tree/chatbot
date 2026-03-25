@echo off
chcp 65001 >nul
echo =========================================
echo   RAG Chatbot Server Stopping...
echo =========================================
echo.

cd /d "%~dp0"

docker-compose down
echo.
echo   OK - 서버 종료 완료
echo =========================================
pause
