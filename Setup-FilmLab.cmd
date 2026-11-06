@echo off
rem FilmLab Studio setup: double-click to install or repair FilmLab on this PC.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install\Setup-FilmLab.ps1" %*
pause
