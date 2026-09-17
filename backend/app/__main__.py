"""便捷启动入口：在 backend/ 目录下执行 `python -m app` 即可启动服务。

注意：
- 必须在 backend/ 目录（而非 backend/app）执行，才能保证 `app` 作为包被加载、相对导入正常工作。
- 必须使用已安装依赖的虚拟环境 Python（medical-agent/.venv/Scripts/python.exe）。
- 等效于 `python -m uvicorn app.main:app`，host/port 可用环境变量覆盖。
"""
import os

import uvicorn

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8600"))
    reload = os.getenv("APP_RELOAD", "0") in ("1", "true", "True")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)
