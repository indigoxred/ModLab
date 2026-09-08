"""Narrow xEdit 4.1.5f script-mode handshake for a private, read-only job.

This release ignores autoload/autoexit in script mode. As with PGPatcher's
completion dialog, handle only the process MO2 launched, never a user's xEdit.
The structured report and unchanged files, not dialog closure, prove success.
"""

def report_finished(text,job_id):
    lines=text.lstrip('\ufeff').splitlines()
    return bool(lines and lines[0]=='MODLAB-WINNERS-1\t'+job_id and lines[-1]=='END\t'+job_id)


def dialog_action(cls,title,selected,complete,children):
    if not selected and cls=='TfrmModuleSelect' and title=='Module Selection' and children.count(('TButton','OK'))==1:
        return 'select'
    if complete and cls=='TfrmMain' and title=='SSEScript 4.1.5f x64':return 'close'
    return None


def selection_step(state,present):
    if not present:
        if state.get('seen'):state['selected']=True
        return None
    state['seen']=True
    state['attempts']=state.get('attempts',0)+1
    return 'click' if state['attempts']<=20 else 'cancel'


def advance(process_handle,job,names,state):
    import ctypes
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    user=ctypes.WinDLL('user32',use_last_error=True)
    kernel.GetProcessId.argtypes=[w.HANDLE];kernel.GetProcessId.restype=w.DWORD
    pid=kernel.GetProcessId(w.HANDLE(int(process_handle)))
    if not pid:raise OSError('The launched xEdit process could not be identified.')
    cb=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    user.EnumWindows.argtypes=[cb,w.LPARAM];user.EnumChildWindows.argtypes=[w.HWND,cb,w.LPARAM]
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    user.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    user.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    user.IsWindowVisible.argtypes=[w.HWND];user.IsWindowEnabled.argtypes=[w.HWND]
    user.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];user.PostMessageW.restype=w.BOOL
    complete=False
    report=job/'winning-records.tsv'
    if report.is_file():
        try:
            # Completion permits closing this private helper, never accepting
            # its result. Full parsing follows process exit; malformed reports
            # must fail there instead of leaving an idle helper open forever.
            complete=report_finished(report.read_text(encoding='utf-8-sig'),job.name)
        except (OSError,ValueError):pass  # An incomplete write is not completion.
    actions=[];selection_present=[]
    def describe(hwnd):
        cls=ctypes.create_unicode_buffer(256);title=ctypes.create_unicode_buffer(512)
        user.GetClassNameW(hwnd,cls,len(cls));user.GetWindowTextW(hwnd,title,len(title))
        return cls.value,title.value
    @cb
    def visit(hwnd,unused):
        owner=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value!=pid or not user.IsWindowVisible(hwnd):return True
        children=[]
        @cb
        def child(button,unused):
            if user.IsWindowVisible(button) and user.IsWindowEnabled(button):children.append((button,describe(button)))
            return True
        user.EnumChildWindows(hwnd,child,0)
        action=dialog_action(*describe(hwnd),state.get('selected',False),complete,[d for _,d in children])
        if action=='select':
            selection_present.append(hwnd)
            step=selection_step(state,True)
            if step=='click':
                button=next(h for h,d in children if d==('TButton','OK'))
                user.PostMessageW(button,0x00F5,0,0)  # Queued BM_CLICK is not acknowledgment.
            elif user.PostMessageW(hwnd,0x0010,0,0):
                actions.append('cancel')  # Cancel only this private job's selection dialog.
        elif action=='close' and user.IsWindowEnabled(hwnd):
            if user.PostMessageW(hwnd,0x0010,0,0):actions.append('close')  # WM_CLOSE
        return True
    user.EnumWindows(visit,0)
    if not selection_present:selection_step(state,False)
    if 'cancel' in actions:raise RuntimeError('xEdit did not accept its prepared selection. The private check was cancelled; no result was accepted.')
    return 'close' in actions
