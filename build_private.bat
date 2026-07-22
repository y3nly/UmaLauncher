@echo off
call gitinfo.bat || exit /b 1
python scripts\refresh_adblock_list.py || exit /b 1
cd /d "%~dp0umalauncher" || exit /b 1
python create_version_private.py || exit /b 1
python -m PyInstaller threader_private.spec || exit /b 1
cd /d "%~dp0" || exit /b 1
