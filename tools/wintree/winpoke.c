/* winpoke.exe pw TEXT | click CAPTION | menu CAPTION | file PATH | at TITLE,X,Y: drive a GUI
 * program under test.  menu posts the command of the first menu item with that
 * caption (without &); file fills a common file dialog's name box and presses Open.
 * pw puts TEXT in the first visible password box of the session; click
 * presses the first visible enabled button whose caption, without &, is
 * CAPTION (posted: a modal dialog it opens does not block us).  at presses the left
 * button at client point X,Y of the visible top-level window called TITLE, posted to whatever
 * child is there (owner-drawn buttons have no caption to find; the real mouse stays where it is).
 * key TITLE,X,Y posts Space to it instead: a control that looks at the real cursor before it takes
 * a click still takes the key.  mouse TITLE,X,Y goes through Wine's input queue (SendInput), for
 * what checks the input state too (a VB UserControl's Click): meant for a virtual desktop.
 * set TITLE,X,Y=TEXT puts TEXT in the text box at that point.
 * Exit status 0 when the window was found. */
#include <windows.h>

static const WCHAR *want;
static int mode, found;

static void strip(WCHAR *d, const WCHAR *s)
{
    for (; *s; s++)
        if (*s != L'&')
            *d++ = *s;
    *d = 0;
}

static BOOL CALLBACK child(HWND w, LPARAM lp)
{
    WCHAR cls[64], text[256], clean[256];
    LONG st = GetWindowLongW(w, GWL_STYLE);
    if (!IsWindowVisible(w))
        return TRUE;
    GetClassNameW(w, cls, 64);
    /* ask the box itself: a program's multi-line disclaimer box had style bit
     * 20h too, and took the password */
    if (mode == 0 && (!lstrcmpW(cls, L"ThunderRT6TextBox") || !lstrcmpW(cls, L"Edit")) &&
        SendMessageW(w, EM_GETPASSWORDCHAR, 0, 0) != 0) {
        SendMessageW(w, WM_SETTEXT, 0, (LPARAM)want);
        found = 1;
        return FALSE;
    }
    if (mode == 1 && !(st & WS_DISABLED) &&
        (!lstrcmpW(cls, L"ThunderRT6CommandButton") || !lstrcmpW(cls, L"Button"))) {
        text[0] = 0;
        GetWindowTextW(w, text, 256);
        strip(clean, text);
        if (!lstrcmpW(clean, want)) {
            PostMessageW(w, BM_CLICK, 0, 0);
            found = 1;
            return FALSE;
        }
    }
    return TRUE;
}

/* menu handles belong to their process under Wine: winpokedll.dll reads them */
static void inject(HWND w)
{
    WCHAR path[MAX_PATH], *e;
    DWORD pid;
    HANDLE map, proc, th;
    char *view;
    SIZE_T n;
    void *remote;
    GetModuleFileNameW(NULL, path, MAX_PATH);
    e = path + lstrlenW(path);
    while (e > path && e[-1] != L'\\')
        e--;
    lstrcpyW(e, L"winpokedll.dll");
    map = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 4096, L"WinpokeMenu");
    view = MapViewOfFile(map, FILE_MAP_ALL_ACCESS, 0, 0, 0);
    lstrcpyW((WCHAR *)view, want);
    *(int *)(view + 1024) = 0;
    GetWindowThreadProcessId(w, &pid);
    proc = OpenProcess(PROCESS_ALL_ACCESS, FALSE, pid);
    remote = VirtualAllocEx(proc, NULL, sizeof(path), MEM_COMMIT, PAGE_READWRITE);
    WriteProcessMemory(proc, remote, path, sizeof(path), &n);
    th = CreateRemoteThread(proc, NULL, 0, (LPTHREAD_START_ROUTINE)GetProcAddress(
                            GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW"), remote, 0, NULL);
    if (th) {
        WaitForSingleObject(th, 10000);
        CloseHandle(th);
    }
    found = *(int *)(view + 1024);
    CloseHandle(proc);
}

static BOOL CALLBACK file_child(HWND w, LPARAM lp)
{
    WCHAR cls[64];
    GetClassNameW(w, cls, 64);
    if (!lstrcmpW(cls, L"Edit") && IsWindowVisible(w)) {
        *(HWND *)lp = w;
        return FALSE;
    }
    return TRUE;
}

static BOOL CALLBACK toplevel(HWND w, LPARAM lp)
{
    WCHAR cls[64];
    if (!IsWindowVisible(w))
        return TRUE;
    if (mode == 2) {
        if (GetMenu(w))
            inject(w);
    } else if (mode == 3) {
        GetClassNameW(w, cls, 64);
        if (!lstrcmpW(cls, L"#32770")) {
            HWND edit = 0;
            EnumChildWindows(w, file_child, (LPARAM)&edit);
            if (edit) {
                SendMessageW(edit, WM_SETTEXT, 0, (LPARAM)want);
                PostMessageW(w, WM_COMMAND, IDOK, 0);
                found = 1;
            }
        }
    } else
        EnumChildWindows(w, child, 0);
    return !found;
}

static int number(const WCHAR *s)
{
    int n = 0;
    while (*s >= L'0' && *s <= L'9')
        n = n * 10 + *s++ - L'0';
    return n;
}

static void send_mouse(HWND top, POINT pt)
{
    INPUT in;
    int sx = GetSystemMetrics(SM_CXSCREEN), sy = GetSystemMetrics(SM_CYSCREEN);
    ClientToScreen(top, &pt);
    SetForegroundWindow(top);
    in.type = INPUT_MOUSE;
    in.mi.mouseData = 0;
    in.mi.time = 0;
    in.mi.dwExtraInfo = 0;
    in.mi.dx = (pt.x * 65536 + sx / 2) / sx;
    in.mi.dy = (pt.y * 65536 + sy / 2) / sy;
    in.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE;
    SendInput(1, &in, sizeof(in));
    Sleep(150);
    in.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE;
    SendInput(1, &in, sizeof(in));
    Sleep(300);
    in.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE;
    SendInput(1, &in, sizeof(in));
    Sleep(150);
}

static void click_at(WCHAR *arg, int key)
{
    WCHAR *x = 0, *y = 0, *p, *text = 0;
    POINT pt;
    HWND w, c;
    LPARAM lp;
    if (key == 3) {
        for (p = arg; *p; p++)
            if (*p == L'=') {
                *p = 0;
                text = p + 1;
                break;
            }
        if (!text)
            return;
    }
    for (p = arg; *p; p++)
        if (*p == L',') {
            x = y;
            y = p;
        }
    if (!x)
        return;
    *x++ = 0;
    *y++ = 0;
    w = FindWindowW(NULL, arg);
    if (!w || !IsWindowVisible(w))
        return;
    pt.x = number(x);
    pt.y = number(y);
    if (key == 2) {
        send_mouse(w, pt);
        found = 1;
        return;
    }
    /* the deepest child under the point: a button on a frame on the form */
    while ((c = ChildWindowFromPointEx(w, pt, CWP_SKIPINVISIBLE)) && c != w) {
        MapWindowPoints(w, c, &pt, 1);
        w = c;
    }
    if (key == 3) {
        SendMessageW(w, WM_SETTEXT, 0, (LPARAM)text);
        found = 1;
        return;
    }
    if (key) {
        PostMessageW(w, WM_KEYDOWN, VK_SPACE, 0x00390001);
        PostMessageW(w, WM_KEYUP, VK_SPACE, 0xC0390001);
        found = 1;
        return;
    }
    lp = MAKELPARAM(pt.x, pt.y);
    {
        /* sent, in order, the program's thread working each one through; a modal form opened by the
         * click would hold the send, so it gives up after a while */
        DWORD_PTR r;
        BYTE ks[256];
        DWORD me = GetCurrentThreadId(), him = GetWindowThreadProcessId(w, NULL);
        /* VB asks GetKeyState(VK_LBUTTON) in its MouseDown: without the button down there it takes no
         * capture and no Click comes.  The state belongs to the input queue, which an attached thread
         * shares: the program's alone, the real keyboard and mouse are not touched. */
        AttachThreadInput(me, him, TRUE);
        GetKeyboardState(ks);
        SendMessageTimeoutW(w, WM_MOUSEACTIVATE, (WPARAM)GetAncestor(w, GA_ROOT),
                            MAKELPARAM(HTCLIENT, WM_LBUTTONDOWN), SMTO_NORMAL, 2000, &r);
        SendMessageTimeoutW(w, WM_MOUSEMOVE, 0, lp, SMTO_NORMAL, 2000, &r);
        ks[VK_LBUTTON] |= 0x80;
        SetKeyboardState(ks);
        SendMessageTimeoutW(w, WM_LBUTTONDOWN, MK_LBUTTON, lp, SMTO_NORMAL, 2000, &r);
        /* as long as a finger takes: VB posts itself the focus change, and an up before that
         * gives MouseUp but no Click */
        Sleep(300);
        ks[VK_LBUTTON] &= ~0x80;
        SetKeyboardState(ks);
        SendMessageTimeoutW(w, WM_LBUTTONUP, 0, lp, SMTO_NORMAL, 2000, &r);
        AttachThreadInput(me, him, FALSE);
    }
    found = 1;
}

void __stdcall start(void)
{
    int argc;
    WCHAR **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argc < 3)
        ExitProcess(2);
    if (!lstrcmpW(argv[1], L"at") || !lstrcmpW(argv[1], L"key") || !lstrcmpW(argv[1], L"mouse") ||
        !lstrcmpW(argv[1], L"set")) {
        click_at(argv[2], argv[1][0] == L'k' ? 1 : argv[1][0] == L'm' ? 2 : argv[1][0] == L's' ? 3 : 0);
        ExitProcess(found ? 0 : 1);
    }
    mode = !lstrcmpW(argv[1], L"pw") ? 0 : !lstrcmpW(argv[1], L"click") ? 1 :
           !lstrcmpW(argv[1], L"menu") ? 2 : 3;
    want = argv[2];
    EnumWindows(toplevel, 0);
    ExitProcess(found ? 0 : 1);
}
