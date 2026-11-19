/* ocxbag.exe JOBFILE: what ActiveX controls would write into a .frm.
 *
 * An EXE keeps a control's design-time state as the bytes its
 * IPersistStreamInit::Save wrote; a .frm keeps the property bag text VB
 * writes through IPersistPropertyBag::Save.  For each job this loads a
 * control from the bytes and saves it into a property bag that writes the
 * .frm lines the way VB's own does:
 *
 *   - simple values as text (English number format, Booleans as -1/0);
 *   - an object that persists to a property bag (ImageList's ListImages,
 *     ListView's ColumnHeaders) as BeginProperty NAME {CLSID} ... EndProperty
 *     around its own properties; a font the same way, from its IFont (Wine's
 *     StdFont cannot save itself to a property bag);
 *   - an object that only persists to a stream (StdPicture) into the .frx:
 *     u32 length, the object's CLSID, then its stream (VB creates the object
 *     from the CLSID: without it VB6 read a picture's "lt" header as one and
 *     crashed); a byte array as u32 length + the bytes.
 *
 * JOBFILE: one job a line, fields separated by tabs:
 *     {CLSID}  STREAMFILE  TEXTOUT  FRXOUT  FRXBASE(hex)  FRXNAME
 * TEXTOUT gets the property lines (three spaces a nesting level, none for
 * the control's own level), FRXOUT the .frx bytes; references in the text
 * count from FRXBASE, where the caller will append FRXOUT.  A failed job
 * leaves "' vb6dec: ..." and the HRESULT in TEXTOUT.  Run under the Wine
 * prefix the controls are registered in (tools/vbmake.py's). */
#define COBJMACROS
#include <windows.h>
#include <ole2.h>
#include <ocidl.h>

/* ---- growing byte buffers (no C runtime) ---- */
typedef struct { char *p; DWORD n, cap; } Buf;

static void buf_add(Buf *b, const void *data, DWORD n)
{
    if (b->n + n + 1 > b->cap) {
        DWORD cap = (b->n + n + 1) * 2;
        b->p = b->p ? HeapReAlloc(GetProcessHeap(), 0, b->p, cap) : HeapAlloc(GetProcessHeap(), 0, cap);
        b->cap = cap;
    }
    CopyMemory(b->p + b->n, data, n);
    b->n += n;
    b->p[b->n] = 0;
}

static void buf_str(Buf *b, const char *s) { buf_add(b, s, lstrlenA(s)); }

static void buf_wstr(Buf *b, const WCHAR *s, int len)
{
    int n = WideCharToMultiByte(1252, 0, s, len, NULL, 0, NULL, NULL);
    char *tmp = HeapAlloc(GetProcessHeap(), 0, n + 1);
    WideCharToMultiByte(1252, 0, s, len, tmp, n, NULL, NULL);
    buf_add(b, tmp, n);
    HeapFree(GetProcessHeap(), 0, tmp);
}

/* ---- the property bag ---- */
typedef struct {
    IPropertyBag iface;
    LONG ref;
    int depth;
    Buf *text, *frx;
    DWORD frxbase;
    const WCHAR *frxname;
} Bag;

static void indent(Bag *b)
{
    int i;
    for (i = 0; i < b->depth; i++)
        buf_str(b->text, "   ");
}

static void name_eq(Bag *b, LPCOLESTR name)
{
    int n = lstrlenW(name);
    indent(b);
    buf_wstr(b->text, name, n);
    for (; n < 16; n++)
        buf_str(b->text, " ");
    buf_str(b->text, "=   ");
}

/* data into the .frx (u32 length, the CLSID if any, the bytes); its
 * reference into the text */
static void frx_ref(Bag *b, LPCOLESTR name, const CLSID *clsid, const void *data, DWORD n)
{
    char ref[32];
    DWORD off = b->frxbase + b->frx->n, total = n + (clsid ? sizeof(CLSID) : 0);
    buf_add(b->frx, &total, 4);
    if (clsid)
        buf_add(b->frx, clsid, sizeof(CLSID));
    buf_add(b->frx, data, n);
    name_eq(b, name);
    buf_str(b->text, "\"");
    buf_wstr(b->text, b->frxname, lstrlenW(b->frxname));
    wsprintfA(ref, "\":%04lX\r\n", (unsigned long)off);
    buf_str(b->text, ref);
}

static void comment(Bag *b, const char *what, LPCOLESTR name, HRESULT hr)
{
    char tail[48];
    indent(b);
    buf_str(b->text, "' vb6dec: ");
    buf_str(b->text, what);
    buf_str(b->text, " ");
    buf_wstr(b->text, name, lstrlenW(name));
    wsprintfA(tail, " (%08lx)\r\n", (unsigned long)hr);
    buf_str(b->text, tail);
}

static IPropertyBagVtbl bag_vtbl;

static void begin(Bag *b, LPCOLESTR name, const WCHAR *guid)
{
    indent(b);
    buf_str(b->text, "BeginProperty ");
    buf_wstr(b->text, name, lstrlenW(name));
    buf_str(b->text, " ");
    buf_wstr(b->text, guid, lstrlenW(guid));
    buf_str(b->text, " \r\n");
}

/* a font as VB writes one: Name, Size, Charset, Weight, Underline, Italic,
 * Strikethrough, in a block with StdFont's CLSID */
static BOOL save_font(Bag *b, LPCOLESTR name, IUnknown *obj)
{
    IFont *f;
    BSTR face = NULL, size_text = NULL;
    CY size;
    BOOL under = FALSE, italic = FALSE, strike = FALSE;
    SHORT weight = 400, charset = 0;
    char num[32];
    Bag inner = { { &bag_vtbl }, 1, b->depth + 1, b->text, b->frx, b->frxbase, b->frxname };
    if (FAILED(IUnknown_QueryInterface(obj, &IID_IFont, (void **)&f)))
        return FALSE;
    IFont_get_Name(f, &face);
    IFont_get_Size(f, &size);
    IFont_get_Charset(f, &charset);
    IFont_get_Weight(f, &weight);
    IFont_get_Underline(f, &under);
    IFont_get_Italic(f, &italic);
    IFont_get_Strikethrough(f, &strike);
    IFont_Release(f);
    begin(b, name, L"{0BE35203-8F91-11CE-9DE3-00AA004BB851}");
    name_eq(&inner, L"Name");
    buf_str(b->text, "\"");
    if (face)
        buf_wstr(b->text, face, SysStringLen(face));
    buf_str(b->text, "\"\r\n");
    name_eq(&inner, L"Size");
    VarBstrFromCy(size, MAKELCID(0x409, SORT_DEFAULT), 0, &size_text);
    if (size_text)
        buf_wstr(b->text, size_text, SysStringLen(size_text));
    buf_str(b->text, "\r\n");
    name_eq(&inner, L"Charset");
    wsprintfA(num, "%d\r\n", charset);
    buf_str(b->text, num);
    name_eq(&inner, L"Weight");
    wsprintfA(num, "%d\r\n", weight);
    buf_str(b->text, num);
    name_eq(&inner, L"Underline");
    buf_str(b->text, under ? "-1  'True\r\n" : "0   'False\r\n");
    name_eq(&inner, L"Italic");
    buf_str(b->text, italic ? "-1  'True\r\n" : "0   'False\r\n");
    name_eq(&inner, L"Strikethrough");
    buf_str(b->text, strike ? "-1  'True\r\n" : "0   'False\r\n");
    indent(b);
    buf_str(b->text, "EndProperty\r\n");
    SysFreeString(face);
    SysFreeString(size_text);
    return TRUE;
}

static HRESULT save_object(Bag *b, LPCOLESTR name, IUnknown *obj)
{
    IPersistPropertyBag *ppb;
    IPersistStream *ps;
    IPersistStreamInit *psi;
    IStream *stm;
    HRESULT hr;
    if (save_font(b, name, obj))
        return S_OK;
    if (SUCCEEDED(IUnknown_QueryInterface(obj, &IID_IPersistPropertyBag, (void **)&ppb))) {
        CLSID clsid = CLSID_NULL;
        WCHAR guid[48];
        Bag inner = { { &bag_vtbl }, 1, b->depth + 1, b->text, b->frx, b->frxbase, b->frxname };
        IPersistPropertyBag_GetClassID(ppb, &clsid);
        StringFromGUID2(&clsid, guid, 48);
        begin(b, name, guid);
        hr = IPersistPropertyBag_Save(ppb, &inner.iface, TRUE, FALSE);
        IPersistPropertyBag_Release(ppb);
        if (FAILED(hr))
            comment(&inner, "could not save", name, hr);
        indent(b);
        buf_str(b->text, "EndProperty\r\n");
        return S_OK;
    }
    hr = CreateStreamOnHGlobal(NULL, TRUE, &stm);
    if (FAILED(hr))
        return hr;
    CLSID clsid = CLSID_NULL;
    if (SUCCEEDED(hr = IUnknown_QueryInterface(obj, &IID_IPersistStream, (void **)&ps))) {
        IPersistStream_GetClassID(ps, &clsid);
        hr = IPersistStream_Save(ps, stm, TRUE);
        IPersistStream_Release(ps);
    } else if (SUCCEEDED(hr = IUnknown_QueryInterface(obj, &IID_IPersistStreamInit, (void **)&psi))) {
        IPersistStreamInit_GetClassID(psi, &clsid);
        hr = IPersistStreamInit_Save(psi, stm, TRUE);
        IPersistStreamInit_Release(psi);
    }
    if (SUCCEEDED(hr)) {
        HGLOBAL mem;
        STATSTG st;
        GetHGlobalFromStream(stm, &mem);
        IStream_Stat(stm, &st, STATFLAG_NONAME);
        frx_ref(b, name, &clsid, GlobalLock(mem), (DWORD)st.cbSize.QuadPart);
        GlobalUnlock(mem);
    }
    IStream_Release(stm);
    return hr;
}

static HRESULT STDMETHODCALLTYPE bag_qi(IPropertyBag *p, REFIID riid, void **v)
{
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IPropertyBag)) {
        *v = p;
        return S_OK;
    }
    *v = NULL;
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE bag_addref(IPropertyBag *p) { return ++((Bag *)p)->ref; }
static ULONG STDMETHODCALLTYPE bag_release(IPropertyBag *p) { return --((Bag *)p)->ref; }
static HRESULT STDMETHODCALLTYPE bag_read(IPropertyBag *p, LPCOLESTR name, VARIANT *v,
                                          IErrorLog *log)
{
    return E_INVALIDARG;
}

static HRESULT STDMETHODCALLTYPE bag_write(IPropertyBag *p, LPCOLESTR name, VARIANT *v)
{
    Bag *b = (Bag *)p;
    VARIANT s;
    HRESULT hr;
    VARTYPE vt = V_VT(v) & ~VT_BYREF;
    if (vt == VT_EMPTY)
        return S_OK;
    if (vt == VT_UNKNOWN || vt == VT_DISPATCH) {
        IUnknown *obj = (V_VT(v) & VT_BYREF) ? *V_UNKNOWNREF(v) : V_UNKNOWN(v);
        if (obj && FAILED(hr = save_object(b, name, obj)))
            comment(b, "could not save object", name, hr);
        return S_OK;
    }
    if (vt == (VT_ARRAY | VT_UI1)) {
        SAFEARRAY *sa = (V_VT(v) & VT_BYREF) ? *V_ARRAYREF(v) : V_ARRAY(v);
        LONG lo = 0, hi = -1;
        void *data;
        SafeArrayGetLBound(sa, 1, &lo);
        SafeArrayGetUBound(sa, 1, &hi);
        if (SUCCEEDED(SafeArrayAccessData(sa, &data))) {
            frx_ref(b, name, NULL, data, (DWORD)(hi - lo + 1));
            SafeArrayUnaccessData(sa);
        }
        return S_OK;
    }
    name_eq(b, name);
    if (vt == VT_BOOL) {
        VARIANT_BOOL x = (V_VT(v) & VT_BYREF) ? *V_BOOLREF(v) : V_BOOL(v);
        buf_str(b->text, x ? "-1  'True\r\n" : "0   'False\r\n");
        return S_OK;
    }
    VariantInit(&s);
    hr = VariantChangeTypeEx(&s, v, MAKELCID(0x409, SORT_DEFAULT), 0, VT_BSTR);
    if (FAILED(hr)) {
        buf_str(b->text, "0\r\n");
        comment(b, "value not converted:", name, hr);
        return S_OK;
    }
    if (vt == VT_BSTR) {
        /* quoted; a quote doubles */
        const WCHAR *c = V_BSTR(&s);
        buf_str(b->text, "\"");
        for (; *c; c++) {
            if (*c == '"')
                buf_str(b->text, "\"\"");
            else
                buf_wstr(b->text, c, 1);
        }
        buf_str(b->text, "\"\r\n");
    } else {
        buf_wstr(b->text, V_BSTR(&s), SysStringLen(V_BSTR(&s)));
        buf_str(b->text, "\r\n");
    }
    VariantClear(&s);
    return S_OK;
}

static IPropertyBagVtbl bag_vtbl = { bag_qi, bag_addref, bag_release, bag_read, bag_write };

/* ---- jobs ---- */
static BOOL write_file(const WCHAR *path, const void *data, DWORD n)
{
    DWORD got;
    HANDLE f = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    if (f == INVALID_HANDLE_VALUE)
        return FALSE;
    WriteFile(f, data, n, &got, NULL);
    CloseHandle(f);
    return TRUE;
}

static void *read_file(const WCHAR *path, DWORD *n)
{
    DWORD got;
    void *p;
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE)
        return NULL;
    *n = GetFileSize(f, NULL);
    p = HeapAlloc(GetProcessHeap(), 0, *n + 2);
    ReadFile(f, p, *n, &got, NULL);
    ((char *)p)[*n] = ((char *)p)[*n + 1] = 0;
    CloseHandle(f);
    return p;
}

static void job(WCHAR **field)
{
    CLSID clsid;
    IPersistStreamInit *psi = NULL;
    IPersistPropertyBag *ppb = NULL;
    IStream *stm = NULL;
    Buf text = { 0 }, frx = { 0 };
    Bag bag = { { &bag_vtbl }, 1, 0, &text, &frx, 0, field[5] };
    DWORD size;
    void *data;
    HGLOBAL mem;
    HRESULT hr;
    WCHAR *end;

    bag.frxbase = 0;
    for (end = field[4]; *end; end++)
        bag.frxbase = bag.frxbase * 16 + (*end <= '9' ? *end - '0' : (*end | 0x20) - 'a' + 10);
    buf_str(&text, "");
    hr = CLSIDFromString(field[0], &clsid);
    if (SUCCEEDED(hr))
        hr = CoCreateInstance(&clsid, NULL, CLSCTX_INPROC_SERVER, &IID_IPersistStreamInit,
                              (void **)&psi);
    if (SUCCEEDED(hr) && (data = read_file(field[1], &size)) != NULL) {
        mem = GlobalAlloc(GMEM_MOVEABLE, size ? size : 1);
        CopyMemory(GlobalLock(mem), data, size);
        GlobalUnlock(mem);
        HeapFree(GetProcessHeap(), 0, data);
        CreateStreamOnHGlobal(mem, TRUE, &stm);
        hr = IPersistStreamInit_Load(psi, stm);
        IStream_Release(stm);
        if (SUCCEEDED(hr))
            hr = IPersistStreamInit_QueryInterface(psi, &IID_IPersistPropertyBag, (void **)&ppb);
        if (SUCCEEDED(hr)) {
            hr = IPersistPropertyBag_Save(ppb, &bag.iface, TRUE, FALSE);
            IPersistPropertyBag_Release(ppb);
        }
    }
    if (psi)
        IPersistStreamInit_Release(psi);
    if (FAILED(hr))
        comment(&bag, "the control's properties could not be read:", field[0], hr);
    write_file(field[2], text.p, text.n);
    write_file(field[3], frx.p ? frx.p : "", frx.n);
}

void __stdcall start(void)
{
    int argc, nf;
    WCHAR **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    DWORD n;
    char *jobs;
    WCHAR *wjobs, *line, *p, *field[6];
    int wn;

    if (argc < 2)
        ExitProcess(2);
    if ((jobs = read_file(argv[1], &n)) == NULL)
        ExitProcess(1);
    wn = MultiByteToWideChar(CP_UTF8, 0, jobs, n, NULL, 0);
    wjobs = HeapAlloc(GetProcessHeap(), 0, (wn + 1) * sizeof(WCHAR));
    MultiByteToWideChar(CP_UTF8, 0, jobs, n, wjobs, wn);
    wjobs[wn] = 0;
    OleInitialize(NULL);
    for (line = wjobs; *line; ) {
        for (p = line; *p && *p != '\n'; p++)
            ;
        if (*p)
            *p++ = 0;
        if (p - line > 1 && p[-2] == '\r')
            p[-2] = 0;
        nf = 0;
        field[0] = line;
        for (; *line && nf < 5; line++) {
            if (*line == '\t') {
                *line = 0;
                field[++nf] = line + 1;
            }
        }
        if (nf == 5)
            job(field);
        line = p;
    }
    OleUninitialize();
    ExitProcess(0);
}
