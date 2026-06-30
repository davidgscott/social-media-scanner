@echo off
REM Weekly voice-correction distiller for Windows Task Scheduler.
REM Reads David's recent draft->posted edits and regenerates the auto-distilled
REM half of voice-corrections.md. His "My rules" section is never touched.
REM
REM ONE-TIME SETUP: set PYTHON below to the SAME python.exe your nightly
REM "STG Content Monitor" task uses. To find it, run:
REM   schtasks /Query /TN "STG Content Monitor" /V /FO LIST
REM and copy the "Task To Run" python path.

set "PYTHON=C:\apps\social-media-scanner\.venv\Scripts\python.exe"

REM Repo root = the folder above this script. No need to edit this.
set "PROJ=%~dp0.."

cd /d "%PROJ%"
"%PYTHON%" "%PROJ%\src\analyze_edits.py" >> "%PROJ%\runs.log" 2>&1
