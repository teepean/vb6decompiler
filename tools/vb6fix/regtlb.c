/* regtlb.exe FILE...: register type libraries (LoadTypeLibEx with
 * REGKIND_REGISTER) - Wine has no regtlib.  Used for MSDATSRC.TLB, which
 * MSCOMCTL.OCX and MSSTDFMT.DLL refer to. */
#define COBJMACROS
#include <windows.h>
#include <oleauto.h>
#include <shellapi.h>

void __stdcall start(void)
{
    int argc, i, bad = 0;
    WCHAR **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    for (i = 1; i < argc; i++) {
        ITypeLib *tl;
        if (SUCCEEDED(LoadTypeLibEx(argv[i], REGKIND_REGISTER, &tl)))
            ITypeLib_Release(tl);
        else
            bad++;
    }
    ExitProcess(bad);
}
