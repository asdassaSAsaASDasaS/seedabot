import sys
import importlib
import time

sys.path.insert(0, '/Users/fathimathishana/Desktop/seedemanalise')

print('DEBUG: importing dashboard module')
dashboard = importlib.import_module('dashboard')

import flet
print('DEBUG: exporting ASGI app from flet')
asgi_app = flet.run(dashboard.main_dashboard, export_asgi_app=True)
print('DEBUG: exported ASGI app:', type(asgi_app))

try:
    import uvicorn
    print('DEBUG: starting uvicorn on 127.0.0.1:8550')
    uvicorn.run(asgi_app, host='127.0.0.1', port=8550)
except Exception as e:
    print('DEBUG: uvicorn failed to start:', e)
