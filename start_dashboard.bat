@echo off
title AgentRCON Standalone Dashboard Launcher
cd "%~dp0\dashboard"
if not exist node_modules (
    echo [!] Node modules not found. Running npm install...
    call npm install
)
echo [+] Launching AgentRCON Dashboard...
call npm start
