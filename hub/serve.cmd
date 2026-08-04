@echo off
rem 프로젝트 허브 실행: 레포 루트에서 로컬 서버를 띄우고 브라우저를 연다
cd /d "%~dp0.."
start "" http://localhost:8787/hub/
python -m http.server 8787
