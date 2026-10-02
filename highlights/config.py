"""Runtime locations independent of the caller's working directory."""
import os
from pathlib import Path

CHECKOUT=Path(__file__).resolve().parents[1]

def data_dir():
    if os.environ.get('MATCHLORE_DATA'):return Path(os.environ['MATCHLORE_DATA']).expanduser()
    local=CHECKOUT/'data'
    if local.is_dir():return local
    return Path.home()/'.local/share/matchlore/data'

def env_file():
    if os.environ.get('MATCHLORE_ENV_FILE'):return Path(os.environ['MATCHLORE_ENV_FILE']).expanduser()
    local=CHECKOUT/'.env'
    if (CHECKOUT/'pyproject.toml').exists() and local.is_file():return local
    return Path.home()/'.config/matchlore/.env'
