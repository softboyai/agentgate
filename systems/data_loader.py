import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_json(filename: str) -> Any:
    with (DATA_DIR / filename).open(encoding="utf-8") as data_file:
        return json.load(data_file)
