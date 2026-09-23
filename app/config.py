import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')

def env_int(name, default, minimum=1, maximum=1000000000000000):
    value = int(os.getenv(name, default))
    if not minimum <= value <= maximum:
        raise ValueError(f'{name}: value out of range')
    return value

DATABASE_PATH = Path(os.getenv('DATABASE_PATH', str(ROOT / 'data' / 'akim.sqlite3')))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = ROOT / DATABASE_PATH

def credentials():
    return {'admin': os.getenv('ADMIN_API_TOKEN', ''), 'agent': os.getenv('AGENT_API_TOKEN', '')}

def openai_config():
    return {
        'key': os.getenv('OPENAI_API_KEY', ''),
        'model': os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
        'agent_model': os.getenv('OPENAI_AGENT_MODEL', 'gpt-4.1-mini'),
        'base': os.getenv('OPENAI_API_BASE', 'https://api.openai.com/v1').rstrip('/'),
        'timeout': env_int('OPENAI_TIMEOUT_SECONDS', 40, maximum=120),
        'max_steps': env_int('AGENT_MAX_STEPS', 8, maximum=16),
    }
