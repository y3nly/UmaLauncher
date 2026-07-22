@echo off
pushd "%~dp0umalauncher\_assets" || exit /b 1
git symbolic-ref -q HEAD >nul 2>&1
if %errorlevel% neq 0 (
    echo Detached HEAD > branch.txt
) else (
    git branch --show-current > branch.txt || (popd & exit /b 1)
)
git rev-parse --short HEAD > commit_hash.txt || (popd & exit /b 1)
powershell -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'" > build_date.txt || (popd & exit /b 1)
popd
