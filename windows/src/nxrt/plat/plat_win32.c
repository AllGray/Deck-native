/* Win32 implementation of plat.h. */
#include "plat.h"

#include <windows.h>
#include <bcrypt.h>
#include <intrin.h>
#include <process.h>
#include <stdio.h>
#include <string.h>

#ifndef CREATE_WAITABLE_TIMER_HIGH_RESOLUTION
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x00000002
#endif

static uint64_t qpc_freq(void) {
    static uint64_t f;
    if (!f) { LARGE_INTEGER li; QueryPerformanceFrequency(&li); f = (uint64_t)li.QuadPart; }
    return f;
}
uint64_t plat_qpc(void) { LARGE_INTEGER li; QueryPerformanceCounter(&li); return (uint64_t)li.QuadPart; }
uint64_t plat_qpc_freq(void) { return qpc_freq(); }
uint64_t plat_now_ns(void) {
    uint64_t t = plat_qpc(), f = qpc_freq();
    return t / f * 1000000000u + t % f * 1000000000u / f;   /* no 128-bit division: compiler-rt is not linked on this target */
}
uint64_t plat_realtime_ns(void) {
    FILETIME ft; GetSystemTimePreciseAsFileTime(&ft);
    uint64_t v = ((uint64_t)ft.dwHighDateTime << 32) | ft.dwLowDateTime;   /* 100 ns since 1601 */
    return (v - 116444736000000000ull) * 100u;
}

#ifndef PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION
#define NXRT_THROTTLE_TIMER 0x4   /* PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION (Windows 11 SDK) */
#else
#define NXRT_THROTTLE_TIMER PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION
#endif
static _Thread_local HANDLE t_timer;
void plat_sleep_ns(uint64_t ns) {
    if (!ns) { SwitchToThread(); return; }
    if (!t_timer) {
        t_timer = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
        if (!t_timer) t_timer = CreateWaitableTimerExW(NULL, NULL, 0, TIMER_ALL_ACCESS);   /* before Windows 10 1803 */
    }
    LARGE_INTEGER due; due.QuadPart = -(LONGLONG)((ns + 99) / 100);   /* relative, 100 ns units */
    if (t_timer && SetWaitableTimer(t_timer, &due, 0, NULL, NULL, FALSE)) WaitForSingleObject(t_timer, INFINITE);
    else Sleep((DWORD)((ns + 999999) / 1000000));
}
void plat_yield(void) { SwitchToThread(); }

void plat_timer_resolution(void) {
    typedef UINT (WINAPI *tbp_t)(UINT);
    HMODULE w = LoadLibraryW(L"winmm.dll");
    tbp_t tbp = w ? (tbp_t)(void *)GetProcAddress(w, "timeBeginPeriod") : NULL;
    if (tbp) tbp(1);
    /* Windows 11 runs a process whose window is behind others or hidden at a lower quality of service (EcoQoS: efficiency cores, lower clocks) and stops honouring its
       timer resolution: a game paced at 60 Hz opts out of both (M6 phase B: 40 presents/s with the window behind others, 59-60 with this) */
    PROCESS_POWER_THROTTLING_STATE ps = {PROCESS_POWER_THROTTLING_CURRENT_VERSION, PROCESS_POWER_THROTTLING_EXECUTION_SPEED | NXRT_THROTTLE_TIMER, 0};
    SetProcessInformation(GetCurrentProcess(), ProcessPowerThrottling, &ps, sizeof ps);
}

int plat_read(uint64_t addr, void *out, size_t n) {
    SIZE_T got = 0;
    if (!addr || !ReadProcessMemory(GetCurrentProcess(), (LPCVOID)(uintptr_t)addr, out, n, &got) || got != n) return -1;
    return 0;
}

typedef struct { plat_thread_fn fn; void *arg; } start_t;
static unsigned __stdcall thread_tramp(void *p) {
    start_t s = *(start_t *)p; HeapFree(GetProcessHeap(), 0, p);
    ULONG guarantee = 64 * 1024; SetThreadStackGuarantee(&guarantee);   /* room for the crash report on a stack overflow */
    return s.fn(s.arg);
}
uint32_t plat_thread_start(plat_thread_fn fn, void *arg, size_t stack_reserve) {
    start_t *s = HeapAlloc(GetProcessHeap(), 0, sizeof *s);
    if (!s) return 0;
    s->fn = fn; s->arg = arg;
    unsigned tid = 0;
    uintptr_t h = _beginthreadex(NULL, (unsigned)stack_reserve, thread_tramp, s, STACK_SIZE_PARAM_IS_A_RESERVATION, &tid);
    if (!h) { HeapFree(GetProcessHeap(), 0, s); return 0; }
    CloseHandle((HANDLE)h);
    return tid;
}
uint32_t plat_thread_id(void) { return GetCurrentThreadId(); }
void plat_thread_name(const char *name) {
    wchar_t w[128]; int n = MultiByteToWideChar(CP_UTF8, 0, name, -1, w, 128);
    if (n > 0) SetThreadDescription(GetCurrentThread(), w);
}
/* Horizon priorities run 0 (highest) to 63; this game uses 28..59 (its npdm). Windows: only a nudge, so a busy PC never starves the game's
   own lower-priority workers (research/01 lesson 11) and the game never starves the rest of the PC */
int plat_priority_for(int p) {
    if (p <= 27) return THREAD_PRIORITY_ABOVE_NORMAL;
    if (p <= 44) return THREAD_PRIORITY_NORMAL;
    if (p <= 59) return THREAD_PRIORITY_BELOW_NORMAL;
    return THREAD_PRIORITY_LOWEST;
}
void plat_thread_set_priority(uint32_t tid, int horizon_prio) {
    HANDLE h = tid == GetCurrentThreadId() ? GetCurrentThread() : OpenThread(THREAD_SET_LIMITED_INFORMATION, FALSE, tid);
    if (!h) return;
    SetThreadPriority(h, plat_priority_for(horizon_prio));
    if (h != GetCurrentThread()) CloseHandle(h);
}
double plat_thread_cpu_seconds(uint32_t tid) {
    HANDLE h = OpenThread(THREAD_QUERY_LIMITED_INFORMATION, FALSE, tid);
    if (!h) return -1;
    FILETIME c, e, k, u; double s = -1;
    if (GetThreadTimes(h, &c, &e, &k, &u))
        s = (double)((((uint64_t)k.dwHighDateTime << 32) | k.dwLowDateTime) + (((uint64_t)u.dwHighDateTime << 32) | u.dwLowDateTime)) / 1e7;
    CloseHandle(h);
    return s;
}
_Noreturn void plat_thread_exit(void) { _endthreadex(0); for (;;) ExitThread(0); }

void plat_cpu_brand(char *out, size_t n) {
    int r[4]; char b[49] = {0};
    __cpuid(r, (int)0x80000000);
    if ((unsigned)r[0] >= 0x80000004u) {
        for (int i = 0; i < 3; i++) { __cpuid(r, (int)(0x80000002u + (unsigned)i)); memcpy(b + 16 * i, r, 16); }
    }
    const char *s = b; while (*s == ' ') s++;
    snprintf(out, n, "%s", *s ? s : "unknown x86-64 CPU");
}
int plat_cpu_x86_64_v3(void) {
    int r[4];
    __cpuid(r, 1);
    int fma = (r[2] >> 12) & 1, osxsave = (r[2] >> 27) & 1, avx = (r[2] >> 28) & 1, movbe = (r[2] >> 22) & 1;
    __cpuidex(r, 7, 0);
    int avx2 = (r[1] >> 5) & 1, bmi1 = (r[1] >> 3) & 1, bmi2 = (r[1] >> 8) & 1;
    int ymm = osxsave && (_xgetbv(0) & 6) == 6;   /* the OS saves YMM state */
    return fma && avx && avx2 && bmi1 && bmi2 && movbe && ymm;
}
unsigned plat_cpu_count(void) { return (unsigned)GetActiveProcessorCount(ALL_PROCESSOR_GROUPS); }
void plat_windows_version(char *out, size_t n) {
    typedef LONG (WINAPI *rgv_t)(PRTL_OSVERSIONINFOW);
    RTL_OSVERSIONINFOW v = {sizeof v};
    rgv_t rgv = (rgv_t)(void *)GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlGetVersion");
    if (rgv) rgv(&v);
    DWORD ubr = 0, sz = sizeof ubr; char disp[64] = "";
    DWORD dsz = sizeof disp;
    RegGetValueA(HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion", "UBR", RRF_RT_REG_DWORD, NULL, &ubr, &sz);
    RegGetValueA(HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion", "DisplayVersion", RRF_RT_REG_SZ, NULL, disp, &dsz);
    snprintf(out, n, "Windows %lu.%lu build %lu.%lu%s%s", v.dwMajorVersion, v.dwMinorVersion, v.dwBuildNumber, ubr, *disp ? " " : "", disp);
}
uint64_t plat_ram_bytes(void) { MEMORYSTATUSEX m = {sizeof m}; return GlobalMemoryStatusEx(&m) ? m.ullTotalPhys : 0; }

void plat_random(void *out, size_t n) {
    if (BCryptGenRandom(NULL, out, (ULONG)n, BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0) {
        uint8_t *p = out; uint64_t x = plat_qpc() ^ 0x9E3779B97F4A7C15ull;
        for (size_t i = 0; i < n; i++) { x ^= x << 13; x ^= x >> 7; x ^= x << 17; p[i] = (uint8_t)x; }
    }
}
