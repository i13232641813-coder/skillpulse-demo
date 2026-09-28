from http.server import HTTPServer, SimpleHTTPRequestHandler
import os

PORT = 8000
DIR = os.path.abspath('..')

os.chdir(DIR)
print('serving', DIR, 'on', PORT)
HTTPServer(('127.0.0.1', PORT), SimpleHTTPRequestHandler).serve_forever()

