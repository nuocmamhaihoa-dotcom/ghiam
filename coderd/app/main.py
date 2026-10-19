from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="coderd")


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html>
<html lang="vi">
<meta charset="utf-8">
<title>coderd</title>
<p>coderd đang chạy.</p>
"""
