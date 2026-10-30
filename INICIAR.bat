@echo off
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  python scripts\launch.py
) else (
  py -3 scripts\launch.py
)
if errorlevel 1 (
  echo.
  echo Si no tienes Python, instala Python 3.12 o posterior desde python.org.
  echo Para generar PDF necesitas tambien LibreOffice Writer.
  pause
)
