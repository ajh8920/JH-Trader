@echo off
chcp 65001 >nul
cd /d "c:\Users\ajh89\Desktop\AI 공부\000.Project\stock-tracker-py"
"C:\Users\ajh89\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe" -u -m data_pipeline.fetch_fundamentals_dart --api-key-env DART_API_KEY --year-shard 0 --shard-count 2 >> "C:\Users\ajh89\Desktop\AI 공부\000.Data\logs\fetch_fundamentals_dart.log" 2>&1