"""Run the personal app on loopback and open its browser after readiness."""
import json
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def open_when_ready(url):
    for _ in range(100):
        try:
            with urlopen(url + '/api/health', timeout=1) as response:
                if json.load(response).get('status') == 'ok':
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(.1)
    print('El servidor no terminó de arrancar. Revisa el mensaje de error de esta ventana.')


def main():
    import uvicorn
    from app.main import app
    from app.documents import office_binary
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 8000))
        except OSError:
            probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    print('\nCerticoex se abrirá en tu navegador. Mantén esta ventana abierta mientras trabajas.')
    print('Para cerrar la app, pulsa Ctrl+C en esta ventana.\n')
    if not office_binary():
        print('AVISO: instala LibreOffice Writer para generar también PDF. El Word sí está disponible.\n')
    threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host='127.0.0.1', port=port)


if __name__ == '__main__':
    main()
