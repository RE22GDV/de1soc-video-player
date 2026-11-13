# -*- coding: utf-8 -*-
"""Посередник CPUlator: розірване сервером з'єднання пробується ще раз (без інтернету)."""
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
from devideo import helper                         # noqa: E402

BODY = b'<html><body>CPUlator</body></html>'


class FlakyServer:
    """Перші `drops` з'єднань обриває (RST), далі відповідає сторінкою."""

    def __init__(self, drops):
        self.drops, self.seen = drops, 0
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.seen += 1
            conn.recv(65536)
            if self.seen <= self.drops:
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            else:
                conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\n'
                             b'Connection: close\r\n\r\n%s' % (len(BODY), BODY))
            conn.close()

    def close(self):
        self.sock.close()


class Proxy(unittest.TestCase):
    def setUp(self):
        self.saved = helper.UP, helper.UP_RETRY_DELAY
        helper.UP_RETRY_DELAY = 0.01
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        helper.UP, helper.UP_RETRY_DELAY = self.saved
        self.tmp.cleanup()

    def fetch(self, drops):
        srv = FlakyServer(drops)
        helper.UP = 'http://127.0.0.1:%d' % srv.port
        h = helper.start(self.tmp.name, 0, port=18766)
        try:
            return urllib.request.urlopen('http://127.0.0.1:%d/?sys=arm-de1soc' % h.port, timeout=20)
        finally:
            h.stop()
            srv.close()

    def test_dropped_connections_are_retried(self):
        r = self.fetch(drops=helper.UP_ATTEMPTS - 1)
        self.assertEqual(r.status, 200)
        self.assertIn(b'CPUlator', r.read())

    def test_gives_up_with_clear_message(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.fetch(drops=helper.UP_ATTEMPTS)
        self.assertEqual(cm.exception.code, 502)
        self.assertIn('CPUlator', cm.exception.read().decode('utf-8'))

    def test_local_file_is_served_only_from_output_folder(self):
        with open(os.path.join(self.tmp.name, 'clip.elf'), 'wb') as f:
            f.write(b'\x7fELF')
        h = helper.start(self.tmp.name, 0, port=18766)
        try:
            ok = urllib.request.urlopen('http://127.0.0.1:%d/local/clip.elf' % h.port, timeout=5)
            self.assertEqual(ok.read(), b'\x7fELF')
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen('http://127.0.0.1:%d/local/..%%2F..%%2Fsecret.txt' % h.port, timeout=5)
            self.assertEqual(cm.exception.code, 404)
        finally:
            h.stop()


if __name__ == '__main__':
    unittest.main()
