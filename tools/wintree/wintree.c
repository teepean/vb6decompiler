/* wintree.exe [OUTFILE]: the windows a GUI program shows, as text - every
 * visible top-level window of the Wine session and its child windows, one
 * a line: depth, class, rectangle (relative to the top-level window),
 * visible/enabled, and the window text.  Run it while an original and its
 * decompiled build each show their first form, and diff the two dumps
 * (tools/guicmp.py).  Without OUTFILE it writes to stdout. */
#include <windows.h>

static HANDLE out;
static RECT top;

static void put(const char *s)
{
    DWORD n;
    WriteFile(out, s, lstrlenA(s), &n, NULL);
}

static void line(HWND w, int depth)
{
    char cls[128], text[512], buf[900];
    RECT r;
    LONG style = GetWindowLongA(w, GWL_STYLE);
    GetClassNameA(w, cls, sizeof(cls));
    text[0] = 0;
    SendMessageTimeoutA(w, WM_GETTEXT, sizeof(text), (LPARAM)text, SMTO_ABORTIFHUNG, 500, NULL);
    text[sizeof(text) - 1] = 0;
    GetWindowRect(w, &r);
    wsprintfA(buf, "%d %s (%d,%d %dx%d) %s%s%s%s \"%s\"\r\n", depth, cls,
              (int)(r.left - top.left), (int)(r.top - top.top),
              (int)(r.right - r.left), (int)(r.bottom - r.top),
              (style & WS_VISIBLE) ? "v" : "-", (style & WS_DISABLED) ? "d" : "e",
              (style & WS_MINIMIZE) ? "m" : "", (style & WS_MAXIMIZE) ? "M" : "", text);
    put(buf);
}

typedef struct { HWND parent; int depth; } Walk;

static BOOL CALLBACK child(HWND w, LPARAM lp)
{
    Walk *k = (Walk *)lp, inner;
    if (GetParent(w) != k->parent)
        return TRUE;                        /* deeper ones come from their own parent */
    line(w, k->depth);
    inner.parent = w;
    inner.depth = k->depth + 1;
    EnumChildWindows(w, child, (LPARAM)&inner);
    return TRUE;
}

static BOOL CALLBACK toplevel(HWND w, LPARAM lp)
{
    Walk k;
    char cls[64];
    if (!IsWindowVisible(w))
        return TRUE;
    GetClassNameA(w, cls, sizeof(cls));
    if (!lstrcmpA(cls, "Shell_TrayWnd") || !lstrcmpA(cls, "Progman"))
        return TRUE;
    GetWindowRect(w, &top);
    line(w, 0);
    k.parent = w;
    k.depth = 1;
    EnumChildWindows(w, child, (LPARAM)&k);
    return TRUE;
}

void __stdcall start(void)
{
    int argc;
    WCHAR **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    out = argc > 1 ? CreateFileW(argv[1], GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL)
                   : GetStdHandle(STD_OUTPUT_HANDLE);
    EnumWindows(toplevel, 0);
    if (argc > 1)
        CloseHandle(out);
    ExitProcess(0);
}
