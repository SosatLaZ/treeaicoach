@echo off
rem ==========================================================================
rem  TreeAI Coach - builds dist\TreeAICoach.exe on Windows.
rem  Usage: double-click, or  packaging\build_exe.bat [--skip-tests] [--no-pause]
rem  Steps: venv (build\venv) - dependencies - tests - PyInstaller - exe self-test.
rem  This file must keep CRLF line endings and ASCII before the "chcp" line.
rem ==========================================================================
setlocal EnableExtensions
chcp 65001 >nul
title Construction de TreeAICoach.exe

set "SKIP_TESTS=0"
set "NO_PAUSE=0"
set "RC=0"
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--skip-tests" set "SKIP_TESTS=1"
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"
shift
goto parse_args
:args_done

pushd "%~dp0.." || (set "ERRMSG=Dossier du projet introuvable." & goto fail)

echo.
echo ==============================================================
echo    TreeAI Coach : construction de TreeAICoach.exe
echo ==============================================================
echo    Dossier du projet : %CD%
echo.

rem ---------------------------------------------------------------- Python
set "PY="
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3.11"
:try_python
if defined PY goto python_found
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
:python_found
if not defined PY (
    set "ERRMSG=Python 3.11 est introuvable. Installe-le depuis https://www.python.org/downloads/ en cochant « Add python.exe to PATH », puis relance ce script."
    goto fail
)
echo    Python utilisé : %PY%

rem ------------------------------------------------------------------ venv
set "VENV=%CD%\build\venv"
set "VPY=%VENV%\Scripts\python.exe"
echo.
echo [1/5] Environnement virtuel : %VENV%
if exist "%VPY%" goto venv_ready
%PY% -m venv "%VENV%"
if errorlevel 1 (
    set "ERRMSG=Impossible de créer l'environnement virtuel."
    goto fail
)
:venv_ready

rem ---------------------------------------------------------- dependencies
echo.
echo [2/5] Installation des dépendances (requirements-dev.txt)...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install -r requirements-dev.txt --disable-pip-version-check
if errorlevel 1 (
    set "ERRMSG=L'installation des dépendances a échoué. Vérifie ta connexion Internet."
    goto fail
)

rem ----------------------------------------------------------------- tests
echo.
if "%SKIP_TESTS%"=="1" (
    echo [3/5] Tests ignorés à la demande.
    goto build
)
echo [3/5] Lancement des tests...
"%VPY%" -m pytest -q
if errorlevel 1 (
    set "ERRMSG=Des tests ont échoué : construction annulée. Relance avec --skip-tests pour forcer."
    goto fail
)

rem ----------------------------------------------------------------- build
:build
echo.
echo [4/5] Construction de l'exécutable avec PyInstaller (quelques minutes)...
"%VPY%" -m PyInstaller packaging\treeaicoach.spec --noconfirm --clean
if errorlevel 1 (
    set "ERRMSG=PyInstaller a échoué. Regarde les messages ci-dessus."
    goto fail
)
if not exist "dist\TreeAICoach.exe" (
    set "ERRMSG=dist\TreeAICoach.exe est introuvable après la construction."
    goto fail
)

rem ------------------------------------------------------------- self-test
echo.
echo [5/5] Autotest de l'exécutable...
set "SELFTEST=%CD%\build\selftest.txt"
if exist "%SELFTEST%" del /q "%SELFTEST%"
start "TreeAICoach selftest" /wait "dist\TreeAICoach.exe" --selftest --selftest-out "%SELFTEST%"
set "SELFTEST_RC=%ERRORLEVEL%"
if exist "%SELFTEST%" type "%SELFTEST%"
echo.
if "%SELFTEST_RC%"=="0" (
    echo    Autotest réussi.
) else (
    echo    ATTENTION : l'autotest a renvoyé le code %SELFTEST_RC%. L'exe est construit, mais vérifie le rapport ci-dessus.
)

echo.
echo ==============================================================
echo    TERMINÉ. Ton exécutable est prêt :
echo    %CD%\dist\TreeAICoach.exe
echo ==============================================================
goto end

:fail
set "RC=1"
echo.
echo [ERREUR] %ERRMSG%

:end
popd
if "%NO_PAUSE%"=="0" (
    echo.
    pause
)
endlocal & exit /b %RC%
