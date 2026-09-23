"""Generate local application access tokens without printing them."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent
target = root / '.env'
if target.exists():
    print('.env already exists; unchanged.')
else:
    text = (root / '.env.example').read_text(encoding='utf-8')
    text = text.replace('ADMIN_API_TOKEN=\n', 'ADMIN_API_TOKEN=' + secrets.token_urlsafe(32) + '\n')
    text = text.replace('AGENT_API_TOKEN=\n', 'AGENT_API_TOKEN=' + secrets.token_urlsafe(32) + '\n')
    target.write_text(text, encoding='utf-8')
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print('Created .env with separate random access tokens. Add OPENAI_API_KEY locally.')
