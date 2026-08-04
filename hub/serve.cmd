@echo off
rem 프로젝트 허브 실행: 레포 루트에서 로컬 서버를 띄우고 브라우저를 연다
cd /d "%~dp0.."
where python >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (set "PY=py")
start "" cmd /c "timeout /t 2 /nobreak >nul & start "" http://localhost:8787/hub/"
%PY% -m http.server 8787
if errorlevel 1 (
  echo.
  echo 서버 시작 실패 - 8787 포트 사용 중이거나 Python 미설치일 수 있습니다.
  pause
)
