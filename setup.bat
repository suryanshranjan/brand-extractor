@echo off
echo Installing Brand Extractor...
py -m pip install -e ".[server,login,dev]" || goto :error
py -m spacy download en_core_web_sm || goto :error
py -m playwright install chromium || goto :error
if not exist .env copy .env.example .env >nul
echo.
echo For reading sponsor posters, either put a free OCR.space key in .env (OCRSPACE_API_KEY),
set /p OCR="or install local OCR now (about 2 GB, includes PyTorch)? [y/N]: "
if /i "%OCR%"=="y" py -m pip install -r requirements-ocr.txt || goto :error
echo.
echo Setup complete. Try:  brandscan serve   then open http://127.0.0.1:8000
pause
exit /b 0
:error
echo Setup failed - see the messages above.
pause
exit /b 1
