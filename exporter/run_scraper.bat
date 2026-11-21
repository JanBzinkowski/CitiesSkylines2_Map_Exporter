@echo off
cd /d "%~dp0"
python reddit_scraper.py --hours 4 %*
pause
