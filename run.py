#!/usr/bin/env python3
"""Anota Workbench entry point.  python run.py --demo  ->  http://localhost:8420"""
import argparse
import os
import tempfile
from pathlib import Path

import uvicorn

from app import importer
from app.main import create_app

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Anota Workbench")
    ap.add_argument("--demo", action="store_true", help="fresh demo DB + synthetic data")
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument("--host", default="127.0.0.1",
                     help="bind address (use 0.0.0.0 inside containers)")
    ap.add_argument("--db", default="anota.db")
    ap.add_argument("--import-file", help="JSONL file to import as a new batch")
    ap.add_argument("--profile", choices=["generic", "aqb"], default="generic",
                     help="field-mapping profile for --import-file (default: generic)")
    ap.add_argument("--lang", choices=["en-es", "zh-en"], default="en-es",
                     help="lang_profile used for LFs on the imported batch (default: en-es)")
    ap.add_argument("--batch", help="batch name for --import-file (default: file stem)")
    ap.add_argument("--golden", help="optional golden-answers JSONL to load after import")
    ap.add_argument("--suggestions", action="store_true",
                     help="show machine suggestions for the imported batch (routing batch)")
    ap.add_argument("--overlap", type=int, default=1,
                     help="annotator overlap for the imported batch (default: 1)")
    a = ap.parse_args()
    db_path = (os.path.join(tempfile.mkdtemp(prefix="anota_demo_"), "demo.db")
               if a.demo else a.db)
    app = create_app(db_path=db_path, demo=a.demo)
    if a.import_file:
        batch_name = a.batch or Path(a.import_file).stem
        res = importer.import_jsonl(app.state.db, a.import_file, a.profile, batch_name,
                                     lang_profile=a.lang, show_suggestions=a.suggestions,
                                     overlap=a.overlap, actor="cli")
        print(f"Imported batch {batch_name!r} (id={res['batch_id']}): "
              f"{res['n']} tasks, {res['skipped']} skipped (duplicate ids)")
        if a.golden:
            n_golden = importer.load_golden(app.state.db, a.golden, actor="cli")
            print(f"Loaded {n_golden} golden answers from {a.golden}")
    print(f"Anota Workbench → http://localhost:{a.port}   (db: {db_path})")
    uvicorn.run(app, host=a.host, port=a.port)
