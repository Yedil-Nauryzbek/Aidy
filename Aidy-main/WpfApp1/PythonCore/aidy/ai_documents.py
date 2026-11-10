"""Fixed Microsoft Word document operations via its registered COM object model."""
import hashlib

from .ai_tools import AIError, AIPlanError
from .ai_windows import owner, process_name, _user


def normalized(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


class WordDocuments:
    def __init__(self):
        self.owned = {}

    def write(self, window, text, new_document=True):
        import win32com.client
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise AIError('The draft is empty or too large.')
        if new_document:
            # A separate registered instance never replaces the user's document.
            app = win32com.client.DispatchEx('Word.Application')
            app.Visible = True
            doc = app.Documents.Add(Visible=True)
            hwnd = int(doc.ActiveWindow.Hwnd)
            pid = owner(hwnd)
            if not pid or process_name(pid) != 'winword.exe':
                raise AIError('Word did not expose the created document window.')
            self.owned[hwnd] = (app, doc)
        else:
            if not window or process_name(window['pid']) != 'winword.exe' or owner(window['hwnd']) != window['pid']:
                raise AIError('Select the intended Word document first.')
            hwnd = window['hwnd']
            if hwnd in self.owned:
                app, doc = self.owned[hwnd]
            else:
                app = win32com.client.GetActiveObject('Word.Application')
                if int(app.ActiveWindow.Hwnd) != hwnd:
                    raise AIError('Word selected a different document. No text was changed.')
                doc = app.ActiveDocument
            # A follow-up write into an existing editor is allowed only if empty.
            if normalized(doc.Content.Text).strip():
                raise AIPlanError('No text was inserted: this Word document contains user text. For a new draft use document_write with new_document=true, preserving the existing document.')
        if int(doc.ProtectionType) != -1:
            raise AIError('The Word document is protected. No text was changed.')
        doc.Activate()
        doc.Content.Text = normalized(text).replace('\n', '\r')
        actual = doc.Content.Text
        if actual.endswith('\r'):
            actual = actual[:-1]  # Word's required final paragraph marker.
        if normalized(actual) != normalized(text):
            raise AIError('Word text readback did not match. It will not be repeated.')
        pid = owner(hwnd)
        return dict(status='verified', message='Written.',
                    window=dict(hwnd=hwnd, pid=pid, process='winword.exe', title=str(doc.Name) + ' - Word'),
                    word_count=len(text.split()), text_sha256=hashlib.sha256(normalized(actual).encode()).hexdigest())
