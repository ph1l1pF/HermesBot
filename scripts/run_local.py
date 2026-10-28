"""Start one local worker with literal (non-interpolated) dotenv secrets."""
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

root = Path(__file__).resolve().parents[1]
os.chdir(root)
load_dotenv(root / '.env', interpolate=False)
sys.path.insert(0, str(root / 'src'))
uvicorn.run('shopify_cargoboard_bridge.app:app', host='127.0.0.1', port=8000, workers=1)
