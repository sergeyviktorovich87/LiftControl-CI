"""Disposable emulator fixture: break API connectivity without a production hook."""
import http.server
import json
import socket
import threading
import urllib.error
import urllib.request

state = {'offline': False, 'lose_next_response': False,'lose_next_media_response': False}
lock = threading.Lock()


class Proxy(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle_request(self):
        with lock:
            offline = state['offline']
        if offline:
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        size = int(self.headers.get('Content-Length', 0))
        if size > 32 * 1024 * 1024:
            self.send_error(413); return
        headers = {k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','content-length')}
        request = urllib.request.Request('http://127.0.0.1:8000'+self.path,
            data=self.rfile.read(size) if size else None, method=self.command, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=40)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = response.read()
            with lock:
                lose = state['lose_next_response'] and self.path == '/api/mobile/sync/' and response.status in (200,201)
                if lose: state['lose_next_response'] = False
                if state['lose_next_media_response'] and self.path == '/api/media/uploads/' and response.status in (200,201):
                    lose=True;state['lose_next_media_response']=False
            if lose:
                self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
            self.send_response(response.status)
            for k,v in response.headers.items():
                if k.lower() not in ('connection','transfer-encoding','content-length'): self.send_header(k,v)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers(); self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = do_DELETE = handle_request


class Control(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length',0)),1024)))
        with lock:
            for key in state:
                if key in data: state[key] = bool(data[key])
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')


if __name__ == '__main__':
    threading.Thread(target=http.server.ThreadingHTTPServer(('127.0.0.1',18083),Control).serve_forever,daemon=True).start()
    http.server.ThreadingHTTPServer(('127.0.0.1',18082),Proxy).serve_forever()
