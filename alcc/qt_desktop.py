"""Two native Qt WebEngine views in ONE top-level window.

The local control dashboard has a narrowly scoped Python bridge. The remote
ChatGPT view has NO bridge to Python/SSH and cannot access dashboard secrets.
"""
from __future__ import annotations
import json
import threading
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QSplitter, QStackedWidget, QVBoxLayout, QWidget
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from .api import ControlAPI
from .chat_resume import read_chat_url, store_chat_url

# Do NOT expose arbitrary object attributes to untrusted JavaScript.
API_METHODS = frozenset({
    'bootstrap','save_settings','environment_check','set_chat_visible',
    'coord_action','coord_start','coord_auto','coord_manual','coord_advanced','coord_stop','coord_output',
    'pi_status','pi_logs','export_logs_desktop','pi_pull','sync_bot','sync_cache','list_workspaces','list_files','read_file',
    'save_file','file_diff','project_update','project_note','project_prompt','prepare_chat_context',
    'open_chat','open_chat_browser','new_chat','confirm_new_chat',
    'validate_workspace','test_candidate','one_click_release','deploy_preview','deploy_apply',
    'deploy_rollback','open_data_dir'
})
# These methods must execute on the GUI thread because they manage Qt widgets.
GUI_METHODS = frozenset({'set_chat_visible','open_chat','new_chat','confirm_new_chat'})


class Bridge(QObject):
    resultReady = Signal(str, str)

    def __init__(self, api):
        super().__init__()
        self.api=api

    @Slot(str, str, str)
    def invoke(self, ident, method, arguments):
        if method not in API_METHODS:
            self._result(ident, False, error='Nicht erlaubte API-Methode')
            return
        try:
            args=json.loads(arguments)
            if not isinstance(args,list) or len(args)>8:
                raise ValueError('Ungültige Argumentliste')
        except Exception as exc:
            self._result(ident, False, error=str(exc))
            return
        if method in GUI_METHODS:
            self._call(ident,method,args)
        else:
            threading.Thread(target=self._call,args=(ident,method,args),daemon=True).start()

    def _result(self, ident, ok, value=None, error=None):
        data=json.dumps({'ok':ok,'value':value,'error':error},ensure_ascii=False,default=str)
        self.resultReady.emit(ident,data)

    def _call(self,ident,method,args):
        try:
            value=getattr(self.api,method)(*args)
            self._result(ident,True,value=value)
        except Exception as exc:
            self._result(ident,False,error=str(exc))


class ChatHandle:
    def __init__(self, host, view):
        self.host=host
        self.view=view
        self.destroyed=False

    def destroy(self):
        if self.destroyed:return
        self.destroyed=True
        if self.host.stack.indexOf(self.view)>=0:
            self.host.stack.removeWidget(self.view)
        self.view.setParent(None)
        self.view.deleteLater()


class ChatHost:
    def __init__(self, splitter, settings):
        self.splitter=splitter
        self.settings=settings
        self.stack=QStackedWidget()
        self.placeholder=QLabel('ChatGPT / Bot – Chatansicht wird geöffnet …')
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet('background:#101b2b;color:#bac8d9;font-size:15px;')
        self.stack.addWidget(self.placeholder)
        self.splitter.addWidget(self.stack)
        self.stack.hide()
        self.current=None
        self.api=None
        profile=QWebEngineProfile('ALCC-ChatGPT',self.stack)
        storage=Path(settings.data)/'qt-chat-profile'
        storage.mkdir(parents=True,exist_ok=True)
        profile.setPersistentStoragePath(str(storage))
        profile.setCachePath(str(storage/'cache'))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile=profile

    def create_window(self,title,url,**kwargs):
        # This creates a browser TAB in the same main window, not a top-level window.
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname not in ('chatgpt.com','www.chatgpt.com'):
            raise ValueError('Nur ChatGPT.com als Chat-Startadresse erlaubt')
        from PySide6.QtWebEngineCore import QWebEnginePage
        view=QWebEngineView(self.stack)
        view.setPage(QWebEnginePage(self.profile,view))
        self.stack.addWidget(view)
        view.urlChanged.connect(lambda next_url: store_chat_url(Path(self.settings.data), next_url.toString()))
        view.setUrl(QUrl(url))
        self.stack.setCurrentWidget(view)
        self.current=ChatHandle(self,view)
        self.set_visible(True)
        return self.current

    def ensure_chat(self,url):
        if self.current and not self.current.destroyed:
            self.stack.setCurrentWidget(self.current.view)
            self.set_visible(True)
            return self.current
        return self.create_window('ChatGPT',read_chat_url(Path(self.settings.data)) or url)

    def set_visible(self,visible):
        if visible and not self.current:
            self.ensure_chat(self.settings.values['chat_url'])
            if self.api is not None:self.api.chat=self.current
        self.stack.setVisible(bool(visible))
        if visible:self.splitter.setSizes([730,960])
        return {'integrated':True,'visible':bool(visible),'same_window':True}


class DesktopWindow(QMainWindow):
    def __init__(self, resource_root):
        super().__init__()
        self.setWindowTitle('AdventureLand Control Center – ChatGPT integriert')
        self.resize(1720,990)
        self.setMinimumSize(1080,700)
        self.splitter=QSplitter(Qt.Orientation.Horizontal,self)
        self.setCentralWidget(self.splitter)
        self.dashboard=QWebEngineView(self.splitter)
        self.splitter.addWidget(self.dashboard)
        self.chat_host=None
        self.api=ControlAPI()
        self.chat_host=ChatHost(self.splitter,self.api.settings)
        self.chat_host.api=self.api
        self.api.webview=self.chat_host
        self.bridge=Bridge(self.api)
        self.channel=QWebChannel(self.dashboard.page())
        self.channel.registerObject('alccBridge',self.bridge)
        self.dashboard.page().setWebChannel(self.channel)
        html=Path(resource_root)/'assets'/'index.html'
        self.dashboard.setUrl(QUrl.fromLocalFile(str(html.resolve())))
        self.splitter.setCollapsible(0,False)
        self.splitter.setCollapsible(1,False)
        # Reopen the existing ChatGPT WebEngine profile with the application.
        # The remote page has no local API bridge and receives NO context until
        # the user deliberately pastes it.
        self.chat_host.set_visible(True)


def main(resource_root):
    import sys
    app=QApplication(sys.argv)
    app.setApplicationName('AdventureLand Control Center')
    window=DesktopWindow(resource_root)
    window.show()
    return app.exec()
