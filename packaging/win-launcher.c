/* AVF.exe — the Windows launcher.
 *
 * Starts the bundled python\pythonw.exe on desktop.py and waits for it, so:
 *   - no console window ever flashes (pythonw + -mwindows),
 *   - the taskbar/pin carries AVF's icon (compiled in via windres),
 *   - the process lifetime is AVF's: closing the console-less python window
 *     ends this exe, which is what a taskbar pin expects.
 *
 * Cross-compiled on the build Mac:
 *   x86_64-w64-mingw32-windres win-launcher.rc -O coff -o win-launcher.o
 *   x86_64-w64-mingw32-gcc win-launcher.c win-launcher.o -o AVF.exe \
 *       -mwindows -s -static
 * -static: libgcc/winpthread must not be a missing-DLL surprise on the user's
 * machine — the exe is the only thing beside a full python tree.
 */
#include <windows.h>

static int run(void) {
    wchar_t exe[MAX_PATH], python[MAX_PATH], cmdline[MAX_PATH * 2];
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD exit_code = 1;

    GetModuleFileNameW(NULL, exe, MAX_PATH);
    wchar_t *cut = wcsrchr(exe, L'\\');
    if (!cut) return 1;
    *cut = L'\0';                                   /* exe → its folder */

    wsprintfW(python, L"%s\\python\\pythonw.exe", exe);
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES) return 2;
    wsprintfW(cmdline, L"\"%s\" \"%s\\desktop.py\"", python, exe);

    ZeroMemory(&si, sizeof si); si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(python, cmdline, NULL, NULL, FALSE,
                        CREATE_NO_WINDOW, NULL, exe, &si, &pi))
        return 3;
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return (int)exit_code;
}

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE hPrev, PWSTR pCmdLine, int nShow) {
    (void)hInst; (void)hPrev; (void)pCmdLine; (void)nShow;
    return run();
}
