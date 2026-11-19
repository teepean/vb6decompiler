/* Loaded into the program under test by winpoke.exe (menu step): menu handles are
 * per process under Wine, so the items can only be read from inside.  Takes the
 * caption from the named mapping WinpokeMenu (a WCHAR string, then a found flag at
 * byte 1024) and posts the command of the first item with that caption.  The caption
 * *LIST* writes every item's caption, one a line in UTF-16, to menus.txt in the program's
 * current folder instead. */
#include <windows.h>

static const WCHAR *want;
static HANDLE listing;

static void strip(WCHAR *d, const WCHAR *s)
{
    for (; *s; s++)
        if (*s != L'&')
            *d++ = *s;
    *d = 0;
}

static int walk(HWND w, HMENU m)
{
    int i, n = GetMenuItemCount(m);
    for (i = 0; i < n; i++) {
        WCHAR text[256], clean[256];
        HMENU sub = GetSubMenu(m, i);
        text[0] = 0;
        GetMenuStringW(m, i, text, 256, MF_BYPOSITION);
        strip(clean, text);
        if (listing && clean[0] && !sub) {
            DWORD done;
            WriteFile(listing, clean, lstrlenW(clean) * 2, &done, 0);
            WriteFile(listing, L"\n", 2, &done, 0);
        }
        if (sub) {
            if (walk(w, sub))
                return 1;
        } else if (!listing && !lstrcmpW(clean, want)) {
            PostMessageW(w, WM_COMMAND, GetMenuItemID(m, i), 0);
            return 1;
        }
    }
    return 0;
}

static BOOL CALLBACK top(HWND w, LPARAM lp)
{
    DWORD pid;
    GetWindowThreadProcessId(w, &pid);
    if (pid == GetCurrentProcessId() && IsWindowVisible(w) && GetMenu(w) && walk(w, GetMenu(w))) {
        *(int *)lp = 1;
        return FALSE;
    }
    return TRUE;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD why, LPVOID r)
{
    if (why == DLL_PROCESS_ATTACH) {
        HANDLE map = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, L"WinpokeMenu");
        if (map) {
            char *p = MapViewOfFile(map, FILE_MAP_ALL_ACCESS, 0, 0, 0);
            if (p) {
                want = (const WCHAR *)p;
                if (!lstrcmpW(want, L"*LIST*"))
                    listing = CreateFileW(L"menus.txt", GENERIC_WRITE, 0, 0, CREATE_ALWAYS, 0, 0);
                EnumWindows(top, (LPARAM)(p + 1024));
                if (listing) {
                    CloseHandle(listing);
                    *(int *)(p + 1024) = 1;
                }
                UnmapViewOfFile(p);
            }
            CloseHandle(map);
        }
    }
    return FALSE;                           /* do its work and unload again */
}
