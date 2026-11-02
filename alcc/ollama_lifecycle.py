"""Conservative local Ollama resource lifecycle for Control Center sessions.

Uses only localhost and a fixed model. Does not kill unrelated desktop services.
"""
from __future__ import annotations
import json, os, shutil, subprocess, time
from urllib import request, error

MODEL='qwen3.8:27b'
URL='http://127.0.0.1:11434'

class OllamaLifecycle:
    def __init__(self):
        self.owned_server=None

    @staticmethod
    def _request(path, payload=None, timeout=3):
        data=json.dumps(payload).encode('utf-8') if payload is not None else None
        req=request.Request(URL+path,data=data,headers={'Content-Type':'application/json'} if data else {},method='POST' if data else 'GET')
        opener=request.build_opener(request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return resp.read(512*1024)

    def ensure_ready(self):
        try:
            self._request('/api/tags')
            return {'running':True,'owned':False}
        except (OSError, ValueError):
            pass
        exe=shutil.which('ollama')
        if not exe:
            # The coordinator may still be able to use Ollama via its own setup.
            return {'running':False,'owned':False,'reason':'ollama_cli_not_found'}
        kwargs={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
        if os.name=='nt': kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
        self.owned_server=subprocess.Popen([exe,'serve'],**kwargs)
        for _ in range(15):
            if self.owned_server.poll() is not None:break
            try:
                self._request('/api/tags',timeout=1)
                return {'running':True,'owned':True}
            except (OSError,ValueError):
                time.sleep(.5)
        # Do not start a coordinator session against a failed managed backend.
        self.owned_server.terminate()
        try:self.owned_server.wait(timeout=3)
        except subprocess.TimeoutExpired:pass
        self.owned_server=None
        raise RuntimeError('Ollama konnte lokal nicht gestartet werden')

    def shutdown(self):
        unloaded=False
        try:
            # keep_alive=0 releases the coordinator's model RAM/VRAM.
            self._request('/api/generate',{'model':MODEL,'keep_alive':0,'stream':False},timeout=12)
            unloaded=True
        except (OSError,ValueError):
            pass
        owned=self.owned_server
        self.owned_server=None
        if owned is not None:
            if owned.poll() is None:
                try:owned.terminate();owned.wait(timeout=8)
                except (OSError,subprocess.TimeoutExpired):
                    return {'unloaded':unloaded,'server_terminated':False,'message':'Modell entladen; verwalteter Ollama-Server beendet sich noch.'}
            return {'unloaded':unloaded,'server_terminated':True,'message':'Ollama-Modell entladen, eigener Ollama-Server beendet.'}
        return {'unloaded':unloaded,'server_terminated':False,'message':('Koordinator-Modell entladen; externer Ollama-Dienst bleibt aus Rücksicht auf andere Anwendungen bestehen.' if unloaded else 'Ollama-Modell konnte nicht entladen werden; externer Dienst bleibt unverändert.')}
