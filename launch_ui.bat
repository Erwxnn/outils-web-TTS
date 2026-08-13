@echo off
setlocal
cd /d "%~dp0"
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
)

rem On installe depuis le lock complet (versions transitives figees), pas depuis
rem requirements.txt, pour que l'environnement soit reproductible a l'identique.
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
if errorlevel 1 (
  echo.
  echo [ERREUR] L'installation des dependances a echoue.
  echo L'application n'est pas lancee : une dependance manquante casserait
  echo silencieusement une fonctionnalite ^(import .docx / .pdf, par exemple^).
  echo Corrige l'erreur affichee ci-dessus, puis relance ce script.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m streamlit run ui.py

endlocal
