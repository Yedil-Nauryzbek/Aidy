"""Task-local explicit path grants and bounded text operations."""
import os
from pathlib import Path
import re
import json
import fnmatch
import shutil
import time
from itertools import islice

from .ai_tools import AIError
from .ai_engine import Result


def known_folders():
    import winreg
    values={}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
            for label,name in [('desktop','Desktop'),('documents','Personal'),('downloads','{374DE290-123F-4565-9164-39C4925E467B}'),
                               ('pictures','My Pictures'),('music','My Music'),('videos','My Video')]:
                try:
                    path=Path(os.path.expandvars(winreg.QueryValueEx(key,name)[0]))
                    if path.is_dir():values[label]=str(path)
                except OSError:pass
    except OSError:pass
    return values


class TaskFiles:
    TEXT_TYPES = {'.txt', '.md', '.csv', '.json', '.log', '.rtf'}

    def __init__(self, request):
        self.grants = []
        names=known_folders()
        for name,pattern in [('desktop',r'\bdesktop\b|рабоч(?:ем|ий|его)\s+стол'),
                             ('documents',r'\b(?:my )?documents\b|документ(?:ах|ов|ы)\b'),
                             ('downloads',r'\bdownloads\b|загрузк(?:ах|и|у)\b'),
                             ('pictures',r'\bpictures\b|изображени|фотограф'),
                             ('music',r'\bmusic\b|музык'),('videos',r'\bvideos\b|видео')]:
            if name in names and re.search(pattern,request,re.I):
                self.grants.append((self.canonical(names[name]),True))
        # Quoted paths support spaces. Bare paths stop at whitespace/punctuation.
        pattern = r'["\u201c]([A-Za-z]:\\[^"\u201d\r\n]+)["\u201d]|([A-Za-z]:\\[^\s"<>|,;]+)'
        for match in re.finditer(pattern, request):
            value = (match[1] or match[2]).rstrip('.!?')
            path = self.canonical(value)
            self.grants.append((path, path.is_dir()))

    @staticmethod
    def canonical(value):
        if not isinstance(value, str) or not re.match(r'^[A-Za-z]:\\', value) or any(c in value[2:] for c in ':*?<>|\x00'):
            raise AIError('Use an explicit local absolute path, quoted if it contains spaces.')
        path = Path(value)
        # Reject junctions/symlinks, alternate streams, device and network paths.
        for part in (path, *path.parents):
            if part.exists() and (part.is_symlink() or (part.stat().st_file_attributes & 0x400)):
                raise AIError('Links and junctions are outside this task path scope.')
        return path.resolve()

    def path(self, value, write=False):
        if isinstance(value,str):
            value=value.strip()
            for _ in range(2):
                if value.startswith('\\"') and value.endswith('\\"'):
                    value=value[2:-2]
                elif len(value)>1 and (value[0],value[-1]) in {('"','"'),("'","'"),('“','”')}:
                    value=value[1:-1]
                else:
                    break
        path = self.canonical(value)
        if not any(path == grant or (directory and grant in path.parents) for grant, directory in self.grants):
            raise AIError('Name the file or existing folder path in your request first.')
        if write and path.suffix and path.suffix.lower() not in self.TEXT_TYPES:
            raise AIError('Only text document types can be written by this tool.')
        return path

    def execute(self, args):
        action = args['operation']
        source = self.path(args['path'], write=action == 'write')
        if action in {'list','search'}:
            if not source.is_dir():raise AIError('Select an existing folder to list or search.')
            rows=[]
            deadline=time.monotonic()+2
            pending=[source]
            scanned=0
            if action=='search' and not args['text'].strip():raise AIError('What filename or pattern should I search for?')
            while pending and len(rows)<100 and scanned<4000 and time.monotonic()<deadline:
                folder=pending.pop(0)
                try:children=list(islice(folder.iterdir(),4001-scanned))
                except OSError:continue
                for child in children:
                    scanned+=1
                    try:
                        child=self.path(str(child))
                        directory=child.is_dir()
                        if action=='search' and directory:pending.append(child)
                        query=args['text'].strip().casefold()
                        if action=='list' or query and (fnmatch.fnmatchcase(child.name.casefold(),query) or query in child.name.casefold()):
                            rows.append(dict(path=str(child),kind='folder' if directory else 'file',size=None if directory else child.stat().st_size))
                    except (OSError,AIError):continue
                    if len(rows)>=100 or scanned>=4000 or time.monotonic()>=deadline:break
                if action=='list':break
            return Result('verified',json.dumps(dict(items=rows,truncated=bool(pending) or len(rows)>=100 or scanned>=4000),ensure_ascii=False),data=dict(items=rows))
        if action=='copy':
            destination=self.path(args['destination'])
            blocked={'.exe','.dll','.bat','.cmd','.ps1','.vbs','.js','.msi','.scr','.lnk','.url','.py'}
            if not source.is_file() or source.stat().st_size>20*1024*1024 or source.suffix.casefold() in blocked or destination.suffix.casefold() in blocked:
                raise AIError('Copy supports a selected data file up to 20 MB; executable outputs are unavailable.')
            try:
                with source.open('rb') as reader,destination.open('xb') as writer:shutil.copyfileobj(reader,writer)
            except FileExistsError:raise AIError('The destination exists. Nothing was overwritten.') from None
            return Result('verified' if source.read_bytes()==destination.read_bytes() else 'failed','Copied.')
        if action == 'read':
            if not source.is_file() or source.stat().st_size > 65536:
                raise AIError('Choose a text file smaller than 64 KB.')
            try:
                content = source.read_text(encoding='utf-8-sig')
            except UnicodeError:
                raise AIError('This file is not UTF-8 text.') from None
            return Result('verified', content[:8000] or '(Empty file)')
        if action == 'mkdir':
            source.mkdir(exist_ok=False)
            self.grants.append((source, True))
            return Result('verified', 'Folder created.' if source.is_dir() else 'Folder creation could not be verified.')
        if action == 'write':
            try:
                with source.open('x', encoding='utf-8', newline='') as stream:
                    stream.write(args['text'])
            except FileExistsError:
                raise AIError('The output file already exists. Choose a new file path; nothing was overwritten.') from None
            return Result('verified' if source.read_bytes().decode('utf-8') == args['text'] else 'failed', 'Saved.')
        if action in {'rename', 'move'}:
            destination = self.path(args['destination'], write=source.is_file())
            if destination.exists():
                raise AIError('The destination already exists; nothing was overwritten.')
            source.rename(destination)
            return Result('verified' if destination.exists() and not source.exists() else 'failed', 'Moved.')
        if action == 'recycle':
            if not source.is_file() or source.stat().st_size > 10 * 1024 * 1024:
                raise AIError('Recycle supports a single local file up to 10 MB.')
            # Existing pywin32, modern shell API. RECYCLEONDELETE explicitly
            # refuses permanent deletion when recycling is unavailable.
            import pythoncom
            from win32com.shell import shell, shellcon
            pythoncom.CoInitialize()
            try:
                operation = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None,
                    pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
                operation.SetOperationFlags(0x00080000 | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI)
                item = shell.SHCreateItemFromParsingName(str(source), None, shell.IID_IShellItem)
                operation.DeleteItem(item, None)
                operation.PerformOperations()
                ok = not operation.GetAnyOperationsAborted() and not source.exists()
                return Result('verified' if ok else 'failed', 'Recycled.' if ok else 'Recycle could not be verified; no permanent-delete fallback was used.')
            finally:
                pythoncom.CoUninitialize()
        raise AIError('Unsupported file operation.')
