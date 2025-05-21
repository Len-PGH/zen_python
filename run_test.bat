@echo off
echo Starting Flask app...
start /B python app.py
REM Wait for 5 seconds to allow the server to start
timeout /t 5 > NUL
echo Running test...
python test_swaig.py 