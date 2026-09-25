"""Start the app:  python -m trainer   (then open http://localhost:8000)"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("trainer.app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
