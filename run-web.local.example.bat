@echo off
rem Copy to run-web.local.bat and fill in passwords locally. That file is ignored by Git.
set "DASHBOARD_USER=edi-admin"
set "DASHBOARD_PASSWORD="
set "RADIUS_DSN=Radius_NewLive"
set "RADIUS_DB_USER=vision"
set "RADIUS_DB_PASS="
set "RADIUS_TEST_MONITOR_DSN=Radius_NewLive"
set "RADIUS_TEST_MONITOR_USER=vision"
set "RADIUS_TEST_MONITOR_PASS="
rem For an explicitly approved LIVE read-only observer, configure its authorization and roots locally.
rem set "EDI_CENTRAL_LIVE_OBSERVATION=1"
rem set "EDI_CENTRAL_ALLOWED_ROOTS=\\approved-server\approved-share"
