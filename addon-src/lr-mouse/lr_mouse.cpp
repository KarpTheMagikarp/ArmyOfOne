// Mouse support for Lightning Returns, as a NovaFix add-on. While you're
// walking around the mouse turns the camera, left click strikes and right
// click jumps. In a fight the mouse turns the battle camera and the clicks
// stay out of it. In a menu the wheel moves the pointing hand up and down,
// left click picks and right click backs out, same as XIII-2's own mouse.
//
// LR on keyboard never reads a controller, so none of this can ride in as a
// virtual pad. It goes into the game's own input code, at the spots taking
// LRFF13.exe apart (and XIII-2's ffxiii2img.exe, which has real mouse
// support) turned up:
//
//   Clicks: the pad update (0x744030) builds the held buttons every frame as
//   controller | keyboard | pad+0xE8, then zeroes +0xE8. Filling that one-frame
//   mask before the update makes a click a real button press, edges and
//   repeat done by the game. Left is RB, the overworld strike. Right is the
//   game's cancel mask, which is what jumps in the field, follows the button
//   layout setting, and is the same mask XIII-2 puts right click on.
//
//   Camera: the field's PlayerObjectController (vtable slot 9, 0x6270E0)
//   reads pad 0's right stick straight off the pad, and slot 10 runs it
//   through a deadzone before the camera turns it into an angle. XIII-2 adds
//   the mouse in at the same stick read, so here it's added to pad 0's right
//   stick after the update, lifted past the deadzone and divided by frame
//   time so a given flick turns the same amount at any fps.
//
//   Battle: BattleCameraControl (slot 9, 0x579540) reads that same stick
//   through 0x576990 with a deadzone of its own, 0.4, much wider than the
//   field's. Same stick, so the same addition works, lifted past that one.
//
//   Menus: the GUI manager's input pass (0x7AF050) only hands the pad to the
//   top window when its gate at +0xBC is open, and the top window is +0x4D0.
//   The field keeps running under the main menu, shops and schemata, so on
//   its own that can't tell a menu from the Talk prompt. The top window's
//   flags can: every menu has 0x80, the bit the input pass lets through a
//   hold, and 0x02 while it's open, and the field's prompts have neither. The
//   wheel goes in as one press of up or down per notch through the same +0xE8
//   mask, left click as the game's decide mask, right click as cancel.
//
// Movement comes from our own DirectInput mouse, raw counts with no Windows
// acceleration, like XIII-2. The cursor is clipped to a point in the middle of
// the window and hidden while the mouse is driving, and handed back the moment
// it isn't.
//
// Settings live in lr-mouse.ini beside this file and are picked up within a
// second of being saved. Only the Steam LRFF13.exe is supported. Anything else
// gets a line in lr-mouse.log and the add-on stays out of the way.

#include <windows.h>
#include <initguid.h>
#define DIRECTINPUT_VERSION 0x0800
#include <dinput.h>

#include <MinHook.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <string>

namespace {

std::wstring g_dir;     // ours, not the game's: the ini and log live beside the .asi
HMODULE g_module = nullptr;

void Log(const char* format, ...) {
    FILE* file = nullptr;
    if (_wfopen_s(&file, (g_dir + L"\\lr-mouse.log").c_str(), L"a") != 0 || !file) return;
    va_list args;
    va_start(args, format);
    vfprintf(file, format, args);
    va_end(args);
    fputc('\n', file);
    fclose(file);
}

// RVAs in the Steam build (LRFF13.exe, sha256 31a2bc59...). The image moves
// with ASLR, so everything is base + RVA, and globals are read out of the
// instructions that use them, which the loader has already relocated
constexpr DWORD kFieldInputRva = 0x1B79E0;
constexpr unsigned char kFieldInputHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x10, 0x83, 0x3D};
constexpr DWORD kGateImm = 0x1B79E6 + 2;        // cmp dword ptr [gate], 0
constexpr DWORD kHidImm = 0x1B79F3 + 1;         // mov eax, [hid]
constexpr DWORD kFieldImm = 0x1B7A09 + 1;       // mov eax, [field]

constexpr DWORD kPadUpdateRva = 0x344030;
constexpr unsigned char kPadUpdateHead[] = {0x53, 0x8B, 0xDC, 0x83, 0xEC, 0x08, 0x83, 0xE4, 0xF0};

// same aligned-frame prologue as the pad update, so check far enough in to
// tell them apart (the push of its SEH handler)
constexpr DWORD kPlayerUpdateRva = 0x2270E0;
constexpr unsigned char kPlayerUpdateHead[] = {
    0x53, 0x8B, 0xDC, 0x83, 0xEC, 0x08, 0x83, 0xE4, 0xF0, 0x83, 0xC4, 0x04, 0x55,
    0x8B, 0x6B, 0x04, 0x89, 0x6C, 0x24, 0x04, 0x8B, 0xEC, 0x6A, 0xFF, 0x68};

constexpr DWORD kCancelLoadRva = 0x1B6414;      // mov eax, [cancel mask]
constexpr unsigned char kCancelLoadHead[] = {0xA1};

// GuiHidInterface's slot 1: mov ecx, [gui manager] then jmp to its input pass
constexpr DWORD kGuiLoadRva = 0x39A2C0;
constexpr unsigned char kGuiLoadHead[] = {0x8B, 0x0D};
// the input pass checks the gate first, and reads the decide mask further in
constexpr DWORD kGuiPassRva = 0x3AF05A;
constexpr unsigned char kGuiPassHead[] = {0x39, 0x96, 0xBC, 0x00, 0x00, 0x00};
constexpr DWORD kDecideLoadRva = 0x3AF0EC;      // mov ecx, [decide mask]
constexpr unsigned char kDecideLoadHead[] = {0x8B, 0x0D};

constexpr DWORD kGuiGate = 0xBC, kGuiTop = 0x4D0;
// with one of these set the pass only lets a top window flagged 0x80 have the pad
constexpr DWORD kGuiHoldA = 0x9C0, kGuiHoldB = 0x9C4, kGuiHoldRelease = 0x9C8;
constexpr DWORD kGuiTopFlags = 0x38;
constexpr BYTE kGuiTopThrough = 0x80, kGuiTopOpen = 0x02;

// movss xmm3/xmm1, [deadzone] in slot 10. Pad x ends up against the second
// one, pad y against the first
constexpr DWORD kDeadYLoadRva = 0x224533;
constexpr unsigned char kDeadYLoadHead[] = {0xF3, 0x0F, 0x10, 0x1D};
constexpr DWORD kDeadXLoadRva = 0x2245D0;
constexpr unsigned char kDeadXLoadHead[] = {0xF3, 0x0F, 0x10, 0x0D};

// the battle camera's update, and the game state it checks before it reads
// the stick. Its first 5 bytes then the 11 after the state's address
constexpr DWORD kBattleCameraRva = 0x179540;
constexpr unsigned char kBattleCameraHead[] = {0x55, 0x8B, 0xEC, 0x51, 0xA1};
constexpr DWORD kBattleCameraTailRva = 0x179549;
constexpr unsigned char kBattleCameraTail[] = {0x89, 0x4D, 0xFC, 0xB9, 0x00, 0x00, 0x00, 0x20, 0x85, 0x48, 0x3C};
constexpr DWORD kStateImm = 0x179544 + 1;       // mov eax, [game state]

// movsd xmm1, [deadzone] in the battle camera's stick read, a double
constexpr DWORD kBattleDeadLoadRva = 0x176A29;
constexpr unsigned char kBattleDeadLoadHead[] = {0xF2, 0x0F, 0x10, 0x0D};

// the battle camera skips its update while either word has this, and its
// stick read zeroes the stick while the first has the other
constexpr DWORD kStatePaused = 0x20000000, kStateNoStick = 0x100;

constexpr DWORD kFieldFlags = 0x30;
constexpr BYTE kFieldNoCamera = 0x20;           // fights, events, anywhere the camera's not ours
constexpr DWORD kPadRightX = 0x2C, kPadRightY = 0x30, kPadExtra = 0xE8;
constexpr DWORD kStrike = 0x800;                // RightButton1, RB
constexpr DWORD kUp = 0x1, kDown = 0x2;         // LeftButtonUp/Down, what the arrow keys send

// at 60 fps and sensitivity 1. Picked by feel in testing
constexpr float kStickPerCount = 0.04f;

BYTE* g_base = nullptr;
DWORD* g_gate = nullptr;
BYTE** g_hid = nullptr;       // pad 0 hangs off this at +4
BYTE** g_field = nullptr;
DWORD* g_cancel = nullptr;
float* g_deadX = nullptr;
float* g_deadY = nullptr;
BYTE** g_state = nullptr;
double* g_battleDead = nullptr;
BYTE** g_gui = nullptr;
DWORD* g_decide = nullptr;

using FieldInputFn = void(__fastcall*)(void*, void*);
using PadUpdateFn = void(__fastcall*)(BYTE*, void*, void*);
using PlayerUpdateFn = int(__fastcall*)(BYTE*, void*, void*, void*, void*);
using BattleCameraFn = int(__fastcall*)(BYTE*, void*, void*, void*, void*);
using PeekMessageFn = BOOL(WINAPI*)(LPMSG, HWND, UINT, UINT, UINT);
FieldInputFn g_fieldInput = nullptr;
PadUpdateFn g_padUpdate = nullptr;
PlayerUpdateFn g_playerUpdate = nullptr;
BattleCameraFn g_battleCamera = nullptr;
PeekMessageFn g_peekMessage = nullptr;

std::atomic<bool> g_fieldLive{false};
std::atomic<ULONGLONG> g_playerTick{0}, g_battleTick{0};
std::atomic<HWND> g_window{nullptr};

std::atomic<bool> g_enabled{true}, g_menus{true}, g_diagnose{false};
std::atomic<float> g_sens{1.0f};
std::atomic<bool> g_invertX{false}, g_invertY{false};

// the message loop's wheel, only used when there's no DirectInput mouse
std::atomic<int> g_wheelMessages{0};
std::atomic<bool> g_haveMouse{false};

// game thread only
bool g_captured = false;
bool g_clipped = false;
LARGE_INTEGER g_lastPad{}, g_freq{};
IDirectInputDevice8W* g_mouse = nullptr;
bool g_mouseTried = false;

// ShowCursor only counts for the thread that owns the window, so the hide is
// handed to the game's message loop to apply
std::atomic<bool> g_wantHidden{false};
bool g_hidden = false;

BYTE* Pad0() {
    return *g_hid ? *reinterpret_cast<BYTE**>(*g_hid + 4) : nullptr;
}

// walking around = the field takes camera input, the player controller ran
// in the last few frames, and the game has focus. Fights flip the field's
// camera flag, and the controller check catches anything else that takes
// Lightning out of your hands
bool Exploring() {
    HWND w = g_window;
    return g_enabled && g_fieldLive && GetTickCount64() - g_playerTick < 100 && w &&
           GetForegroundWindow() == w;
}

// in a fight = the battle camera ran in the last few frames and is past the
// same checks it makes before reading the stick, so pausing a fight hands the
// mouse back
bool Fighting() {
    HWND w = g_window;
    if (!g_enabled || GetTickCount64() - g_battleTick >= 100 || !w || GetForegroundWindow() != w) return false;
    BYTE* state = *g_state;
    if (!state) return false;
    const DWORD a = *reinterpret_cast<DWORD*>(state + 0x3C), b = *reinterpret_cast<DWORD*>(state + 0x38);
    return !((a | b) & kStatePaused) && !(a & kStateNoStick);
}

template <typename T>
T At(BYTE* p, DWORD offset) {
    return *reinterpret_cast<T*>(p + offset);
}

// the same test the GUI's input pass makes before it gives the top window
// the pad, minus the bits about which button it's waiting on
BYTE* MenuTop() {
    BYTE* gui = *g_gui;
    if (!gui || !At<DWORD>(gui, kGuiGate)) return nullptr;
    BYTE* top = At<BYTE*>(gui, kGuiTop);
    const bool held = (At<DWORD>(gui, kGuiHoldA) && !At<DWORD>(gui, kGuiHoldRelease)) ||
                      At<DWORD>(gui, kGuiHoldB);
    return top && (!held || (top[kGuiTopFlags] & kGuiTopThrough)) ? top : nullptr;
}

// a menu over the field outranks the field. Away from it (the title screen,
// a save) any window the pass would feed is a menu
bool InMenu(bool roaming) {
    HWND w = g_window;
    if (!g_enabled || !g_menus || !w || GetForegroundWindow() != w) return false;
    BYTE* top = MenuTop();
    const BYTE modal = kGuiTopThrough | kGuiTopOpen;
    return top && (!roaming || (top[kGuiTopFlags] & modal) == modal);
}

bool CursorInGame() {
    HWND w = g_window;
    POINT p;
    RECT r;
    GetCursorPos(&p);
    ScreenToClient(w, &p);
    GetClientRect(w, &r);
    return PtInRect(&r, p);
}

enum class Mode { None, Field, Menu };

const char* Name(Mode m) {
    return m == Mode::Field ? "field" : m == Mode::Menu ? "menu" : "none";
}

struct Button {
    bool down = false;
    Mode from = Mode::None;
};
Button g_left, g_right;

// a click only counts in the mode it went down in, so the click that picks
// Resume doesn't carry into the field a frame later as a strike, and the
// right click that closes a menu doesn't turn into a jump
bool Held(Button& b, int vk, Mode mode, const char* which) {
    const bool down = GetAsyncKeyState(vk) & 0x8000;
    if (down && !b.down) {
        b.from = mode;
        if (g_diagnose && mode != Mode::None) Log("%s click in the %s", which, Name(mode));
    }
    b.down = down;
    return down && b.from == mode;
}

// GetAsyncKeyState reads physical buttons, so a left-handed swap has to be
// undone by hand or right click would strike. Runs every frame whatever the
// mode, or a press from a cutscene would count once a menu opened
DWORD Clicks(Mode mode) {
    const bool swap = GetSystemMetrics(SM_SWAPBUTTON);
    const bool left = Held(g_left, swap ? VK_RBUTTON : VK_LBUTTON, mode, "left");
    const bool right = Held(g_right, swap ? VK_LBUTTON : VK_RBUTTON, mode, "right");
    if (mode == Mode::None) return 0;
    DWORD bits = 0;
    if (left) bits |= mode == Mode::Menu ? *g_decide : kStrike;
    if (right) bits |= *g_cancel;
    return bits;
}

int g_wheelRest = 0, g_notches = 0;
bool g_pulsed = false;

// one press per notch, with a frame up between each so two quick notches
// don't read as one held press. Spinning it hard queues a few, no more
DWORD Scroll(int delta, bool menu) {
    if (!menu) {
        g_wheelRest = g_notches = 0;
        g_pulsed = false;
        return 0;
    }
    g_wheelRest += delta;
    g_notches += g_wheelRest / WHEEL_DELTA;
    g_wheelRest %= WHEEL_DELTA;
    g_notches = std::max(-8, std::min(8, g_notches));
    if (g_pulsed || !g_notches) {
        g_pulsed = false;
        return 0;
    }
    g_pulsed = true;
    if (g_diagnose) Log("wheel %s", g_notches > 0 ? "up" : "down");
    if (g_notches > 0) return --g_notches, kUp;
    return ++g_notches, kDown;
}

// c_dfDIMouse lives in dinput8.lib, which would drag an import table entry in
// for one struct. It's just the DIMOUSESTATE layout
DIOBJECTDATAFORMAT g_mouseObjects[] = {
    {&GUID_XAxis, DIMOFS_X, DIDFT_AXIS | DIDFT_ANYINSTANCE, 0},
    {&GUID_YAxis, DIMOFS_Y, DIDFT_AXIS | DIDFT_ANYINSTANCE, 0},
    {&GUID_ZAxis, DIMOFS_Z, DIDFT_AXIS | DIDFT_ANYINSTANCE | DIDFT_OPTIONAL, 0},
    {nullptr, DIMOFS_BUTTON0, DIDFT_BUTTON | DIDFT_ANYINSTANCE, 0},
    {nullptr, DIMOFS_BUTTON1, DIDFT_BUTTON | DIDFT_ANYINSTANCE, 0},
    {nullptr, DIMOFS_BUTTON2, DIDFT_BUTTON | DIDFT_ANYINSTANCE | DIDFT_OPTIONAL, 0},
    {nullptr, DIMOFS_BUTTON3, DIDFT_BUTTON | DIDFT_ANYINSTANCE | DIDFT_OPTIONAL, 0},
};
DIDATAFORMAT g_mouseFormat = {sizeof(DIDATAFORMAT), sizeof(DIOBJECTDATAFORMAT), DIDF_RELAXIS,
                              sizeof(DIMOUSESTATE), 7, g_mouseObjects};

// non-exclusive and background so it never fights the game, NovaFix or
// ReShade for the mouse. The game imports dinput8 for its keyboard already
void OpenMouse() {
    g_mouseTried = true;
    using CreateFn = HRESULT(WINAPI*)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);
    HMODULE dll = GetModuleHandleW(L"dinput8.dll");
    auto create = dll ? reinterpret_cast<CreateFn>(reinterpret_cast<void*>(GetProcAddress(dll, "DirectInput8Create")))
                      : nullptr;
    IDirectInput8W* di = nullptr;
    if (create && SUCCEEDED(create(g_module, DIRECTINPUT_VERSION, IID_IDirectInput8W, reinterpret_cast<LPVOID*>(&di), nullptr)) &&
        SUCCEEDED(di->CreateDevice(GUID_SysMouse, &g_mouse, nullptr)) &&
        SUCCEEDED(g_mouse->SetDataFormat(&g_mouseFormat)) &&
        SUCCEEDED(g_mouse->SetCooperativeLevel(g_window, DISCL_NONEXCLUSIVE | DISCL_BACKGROUND))) {
        g_mouse->Acquire();
        return;
    }
    if (g_mouse) g_mouse->Release();
    g_mouse = nullptr;
    Log("DirectInput mouse unavailable, measuring the cursor instead");
}

bool ReadMouse(long& dx, long& dy, long& dz) {
    DIMOUSESTATE state{};
    HRESULT hr = g_mouse->GetDeviceState(sizeof state, &state);
    if (hr == DIERR_INPUTLOST || hr == DIERR_NOTACQUIRED) {
        g_mouse->Acquire();
        hr = g_mouse->GetDeviceState(sizeof state, &state);
    }
    if (FAILED(hr)) return false;
    dx = state.lX;
    dy = state.lY;
    dz = state.lZ;
    return true;
}

POINT WindowCenter() {
    HWND w = g_window;
    RECT r;
    GetClientRect(w, &r);
    POINT c{(r.right - r.left) / 2, (r.bottom - r.top) / 2};
    ClientToScreen(w, &c);
    return c;
}

// a one pixel box, so the cursor can't drift onto anything and the game never
// sees it move. Windows drops clips on focus changes, hence checking each frame
void Clip(POINT c) {
    RECT want{c.x, c.y, c.x + 1, c.y + 1}, now;
    GetClipCursor(&now);
    if (!EqualRect(&want, &now)) ClipCursor(&want);
    g_clipped = true;
}

void Release() {
    g_captured = false;
    g_wantHidden = false;
    if (g_clipped) ClipCursor(nullptr);
    g_clipped = false;
}

// movement since last frame. The first frame after grabbing just flushes
// whatever piled up while we weren't looking
bool MouseDelta(long& dx, long& dy, long& dz) {
    dx = dy = dz = 0;
    if (!g_mouseTried) OpenMouse();
    g_haveMouse = g_mouse != nullptr;
    const POINT c = WindowCenter();
    const bool fresh = !g_captured;
    g_captured = true;
    g_wantHidden = true;
    if (g_mouse) {
        Clip(c);
        return ReadMouse(dx, dy, dz) && !fresh;
    }
    dz = g_wheelMessages.exchange(0);
    POINT p;
    GetCursorPos(&p);
    if (p.x != c.x || p.y != c.y) SetCursorPos(c.x, c.y);
    if (fresh) return false;
    dx = p.x - c.x;
    dy = p.y - c.y;
    return true;
}

// the camera zeroes anything under the deadzone and rescales the rest, so
// feed it the value that comes out the other side as v
float PastDeadzone(float v, float dead) {
    if (v == 0) return 0;
    const float out = dead + std::fabs(v) * (1 - dead);
    return v < 0 ? -out : out;
}

void Look(BYTE* pad, float dt, float deadX, float deadY, long dx, long dy) {
    if (!dx && !dy) return;
    // stick values are a turn rate, so dividing by frame time makes them a
    // turn per count instead
    const float scale = kStickPerCount * g_sens * (1.0f / 60) / dt;
    float x = dx * scale, y = -dy * scale;
    if (g_invertX) x = -x;
    if (g_invertY) y = -y;
    auto* rx = reinterpret_cast<float*>(pad + kPadRightX);
    auto* ry = reinterpret_cast<float*>(pad + kPadRightY);
    // stacking on the camera keys is fine as is, only a lone mouse needs the lift
    *rx += *rx == 0 ? PastDeadzone(x, deadX) : x;
    *ry += *ry == 0 ? PastDeadzone(y, deadY) : y;
}

char g_lastState[256] = "";
int g_logged = 0;

// what the menu test sees, a line each time it changes. Capped, since a
// screen that flips a flag every frame would otherwise fill the disk
void Diagnose(bool roaming, bool fighting, bool menu) {
    if (!g_diagnose || g_logged > 4000) return;
    BYTE* gui = *g_gui;
    BYTE* top = gui ? At<BYTE*>(gui, kGuiTop) : nullptr;
    const char* name = "none";
    if (top) {
        // vtable, then its RTTI locator, then the type descriptor's name
        auto* vtable = At<BYTE*>(top, 0);
        auto* locator = *reinterpret_cast<BYTE**>(vtable - 4);
        name = reinterpret_cast<const char*>(At<BYTE*>(locator, 12) + 8);
    }
    char now[256];
    std::snprintf(now, sizeof now,
                  "roaming %d fighting %d menu %d | field %d player %d | gate %lu top %s flags %02x hold %lu %lu %lu",
                  roaming, fighting, menu, g_fieldLive.load(), GetTickCount64() - g_playerTick < 100,
                  gui ? At<DWORD>(gui, kGuiGate) : 0, name, top ? top[kGuiTopFlags] : 0,
                  gui ? At<DWORD>(gui, kGuiHoldA) : 0, gui ? At<DWORD>(gui, kGuiHoldB) : 0,
                  gui ? At<DWORD>(gui, kGuiHoldRelease) : 0);
    if (std::strcmp(now, g_lastState) == 0) return;
    std::strcpy(g_lastState, now);
    ++g_logged;
    Log("%llu %s", GetTickCount64(), now);
}

void __fastcall PadUpdateHook(BYTE* pad, void* edx, void* elapsed) {
    if (pad != Pad0()) {
        g_padUpdate(pad, edx, elapsed);
        return;
    }
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    float dt = g_lastPad.QuadPart ? float(now.QuadPart - g_lastPad.QuadPart) / g_freq.QuadPart : 1.0f / 60;
    g_lastPad = now;
    dt = std::fmin(std::fmax(dt, 1.0f / 1000), 1.0f / 10);

    // only grab when the cursor's already over the game, so clicking into the
    // window from outside doesn't yank it or swing a sword. Clicks are for the
    // field only: in a fight those buttons are abilities and schemata
    const bool fighting = Fighting();
    const bool roaming = !fighting && Exploring();
    const bool menu = !fighting && InMenu(roaming);
    const bool exploring = roaming && !menu;
    Diagnose(roaming, fighting, menu);
    const bool driving = (fighting || exploring || menu) && (g_captured || CursorInGame());
    const Mode mode = !driving ? Mode::None : exploring ? Mode::Field : menu ? Mode::Menu : Mode::None;

    // the mouse is read before the update so the wheel can go in with the clicks
    long dx = 0, dy = 0, dz = 0;
    const bool moved = driving && MouseDelta(dx, dy, dz);
    auto& extra = *reinterpret_cast<DWORD*>(pad + kPadExtra);
    extra |= Clicks(mode) | Scroll(moved ? dz : 0, mode == Mode::Menu);
    g_padUpdate(pad, edx, elapsed);
    if (!driving) {
        if (g_captured) Release();
        return;
    }
    if (!moved) return;
    // a menu keeps the cursor hidden and still, but movement turns nothing
    if (fighting) Look(pad, dt, float(*g_battleDead), float(*g_battleDead), dx, dy);
    else if (exploring) Look(pad, dt, *g_deadX, *g_deadY, dx, dy);
}

void __fastcall FieldInputHook(void* self, void* edx) {
    g_fieldInput(self, edx);
    BYTE* field = (*g_gate && Pad0()) ? *g_field : nullptr;
    g_fieldLive = field && !(field[kFieldFlags] & kFieldNoCamera);
}

int __fastcall PlayerUpdateHook(BYTE* self, void* edx, void* a, void* b, void* c) {
    const int r = g_playerUpdate(self, edx, a, b, c);
    g_playerTick = GetTickCount64();
    return r;
}

int __fastcall BattleCameraHook(BYTE* self, void* edx, void* a, void* b, void* c) {
    const int r = g_battleCamera(self, edx, a, b, c);
    g_battleTick = GetTickCount64();
    return r;
}

// the game's own cursor show/hide is a ShowCursor(TRUE)/(FALSE) pair driven by
// recent mouse activity, and clicks count. One extra FALSE on top keeps it
// hidden whatever the game decides, and taking it back off leaves the game's
// count exactly where it was
BOOL WINAPI PeekMessageHook(LPMSG msg, HWND hwnd, UINT lo, UINT hi, UINT remove) {
    const bool want = g_wantHidden;
    if (want != g_hidden) {
        HWND w = g_window;
        if (w && GetWindowThreadProcessId(w, nullptr) == GetCurrentThreadId()) {
            ShowCursor(want ? FALSE : TRUE);
            g_hidden = want;
        }
    }
    const BOOL got = g_peekMessage(msg, hwnd, lo, hi, remove);
    if (got && (remove & PM_REMOVE) && msg->message == WM_MOUSEWHEEL && !g_haveMouse)
        g_wheelMessages += GET_WHEEL_DELTA_WPARAM(msg->wParam);
    return got;
}

bool Matches(DWORD rva, const unsigned char* head, size_t n, const char* what) {
    // some other build can be smaller than the spot we'd read, so stay inside it
    auto* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(g_base);
    auto* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(g_base + dos->e_lfanew);
    if (rva + n <= nt->OptionalHeader.SizeOfImage && memcmp(g_base + rva, head, n) == 0) return true;
    Log("this LRFF13.exe isn't the Steam build mouse support was made for (%s doesn't match), "
        "so it's staying off", what);
    return false;
}

template <typename T>
T Imm(DWORD rva) {
    return *reinterpret_cast<T*>(g_base + rva);
}

bool Verify() {
    g_base = reinterpret_cast<BYTE*>(GetModuleHandleW(nullptr));
    if (!Matches(kFieldInputRva, kFieldInputHead, sizeof kFieldInputHead, "field input") ||
        !Matches(kPadUpdateRva, kPadUpdateHead, sizeof kPadUpdateHead, "pad update") ||
        !Matches(kPlayerUpdateRva, kPlayerUpdateHead, sizeof kPlayerUpdateHead, "player update") ||
        !Matches(kCancelLoadRva, kCancelLoadHead, sizeof kCancelLoadHead, "jump button") ||
        !Matches(kGuiLoadRva, kGuiLoadHead, sizeof kGuiLoadHead, "menu input") ||
        !Matches(kGuiPassRva, kGuiPassHead, sizeof kGuiPassHead, "menu input") ||
        !Matches(kDecideLoadRva, kDecideLoadHead, sizeof kDecideLoadHead, "menu decide button") ||
        !Matches(kDeadXLoadRva, kDeadXLoadHead, sizeof kDeadXLoadHead, "camera deadzone") ||
        !Matches(kDeadYLoadRva, kDeadYLoadHead, sizeof kDeadYLoadHead, "camera deadzone") ||
        !Matches(kBattleCameraRva, kBattleCameraHead, sizeof kBattleCameraHead, "battle camera") ||
        !Matches(kBattleCameraTailRva, kBattleCameraTail, sizeof kBattleCameraTail, "battle camera") ||
        !Matches(kBattleDeadLoadRva, kBattleDeadLoadHead, sizeof kBattleDeadLoadHead, "battle deadzone"))
        return false;
    g_gate = Imm<DWORD*>(kGateImm);
    g_hid = Imm<BYTE**>(kHidImm);
    g_field = Imm<BYTE**>(kFieldImm);
    g_cancel = Imm<DWORD*>(kCancelLoadRva + 1);
    g_deadX = Imm<float*>(kDeadXLoadRva + 4);
    g_deadY = Imm<float*>(kDeadYLoadRva + 4);
    g_state = Imm<BYTE**>(kStateImm);
    g_battleDead = Imm<double*>(kBattleDeadLoadRva + 4);
    g_gui = Imm<BYTE**>(kGuiLoadRva + 2);
    g_decide = Imm<DWORD*>(kDecideLoadRva + 2);
    return true;
}

bool Hook(LPVOID target, LPVOID detour, LPVOID* original) {
    return MH_CreateHook(target, detour, original) == MH_OK && MH_EnableHook(target) == MH_OK;
}

BOOL CALLBACK FindGameWindow(HWND hwnd, LPARAM best) {
    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid != GetCurrentProcessId() || !IsWindowVisible(hwnd)) return TRUE;
    RECT r{};
    GetClientRect(hwnd, &r);
    auto* pick = reinterpret_cast<std::pair<HWND, long>*>(best);
    const long area = (r.right - r.left) * (r.bottom - r.top);
    if (area > pick->second) *pick = {hwnd, area};
    return TRUE;
}

// the window can be recreated on a display mode switch, so keep looking it up
void RefreshWindow() {
    std::pair<HWND, long> best{nullptr, 0};
    EnumWindows(FindGameWindow, reinterpret_cast<LPARAM>(&best));
    g_window = best.first;
}

// a missing ini or key just means the default. The patcher installs the file,
// but somebody deleting it shouldn't take the mouse away
void ReadSettings() {
    const std::wstring ini = g_dir + L"\\lr-mouse.ini";
    wchar_t buf[32];
    GetPrivateProfileStringW(L"Mouse", L"Sensitivity", L"1.0", buf, 32, ini.c_str());
    float sens = std::wcstof(buf, nullptr);
    if (!(sens > 0)) sens = 1.0f;
    g_sens = std::fmin(sens, 20.0f);
    g_enabled = GetPrivateProfileIntW(L"Mouse", L"Enabled", 1, ini.c_str()) != 0;
    g_invertX = GetPrivateProfileIntW(L"Mouse", L"InvertX", 0, ini.c_str()) != 0;
    g_invertY = GetPrivateProfileIntW(L"Mouse", L"InvertY", 0, ini.c_str()) != 0;
    g_menus = GetPrivateProfileIntW(L"Mouse", L"Menus", 1, ini.c_str()) != 0;
    // not in the shipped ini: Diagnostics=1 logs what the menu test sees, for
    // when a screen nobody's tried yet doesn't answer the mouse
    g_diagnose = GetPrivateProfileIntW(L"Mouse", L"Diagnostics", 0, ini.c_str()) != 0;
    Log("settings: %s, sensitivity %.2f, invert x %d y %d, menus %d", g_enabled ? "on" : "off", g_sens.load(),
        g_invertX.load(), g_invertY.load(), g_menus.load());
}

FILETIME SettingsStamp() {
    WIN32_FILE_ATTRIBUTE_DATA data{};
    GetFileAttributesExW((g_dir + L"\\lr-mouse.ini").c_str(), GetFileExInfoStandard, &data);
    return data.ftLastWriteTime;
}

DWORD WINAPI Worker(LPVOID) {
    Log("lr_mouse 1.2, pid %lu", GetCurrentProcessId());
    if (!Verify()) return 0;
    QueryPerformanceFrequency(&g_freq);
    ReadSettings();
    RefreshWindow();
    if (MH_Initialize() != MH_OK ||
        !Hook(g_base + kPadUpdateRva, reinterpret_cast<LPVOID>(&PadUpdateHook), reinterpret_cast<LPVOID*>(&g_padUpdate)) ||
        !Hook(g_base + kFieldInputRva, reinterpret_cast<LPVOID>(&FieldInputHook), reinterpret_cast<LPVOID*>(&g_fieldInput)) ||
        !Hook(g_base + kPlayerUpdateRva, reinterpret_cast<LPVOID>(&PlayerUpdateHook), reinterpret_cast<LPVOID*>(&g_playerUpdate)) ||
        !Hook(g_base + kBattleCameraRva, reinterpret_cast<LPVOID>(&BattleCameraHook), reinterpret_cast<LPVOID*>(&g_battleCamera)) ||
        !Hook(reinterpret_cast<LPVOID>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "PeekMessageA")),
              reinterpret_cast<LPVOID>(&PeekMessageHook), reinterpret_cast<LPVOID*>(&g_peekMessage))) {
        // all or nothing: half the hooks in is a mouse that grabs the cursor
        // and never turns the camera
        MH_DisableHook(MH_ALL_HOOKS);
        Log("couldn't hook the game, mouse support is off this run");
        return 0;
    }
    Log("ready");
    FILETIME stamp = SettingsStamp();
    for (;;) {
        Sleep(1000);
        RefreshWindow();
        const FILETIME now = SettingsStamp();
        if (CompareFileTime(&now, &stamp) != 0) {
            stamp = now;
            ReadSettings();
        }
    }
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(module);
    g_module = module;
    wchar_t path[MAX_PATH]{};
    GetModuleFileNameW(module, path, MAX_PATH);
    g_dir = path;
    g_dir.resize(g_dir.find_last_of(L"\\/"));
    // a fresh log each launch, so it's only ever about the run you just had
    DeleteFileW((g_dir + L"\\lr-mouse.log").c_str());
    // never in the loader lock: MinHook suspends threads to patch
    HANDLE thread = CreateThread(nullptr, 0, Worker, nullptr, 0, nullptr);
    if (thread) CloseHandle(thread);
    return TRUE;
}
