import os
import uvicorn
from app import config

if __name__ == '__main__':
    uvicorn.run('app.main:app', host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '8000')),
                log_level=os.getenv('LOG_LEVEL', 'info'), proxy_headers=True,
                forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS', '127.0.0.1'))
