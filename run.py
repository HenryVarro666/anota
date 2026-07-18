#!/usr/bin/env python3
"""PropioQA Workbench entry point.  python run.py --demo  ->  http://localhost:8420"""
import argparse
import os
import tempfile

import uvicorn

from app.main import create_app

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PropioQA Workbench")
    ap.add_argument("--demo", action="store_true", help="fresh demo DB + synthetic data")
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument("--db", default="propioqa.db")
    a = ap.parse_args()
    db_path = (os.path.join(tempfile.mkdtemp(prefix="propioqa_demo_"), "demo.db")
               if a.demo else a.db)
    app = create_app(db_path=db_path, demo=a.demo)
    print(f"PropioQA Workbench → http://localhost:{a.port}   (db: {db_path})")
    uvicorn.run(app, host="127.0.0.1", port=a.port)
