/* vb6fix.dll: Wine compatibility fixes for VB6.EXE, loaded through an import
 * that tools/patchvb6.py adds to a copy of VB6.EXE.  Without them VB6 cannot
 * build a project with an OCX control under Wine.
 *
 * The first time VB6 uses an OCX it writes a cache type library for it (the
 * .oca file), copying the OCX's type library with ICreateTypeLib2:
 *
 * 1. Wine's ICreateTypeLib2::SetHelpStringContext and the per-member help
 *    context and custom data setters of ICreateTypeInfo2 are stubs that
 *    return E_NOTIMPL, and VB6 stops there ("System Error &H80004001").
 *    What they set only matters for help text and tools in the IDE: report
 *    success.
 * 2. A dual interface has two views, TKIND_DISPATCH and TKIND_INTERFACE;
 *    GetRefTypeOfImplType(-1) and GetRefTypeInfo lead from each to the
 *    other.  Wine's GetRefTypeInfo makes a new copy of the other view every
 *    time, and VB6, which walks from view to view until it meets one it has
 *    seen, flips between copies of MSCOMCTL's ITabStrip until the stack
 *    overflows.  The fix hands out each other view once: the second request
 *    gets the same object, and the other view of a copy is its original.
 * 3. Wine's ITypeInfo2::GetDocumentation2 looks only at the type's own
 *    members, not at the inherited ones (IUnknown's QueryInterface, memid
 *    60000000h, in a dual interface's dispatch view); GetDocumentation looks
 *    at the bases too.  VB6 asks for every member, and calls a failure an
 *    invalid object library.
 * 4. Wine's ICreateTypeLib2::SaveAllChanges fails (E_UNEXPECTED, "Unexpected
 *    error") when the library holds an empty string: WideCharToMultiByte's
 *    0 for it counts as an error.  VB6 sets the help string DLL to "" before
 *    copying each type of another library.  An empty help file, help string
 *    DLL or doc string means none: those calls are not passed on.
 *
 * 5. Wine's IPicture::SaveAsFile fails (E_FAIL) for icons and bitmaps unless
 *    fSaveMemCopy is TRUE; VB6 copies a form's Icon with fSaveMemCopy =
 *    !KeepOriginalFormat = FALSE, gets no stream, and reads through the NULL
 *    (VB6.EXE+D58D, "Unhandled page fault on read access to 00000010").
 *    With TRUE Wine writes the picture's original data, which is what
 *    Windows writes for FALSE: try again with TRUE.
 *
 * Each is fixed by replacing vtable entries: every type info, type library,
 * type info being created and picture that Wine's oleaut32 makes shares its
 * vtable. */
#define COBJMACROS
#include <windows.h>
#include <oleauto.h>
#include <olectl.h>

typedef HRESULT (STDMETHODCALLTYPE *GetRefTypeInfo_fn)(ITypeInfo *, HREFTYPE, ITypeInfo **);
typedef HRESULT (STDMETHODCALLTYPE *SetHelpStringContext_fn)(ICreateTypeLib2 *, ULONG);
typedef HRESULT (STDMETHODCALLTYPE *GetDocumentation2_fn)(ITypeInfo2 *, MEMBERID, LCID, BSTR *,
                                                         DWORD *, BSTR *);

static GetRefTypeInfo_fn real_GetRefTypeInfo;
static SetHelpStringContext_fn real_SetHelpStringContext;
static GetDocumentation2_fn real_GetDocumentation2;

/* (view, other view) pairs handed out; the table keeps both alive */
#define MAX_PAIRS 4096
static struct { ITypeInfo *view, *other; } pairs[MAX_PAIRS];
static int npairs;
static CRITICAL_SECTION lock;

static HRESULT STDMETHODCALLTYPE GetRefTypeInfo(ITypeInfo *ti, HREFTYPE href, ITypeInfo **out)
{
    HRESULT hr;
    int i;
    if (href != (HREFTYPE)-2 || !out)       /* -2: the other view of a dual */
        return real_GetRefTypeInfo(ti, href, out);
    EnterCriticalSection(&lock);
    for (i = 0; i < npairs; i++) {
        ITypeInfo *other = pairs[i].view == ti ? pairs[i].other
                         : pairs[i].other == ti ? pairs[i].view : NULL;
        if (other) {
            ITypeInfo_AddRef(other);
            *out = other;
            LeaveCriticalSection(&lock);
            return S_OK;
        }
    }
    hr = real_GetRefTypeInfo(ti, href, out);
    if (SUCCEEDED(hr) && npairs < MAX_PAIRS) {
        ITypeInfo_AddRef(ti);
        ITypeInfo_AddRef(*out);
        pairs[npairs].view = ti;
        pairs[npairs].other = *out;
        npairs++;
    }
    LeaveCriticalSection(&lock);
    return hr;
}

static HRESULT STDMETHODCALLTYPE GetDocumentation2(ITypeInfo2 *ti, MEMBERID memid, LCID lcid,
                                                   BSTR *help, DWORD *context, BSTR *dll)
{
    HRESULT hr = real_GetDocumentation2(ti, memid, lcid, help, context, dll);
    BSTR doc = NULL;
    if (hr != TYPE_E_ELEMENTNOTFOUND || memid == MEMBERID_NIL)
        return hr;
    /* an inherited member: GetDocumentation finds it in the base interface */
    if (FAILED(ITypeInfo2_GetDocumentation(ti, memid, NULL, &doc, NULL, NULL)))
        return hr;
    if (help)
        *help = doc;
    else
        SysFreeString(doc);
    if (context)
        *context = 0;
    if (dll)
        *dll = NULL;
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE SetHelpStringContext(ICreateTypeLib2 *tl, ULONG context)
{
    HRESULT hr = real_SetHelpStringContext(tl, context);
    return hr == E_NOTIMPL ? S_OK : hr;
}

/* ICreateTypeInfo2 setters whose stubs return E_NOTIMPL, by argument count
 * (all arguments are 32 bits) */
typedef HRESULT (STDMETHODCALLTYPE *set3_fn)(void *, DWORD, DWORD);
typedef HRESULT (STDMETHODCALLTYPE *setstr_fn)(void *, LPOLESTR);
typedef HRESULT (STDMETHODCALLTYPE *setstr2_fn)(void *, DWORD, LPOLESTR);
#define SETSTR(name) static setstr_fn real_##name; \
    static HRESULT STDMETHODCALLTYPE name(void *p, LPOLESTR s) \
    { return s && !*s ? S_OK : real_##name(p, s); }
#define SETSTR2(name) static setstr2_fn real_##name; \
    static HRESULT STDMETHODCALLTYPE name(void *p, DWORD i, LPOLESTR s) \
    { return s && !*s ? S_OK : real_##name(p, i, s); }
SETSTR(SetLibDocString)
SETSTR(SetHelpFileName)
SETSTR(SetHelpStringDll)
SETSTR(SetTypeDocString)
SETSTR2(SetFuncDocString)           /* (index, string) */
SETSTR2(SetVarDocString)
typedef HRESULT (STDMETHODCALLTYPE *set4_fn)(void *, DWORD, DWORD, DWORD);
typedef HRESULT (STDMETHODCALLTYPE *set5_fn)(void *, DWORD, DWORD, DWORD, DWORD);
#define SETTER3(name) static set3_fn real_##name; \
    static HRESULT STDMETHODCALLTYPE name(void *p, DWORD a, DWORD b) \
    { HRESULT hr = real_##name(p, a, b); return hr == E_NOTIMPL ? S_OK : hr; }
#define SETTER4(name) static set4_fn real_##name; \
    static HRESULT STDMETHODCALLTYPE name(void *p, DWORD a, DWORD b, DWORD c) \
    { HRESULT hr = real_##name(p, a, b, c); return hr == E_NOTIMPL ? S_OK : hr; }
#define SETTER5(name) static set5_fn real_##name; \
    static HRESULT STDMETHODCALLTYPE name(void *p, DWORD a, DWORD b, DWORD c, DWORD d) \
    { HRESULT hr = real_##name(p, a, b, c, d); return hr == E_NOTIMPL ? S_OK : hr; }
SETTER4(SetFuncCustData)            /* (index, guid, value) */
SETTER5(SetParamCustData)           /* (func, param, guid, value) */
SETTER4(SetVarCustData)
SETTER4(SetImplTypeCustData)
SETTER3(SetFuncHelpStringContext)   /* (index, context) */
SETTER3(SetVarHelpStringContext)

/* failures worth seeing (WINEDEBUG=+debugstr prints them) */
typedef HRESULT (STDMETHODCALLTYPE *method0_fn)(void *);
static method0_fn real_SaveAllChanges, real_LayOut;
static HRESULT logged(const char *what, HRESULT hr)
{
    char buf[80];
    if (FAILED(hr)) {
        wsprintfA(buf, "vb6fix: %s failed, %08lx\n", what, (unsigned long)hr);
        OutputDebugStringA(buf);
    }
    return hr;
}
static HRESULT STDMETHODCALLTYPE SaveAllChanges(void *p)
{
    return logged("ICreateTypeLib2::SaveAllChanges", real_SaveAllChanges(p));
}
static HRESULT STDMETHODCALLTYPE LayOut(void *p)
{
    return logged("ICreateTypeInfo::LayOut", real_LayOut(p));
}

typedef HRESULT (STDMETHODCALLTYPE *SaveAsFile_fn)(IPicture *, IStream *, BOOL, LONG *);
static SaveAsFile_fn real_SaveAsFile;
static HRESULT STDMETHODCALLTYPE SaveAsFile(IPicture *pic, IStream *stm, BOOL mem_copy, LONG *size)
{
    HRESULT hr = real_SaveAsFile(pic, stm, mem_copy, size);
    if (hr == E_FAIL && !mem_copy)
        hr = real_SaveAsFile(pic, stm, TRUE, size);
    return hr;
}

/* Replace entry `slot` of the vtable of `obj`; returns the old entry. */
static void *replace(void *obj, int slot, void *fn)
{
    void **vt = *(void ***)obj;
    void *old_fn = vt[slot];
    DWORD prot;
    if (old_fn == fn || !VirtualProtect(&vt[slot], sizeof(void *), PAGE_READWRITE, &prot))
        return NULL;
    vt[slot] = fn;
    VirtualProtect(&vt[slot], sizeof(void *), prot, &prot);
    return old_fn;
}

static void install(void)
{
    ITypeLib *tl;
    ITypeInfo *ti;
    ICreateTypeLib2 *ctl;
    WCHAR tmp[MAX_PATH + 16];

    if (SUCCEEDED(LoadTypeLib(L"stdole2.tlb", &tl))) {
        if (SUCCEEDED(ITypeLib_GetTypeInfo(tl, 0, &ti))) {
            void *old_fn = replace(ti, 14, (void *)GetRefTypeInfo);
            if (old_fn)
                real_GetRefTypeInfo = (GetRefTypeInfo_fn)old_fn;
            old_fn = replace(ti, 31, (void *)GetDocumentation2);    /* an ITypeInfo2 entry */
            if (old_fn)
                real_GetDocumentation2 = (GetDocumentation2_fn)old_fn;
            ITypeInfo_Release(ti);
        }
        ITypeLib_Release(tl);
    }
    {
        PICTDESC pd = { sizeof(PICTDESC), PICTYPE_NONE };
        IPicture *pic;
        if (SUCCEEDED(OleCreatePictureIndirect(&pd, &IID_IPicture, FALSE, (void **)&pic))) {
            void *old_fn = replace(pic, 15, (void *)SaveAsFile);
            if (old_fn)
                real_SaveAsFile = (SaveAsFile_fn)old_fn;
            IPicture_Release(pic);
        }
    }
    /* never saved, so no file is written */
    GetTempPathW(MAX_PATH, tmp);
    lstrcatW(tmp, L"vb6fix.tlb");
    if (SUCCEEDED(CreateTypeLib2(SYS_WIN32, tmp, &ctl))) {
        ICreateTypeInfo *cti;
        void *old_fn = replace(ctl, 15, (void *)SetHelpStringContext);
        if (old_fn)
            real_SetHelpStringContext = (SetHelpStringContext_fn)old_fn;
        if ((old_fn = replace(ctl, 12, (void *)SaveAllChanges)))
            real_SaveAllChanges = (method0_fn)old_fn;
        if ((old_fn = replace(ctl, 7, (void *)SetLibDocString)))
            real_SetLibDocString = (setstr_fn)old_fn;
        if ((old_fn = replace(ctl, 8, (void *)SetHelpFileName)))
            real_SetHelpFileName = (setstr_fn)old_fn;
        if ((old_fn = replace(ctl, 16, (void *)SetHelpStringDll)))
            real_SetHelpStringDll = (setstr_fn)old_fn;
        if (SUCCEEDED(ICreateTypeLib2_CreateTypeInfo(ctl, L"vb6fix", TKIND_INTERFACE, &cti))) {
            /* the ICreateTypeInfo2 slots */
#define HOOK(slot, name, type) if ((old_fn = replace(cti, slot, (void *)name))) \
                                   real_##name = (type)old_fn;
            HOOK(32, SetFuncCustData, set4_fn)
            HOOK(33, SetParamCustData, set5_fn)
            HOOK(34, SetVarCustData, set4_fn)
            HOOK(35, SetImplTypeCustData, set4_fn)
            HOOK(37, SetFuncHelpStringContext, set3_fn)
            HOOK(38, SetVarHelpStringContext, set3_fn)
            HOOK(25, LayOut, method0_fn)
            HOOK(5, SetTypeDocString, setstr_fn)
            HOOK(19, SetFuncDocString, setstr2_fn)
            HOOK(20, SetVarDocString, setstr2_fn)
#undef HOOK
            ICreateTypeInfo_Release(cti);
        }
        ICreateTypeLib2_Release(ctl);
    }
}

/* the function the import added to VB6.EXE names */
__declspec(dllexport) int vb6fix(void)
{
    return real_GetRefTypeInfo != NULL;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, void *reserved)
{
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(inst);
        InitializeCriticalSection(&lock);
        install();
    }
    return TRUE;
}
