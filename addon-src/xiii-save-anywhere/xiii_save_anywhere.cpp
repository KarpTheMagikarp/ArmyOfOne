// Save Anywhere for FFXIII, as a NovaFix add-on. Pause out in the field and
// press Y (or whatever key XIII puts it on) to save through the game's own
// save screen, and loading that save puts you back where you were standing
// instead of at the station.
//
// None of this is new game logic, it's the game's own moves in a new order.
// XIII runs on one zone state machine (ZoneStateMachine.cpp), and a station
// saves by firing TRG_SAVE_REQ, which the machine only takes from FIELD_IDLE:
// free roam, never an event, a battle or a cutscene. Pausing out in the field
// is its own state too (FIELD_PAUSE), apart from the event and battle pauses.
// So from that pause we press Resume for you, then fire SAVE_REQ the moment
// the machine is back in FIELD_IDLE, and the save screen runs exactly the way
// it does at a station.
//
// A load drops you at a save point: an id the game sets whenever you walk into
// a station's area, looked up in the zone's r_savepoint records. We only offer
// the save while that lookup works, so the save always points into the area
// you're in, and a load can never start you somewhere you haven't reached.
// Where you actually stood (the live world matrix, so facing too) goes in a
// .where file in this folder, one per slot. When that save is loaded, if it
// still matches, the field's start marker answers with that matrix instead of
// the station's, so the game's own spawn lands you there. Anything off and you
// start at the station, same as vanilla.
//
// The prompt goes on the pause screen itself, beside Quit, in the game's own
// font and button glyph, so it follows whatever you're playing on. (A NovaFix
// notification can't do it: the pause freezes the layer those draw on, so one
// only shows once you've unpaused, out where Y opens the main menu.) Only the
// Steam ffxiiiimg.exe is supported. Anything else gets a line in the log and
// the add-on stays out of the way.

#include <windows.h>

#include <MinHook.h>
#include <novafix/plugin_api.h>

#include <atomic>
#include <algorithm>
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwctype>
#include <string>
#include <map>
#include <set>
#include <vector>

namespace {

std::wstring g_dir;
std::uintptr_t g_base = 0;
bool g_debug = false;

void Write(const char* format, va_list args) {
    FILE* file = nullptr;
    if (_wfopen_s(&file, (g_dir + L"\\xiii-save-anywhere.log").c_str(), L"a") != 0 || !file) return;
    vfprintf(file, format, args);
    fputc('\n', file);
    fclose(file);
}

void Log(const char* format, ...) {
    va_list args;
    va_start(args, format);
    Write(format, args);
    va_end(args);
}

// the pause bar, button and spawn traces that pinned this down. Worth having
// when a report comes in, noise in everyone else's log, so Debug=1 only
void Trace(const char* format, ...) {
    if (!g_debug) return;
    va_list args;
    va_start(args, format);
    Write(format, args);
    va_end(args);
}

// the add-on's own folder, plus one of its subfolders, made on first use. A
// player with a hundred slots shouldn't find a hundred files next to the ini
std::string Folder(const char* sub) {
    const std::wstring wide = g_dir + L"\\" + std::wstring(sub, sub + std::strlen(sub));
    CreateDirectoryW(wide.c_str(), nullptr);
    return std::string(g_dir.begin(), g_dir.end()) + "\\" + sub;
}

// RVAs in the Steam build (ffxiiiimg.exe, sha256 a633de01..., 2014). XIII has
// no relocations so it always sits at 0x400000, but it's base + RVA anyway,
// and the globals are read out of the instructions that use them
// The per-frame hook is the pad update, not InputManager::Update (0x41FBE0)
// that calls it: NovaFix detours that one for its dynamic prompts and checks
// its prologue first, so our jump there switched them off
constexpr DWORD kPadUpdateRva = 0x4206C0;       // Pad::Update, thiscall(arg), once a frame per pad
constexpr unsigned char kPadUpdateHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xE4, 0xF0, 0x81, 0xEC, 0x90, 0x01, 0x00, 0x00};
constexpr DWORD kGetStateRva = 0x417850;        // ZoneStateMachine::GetState, thiscall
constexpr unsigned char kGetStateHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08, 0x89, 0x4D, 0xF8, 0x6A, 0x00};
constexpr DWORD kZoneFireRva = 0x40D070;        // Zone::FireTrigger, thiscall(trigger)
constexpr unsigned char kZoneFireHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08, 0x89, 0x4D, 0xF8, 0x8B, 0x45, 0xF8};
constexpr DWORD kGetSavePointRva = 0x728EE0;    // cdecl(out 16 bytes)
constexpr unsigned char kGetSavePointHead[] = {0x55, 0x8B, 0xEC, 0x8B, 0x45, 0x08, 0x50, 0xE8};
constexpr DWORD kFindSavePointRowRva = 0x43D9C0;  // cdecl(), the r_savepoint row a load spawns from, or null
constexpr unsigned char kFindSavePointRowHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x5C, 0xA1};
// where a chara stands. Chara::getPos is no use from here: it only queues
// script command 0xB8 to fill a script variable later. Under the actor
// registry's lock (+0x6C0) the handle resolves to the actor, whose live world
// matrix is at +0x78 (translation row at +0xA8). +0x150 is only a request
// block setters like setPosFlash drop values into for 0x7bfca0 to apply, and
// her physics body never follows it: nothing here moves her after the spawn
constexpr DWORD kActorRegistryRva = 0x22E80DC;  // the registry pointer
constexpr DWORD kResolveActorRva = 0x2A18B0;    // thiscall(registry, handle), the same one NovaFix uses
constexpr unsigned char kResolveActorHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x10, 0x89, 0x4D, 0xF0, 0x33, 0xC0, 0x83};
constexpr DWORD kLockRva = 0x09D1A0, kUnlockRva = 0x09D220;  // thiscall(tag), thiscall()
constexpr unsigned char kLockHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x14, 0x89, 0x4D, 0xEC, 0x83, 0x7D, 0x08};
constexpr unsigned char kUnlockHead[] = {0x55, 0x8B, 0xEC, 0x51, 0x89, 0x4D, 0xFC, 0x8B, 0x45, 0xFC, 0x83, 0x78};
constexpr DWORD kActorMatrix = 0x78, kActorTranslation = 0xA8;
// a collision ray the player controller (0x626240) casts along her facing on
// layer 11: thiscall(world, from, to, hit, layers). Not its ground check after
// all (Nov 23), but the same world, so it says what's under her on any layer
constexpr DWORD kGroundRayRva = 0x5B000, kGroundWorldRva = 0x23FCEB4;
constexpr unsigned char kGroundRayHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x10, 0x89, 0x4D, 0xF4, 0x51, 0x89, 0x65};
// the level's own floor isn't in that world at all, and it's the only thing a
// spawned body lands on. What is in it is the stuff the level moves: the Ch 9
// lift's slab reads layer 16 at her feet before the button and after it, and
// every spot that dropped her on load was a 16 one (Nov 23). So anything under
// her feet besides her own body (14) and the flat plane under the field (1)
// isn't floor a load can stand her on
constexpr int kNotFloorLayers = ~((1 << 1) | (1 << 14));
// actor->setMatrix, thiscall(matrix, warp, lock). The field controller moves
// her through it from one call site, the ease-in after a load included, so
// that's where the ease-in gets held to the ground she walked in on
constexpr DWORD kSetMatrixRva = 0x3C1970, kControllerWriteRva = 0x227683;
// the field's player controller, the object the movement update runs on
// (RVA 0x228070 reads it first thing). Its flag word at +0x154 picks the
// path that moves her with no stick. +0x1A8 is where it has her, the exact
// numbers every load's trace started and landed on, and +0x1BC the height
// the ease-in is heading for (Ch 3, Nov 26)
constexpr DWORD kFieldPlayerRva = 0x23FF554;
constexpr size_t kFieldPlayerFlags = 0x154, kFieldPlayerSpan = 0x600;
constexpr size_t kControllerAt = 0x1A8, kControllerAimY = 0x1BC;
// what the ground under her moved this frame: exactly nought on the level's own
// floor, 1e-6 noise on a parked platform, and on a moving one its step, which
// matched the Ch 7 shaft platform's climb in direction and speed (Nov 27)
constexpr size_t kControllerCarry = 0x7C;
constexpr float kCarryMoving = 0.002f;
// and the w after it says what kind of ground that is: exactly 1 on the level's
// own, 0 on anything it moves, parked or not. 48 pauses over Palumpolum,
// Nautilus and Gran Pulse never once had it wrong (Nov 27)
constexpr size_t kControllerGround = 0x88;
constexpr unsigned char kSetMatrixHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x4C, 0x89, 0x4D, 0xB4};
constexpr DWORD kPlayerHandleRva = 0x2C0810;    // cdecl(out), the chara you're controlling
constexpr unsigned char kPlayerHandleHead[] = {0x55, 0x8B, 0xEC, 0x51, 0xA1};
constexpr DWORD kZoneLoadRva = 0x7EB3A4;        // mov ecx, [zone] in Zone's trigger native
constexpr unsigned char kMovEcxMem[] = {0x8B, 0x0D};
// the pause widget's mode setter, thiscall(mode): builds the bottom right
// labels from text ids, $pause_01 (Quit) on the left. NovaFix never touches it
constexpr DWORD kPauseModeRva = 0x546630;
constexpr unsigned char kPauseModeHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x38, 0x89, 0x4D, 0xE0, 0x8B, 0x45, 0xE0};
// the save slot's writer and reader, thiscall. Both go to Steam Cloud when
// it's on (the [0x28339c8] storage object) and to a file when it isn't, so
// these see every save and every load whichever way it went. CreateFileA
// never sees a cloud save at all
constexpr DWORD kSaveWriteRva = 0x439810;       // (?, ?, const void* data, int size), ret 0x10
constexpr unsigned char kSaveWriteHead[] = {0x55, 0x8B, 0xEC, 0x81, 0xEC, 0x68, 0x0C, 0x00, 0x00, 0xA1, 0x84, 0x5A};
// where a field starts you: the start marker a station's row names, turned
// into up to three world matrices (leader and party) by the marker lookup,
// thiscall(marker, out0, out1, out2), count back. The spawn asks it, then the
// camera and the party placement each ask it again for the same marker, so
// it's the lookup that has to answer with the save's spot, not one caller
constexpr DWORD kMarkerLookupRva = 0x304A80;
// a station's area, walked into: thiscall(a, save point id), ret 8. It's what
// makes that station the current save point ("onEnter savepoint area"), so
// it's the moment the game itself would let you save
constexpr DWORD kAreaEnterRva = 0x1BD1C0;
constexpr unsigned char kAreaEnterHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x20, 0xA1, 0x84, 0x5A, 0x6E, 0x02, 0x33};
// White.doRestartSave's engine side, cdecl(vm), plain ret: a script sets the
// save point and writes the restart snapshot that game over's Retry comes back
// to. The only other writer of that snapshot is the new game start, so these
// are the spots the designers marked as safe to rebuild a field from, same as
// a station. The native table's names sit one entry off: this is the func on
// doSave_l's row
constexpr DWORD kRestartSaveRva = 0x7E63D0;
constexpr unsigned char kRestartSaveHead[] = {0x55, 0x8B, 0xEC, 0x81, 0xEC, 0xA0, 0x00, 0x00, 0x00, 0xA1, 0x84, 0x5A,
                                              0x6E, 0x02};
constexpr unsigned char kMarkerLookupHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x44, 0x56, 0x89, 0x4D, 0xC0, 0xA1, 0x70};
// the start marker itself: [[0x27ff068]+0x40] + 0x2CC, read under its lock
constexpr DWORD kFieldStartRva = 0x23FF068;
constexpr DWORD kStartMarkerRva = 0x1D30D0;
constexpr unsigned char kStartMarkerHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08, 0x89, 0x4D, 0xF8, 0x6A, 0x00, 0x8B};
constexpr DWORD kSaveReadRva = 0x439F30;        // (?, void* out, int size), ret 0xC
constexpr unsigned char kSaveReadHead[] = {0x55, 0x8B, 0xEC, 0x81, 0xEC, 0x3C, 0x02, 0x00, 0x00, 0x89, 0x8D, 0xC4};
// TextString::SetId(id, arg), thiscall: looks a text id like "$pause_03" up
// and holds the result. The labels get their text through it, inside the
// mode setter, and its ctor and dtor bracket a lookup of our own. NovaFix
// guards the label calls after it (0x63B920, 0x680090), never these
constexpr DWORD kTextSetIdRva = 0x4FC870;
constexpr unsigned char kTextSetIdHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x44, 0x89, 0x4D, 0xBC, 0x8B, 0x45, 0xBC};
constexpr DWORD kTextCtorRva = 0x4FC7E0, kTextDtorRva = 0x4FC820;
// the story so far, on the party database [0x282b230]: Party.getStoryNumber's
// engine side, cdecl, reads +0x2590 under the database's lock (+0x27210). The
// event flags are 0x2000 bits at +0x1520 under the same lock
constexpr DWORD kStoryRva = 0x76C750;
constexpr unsigned char kStoryHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x14, 0x6A, 0x00, 0x8B, 0x0D, 0x30, 0xB2,
                                        0x82, 0x02, 0x81, 0xC1, 0x10, 0x72, 0x02, 0x00, 0xE8, 0x37, 0x0A, 0x93,
                                        0xFF, 0xA1, 0x30, 0xB2, 0x82, 0x02, 0x8B, 0x88, 0x90, 0x25, 0x00, 0x00};
constexpr DWORD kEventFlagRva = 0x768196;       // inside Party.getEventFlag's lookup
constexpr unsigned char kEventFlagHead[] = {0x8B, 0x0D, 0x30, 0xB2, 0x82, 0x02, 0x23, 0x94, 0x81, 0x20, 0x15, 0x00, 0x00};
constexpr DWORD kPartyLock = 0x27210, kEventFlags = 0x1520, kEventFlagBytes = 0x400;
// the scripts' own variables (Party.setWork), 0x200 dwords right before the
// event flags at +0xD20, so the two are read as one block
constexpr DWORD kWorkRva = 0x76807E;            // inside Party.setWork's store
constexpr unsigned char kWorkHead[] = {0x8B, 0x0D, 0x30, 0xB2, 0x82, 0x02, 0x8B, 0x55,
                                       0x0C, 0x89, 0x94, 0x81, 0x20, 0x0D, 0x00, 0x00};
constexpr DWORD kWork = 0xD20, kWorkBytes = 0x800;
// the zone's own flags (Zone.setFlag), [[zone]+0x10]+0xD8. They live only as
// long as the zone, so a save never keeps them: one that's moved since the
// state was sanctioned is exactly the sort of thing a reload would lose
constexpr DWORD kZoneFlagRva = 0x7EB290;        // Zone.checkFlag's read
constexpr unsigned char kZoneFlagHead[] = {0x8B, 0x0D, 0x90, 0x11, 0x81, 0x02, 0x89, 0x4D, 0xF0, 0x8B, 0x55, 0xF0,
                                           0x8B, 0x42, 0x10, 0x89, 0x45, 0xEC, 0x8B, 0x55, 0xFC, 0xC1, 0xEA, 0x05,
                                           0x8B, 0x4D, 0xFC, 0x83, 0xE1, 0x1F, 0xB8, 0x01, 0x00, 0x00, 0x00, 0xD3,
                                           0xE0, 0x8B, 0x4D, 0xEC, 0x23, 0x84, 0x91, 0xD8, 0x00, 0x00, 0x00};
// how many words of them get compared: the game never bounds the ids, and
// more words than the zone uses only makes the guard stricter, never looser
constexpr DWORD kZoneFlagWords = 8;
// two things a scripted sequence switches: the field's UI-off bits
// ([[0x27ff068]+0x1C], Field.enableFieldUserInterface) and the menu mask bits
// ([[0x282b1f4]+0x184], White.setMenuMaskFlag)
constexpr DWORD kFieldUiRva = 0x7D1AC9;
constexpr unsigned char kFieldUiHead[] = {0x8B, 0x45, 0xEC, 0x8B, 0x48, 0x1C, 0x0B, 0x4D, 0xFC, 0x8B, 0x55, 0xEC};
constexpr DWORD kMenuMaskRva = 0x75D8CD, kMenuMaskBitsRva = 0x75D901;
constexpr unsigned char kMenuMaskHead[] = {0xA1, 0xF4, 0xB1, 0x82, 0x02};
constexpr unsigned char kMenuMaskBitsHead[] = {0x23, 0x84, 0x91, 0x84, 0x01, 0x00, 0x00};
// what scripts do to the field itself, at the engine end, so whatever ran the
// native (a zone script, a cutscene's own track) goes through here. Every one
// takes the target as a 16-byte name, built from the script's string. All but
// the encounter switch are thiscall on the scene, [0x2801b70]
constexpr DWORD kSceneRva = 0x2401B70;
constexpr DWORD kAreaOnRva = 0x3036E0;          // (name, flags), what every activateArea flavour calls
constexpr unsigned char kAreaOnHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08, 0x89, 0x4D, 0xF8};
constexpr DWORD kAreaFadeRva = 0x302950;        // (name, ?), activateAreaAlpha
constexpr unsigned char kAreaFadeHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x40, 0xA1, 0x84, 0x5A, 0x6E, 0x02};
constexpr DWORD kAreaOffRva = 0x303F40;         // (name, 1)
constexpr unsigned char kAreaOffHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08, 0x89, 0x4D, 0xF8};
constexpr DWORD kObjectRva = 0x2FBC70;          // (name, live), validateObject and invalidateObject
constexpr unsigned char kObjectHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x34, 0x89, 0x4D, 0xE8};
// (object, ?, motion, mode), ret 0x10. The script's mode with bit 0 set to
// play, 0 to stop. Scripts use 4 for "at the last frame", 8 to loop
constexpr DWORD kBgAnimRva = 0x2F9750;
constexpr unsigned char kBgAnimHead[] = {0x55, 0x8B, 0xEC, 0x81, 0xEC, 0xD8, 0x0C, 0x00, 0x00};
// (name, set, shown), ret 0xC: show/hideLayoutInstance pass the fixed layout
// set at [0x2802b98] as set, show/hideBgModel a second name
constexpr DWORD kLayoutRva = 0x2F8DD0, kLayoutSetRva = 0x2402B98;
constexpr unsigned char kLayoutHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x40, 0xA1, 0x84, 0x5A, 0x6E, 0x02};
constexpr DWORD kEncountRva = 0x1B0210;         // cdecl(on), Field.enableEncount
constexpr unsigned char kEncountHead[] = {0x55, 0x8B, 0xEC, 0x8B, 0x45, 0x08, 0x50};
// a map set asked for: thiscall on the zone (name), Resource.loadBg and a
// cutscene's own loads both. The name in play is [[0x2731238]+0xA28]
constexpr DWORD kMapSetRva = 0x40BC20, kMapSetHolderRva = 0x2331238, kMapSetName = 0xA28;
constexpr unsigned char kMapSetHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x20, 0x89, 0x4D, 0xE4};
// game over's Retry: rebuilds the field from the restart snapshot, thiscall()
constexpr DWORD kRetryRva = 0x40BA30;
constexpr unsigned char kRetryHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x34, 0x89, 0x4D, 0xCC};
// natives that change the field in ways the journal can't put back yet, kept
// so the log says when they happen. cdecl(vm), the args in 8-byte slots from
// [vm+0x114]: a type, then the value, a char* for a string
constexpr DWORD kCreateCharaRva = 0x7CC290, kReleaseCharaRva = 0x7CC4F0, kTalkOnRva = 0x7CC580,
                kTalkOffRva = 0x7CC690, kPathEdgeRva = 0x7E1050;
constexpr unsigned char kCreateCharaHead[] = {0x55, 0x8B, 0xEC, 0x81, 0xEC, 0xD0, 0x00, 0x00, 0x00};
constexpr unsigned char kReleaseCharaHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x30, 0xA1, 0x84, 0x5A, 0x6E, 0x02};
constexpr unsigned char kTalkOnHead[] = {0x55, 0x8B, 0xEC, 0x81, 0xEC, 0x80, 0x00, 0x00, 0x00};
constexpr unsigned char kTalkOffHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x30, 0xA1, 0x84, 0x5A, 0x6E, 0x02};
constexpr unsigned char kPathEdgeHead[] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x10, 0x8B, 0x45, 0x08};
// a cutscene's data in or out: thiscall on [[0x27fce04]+8] (name, pack or
// null, 0, load), ret 0x10. Resource.load/unloadCutSceneScheduleData pass no
// pack, the PackData pair a 3 letter one ("a00"), and so do the map's
// event-load boxes. A cutscene only plays if its schedule's in, and a callback
// often loads the next one's for a trigger further on: ac_hiku_011's loads
// ac_hiku_013's for the lift button, and no station's load script ever does.
// Without it the button started a cutscene that never came, a softlock (Ch 9,
// Nov 19)
constexpr DWORD kCutsceneRva = 0x47E80, kCutsceneHolderRva = 0x23FCE04;
// the same loader's charas: a set in (name, 0) and out (name), one chara in
// and out the same way. Scripts and the event-load boxes preload these for
// what's coming too. Only traced for now, with Debug on, to see whether a
// load ever misses one (Eden's crowds, Nautilus)
constexpr DWORD kCharaSetInRva = 0x471E0, kCharaSetOutRva = 0x48070, kCharaInRva = 0x475F0, kCharaOutRva = 0x47FC0;
constexpr unsigned char kLoaderHead[] = {0x55, 0x8B, 0xEC, 0x81, 0xEC, 0x14, 0x02, 0x00, 0x00};
constexpr unsigned char kCutsceneHead[] = {0x55, 0x8B, 0xEC, 0x81, 0xEC, 0x28, 0x02, 0x00, 0x00};
// who's calling. The engine toggles areas on its own too: around fights
// (0x814B90), refreshing enemy groups (0x5AEBF0), and from the area boxes the
// map data puts where one stretch of a level hands over to the next
// (0x5C88D0, four groups a box, all on or all off, plus its helpers up to
// 0x5C9AA5 that preload cutscenes and chara sets). The natives count, the
// script calling, plus the two spots a cutscene's own track plays a bg anim
// from, and the area boxes: those turn on the next stretch's gr_fa group,
// which is where its save station's terminal lives. Left out, a load past the
// box that started at a station before it never got that group, and the
// terminal ahead stood there invisible, solid and dead (Vile Peaks, Nov 16).
// The fights and enemy refreshes keep their own tally, and forcing one of
// those behind the engine's back left a terminal off for good (Nov 15)
constexpr DWORD kNativesFrom = 0x7BA4E0, kNativesTo = 0x7EBA00;
constexpr DWORD kAreaBoxFrom = 0x1C88D0, kAreaBoxTo = 0x1C9AA5;
constexpr DWORD kCutsceneBgAnimA = 0x71EF14, kCutsceneBgAnimB = 0x71F3AF;
// 99 save slots, and anything smaller than this isn't a whole save
constexpr int kSlots = 99, kSaveMinBytes = 0x10000;
// the zone you're in, as the party database keeps it for the save (+0x1D68)
constexpr DWORD kZoneNumber = 0x1D68;
// the spawn sets the leader down kEaseInFrom behind the start marker, along
// its third row (the same in every zone tried), then the field controller
// eases her in to kEaseInTo off it, walking up to the terminal on a station's
// own load. On ours that walked her 1.75 m on along her heading (Ch 7 and
// Ch 3, Nov 23 and 25). So the marker goes kEaseInTo ahead of the spot along
// the line she walked in on: she starts 1.81 m back and the ease-in ends on it
constexpr float kEaseInTo = 2.25f, kEaseInFrom = 4.0625f, kEaseInRun = kEaseInFrom - kEaseInTo;
// and the marker this far under her: every load of ours came up 1.45 over the
// spot, and a station's own marker sits that far under the floor it spawns
// you on (Ch 9's at 72.67, the deck at 74.12). Answering with the floor
// itself dropped her 1.45 m onto whatever was solid in that first second,
// which is how she went through the Ch 9 slab and off the Ch 10 rail. 5 cm
// short of it so she's set down just over where she stood, not into it
constexpr float kSpawnRise = 1.40f;
constexpr unsigned char kTextCtorHead[] = {0x55, 0x8B, 0xEC, 0x51, 0x89, 0x4D, 0xFC, 0x8B, 0x45, 0xFC, 0xC7, 0x00};
constexpr unsigned char kTextDtorHead[] = {0x55, 0x8B, 0xEC, 0x51, 0x89, 0x4D, 0xFC, 0x8B, 0x45, 0xFC, 0xC7, 0x00};

// the zone state machine's enum. Triggers are the table's 0-59, states its
// 60-137, held internally as the table index minus 60
constexpr int kTrgSaveReq = 41;
constexpr int kStateFieldIdle = 3, kStateFieldPause = 23, kStateFieldPauseWait = 27, kStateFieldPauseEnd = 31,
              kStateSave = 60;

// every state with EVENT in its name (the table at 0x26A1728): cutscenes,
// scenes you walk through, cutscenes inside a battle, and their pauses and
// skips. A plain fight (5-8, 24) isn't one
bool InScene(int state) {
    return (state >= 13 && state <= 18) || state == 21 || state == 22 || state == 25 || state == 26 ||
           state == 29 || state == 30 || (state >= 33 && state <= 37) || (state >= 42 && state <= 45) ||
           state == 47 || state == 49 || (state >= 51 && state <= 59) || (state >= 64 && state <= 73);
}

// a pad after its update is 16-bit button masks: held, last frame's held,
// pressed (held & ~last), released, then the two pressed-or-repeating masks
// the menus read. Keyboard and mouse are folded into the same bits before
// this, through XIII's key bindings, so one bit covers every device. Bit 4 is
// Y on an Xbox pad (A 5, X 6, B 7, Back 14, Start 15) and Triangle on a PS3 one
constexpr DWORD kPadHeld = 0x24, kPadPressed = 0x2C, kPadRepeat = 0x34, kPadRepeatToo = 0x38;

using PadUpdateFn = void(__fastcall*)(void*, void*, void*);
using GetStateFn = int(__fastcall*)(void*, void*);
using ZoneFireFn = void(__fastcall*)(void*, void*, int);
using GetSavePointFn = void(__cdecl*)(void*);
using FindSavePointRowFn = void*(__cdecl*)();
using ResolveActorFn = unsigned char*(__fastcall*)(void*, void*, int);
using LockFn = void(__fastcall*)(void*, void*, void*);
using UnlockFn = void(__fastcall*)(void*, void*);
using PlayerHandleFn = DWORD*(__cdecl*)(DWORD*);
using GroundRayFn = int(__fastcall*)(void*, void*, const float*, const float*, float*, int);
using SetMatrixFn = int(__fastcall*)(void*, void*, const float*, int, int);
using SaveWriteFn = int(__fastcall*)(unsigned char*, void*, int, int, const void*, int);
using SaveReadFn = int(__fastcall*)(unsigned char*, void*, int, void*, int);
using MarkerLookupFn = int(__fastcall*)(void*, void*, int, float*, float*, float*);
using AreaEnterFn = void(__fastcall*)(void*, void*, void*, void*);
using RestartSaveFn = int(__cdecl*)(void*);
using StartMarkerFn = int(__fastcall*)(void*, void*);
using TextThisFn = void(__fastcall*)(void*, void*);
using PauseModeFn = void(__fastcall*)(unsigned char*, void*, int);
using TextSetIdFn = void(__fastcall*)(unsigned char*, void*, const char*, int);
using StoryFn = int(__cdecl*)();

PadUpdateFn g_padUpdate = nullptr;
SaveWriteFn g_saveWrite = nullptr;
SaveReadFn g_saveRead = nullptr;
MarkerLookupFn g_markerLookup = nullptr;
AreaEnterFn g_areaEnter = nullptr;
RestartSaveFn g_restartSave = nullptr;
StartMarkerFn g_startMarker = nullptr;
void** g_fieldStart = nullptr;
TextThisFn g_textCtor = nullptr, g_textDtor = nullptr;
GetStateFn g_getState = nullptr;
ZoneFireFn g_zoneFire = nullptr;
GetSavePointFn g_getSavePoint = nullptr;
FindSavePointRowFn g_findSavePointRow = nullptr;
ResolveActorFn g_resolveActor = nullptr;
SetMatrixFn g_setMatrix = nullptr;
LockFn g_lock = nullptr;
UnlockFn g_unlock = nullptr;
unsigned char** g_actorRegistry = nullptr;
PlayerHandleFn g_playerHandle = nullptr;
PauseModeFn g_pauseMode = nullptr;
TextSetIdFn g_textSetId = nullptr;
void** g_zoneSlot = nullptr;
StoryFn g_story = nullptr;
unsigned char** g_party = nullptr;
unsigned char** g_menu = nullptr;

std::atomic<bool> g_enabled{true};
int g_button = 4;
bool g_prompt = true;

struct SavePoint {
    unsigned char bytes[16];
    bool operator==(const SavePoint& o) const { return std::memcmp(bytes, o.bytes, 16) == 0; }
};

// alignas: the engine reads these as vec4s
struct alignas(16) Vec4 {
    float v[4];
};

// a world matrix as the engine keeps one: rows, translation in the last
struct alignas(16) Matrix {
    float m[16];
};

// what the per-frame side keeps between frames. Only the game thread touches
// it, except what the save writer and reader hand over, which may run on
// the game's save thread
enum class Phase { Idle, Resuming, Requested, Saving, Settling };
Phase g_phase = Phase::Idle;
// wall clock, not frames: a frame budget is a second at 180 fps and four at 45
ULONGLONG g_phaseStart = 0;
Matrix g_savedMatrix{};
Vec4 g_savedTrail[6]{};
int g_savedTrailCount = 0;
// the ground she walked in on, oldest crumb first and the spot last, if it's
// a straight walk in. Enough for 2.5 m of crumbs 25 cm apart
constexpr int kWalkMax = 16;
struct Walk {
    int count = 0;
    Vec4 at[kWalkMax]{};
};
Walk g_savedWalk;
// where a load of ours set her down, to start the next path from: she stood
// there, and the ease-in walks her the rest. A save straight after a load has
// nothing else behind it
std::atomic<bool> g_seedPath{false};
Vec4 g_seedFrom{};
SavePoint g_savedPoint{};
int g_lastState = -1;
void* g_leadPad = nullptr;
bool g_pressed = false;
int g_swallow = 0;
int g_learnLines = 0;
bool g_decorated = false;
bool g_relabel = false;
int g_relabelled = 0;
bool g_pressStart = false;
bool g_heldLastFrame = false;
bool g_eatUntilUp = false;
int g_pauseLogLines = 0;
char g_saveLabel[32]{};
char g_barLabel[256]{};

struct SlotHash {
    int slot = -1;
    unsigned long long hash = 0;
    ULONGLONG at = 0;
};

CRITICAL_SECTION g_fileLock;
SlotHash g_written;             // the slot our save wrote, and what it wrote
SlotHash g_loaded;              // the slot last read outside the field: the one being loaded
std::atomic<int> g_stateForFiles{-1};
std::atomic<bool> g_ourSave{false};
// a slot whose spot a station save just made stale, for the log on the game thread
std::atomic<int> g_staleSlot{-1};
// the zone state a station save was written in, for the log
std::atomic<int> g_stationSaveState{-1};

// the load side: wait for the field to settle, warp, then watch the landing
// a load of ours on its way into the field: the save's matrix, the marker it
// answers for, and until when. Then a look at where it actually landed
struct Spawn {
    bool armed = false;
    int marker = -1;
    ULONGLONG until = 0;
    Matrix matrix{};
    int slot = -1;
    int answered = 0;
    Walk walk;
};
Spawn g_spawn;
// the ease-in being held to its walk, from her first real frame till it's on
// the spot. Written only before it's on and read on the game thread after
std::atomic<bool> g_holding{false};
Walk g_holdWalk;
ULONGLONG g_holdUntil = 0;
int g_holdFrames = 0, g_holdAtSpot = 0;
// how far the controller wanted her off the held height, on the way and once
// on the spot: a pin that's never pulling against anything isn't doing work
float g_holdPullWay = 0, g_holdPullSpot = 0;
ULONGLONG g_landCheckAt = 0, g_landGiveUp = 0, g_landSeenAt = 0;
Vec4 g_landTarget{}, g_stationPos{};
// the save's trail, newest first: where a later load of it starts if the spot
// itself dropped her
Vec4 g_fallback[6]{};
int g_fallbackCount = 0;
// the spot dropped her: whatever state she's in now, it isn't one to write
// over a good save with
bool g_lostLanding = false;

void* Zone() { return *g_zoneSlot; }

void* StateMachine() {
    auto* zone = static_cast<unsigned char*>(Zone());
    return zone ? *reinterpret_cast<void**>(zone + 0x14) : nullptr;
}

int State() {
    void* sm = StateMachine();
    return sm ? g_getState(sm, nullptr) : -1;
}

DWORD PlayerHandle() {
    DWORD handle = 0xFFFFFFFF;
    g_playerHandle(&handle);
    return handle;
}

// the actor's live matrix, copied out under the registry's lock
bool ReadMatrix(DWORD handle, Matrix& out) {
    if (handle == 0xFFFFFFFF) return false;
    out = {};
    unsigned char* registry = *g_actorRegistry;
    if (!registry) return false;
    g_lock(registry + 0x6C0, nullptr, nullptr);
    unsigned char* actor = g_resolveActor(registry, nullptr, static_cast<int>(handle));
    if (actor) std::memcpy(out.m, actor + kActorMatrix, sizeof out.m);
    g_unlock(registry + 0x6C0, nullptr);
    if (!actor) return false;
    for (float f : out.m)
        if (!std::isfinite(f)) return false;
    return true;
}

size_t Readable(const unsigned char* at, unsigned char* out, size_t want) {
    size_t got = 0;
    while (got < want) {
        MEMORY_BASIC_INFORMATION info{};
        if (!VirtualQuery(at + got, &info, sizeof info) || info.State != MEM_COMMIT ||
            (info.Protect & (PAGE_NOACCESS | PAGE_GUARD)) ||
            !(info.Protect & (PAGE_READONLY | PAGE_READWRITE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE)))
            break;
        const size_t room = static_cast<const unsigned char*>(info.BaseAddress) + info.RegionSize - (at + got);
        const size_t take = std::min(room, want - got);
        std::memcpy(out + got, at + got, take);
        got += take;
    }
    return got;
}

// what the game itself knows about her footing, in the object at actor+0x10:
// bit 22 of +0x380 is on while she stands on the level's own floor and off on
// lifts, platforms and in the air, and +0x398 is the last spot of that floor
// she stood on, frozen the moment she steps onto anything else. Read off two
// labelled test runs (floor, a lift at the top, hop platforms, mid-jump),
// where it matched every pause. The code that writes them is reached through
// a table, so nothing here is byte-checked: anything unreadable or odd reads
// as not on solid ground, and Save just stays off
constexpr DWORD kActorFooting = 0x10, kFootingFlags = 0x380, kFootingGround = 0x398;
constexpr DWORD kOnLevelFloor = 1u << 22;

struct Footing {
    bool solid = false;
    Vec4 ground{};
};

bool ReadFooting(DWORD handle, Footing& out) {
    out = {};
    if (handle == 0xFFFFFFFF) return false;
    unsigned char* registry = *g_actorRegistry;
    if (!registry) return false;
    unsigned char block[0x28]{};
    size_t got = 0;
    g_lock(registry + 0x6C0, nullptr, nullptr);
    unsigned char* actor = g_resolveActor(registry, nullptr, static_cast<int>(handle));
    unsigned char* holder = nullptr;
    if (actor && Readable(actor + kActorFooting, reinterpret_cast<unsigned char*>(&holder), sizeof holder) == sizeof holder &&
        holder)
        got = Readable(holder + kFootingFlags, block, sizeof block);
    g_unlock(registry + 0x6C0, nullptr);
    if (got < sizeof block) return false;
    DWORD flags = 0;
    std::memcpy(&flags, block, sizeof flags);
    std::memcpy(out.ground.v, block + (kFootingGround - kFootingFlags), 3 * sizeof(float));
    out.ground.v[3] = 1.0f;
    for (int i = 0; i < 3; ++i)
        if (!std::isfinite(out.ground.v[i])) return false;
    out.solid = (flags & kOnLevelFloor) != 0;
    return true;
}

bool Position(DWORD handle, Vec4& out) {
    Matrix matrix{};
    if (!ReadMatrix(handle, matrix)) return false;
    std::memcpy(out.v, matrix.m + 12, sizeof out.v);
    return true;
}

// right after a load the player handle points at a stand-in, which sits at
// 0 1.5 -10 whatever the field
bool StandIn(const Vec4& at) {
    return std::fabs(at.v[0]) < 0.01f && std::fabs(at.v[1] - 1.5f) < 0.01f && std::fabs(at.v[2] + 10.0f) < 0.01f;
}

Vec4 Translation(const float* matrix) {
    Vec4 out{};
    std::memcpy(out.v, matrix + 12, sizeof out.v);
    return out;
}

float Distance(const Vec4& a, const Vec4& b) {
    const float dx = a.v[0] - b.v[0], dy = a.v[1] - b.v[1], dz = a.v[2] - b.v[2];
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

// what a save carries that the game's scripts rebuild a field from. A station
// only ever saves a state the designers put a station in, so that's the one
// Save Anywhere may save too. What moves the story on is a scene: the chapter
// 9 softlock was a fight, a cutscene, then a save on the lift before the next
// station. So it's no scene since the last station, and the story number and
// the UI and menu locks as they were there. The event flags, script variables
// and zone flags are kept for the log only: a chest or a jump up a ledge
// flips event flags too, and holding Save back for those made it a station
// mod with extra steps
struct Progress {
    int story = -1;
    DWORD ui = 0;
    DWORD menu[8]{};
    DWORD zone[kZoneFlagWords]{};
    unsigned char work[kWorkBytes]{};
    unsigned char flags[kEventFlagBytes]{};
};

Progress g_sanctioned;
Progress g_sceneStart;
// the first scene state seen since the last station, or -1
int g_sceneSince = -1;
// what makes a state one a station could save, so Save Anywhere may too:
// standing at a station in free roam (walked into its area, or the field
// started you there), a station save, or loading a save of ours that was made
// under this same guard. Not a slot read on its own: the load screen reads
// every slot, and the saves' bytes don't carry the station in the clear
enum SanctionKind { kNone, kLoad, kStation, kOurs };
std::atomic<int> g_sanction{kNone};
std::atomic<bool> g_areaEntered{false};
// a restart point the scripts just set, and what the game held as they did
std::atomic<bool> g_restartPoint{false};
std::atomic<bool> g_startWanted{false};
Vec4 g_startPos{};
ULONGLONG g_idleSince = 0;

void ReadProgress(Progress& out) {
    out.story = g_story();
    unsigned char* party = *g_party;
    if (party) {
        g_lock(party + kPartyLock, nullptr, nullptr);
        std::memcpy(out.work, party + kWork, sizeof out.work);
        std::memcpy(out.flags, party + kEventFlags, sizeof out.flags);
        g_unlock(party + kPartyLock, nullptr);
    }
    auto* field = static_cast<unsigned char*>(*g_fieldStart);
    out.ui = field ? *reinterpret_cast<DWORD*>(field + 0x1C) : 0;
    if (unsigned char* menu = *g_menu) std::memcpy(out.menu, menu + 0x184, sizeof out.menu);
    auto* zone = static_cast<unsigned char*>(Zone());
    auto* holder = zone ? *reinterpret_cast<unsigned char**>(zone + 0x10) : nullptr;
    if (holder) std::memcpy(out.zone, holder + 0xD8, sizeof out.zone);
    else std::memset(out.zone, 0xFF, sizeof out.zone);
}

// the first way a state differs from the sanctioned one, or null
const char* Moved(const Progress& now) {
    if (now.story != g_sanctioned.story) return "the story moved on";
    if (now.ui != g_sanctioned.ui) return "a sequence has the field UI off";
    if (std::memcmp(now.menu, g_sanctioned.menu, sizeof now.menu) != 0) return "a sequence has the menus locked";
    return nullptr;
}

// whether anything that persists moved between a restart point and free roam.
// UI and menu locks are left out: a script sets the point mid-sequence with
// both on, and the sanctioned snapshot gets them from free roam anyway
const char* Drifted(const Progress& now, const Progress& then) {
    if (now.story != then.story) return "the story moved on";
    if (std::memcmp(now.flags, then.flags, sizeof now.flags) != 0) return "an event flag changed";
    if (std::memcmp(now.work, then.work, sizeof now.work) != 0) return "a script variable changed";
    if (std::memcmp(now.zone, then.zone, sizeof now.zone) != 0) return "a zone flag changed";
    return nullptr;
}

// how a state differs from the sanctioned one, for the log: the event flags
// that moved (the first few by number) and the two sequence switches
void LogProgress(const char* when, const Progress& now) {
    char moved[96]{};
    int count = 0, at = 0;
    for (DWORD i = 0; i < kEventFlagBytes * 8; ++i) {
        if (((now.flags[i / 8] ^ g_sanctioned.flags[i / 8]) >> (i % 8) & 1) == 0) continue;
        if (++count <= 6) at += std::snprintf(moved + at, sizeof moved - at, " %lu", i);
    }
    int work = 0;
    for (DWORD i = 0; i < kWorkBytes / 4; ++i)
        work += std::memcmp(now.work + i * 4, g_sanctioned.work + i * 4, 4) != 0;
    Log("%s: story %d (last load or station %d), %d event flags moved%s, %d script variables, zone %08lx %08lx "
        "(was %08lx %08lx), ui %#lx, menu %08lx %08lx", when, now.story, g_sanctioned.story, count, moved, work,
        now.zone[0], now.zone[1], g_sanctioned.zone[0], g_sanctioned.zone[1], now.ui, now.menu[0], now.menu[1]);
}

// The world journal. A save keeps the party, so the story, flags and items
// all come back, but none of what the scripts did to the field: which areas
// are on (enemy groups, NPCs, walls), which boxes are live, where each door
// or bridge animation stopped, which layout pieces show. A load rebuilds
// those from the station's own load script, which only knows the station's
// side of the story. So every one of those calls since the field started is
// kept here, the latest per target, and goes in the .where with the spot.
// Loading puts back whatever the rebuild didn't, through the same calls
enum class Op : unsigned char { AreaOn, AreaFade, AreaOff, Object, BgAnim, Layout, Encount, Chara, Talk, Edge, Cutscene };
constexpr const char* kOpNames[] = {"area on", "area fade in", "area off", "box", "bg anim", "layout",
                                    "encounters", "chara", "talk", "path edge", "cutscene"};

struct Entry {
    Op op = Op::AreaOn;
    char a[16]{};  // the target: area, box, bg object, layout piece, chara
    char b[16]{};  // a bg anim's motion, a bg model's second name, a cutscene's pack
    int i = 0;     // flags, live, shown, on
    int j = 0;     // a bg anim's mode, 1 when a layout's set is the fixed one or an area box toggled an area
    unsigned seq = 0;  // which note it was, this session only
};

int Kind(Op op) {
    switch (op) {
    case Op::AreaOn: case Op::AreaFade: case Op::AreaOff: return 0;
    case Op::Object: return 1;
    case Op::BgAnim: return 2;
    case Op::Layout: return 3;
    case Op::Encount: return 4;
    case Op::Cutscene: return 5;
    default: return 6 + static_cast<int>(op);
    }
}

bool SameTarget(const Entry& x, const Entry& y) {
    if (Kind(x.op) != Kind(y.op)) return false;
    if (x.op == Op::Encount) return true;
    if (std::memcmp(x.a, y.a, 16) != 0) return false;
    if (x.op == Op::Cutscene) return std::memcmp(x.b, y.b, 16) == 0;
    return x.op != Op::Layout || (x.j == y.j && std::memcmp(x.b, y.b, 16) == 0);
}

// an area's j only says who toggled it, a script or an area box
bool FromBox(const Entry& e) { return Kind(e.op) == 0 && e.j == 1; }

bool SameEffect(const Entry& x, const Entry& y) {
    return x.op == y.op && SameTarget(x, y) && x.i == y.i && (Kind(x.op) == 0 || x.j == y.j) &&
           std::memcmp(x.b, y.b, 16) == 0;
}

// what can be put back: the rest is only kept so the log can say it happened
bool Replayable(Op op) { return Kind(op) <= 5; }

// the game thread writes it, the save writer and reader may not run there
CRITICAL_SECTION g_worldLock;
std::vector<Entry> g_world;
int g_worldZone = -1;
// a load of ours: what the save's journal says, put back once the rebuild's done
std::vector<Entry> g_pending;
std::string g_pendingMapSet;
int g_pendingZone = -1;
bool g_mapSetSwapLeft = false;
bool g_pendingWanted = false;
// the load starts at the save's spot, and where that is
bool g_spotUsed = false;
Vec4 g_savedSpot{};
// how far from the spot the area boxes' groups still hold: a load moved onto
// the trail stays this close, the station rarely does
constexpr float kBoxReach = 40.0f;
// with Debug on, a station save keeps its journal too, in debug\, so loading
// it can say how much of that world the game's own rebuild brought back
int g_loadedSlot = -1;
bool g_worldCheckWanted = false;
// free roam since the load, which a pause only holds: checking right after
// loading paused the check too, and Save stayed off a good while (Nov 16)
ULONGLONG g_roamMs = 0, g_roamAt = 0;
// the note count once it was her and not the stand-in after the load: an area
// box after that is one she walked through, the stand-in's box isn't hers
unsigned g_realFrom = ~0u;
// Debug only: her height for the first few seconds she's real after a load
ULONGLONG g_heightTraceUntil = 0, g_heightTraceAt = 0;
// a script or a cutscene has posed a bg anim since this field's journal
// started, rebuild included. Palumpolum's plaza reads layer 16 nearly
// everywhere and has none (Nov 23); the Ch 9 slab and the Ch 10 lift are
// bg anims, and every drop was in a field with them
std::atomic<bool> g_fieldAnimates{false};
// one of those was a cutscene's, which is how the Ch 9 lift and its slab got
// where they were. The ray keeps the say there, nobody's read the controller
// on that slab yet
std::atomic<bool> g_cutscenePosed{false};
// the leader the setMatrix watch is after, and till when
std::atomic<void*> g_watchActor{nullptr};
std::atomic<ULONGLONG> g_watchUntil{0};
std::atomic<int> g_watchLines{0};
// the controller as it was when the trace started, to diff at its end
unsigned char g_ctrlStart[kFieldPlayerSpan];
size_t g_ctrlGot = 0;
bool g_ctrlDiffed = true;
// and the map set then: a swap box she walks over after that is hers too
std::string g_realSet;
// the journal as a save of ours was asked for, for its .where
std::vector<Entry> g_savedWorld;
Progress g_atSave;
int g_savedZone = -1;
std::string g_savedMapSet = "-";
int g_worldLines = 0;
// when the journal last saw a call, so a load can wait out the rebuild
std::atomic<ULONGLONG> g_lastNoteAt{0};
// every call the journal kept, ever, so a scene can tell whether it touched the field
std::atomic<unsigned> g_notes{0};
unsigned g_notesAtScene = 0;
// the calls a load can't put back yet (a chara made or let go, who you can
// talk to, a path edge), counted the same way, and where the count stood at
// the last station
std::atomic<unsigned> g_lockedNotes{0};
unsigned g_lockedAtSanction = 0;
// charas made less charas let go since the last station, by name. One made
// and let go inside a scene (an event enemy) leaves nothing a load misses
std::map<std::string, int> g_charaNet;
// climbing on a chocobo is a scene that lets the party go and loads the bird.
// The game's own save keeps you riding and gives them back when you get off
// (Gran Pulse, Nov 27: saves on the bird loaded on it, and Hope and Fang came
// back at the next dismount). So those let-gos don't count, and neither does
// anything else they do till the next station
constexpr char kChocobo[] = "f112_pc";
std::vector<std::string> g_sceneLetGo;
std::set<std::string> g_rideParty;

bool CharasMoved() {
    EnterCriticalSection(&g_worldLock);
    bool moved = false;
    for (const auto& [name, net] : g_charaNet) moved = moved || net != 0;
    LeaveCriticalSection(&g_worldLock);
    return moved;
}

int ZoneNumber() {
    unsigned char* party = *g_party;
    return party ? *reinterpret_cast<int*>(party + kZoneNumber) : -1;
}

std::string Name(const char* raw) {
    std::string out(raw, strnlen(raw, 16));
    return out.empty() ? "-" : out;
}

void Describe(const Entry& e, char* out, size_t size) {
    switch (e.op) {
    case Op::BgAnim:
        std::snprintf(out, size, "%s %s %s (%d, mode %d)", kOpNames[static_cast<int>(e.op)], Name(e.a).c_str(),
                      Name(e.b).c_str(), e.i, e.j);
        break;
    case Op::Layout:
        std::snprintf(out, size, "%s %s%s%s %s", kOpNames[static_cast<int>(e.op)], Name(e.a).c_str(), e.j ? "" : " / ",
                      e.j ? "" : Name(e.b).c_str(), e.i ? "shown" : "hidden");
        break;
    case Op::Encount:
        std::snprintf(out, size, "%s %s", kOpNames[static_cast<int>(e.op)], e.i ? "on" : "off");
        break;
    case Op::Cutscene:
        std::snprintf(out, size, "%s %s%s%s %s", kOpNames[static_cast<int>(e.op)], Name(e.a).c_str(), e.b[0] ? " pack " : "",
                      e.b[0] ? Name(e.b).c_str() : "", e.i ? "in" : "out");
        break;
    default:
        std::snprintf(out, size, "%s %s (%d)%s", kOpNames[static_cast<int>(e.op)], Name(e.a).c_str(), e.i,
                      FromBox(e) ? " by an area box" : "");
    }
}

// a field that's gone takes its journal with it. A new zone is caught here,
// on its first call, since the party's zone number moves before it loads
void ForgetWorld(const char* why) {
    EnterCriticalSection(&g_worldLock);
    const size_t had = g_world.size();
    g_world.clear();
    g_fieldAnimates = false;
    g_cutscenePosed = false;
    g_holding = false;
    g_worldZone = ZoneNumber();
    // the charas and talk changes of a field that's gone are gone with it.
    // What the new field's own setup does from here still counts
    g_charaNet.clear();
    g_sceneLetGo.clear();
    g_rideParty.clear();
    g_lockedAtSanction = g_lockedNotes.load();
    LeaveCriticalSection(&g_worldLock);
    if (had) Trace("world journal cleared (%s), had %u", why, static_cast<unsigned>(had));
}

void Note(Op op, const char* a, const char* b, int i, int j) {
    Entry e;
    e.op = op;
    if (a) std::memcpy(e.a, a, strnlen(a, 16));
    if (b) std::memcpy(e.b, b, strnlen(b, 16));
    e.i = i;
    e.j = j;
    e.seq = ++g_notes;
    const int zone = ZoneNumber();
    if (zone != g_worldZone) ForgetWorld("a new zone");
    EnterCriticalSection(&g_worldLock);
    // latest last, so a replay runs them in the order they last changed
    g_world.erase(std::remove_if(g_world.begin(), g_world.end(), [&](const Entry& x) { return SameTarget(x, e); }),
                  g_world.end());
    g_world.push_back(e);
    LeaveCriticalSection(&g_worldLock);
    g_lastNoteAt = GetTickCount64();
    if (!Replayable(op) && op != Op::Chara) ++g_lockedNotes;
    // a station load alone is a few dozen of these
    if (g_debug && g_worldLines < 4000) {
        ++g_worldLines;
        char text[96];
        Describe(e, text, sizeof text);
        Log("  world: %s", text);
    }
}

using AreaFn = int(__fastcall*)(void*, void*, const char*, int);
using BgAnimFn = int(__fastcall*)(void*, void*, const char*, int, const char*, int);
using LayoutFn = int(__fastcall*)(void*, void*, const char*, const void*, int);
using EncountFn = int(__cdecl*)(int);
using MapSetFn = int(__fastcall*)(void*, void*, const char*);
using RetryFn = int(__fastcall*)(void*, void*);
using CutsceneFn = int(__fastcall*)(void*, void*, const char*, const char*, int, int);
using LoadFn = int(__fastcall*)(void*, void*, const char*, int);
using UnloadFn = int(__fastcall*)(void*, void*, const char*);
using NativeFn = int(__cdecl*)(void*);

AreaFn g_areaOn = nullptr, g_areaFade = nullptr, g_areaOff = nullptr, g_object = nullptr;
BgAnimFn g_bgAnim = nullptr;
LayoutFn g_layout = nullptr;
EncountFn g_encount = nullptr;
MapSetFn g_mapSet = nullptr;
RetryFn g_retry = nullptr;
CutsceneFn g_cutscene = nullptr;
LoadFn g_charaSetIn = nullptr, g_charaIn = nullptr;
UnloadFn g_charaSetOut = nullptr, g_charaOut = nullptr;
NativeFn g_createChara = nullptr, g_releaseChara = nullptr, g_talkOn = nullptr, g_talkOff = nullptr,
         g_pathEdge = nullptr;

// an empty name is one of a native's unused slots, which the engine skips
bool Named(const char* name) { return name && name[0]; }

DWORD CallerRva(void* returnAddress) {
    return static_cast<DWORD>(reinterpret_cast<std::uintptr_t>(returnAddress) - g_base);
}

bool FromScript(void* returnAddress) {
    const DWORD rva = CallerRva(returnAddress);
    return rva >= kNativesFrom && rva < kNativesTo;
}

bool FromAreaBox(void* returnAddress) {
    const DWORD rva = CallerRva(returnAddress);
    return rva >= kAreaBoxFrom && rva < kAreaBoxTo;
}

// a replay going through the hooked entries, which notes them itself
bool g_replaying = false;

// Debug only: the area calls nobody keeps, so a log shows what the engine did
// on its own and from where
void TraceEngineArea(const char* what, const char* name, int arg, void* from) {
    if (!g_debug || g_replaying || !Named(name) || g_worldLines >= 4000) return;
    ++g_worldLines;
    Log("  engine: %s %s (%d) from 0x%lx", what, Name(name).c_str(), arg, static_cast<unsigned long>(CallerRva(from)));
}

int __fastcall AreaOnHook(void* scene, void* edx, const char* name, int flags) {
    void* from = __builtin_return_address(0);
    const bool box = FromAreaBox(from);
    const bool kept = (FromScript(from) || box) && Named(name);
    const int out = g_areaOn(scene, edx, name, flags);
    if (kept) Note(Op::AreaOn, name, nullptr, flags, box);
    else TraceEngineArea("area on", name, flags, from);
    return out;
}

int __fastcall AreaFadeHook(void* scene, void* edx, const char* name, int arg) {
    const bool kept = FromScript(__builtin_return_address(0)) && Named(name);
    const int out = g_areaFade(scene, edx, name, arg);
    if (kept) Note(Op::AreaFade, name, nullptr, arg, 0);
    return out;
}

int __fastcall AreaOffHook(void* scene, void* edx, const char* name, int arg) {
    void* from = __builtin_return_address(0);
    const bool box = FromAreaBox(from);
    const bool kept = (FromScript(from) || box) && Named(name);
    const int out = g_areaOff(scene, edx, name, arg);
    if (kept) Note(Op::AreaOff, name, nullptr, arg, box);
    else TraceEngineArea("area off", name, arg, from);
    return out;
}

int __fastcall ObjectHook(void* scene, void* edx, const char* name, int live) {
    const bool kept = FromScript(__builtin_return_address(0)) && Named(name);
    const int out = g_object(scene, edx, name, live);
    if (kept) Note(Op::Object, name, nullptr, live, 0);
    return out;
}

int __fastcall BgAnimHook(void* scene, void* edx, const char* object, int arg, const char* motion, int mode) {
    void* from = __builtin_return_address(0);
    const DWORD rva = CallerRva(from);
    const bool kept = (FromScript(from) || rva == kCutsceneBgAnimA || rva == kCutsceneBgAnimB) && Named(object);
    const int out = g_bgAnim(scene, edx, object, arg, motion, mode);
    if (kept) {
        g_fieldAnimates = true;
        if (rva == kCutsceneBgAnimA || rva == kCutsceneBgAnimB) g_cutscenePosed = true;
        Note(Op::BgAnim, object, motion, arg, mode);
    }
    return out;
}

int __fastcall LayoutHook(void* scene, void* edx, const char* name, const void* set, int shown) {
    const bool kept = Named(name);
    const int out = g_layout(scene, edx, name, set, shown);
    const bool fixed = reinterpret_cast<std::uintptr_t>(set) == g_base + kLayoutSetRva;
    if (kept) Note(Op::Layout, name, fixed ? nullptr : static_cast<const char*>(set), shown, fixed);
    return out;
}

int __cdecl EncountHook(int on) {
    const bool kept = FromScript(__builtin_return_address(0));
    const int out = g_encount(on);
    if (kept) Note(Op::Encount, nullptr, nullptr, on, 0);
    return out;
}

// the engine swaps map sets as you walk (0304 to 0304a, 0913b to 1310b),
// and a station keeps being the save point well past where its own set
// ends. Its load script always asks for the set by the station, so a spot
// saved out in another one loaded into sky until a walk across a swap box
// fetched the right one (Nov 15, twice). Whatever the set was when you saved
// is the one that holds your spot, and the zone's areas and boxes don't care
// which set is in: the game swaps under them all the time
int __fastcall MapSetHook(void* zone, void* edx, const char* name) {
    if (g_debug && name) Log("  world: map set %s asked for", Name(name).c_str());
    // only the rebuild's own ask: one you walk across a swap box for before
    // the field's been put back is the game's, and taking that would be the
    // sky all over again
    if (name && g_pendingWanted && g_mapSetSwapLeft && g_pendingMapSet != "-" && g_pendingZone == ZoneNumber()) {
        g_mapSetSwapLeft = false;
        const std::string asked = Name(name);
        if (asked != g_pendingMapSet && g_pendingMapSet.size() < 16) {
            char want[16]{};
            std::memcpy(want, g_pendingMapSet.data(), g_pendingMapSet.size());
            Log("the station's load asked for map set %s, loading %s instead, the one it was saved on",
                asked.c_str(), g_pendingMapSet.c_str());
            return g_mapSet(zone, edx, want);
        }
    }
    return g_mapSet(zone, edx, name);
}

int __fastcall RetryHook(void* self, void* edx) {
    ForgetWorld("retry");
    return g_retry(self, edx);
}

// a script's or an event-load box's loads, and everyone's unloads: a
// cutscene's player drops its own data once it's done, and that's what was in
// at the save
int __fastcall CutsceneHook(void* loader, void* edx, const char* name, const char* pack, int arg, int load) {
    void* from = __builtin_return_address(0);
    const bool kept = Named(name) && (!load || FromScript(from) || FromAreaBox(from));
    char which[16]{};
    if (kept && pack) std::memcpy(which, pack, strnlen(pack, 3));
    const int out = g_cutscene(loader, edx, name, pack, arg, load);
    if (kept) Note(Op::Cutscene, name, which, load, arg);
    return out;
}

void TraceLoad(const char* what, const char* name, void* from) {
    if (!g_debug || !Named(name) || g_worldLines >= 4000) return;
    ++g_worldLines;
    Log("  resource: %s %s from 0x%lx", what, Name(name).c_str(), static_cast<unsigned long>(CallerRva(from)));
}

int __fastcall CharaSetInHook(void* loader, void* edx, const char* name, int arg) {
    TraceLoad("chara set in", name, __builtin_return_address(0));
    return g_charaSetIn(loader, edx, name, arg);
}

int __fastcall CharaSetOutHook(void* loader, void* edx, const char* name) {
    TraceLoad("chara set out", name, __builtin_return_address(0));
    return g_charaSetOut(loader, edx, name);
}

int __fastcall CharaInHook(void* loader, void* edx, const char* name, int arg) {
    TraceLoad("chara in", name, __builtin_return_address(0));
    if (Named(name) && Name(name) == kChocobo) {
        std::string party;
        EnterCriticalSection(&g_worldLock);
        for (const std::string& who : g_sceneLetGo) {
            g_charaNet[who] += 1;
            g_rideParty.insert(who);
            party += " " + who;
        }
        g_sceneLetGo.clear();
        LeaveCriticalSection(&g_worldLock);
        if (!party.empty()) Log("the party stepped off for the chocobo (%s ), the save keeps that", party.c_str() + 1);
    }
    return g_charaIn(loader, edx, name, arg);
}

int __fastcall CharaOutHook(void* loader, void* edx, const char* name) {
    TraceLoad("chara out", name, __builtin_return_address(0));
    return g_charaOut(loader, edx, name);
}

// a native's string arg, if that slot holds a readable one
const char* SlotText(const unsigned char* slot) {
    const char* text = *reinterpret_cast<const char* const*>(slot + 4);
    MEMORY_BASIC_INFORMATION info{};
    if (!text || !VirtualQuery(text, &info, sizeof info) || info.State != MEM_COMMIT ||
        (info.Protect & (PAGE_NOACCESS | PAGE_GUARD)) || !(info.Protect & 0xEE))
        return nullptr;
    for (int k = 0; k < 16 && text[k]; ++k)
        if (text[k] < 0x20 || text[k] > 0x7E) return nullptr;
    return text;
}

void NoteNative(Op op, void* vm, bool on) {
    auto* slots = vm ? *reinterpret_cast<unsigned char**>(static_cast<unsigned char*>(vm) + 0x114) : nullptr;
    const char* texts[4]{};
    int named = 0;
    char raw[160]{};
    int at = 0;
    // which slot is which isn't pinned down for these yet, so the log gets four
    for (int k = 0; slots && k < 4 && at < static_cast<int>(sizeof raw) - 40; ++k) {
        const unsigned char* slot = slots + k * 8;
        const unsigned type = *reinterpret_cast<const unsigned short*>(slot);
        if (const char* text = SlotText(slot)) {
            if (text[0]) texts[named++] = text;
            at += std::snprintf(raw + at, sizeof raw - at, " [%u \"%.16s\"]", type, text);
        } else {
            at += std::snprintf(raw + at, sizeof raw - at, " [%u %#lx]", type,
                                *reinterpret_cast<const unsigned long*>(slot + 4));
        }
    }
    // createChara_l is (spec, name) and releaseChara (name), so the chara a
    // create makes is its second string (ch 3: "p_hope" made "ev_en_ho", and
    // "ev_en_ho" is what got released)
    const char* target = op == Op::Chara && on ? texts[1] : texts[0];
    Note(op, target, nullptr, on, 0);
    if (op == Op::Chara && target) {
        EnterCriticalSection(&g_worldLock);
        const std::string name = Name(target);
        if (!g_rideParty.count(name)) {
            g_charaNet[name] += on ? 1 : -1;
            if (!on) g_sceneLetGo.push_back(name);
        }
        LeaveCriticalSection(&g_worldLock);
    }
    Trace("  world: %s %s, slots%s", kOpNames[static_cast<int>(op)], on ? "set" : "cleared", raw);
}

int __cdecl CreateCharaHook(void* vm) {
    NoteNative(Op::Chara, vm, true);
    return g_createChara(vm);
}

int __cdecl ReleaseCharaHook(void* vm) {
    NoteNative(Op::Chara, vm, false);
    return g_releaseChara(vm);
}

int __cdecl TalkOnHook(void* vm) {
    NoteNative(Op::Talk, vm, true);
    return g_talkOn(vm);
}

int __cdecl TalkOffHook(void* vm) {
    NoteNative(Op::Talk, vm, false);
    return g_talkOff(vm);
}

int __cdecl PathEdgeHook(void* vm) {
    NoteNative(Op::Edge, vm, true);
    return g_pathEdge(vm);
}

std::string MapSetName() {
    auto* holder = *reinterpret_cast<unsigned char**>(g_base + kMapSetHolderRva);
    return holder ? Name(reinterpret_cast<const char*>(holder + kMapSetName)) : "-";
}

std::vector<Entry> WorldNow() {
    EnterCriticalSection(&g_worldLock);
    std::vector<Entry> out = g_world;
    LeaveCriticalSection(&g_worldLock);
    return out;
}

// a bg anim that was playing through once comes back at its last frame: the
// door that was opening is open. Looping ones loop, the rest replay as made
int ReplayMode(int mode) { return mode == 1 ? 5 : mode; }

// false when the game turned it down (a bg anim whose object isn't loaded
// yet), which then stays out of the journal too
bool Replay(const Entry& e) {
    void* scene = *reinterpret_cast<void**>(g_base + kSceneRva);
    if (!scene && e.op != Op::Encount) return false;
    // the hooks only keep what a script asked for, so these go in by hand
    char a[16], b[16];
    std::memcpy(a, e.a, 16);
    std::memcpy(b, e.b, 16);
    g_replaying = true;
    bool took = true;
    switch (e.op) {
    case Op::AreaOn:
        reinterpret_cast<AreaFn>(g_base + kAreaOnRva)(scene, nullptr, a, e.i);
        break;
    case Op::AreaFade:
        reinterpret_cast<AreaFn>(g_base + kAreaFadeRva)(scene, nullptr, a, e.i);
        break;
    case Op::AreaOff:
        reinterpret_cast<AreaFn>(g_base + kAreaOffRva)(scene, nullptr, a, e.i);
        break;
    case Op::Object:
        reinterpret_cast<AreaFn>(g_base + kObjectRva)(scene, nullptr, a, e.i);
        break;
    case Op::BgAnim: {
        took = reinterpret_cast<BgAnimFn>(g_base + kBgAnimRva)(scene, nullptr, a, e.i, b, ReplayMode(e.j)) & 0xFF;
        if (g_debug) Log("  bg anim %s %s back at mode %d: %s", Name(a).c_str(), Name(b).c_str(), ReplayMode(e.j),
                         took ? "took" : "refused");
        break;
    }
    case Op::Layout:
        reinterpret_cast<LayoutFn>(g_base + kLayoutRva)(scene, nullptr, a,
                                                        e.j ? reinterpret_cast<const void*>(g_base + kLayoutSetRva) : b, e.i);
        break;
    case Op::Encount:
        reinterpret_cast<EncountFn>(g_base + kEncountRva)(e.i);
        break;
    case Op::Cutscene: {
        auto* holder = *reinterpret_cast<unsigned char**>(g_base + kCutsceneHolderRva);
        void* loader = holder ? *reinterpret_cast<void**>(holder + 8) : nullptr;
        if (!loader) {
            g_replaying = false;
            return false;
        }
        reinterpret_cast<CutsceneFn>(g_base + kCutsceneRva)(loader, nullptr, a, b[0] ? b : nullptr, e.j, e.i);
        break;
    }
    default:
        g_replaying = false;
        return false;
    }
    g_replaying = false;
    if (took) Note(e.op, e.a[0] ? a : nullptr, e.b[0] ? b : nullptr, e.i, e.op == Op::BgAnim ? ReplayMode(e.j) : e.j);
    return took;
}

// what the rebuild left different from the journal, so what a replay would
// put back. Only targets the journal knows: whatever else the station's load
// script turned on, the game turned on at a station too. An area box's call
// only goes back where the boxes are the ones it was made under, and not over
// one she's walked through since the load, which is newer than the save
std::vector<Entry> Missing(const std::vector<Entry>& want, const std::vector<Entry>& have, int* other,
                           bool boxes = true, unsigned walked = ~0u) {
    std::vector<Entry> out;
    if (other) *other = 0;
    for (const Entry& w : want) {
        if (!Replayable(w.op) || (FromBox(w) && !boxes)) {
            if (other) ++*other;
            continue;
        }
        // a cutscene's data only ever goes in: the rebuild knows what its own
        // area needs, and one the save had dropped costs nothing loaded
        if (w.op == Op::Cutscene && !w.i) continue;
        auto it = std::find_if(have.begin(), have.end(), [&](const Entry& h) { return SameTarget(h, w); });
        // and one the rebuild already has in stays as it is, whatever arg it
        // went in with. Loading it again over itself took the game down with a
        // pure virtual call (Nautilus, Nov 27: five of them, the load a box
        // gave against the one the rebuild gave)
        if (w.op == Op::Cutscene && it != have.end() && it->i) continue;
        if (FromBox(w) && it != have.end() && FromBox(*it) && it->seq > walked) continue;
        if (it == have.end() || !SameEffect(*it, w)) out.push_back(w);
    }
    return out;
}

void LogMissing(const char* what, const std::vector<Entry>& missing, int other) {
    int counts[6]{};
    for (const Entry& e : missing) ++counts[Kind(e.op)];
    Log("%s: %u differ from the rebuild (areas %d, boxes %d, bg anims %d, layout %d, encounters %d, cutscenes %d), "
        "%d that can't be put back", what, static_cast<unsigned>(missing.size()), counts[0], counts[1], counts[2],
        counts[3], counts[4], counts[5], other);
    int shown = 0;
    for (const Entry& e : missing) {
        if (!g_debug && shown >= 12) break;
        char text[96];
        Describe(e, text, sizeof text);
        Log("  %s", text);
        ++shown;
    }
}

// the leader's track over the last moment in free roam, sampled every 40 ms
struct Sample {
    ULONGLONG at;
    Vec4 pos;
    float axis[2][2];  // rows 0 and 2 of her matrix, flattened: one of them is where she faces
};
Sample g_track[16]{};
int g_trackNext = 0;
// the footing flag as of the last free roam sample. The game clears that bit
// a frame or two into the pause screen, so a live read there always says she
// isn't on the floor. She can't move while paused, so the last sample stands
bool g_idleSolid = false;
// level with the last floor she stood on and close to it, with the bit off.
// After one of the game's hops the bit stayed off on plain ground until she
// was well away (Vile Peaks, Nov 15): the floor spot it keeps froze back at
// the hop while she walked on at the same height. A lift that's taken her up
// or down is metres off that spot, and Steady and Carried still catch
// anything moving. Only the save takes this: the trail a fallback lands on
// stays strictly on floor the game vouched for
bool g_nearFloor = false;
constexpr float kNearFloorAcross = 10.0f, kNearFloorUp = 1.0f;
// rode a lift: going near straight up or down (over 60 degrees) for 2 s and
// 2 m, longer than any jump or climb. At 45 degrees, 1.2 s and 1 m, one of
// the game's hops up onto a junk pile counted (Vile Peaks, Nov 16). Steady
// only sees the last half second, so a slow lift or one that's just stopped
// got past it, and the game may call a lift floor. Cleared once she's stood on
// floor a few steps from where the ride was last seen, so the trail's next
// point is ground off the lift (Ch 9: a save on the lift after a scene, no
// lift on load, a fall to the floor below the beat). Being carried doesn't
// latch it: judged every step it caught plain running round corners and kept
// Save off all over Vile Peaks (Nov 16). The pause still checks it
bool g_rode = false;
Vec4 g_rodeAt{};
// the latch's other way in: a scene that put her somewhere else. It may have
// stood her on something it moved, a lift it brought down, and the game calls
// a parked lift floor (Ch 9, Nov 22: a save on it, no lift on load)
bool g_rodeByScene = false;
Vec4 g_sceneFrom{};
bool g_sceneFromRead = false;
constexpr float kOffRide = 6.0f;
// a scene's spot only has to be stepped off: what it could stand her on is the
// slab the moving-piece check already catches, and 6 m off the Ch 9 jump's
// landing is further than that walkway goes before the button (Nov 23)
constexpr float kOffScene = 1.0f;
// the vertical run that's building: same direction every sample, steeper than 45
float g_runDy = 0;
ULONGLONG g_runMs = 0;
// which of the two rows is her facing, learnt from her own walking: moving
// under her own steam she goes the way she faces. Not judged until it's clear
int g_axisVotes[2]{};

// a lift, a fall or the top of a jump: going up or down faster than sideways,
// or a drop no stairs give. The spot it saves isn't there to stand on when
// the field's rebuilt, the lift's back at its start
bool Steady(float& rise) {
    const Sample* last = nullptr;
    for (const Sample& s : g_track)
        if (s.at && (!last || s.at > last->at)) last = &s;
    rise = 0;
    if (!last) return false;
    float lo = last->pos.v[1], hi = lo, side = 0;
    for (const Sample& s : g_track) {
        if (!s.at || last->at - s.at > 500) continue;
        lo = std::min(lo, s.pos.v[1]);
        hi = std::max(hi, s.pos.v[1]);
        side = std::max(side, std::hypot(s.pos.v[0] - last->pos.v[0], s.pos.v[2] - last->pos.v[2]));
    }
    rise = hi - lo;
    return !((rise > 0.25f && rise > side) || rise > 2.0f);
}

int FacingAxis() {
    const int total = g_axisVotes[0] + g_axisVotes[1];
    if (total < 20) return -1;
    for (int a = 0; a < 2; ++a)
        if (g_axisVotes[a] * 5 >= total * 4) return a;
    return -1;
}

float Along(const Sample& s, int axis, float dx, float dz) {
    const float len = std::max(std::hypot(dx, dz), 1e-6f);
    return std::fabs(s.axis[axis][0] * dx / len + s.axis[axis][1] * dz / len);
}

// sideways (or spinning) platforms: over the last 0.3 s she kept moving in
// directions she wasn't facing, which walking never does. Judged step by
// step against her facing at that step, so a sharp turn doesn't count. More
// than 60 degrees off counts as astray: at 45, running a bend in Vile Peaks
// then pausing read as carried (Nov 16)
bool Carried() {
    const int axis = FacingAxis();
    if (axis < 0) return false;
    const Sample* last = nullptr;
    for (const Sample& s : g_track)
        if (s.at && (!last || s.at > last->at)) last = &s;
    if (!last) return false;
    int moving = 0, astray = 0;
    float travelled = 0;
    for (int i = 0; i < 16; ++i) {
        const Sample& s = g_track[i];
        const Sample& before = g_track[(i + 15) % 16];
        if (!s.at || !before.at || s.at <= before.at || last->at - before.at > 300 || s.at - before.at > 200) continue;
        const float dx = s.pos.v[0] - before.pos.v[0], dz = s.pos.v[2] - before.pos.v[2];
        const float step = std::hypot(dx, dz);
        travelled += step;
        if (step < 0.012f) continue;
        ++moving;
        astray += Along(s, axis, dx, dz) < 0.5f;
    }
    return travelled > 0.3f && moving >= 3 && astray * 10 >= moving * 7;
}

// a short trail of ground she's stood on since the last station, newest
// first, steady and not carried, a few steps apart. If the floor at the spot
// isn't there on load, these come before the station: the last of them is
// where she got on whatever moved
constexpr int kTrailLength = 6;
Vec4 g_trail[kTrailLength]{};
int g_trailCount = 0;

bool OnMovingPiece(const Vec4& at);

void Remember(const Vec4& at) {
    if (g_trailCount && std::hypot(at.v[0] - g_trail[0].v[0], at.v[2] - g_trail[0].v[2]) < 4.0f) return;
    // a load that lands on the trail lands the same way a save at the spot
    // would, so it only keeps ground Save itself would have taken
    if (OnMovingPiece(at)) return;
    for (int i = std::min(g_trailCount, kTrailLength - 1); i > 0; --i) g_trail[i] = g_trail[i - 1];
    g_trail[0] = at;
    g_trailCount = std::min(g_trailCount + 1, kTrailLength);
}

// the last few metres she walked, a breadcrumb every 25 cm, newest last.
// Only steady ground off any ride: a jump, a lift or a teleport starts it
// again, so a line between two crumbs never crosses something she didn't walk
constexpr int kPathLength = 32;
Vec4 g_path[kPathLength]{};
int g_pathCount = 0, g_pathNext = 0;

void NotePath(const Vec4& at) {
    if (g_pathCount) {
        const Vec4& last = g_path[(g_pathNext + kPathLength - 1) % kPathLength];
        const float d = std::hypot(at.v[0] - last.v[0], at.v[2] - last.v[2]);
        if (d > 3.0f || std::fabs(at.v[1] - last.v[1]) > 1.0f) g_pathCount = 0;
        else if (d < 0.25f) return;
    }
    g_path[g_pathNext++ % kPathLength] = at;
    g_pathCount = std::min(g_pathCount + 1, kPathLength);
}

bool OnMovingPiece(const Vec4& at);

// a crumb 1.74 to 2.5 m back from the spot that she walked in from in a
// straight line: every crumb after it closer to the spot and within 40 cm of
// the line, and the newest one on the spot. That's where a load can start her
// for the ease-in to carry her onto it, and the crumbs from there on are the
// heights she walked it at. 1.74 covers a save made straight after a load,
// whose own ease-in is all the path there is (1.75 to 1.81 m, Ch 3, Nov 25),
// and 1.2 m of height a slope she ran down to it (one did 0.7)
bool FindWalk(const Vec4& spot, Walk& walk) {
    walk.count = 0;
    if (g_pathCount < 2) return false;
    const auto at = [](int back) -> const Vec4& { return g_path[(g_pathNext - 1 - back + 2 * kPathLength) % kPathLength]; };
    const auto flat = [](const Vec4& a, const Vec4& b) { return std::hypot(a.v[0] - b.v[0], a.v[2] - b.v[2]); };
    if (flat(at(0), spot) > 0.5f) return false;
    for (int back = 1; back < g_pathCount; ++back) {
        const Vec4& c = at(back);
        const float d = flat(c, spot);
        if (d < 1.74f) continue;
        if (d > 2.5f || std::fabs(c.v[1] - spot.v[1]) > 1.2f || OnMovingPiece(c)) return false;
        const float ux = (spot.v[0] - c.v[0]) / d, uz = (spot.v[2] - c.v[2]) / d;
        float lastAlong = 0;
        for (int k = back - 1; k >= 0; --k) {
            const Vec4& q = at(k);
            const float along = (q.v[0] - c.v[0]) * ux + (q.v[2] - c.v[2]) * uz;
            const float side = std::fabs((q.v[0] - c.v[0]) * uz - (q.v[2] - c.v[2]) * ux);
            if (side > 0.4f || along < lastAlong - 0.1f) return false;
            lastAlong = along;
        }
        if (back + 2 > kWalkMax) return false;
        for (int k = back; k >= 0; --k) walk.at[walk.count++] = at(k);
        // the spot's own height to finish on, not the last crumb's mid-stride one
        if (flat(at(0), spot) < 0.05f) --walk.count;
        walk.at[walk.count++] = spot;
        return true;
    }
    return false;
}

// her height on the walk in at x z, by how far along it that is, and how far
// off the line and short of the spot. Before its start it's the start's
float WalkHeight(const Walk& walk, float x, float z, float* side = nullptr, float* left = nullptr) {
    const Vec4& a = walk.at[0];
    const Vec4& b = walk.at[walk.count - 1];
    const float len = std::max(std::hypot(b.v[0] - a.v[0], b.v[2] - a.v[2]), 1e-3f);
    const float ux = (b.v[0] - a.v[0]) / len, uz = (b.v[2] - a.v[2]) / len;
    const float along = (x - a.v[0]) * ux + (z - a.v[2]) * uz;
    if (side) *side = std::fabs((x - a.v[0]) * uz - (z - a.v[2]) * ux);
    if (left) *left = len - along;
    float lastAlong = 0, lastY = a.v[1];
    for (int i = 1; i < walk.count; ++i) {
        // crumbs can sit a little back of the one before, FindWalk lets 10 cm through
        const float s = std::max(lastAlong, (walk.at[i].v[0] - a.v[0]) * ux + (walk.at[i].v[2] - a.v[2]) * uz);
        if (along <= s) {
            const float t = s > lastAlong ? std::clamp((along - lastAlong) / (s - lastAlong), 0.0f, 1.0f) : 1.0f;
            return lastY + t * (walk.at[i].v[1] - lastY);
        }
        lastAlong = s;
        lastY = walk.at[i].v[1];
    }
    return b.v[1];
}

// a block and what it points at, one hop out, for the dumps below
struct Hops {
    int count = 0;
    DWORD from[64]{}, to[64]{}, size[64]{};
    unsigned char bytes[64][0x400]{};
};

void Hop(const unsigned char* copy, size_t got, Hops& out) {
    out.count = 0;
    // heap pointers only: the exe's own tables are the same for everyone
    const auto image = g_base;
    for (size_t off = 0; off + 4 <= got && out.count < 64; off += 4) {
        const DWORD to = *reinterpret_cast<const DWORD*>(copy + off);
        if (to < 0x10000 || (to >= image && to < image + 0x2A00000) || to >= 0x7FFF0000 || (to & 3)) continue;
        bool seen = false;
        for (int i = 0; i < out.count && !seen; ++i) seen = out.to[i] == to;
        if (seen) continue;
        const size_t n = Readable(reinterpret_cast<const unsigned char*>(static_cast<std::uintptr_t>(to)),
                                  out.bytes[out.count], sizeof out.bytes[0]);
        if (!n) continue;
        out.from[out.count] = static_cast<DWORD>(off);
        out.to[out.count] = to;
        out.size[out.count] = static_cast<DWORD>(n);
        ++out.count;
    }
}

void WriteDump(int dump, const char* what, const unsigned char* copy, size_t got, const Hops& hops) {
    const std::string dir = Folder("debug");
    char name[48];
    std::snprintf(name, sizeof name, "\\pause-%02d%s.bin", dump, what);
    FILE* file = nullptr;
    if (fopen_s(&file, (dir + name).c_str(), "wb") == 0 && file) {
        fwrite(copy, 1, got, file);
        fclose(file);
    }
    // each hop: where in the block it was found, where it points, how much, the bytes
    std::snprintf(name, sizeof name, "\\pause-%02d%s-hops.bin", dump, what);
    if (fopen_s(&file, (dir + name).c_str(), "wb") == 0 && file) {
        for (int i = 0; i < hops.count; ++i) {
            fwrite(&hops.from[i], 4, 1, file);
            fwrite(&hops.to[i], 4, 1, file);
            fwrite(&hops.size[i], 4, 1, file);
            fwrite(hops.bytes[i], 1, hops.size[i], file);
        }
        fclose(file);
    }
}

// Debug only: the leader's actor, raw, once per pause, to find what she's
// standing on by comparing a pause on plain floor with one on a platform.
// The actor and one hop out had nothing that tells a ride from floor
// (Palumpolum, Nov 26), so her controller goes too, the thing that walks her
// and so the likeliest to know what's carrying her. Only pages VirtualQuery
// says are committed and readable get copied
void DumpActor() {
    static int dumps = 0;
    if (dumps >= 40) return;
    unsigned char* registry = *g_actorRegistry;
    if (!registry) return;
    static unsigned char copy[0x1000];
    static Hops hops;
    std::memset(copy, 0, sizeof copy);
    Vec4 at{};
    g_lock(registry + 0x6C0, nullptr, nullptr);
    unsigned char* actor = g_resolveActor(registry, nullptr, static_cast<int>(PlayerHandle()));
    const size_t got = actor ? Readable(actor, copy, sizeof copy) : 0;
    Hop(copy, got, hops);
    const int hopCount = hops.count;
    if (actor) std::memcpy(at.v, actor + kActorTranslation, sizeof at.v);
    g_unlock(registry + 0x6C0, nullptr);
    if (!actor || !got) return;
    ++dumps;
    WriteDump(dumps, "", copy, got, hops);
    static unsigned char ctrlCopy[kFieldPlayerSpan];
    static Hops ctrlHops;
    if (auto* ctrl = *reinterpret_cast<unsigned char**>(g_base + kFieldPlayerRva)) {
        const size_t ctrlGot = Readable(ctrl, ctrlCopy, sizeof ctrlCopy);
        Hop(ctrlCopy, ctrlGot, ctrlHops);
        if (ctrlGot) WriteDump(dumps, "-ctrl", ctrlCopy, ctrlGot, ctrlHops);
    }
    // how she was moving just before, so each dump says what it was
    float rise = 0;
    const bool steady = Steady(rise);
    float speed = 0;
    const Sample* last = nullptr;
    for (const Sample& s : g_track)
        if (s.at && (!last || s.at > last->at)) last = &s;
    if (last)
        for (const Sample& s : g_track)
            if (s.at && last->at - s.at > 0 && last->at - s.at <= 300)
                speed = std::max(speed, std::hypot(last->pos.v[0] - s.pos.v[0], last->pos.v[2] - s.pos.v[2]) * 1000.0f /
                                            static_cast<float>(last->at - s.at));
    Footing footing;
    const bool footed = ReadFooting(PlayerHandle(), footing);
    Log("  dumped the leader (%p, %u bytes, %d hops) to pause-%02d, at %.2f %.2f %.2f: sideways %.2f u/s, "
        "rise %.2f, steady %d, carried %d, facing row %d, up %.2f %.2f %.2f, floor %d (read %d, last free roam %d), "
        "last floor %.2f %.2f %.2f",
        static_cast<void*>(actor), static_cast<unsigned>(got), hopCount, dumps, at.v[0], at.v[1], at.v[2], speed,
        rise, steady, Carried(), FacingAxis() * 2, *reinterpret_cast<float*>(copy + 0x88),
        *reinterpret_cast<float*>(copy + 0x8C), *reinterpret_cast<float*>(copy + 0x90), footing.solid, footed,
        g_idleSolid, footing.ground.v[0], footing.ground.v[1], footing.ground.v[2]);
}

// the spot as the spawn should get it: upright, since the live matrix leans
// on slopes and lifts and a leaned start marker leaves the leader standing
// at that angle, and kEaseInTo ahead along the line she walked in on. With
// no walk in on file it's along her heading, and the walk the ease-in gets
// held to is level with the spot. A matrix with no heading left in it keeps
// the station's. Level with the spot either way, not the height she'll
// start at: the marker is what the controller settles her toward once the
// ease-in's done, and one at the bottom of a wave she'd walked up sank her
// 1.2 m into it the moment the hold let go (0.25.37, Nov 26). The level
// ones landed dead on
Matrix SpawnMatrix(const Matrix& saved, const float* station, Walk& walk) {
    Matrix out = saved;
    const Vec4 spot = Translation(saved.m);
    float fx = saved.m[8], fz = saved.m[10];
    if (walk.count >= 2) {
        fx = spot.v[0] - walk.at[0].v[0];
        fz = spot.v[2] - walk.at[0].v[2];
    }
    float len = std::hypot(fx, fz);
    if (len < 0.2f) {
        fx = station[8];
        fz = station[10];
        len = std::max(std::hypot(fx, fz), 1e-6f);
    }
    fx /= len;
    fz /= len;
    if (walk.count < 2) {
        walk.count = 2;
        walk.at[0] = {{spot.v[0] - kEaseInRun * fx, spot.v[1], spot.v[2] - kEaseInRun * fz, 1.0f}};
        walk.at[1] = spot;
    }
    const float rows[12] = {fz, 0, -fx, 0, 0, 1, 0, 0, fx, 0, fz, 0};
    std::memcpy(out.m, rows, sizeof rows);
    out.m[12] = spot.v[0] + kEaseInTo * fx;
    out.m[13] = spot.v[1] - kSpawnRise;
    out.m[14] = spot.v[2] + kEaseInTo * fz;
    return out;
}

// FNV-1a over the save's own bytes: the .where only counts for the exact
// save it was written with, so a station save over that slot can't be taken
// for ours. The same bytes come back out of the reader on load
unsigned long long Hash(const void* data, int size) {
    unsigned long long hash = 1469598103934665603ULL;
    const auto* bytes = static_cast<const unsigned char*>(data);
    for (int i = 0; i < size; ++i) hash = (hash ^ bytes[i]) * 1099511628211ULL;
    return hash;
}

// for whoever opens the folder wondering what these are and if they can go
// rewritten every time, so an older copy that says these are safe to
// delete doesn't outlive the change that made them matter
void ExplainLocations() {
    const std::string path = Folder("save-locations") + "\\README.txt";
    FILE* file = nullptr;
    if (fopen_s(&file, path.c_str(), "w") != 0 || !file) return;
    fputs("Your Save Anywhere saves, one file per save slot (ff13-07.where is slot 7): where you stood, and\n"
          "what the story had done to the area around you, which the game's own save doesn't keep.\n"
          "Keep them with your saves. Without its file a save made with Save Anywhere starts at its save\n"
          "station, and if it was made after a story scene, parts of the area may not be the way you left them.\n"
          "A file only counts while it matches its save, and one is removed when you save over that slot\n"
          "at a station.\n",
          file);
    fclose(file);
}

std::string StationWorldPath(int slot) {
    char name[32];
    std::snprintf(name, sizeof name, "\\ff13-%02d.world", slot);
    return Folder("debug") + name;
}

std::string WherePath(int slot) {
    char name[32];
    std::snprintf(name, sizeof name, "\\ff13-%02d.where", slot);
    return Folder("save-locations") + name;
}

// a spot a load dropped her through, kept beside its .where. Nothing can move
// her body once it's spawned (every move we tried was written back by it a
// frame later, Ch 9, Nov 23), so the only rescue is the next load: it starts
// on ground she walked, off that stretch, instead of the spot again
std::string LostPath(int slot) { return WherePath(slot) + ".lost"; }

// where spots used to live, right beside the add-on, and the debug dumps
// with them: moved into their folders once, never over a newer one
void Tidy() {
    const std::string top(g_dir.begin(), g_dir.end());
    for (const char* pattern : {"ff13-*.where", "pause-*.bin"}) {
        WIN32_FIND_DATAA found{};
        HANDLE search = FindFirstFileA((top + "\\" + pattern).c_str(), &found);
        if (search == INVALID_HANDLE_VALUE) continue;
        const std::string to = Folder(pattern[0] == 'f' ? "save-locations" : "debug");
        if (pattern[0] == 'f') ExplainLocations();
        do {
            if (found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
            MoveFileExA((top + "\\" + found.cFileName).c_str(), (to + "\\" + found.cFileName).c_str(), 0);
        } while (FindNextFileA(search, &found));
        FindClose(search);
    }
}

// names go as hex: they're the script's strings, and a space in one would
// throw the reader off every line after it
void Moment(const char* what);

void WriteWorld(FILE* file, const std::vector<Entry>& world, int zone, const std::string& mapset) {
    fprintf(file, "world %d %s %u\n", zone, mapset.c_str(), static_cast<unsigned>(world.size()));
    for (const Entry& e : world) {
        fprintf(file, "%d ", static_cast<int>(e.op));
        for (char c : e.a) fprintf(file, "%02x", static_cast<unsigned char>(c));
        fputc(' ', file);
        for (char c : e.b) fprintf(file, "%02x", static_cast<unsigned char>(c));
        fprintf(file, " %d %d\n", e.i, e.j);
    }
}

bool Unhex(const char* hex, char* out) {
    for (int k = 0; k < 16; ++k) {
        unsigned byte = 0;
        if (sscanf_s(hex + k * 2, "%2x", &byte) != 1) return false;
        out[k] = static_cast<char>(byte);
    }
    return true;
}

// a journal that doesn't read back whole is dropped whole: half of one would
// put back half a world
bool ReadWorld(FILE* file, std::vector<Entry>& out, int* zone = nullptr, std::string* mapsetOut = nullptr) {
    out.clear();
    char mapset[32]{};
    int z = -1;
    unsigned count = 0;
    if (fscanf_s(file, " world %d %31s %u", &z, mapset, static_cast<unsigned>(sizeof mapset), &count) != 3 ||
        count > 100000)
        return false;
    if (zone) *zone = z;
    if (mapsetOut) *mapsetOut = mapset;
    for (unsigned n = 0; n < count; ++n) {
        int op = 0;
        char a[40]{}, b[40]{};
        Entry e;
        if (fscanf_s(file, "%d %39s %39s %d %d", &op, a, static_cast<unsigned>(sizeof a), b,
                     static_cast<unsigned>(sizeof b), &e.i, &e.j) != 5 ||
            op < 0 || op > static_cast<int>(Op::Cutscene) || std::strlen(a) != 32 || std::strlen(b) != 32 ||
            !Unhex(a, e.a) || !Unhex(b, e.b)) {
            out.clear();
            return false;
        }
        e.op = static_cast<Op>(op);
        out.push_back(e);
    }
    return true;
}

void WriteWhere(const SlotHash& save) {
    ExplainLocations();
    DeleteFileA(LostPath(save.slot).c_str());
    FILE* file = nullptr;
    if (fopen_s(&file, WherePath(save.slot).c_str(), "w") != 0 || !file) {
        Log("couldn't write %s", WherePath(save.slot).c_str());
        return;
    }
    const float* m = g_savedMatrix.m;
    // 4: made with the full state guard, 6 adds the world journal, 7 where she
    // walked in from, 8 the whole walk in. A 3 is from before the guard, and
    // loading one never counts as a state a station could save. A 5's journal
    // kept the engine's own area streaming too, and replaying that hid things,
    // so a 5 loads with its spot and nothing put back
    fprintf(file, "xiii-save-anywhere 8\nhash %016llx\npoint ", save.hash);
    for (unsigned char b : g_savedPoint.bytes) fprintf(file, "%02x", b);
    fprintf(file, "\npos %.6f %.6f %.6f\nmatrix", m[12], m[13], m[14]);
    for (float f : g_savedMatrix.m) fprintf(file, " %.9g", f);
    fprintf(file, "\ntrail %d", g_savedTrailCount);
    for (int i = 0; i < g_savedTrailCount; ++i)
        fprintf(file, " %.6f %.6f %.6f", g_savedTrail[i].v[0], g_savedTrail[i].v[1], g_savedTrail[i].v[2]);
    fprintf(file, "\nwalk %d", g_savedWalk.count);
    for (int i = 0; i < g_savedWalk.count; ++i)
        fprintf(file, " %.6f %.6f %.6f", g_savedWalk.at[i].v[0], g_savedWalk.at[i].v[1], g_savedWalk.at[i].v[2]);
    fputc('\n', file);
    WriteWorld(file, g_savedWorld, g_savedZone, g_savedMapSet);
    fclose(file);
    Moment("saved with save anywhere");
    Log("saved slot %d at %.2f %.2f %.2f, %u things the scripts did to the field kept with it", save.slot, m[12],
        m[13], m[14], static_cast<unsigned>(g_savedWorld.size()));
    static Progress savedAt;
    ReadProgress(savedAt);
    LogProgress("  saved with", savedAt);
}

bool ReadWhere(int slot, unsigned long long& hash, SavePoint& point, Matrix& matrix, int* format = nullptr,
               Vec4* trail = nullptr, int* trailCount = nullptr, std::vector<Entry>* world = nullptr,
               Walk* walk = nullptr) {
    FILE* file = nullptr;
    if (fopen_s(&file, WherePath(slot).c_str(), "r") != 0 || !file) return false;
    char hex[40]{};
    int version = 0;
    float pos[3]{};
    bool ok = fscanf_s(file, "xiii-save-anywhere %d hash %llx point %32s pos %f %f %f matrix", &version, &hash, hex,
                       static_cast<unsigned>(sizeof hex), &pos[0], &pos[1], &pos[2]) == 6 &&
              version >= 3 && version <= 8 && std::strlen(hex) == 32;
    if (format) *format = version;
    for (int i = 0; ok && i < 16; ++i) ok = fscanf_s(file, "%f", &matrix.m[i]) == 1 && std::isfinite(matrix.m[i]);
    // older files have no trail, which only means the station's the fallback
    Vec4 steps[kTrailLength]{};
    int count = 0, read = 0;
    if (ok && fscanf_s(file, " trail %d", &count) == 1 && count >= 0 && count <= kTrailLength) {
        for (; read < count; ++read) {
            Vec4& v = steps[read];
            if (fscanf_s(file, "%f %f %f", &v.v[0], &v.v[1], &v.v[2]) != 3 || !std::isfinite(v.v[0]) ||
                !std::isfinite(v.v[1]) || !std::isfinite(v.v[2]))
                break;
            v.v[3] = 1.0f;
        }
    }
    if (trail && trailCount) {
        std::copy(steps, steps + read, trail);
        *trailCount = read;
    }
    // a 7 kept only where the walk in started, so its line runs straight from there to the spot
    Walk walked;
    int points = 0, flag = 0;
    if (ok && read == count && version == 7 &&
        fscanf_s(file, " approach %d %f %f %f", &flag, &walked.at[0].v[0], &walked.at[0].v[1], &walked.at[0].v[2]) == 4 &&
        flag == 1) {
        walked.at[1] = {{pos[0], pos[1], pos[2], 1.0f}};
        walked.count = 2;
    } else if (ok && read == count && version >= 8 && fscanf_s(file, " walk %d", &points) == 1 && points >= 2 &&
               points <= kWalkMax) {
        for (; walked.count < points; ++walked.count)
            if (fscanf_s(file, "%f %f %f", &walked.at[walked.count].v[0], &walked.at[walked.count].v[1],
                         &walked.at[walked.count].v[2]) != 3)
                break;
        if (walked.count < points) walked.count = 0;
    }
    for (int i = 0; i < walked.count; ++i) {
        walked.at[i].v[3] = 1.0f;
        for (int k = 0; k < 3; ++k)
            if (!std::isfinite(walked.at[i].v[k])) walked.count = 0;
    }
    if (walk) *walk = walked;
    // and older ones no journal: the spot still works, there's just nothing to put back
    if (world) {
        world->clear();
        if (ok && read == count && version >= 6 && !ReadWorld(file, *world, &g_pendingZone, &g_pendingMapSet))
            Log("slot %d's world journal didn't read back, loading without it", slot);
    }
    fclose(file);
    if (!ok) return false;
    for (int i = 0; i < 16; ++i) {
        unsigned byte = 0;
        sscanf_s(hex + i * 2, "%2x", &byte);
        point.bytes[i] = static_cast<unsigned char>(byte);
    }
    return true;
}

// "ff13-07.dat", maybe with a path in front: 7, or -1 for anything else
int SlotOf(const char* name) {
    if (!name) return -1;
    const char* base = name;
    for (const char* p = name; *p; ++p)
        if (*p == '\\' || *p == '/') base = p + 1;
    if (_strnicmp(base, "ff13-", 5) != 0 || base[5] < '0' || base[5] > '9' || base[6] < '0' || base[6] > '9' ||
        _stricmp(base + 7, ".dat") != 0)
        return -1;
    return (base[5] - '0') * 10 + (base[6] - '0');
}

// the writer formats the slot's name from its own byte at +0x14A. Only our
// save is recorded: a station's own save over a slot just leaves the old
// .where behind, and the hash stops matching it. Any other slot write is the
// game's own save. Not just from the save screen's state: a terminal opens
// it from its menu, and those never passed that test (Nov 15, no station
// save was ever seen)
int __fastcall SaveWriteHook(unsigned char* self, void* edx, int a, int b, const void* data, int size) {
    const int slot = static_cast<signed char>(self[0x14A]);
    // terminal saves still weren't caught by the name check (Nov 15), so
    // every write says what it carried until that's pinned down
    if (g_debug) {
        char name[33]{};
        for (int k = 0; k < 32; ++k) {
            const char c = reinterpret_cast<const char*>(self)[0x172 + k];
            if (!c) break;
            name[k] = c >= 0x20 && c < 0x7F ? c : '?';
        }
        Log("slot write: slot %d, name \"%s\", %d bytes, state %d, ours %d, args %d %d", slot, name, size,
            g_stateForFiles.load(), g_ourSave.load() ? 1 : 0, a, b);
    }
    // a whole save's worth of data into a real slot: a terminal writes from
    // its own menu (state 14) with the name field still empty, so neither the
    // state nor the name tells it apart (Nov 15). 300,080 bytes, same as ours
    if (!g_ourSave.load() && slot >= 0 && slot < kSlots && data && size >= kSaveMinBytes) {
        g_sanction = kStation;
        g_stationSaveState = g_stateForFiles.load();
        // a station save over a slot leaves that slot's spot matching nothing,
        // so it goes rather than piling up
        if (DeleteFileA(WherePath(slot).c_str())) g_staleSlot = slot;
        DeleteFileA(LostPath(slot).c_str());
        if (g_debug) {
            FILE* file = nullptr;
            if (fopen_s(&file, StationWorldPath(slot).c_str(), "w") == 0 && file) {
                WriteWorld(file, WorldNow(), g_worldZone, MapSetName());
                fclose(file);
            }
        }
    }
    if (g_ourSave.load() && data && size > 0) {
        const SlotHash save{static_cast<signed char>(self[0x14A]), Hash(data, size)};
        EnterCriticalSection(&g_fileLock);
        g_written = save;
        LeaveCriticalSection(&g_fileLock);
    }
    return g_saveWrite(self, edx, a, b, data, size);
}

// the load screen reads every slot to list them, then the one you pick.
// Only outside the field, so the save screen's own listing never reads as a
// load, and the last one standing is the one that's loading
int __fastcall SaveReadHook(unsigned char* self, void* edx, int a, void* out, int size) {
    const int result = g_saveRead(self, edx, a, out, size);
    const int state = g_stateForFiles.load();
    const int slot = SlotOf(reinterpret_cast<const char*>(self + 0x172));
    if (slot >= 0 && out && size > 0 && state != kStateFieldIdle && state != kStateFieldPause &&
        state != kStateFieldPauseWait && state != kStateSave) {
        const SlotHash load{slot, Hash(out, size), GetTickCount64()};
        EnterCriticalSection(&g_fileLock);
        g_loaded = load;
        LeaveCriticalSection(&g_fileLock);
        g_sanction = kLoad;
        g_startWanted = true;
        // nothing's running behind the load screen, so whatever the journal held is gone
        ForgetWorld("a load");
    }
    return result;
}

DWORD& Mask(unsigned char* pad, DWORD offset) { return *reinterpret_cast<DWORD*>(pad + offset); }

bool Pressed(unsigned char* pad) { return (Mask(pad, kPadPressed) >> g_button) & 1; }

// only the edges. Held stays, because it becomes next frame's "last": clear
// it and the same press reads as brand new a frame later
void Swallow(unsigned char* pad) {
    const DWORD keep = ~(1u << g_button);
    Mask(pad, kPadPressed) &= keep;
    Mask(pad, kPadRepeat) &= keep;
    Mask(pad, kPadRepeatToo) &= keep;
}

// first few presses on the pause screen, so a wrong button index shows up in
// the log as the index that did light up
void LearnButtons(unsigned char* pad) {
    const DWORD pressed = Mask(pad, kPadPressed);
    for (int i = 0; i < 16 && g_learnLines < 12; ++i) {
        if (!((pressed >> i) & 1)) continue;
        Trace("pause screen: button %d pressed%s", i, i == g_button ? " (save)" : "");
        ++g_learnLines;
    }
}

bool CanSave() {
    // the station this save would load at has to be in what's loaded right now
    return g_findSavePointRow() != nullptr && PlayerHandle() != 0xFFFFFFFF;
}

// Debug only, at each pause: what the ray world has between her waist and
// her head. 0.25.35 to 0.25.38 kept Save off and marked the landing when
// anything was there, and after every load on 0.25.38 something with a flat
// underside sat 0.4 m over her and flagged two landings that were dead on.
// So a landing's judged by her height alone, and this says what that is
void LogOverHer(const Vec4& at) {
    void* world = *reinterpret_cast<void**>(g_base + kGroundWorldRva);
    if (!world) return;
    alignas(16) const float from[4] = {at.v[0], at.v[1] + 1.5f, at.v[2], 1.0f};
    alignas(16) const float to[4] = {at.v[0], at.v[1] + 0.6f, at.v[2], 1.0f};
    alignas(16) float hit[16]{};
    // the hit's point is dwords 1 to 3 and its normal 5 to 7
    if (reinterpret_cast<GroundRayFn>(g_base + kGroundRayRva)(world, nullptr, from, to, hit, kNotFloorLayers) & 0xFF)
        Log("  over her: %.2f %.2f %.2f facing %.2f %.2f %.2f", hit[1], hit[2], hit[3], hit[5], hit[6], hit[7]);
}

// Debug only, at each pause: which collision layers the engine's ray finds
// under her, one bit at a time, to keep checking the Save gate's layers
// against spots that load and spots that drop her as testing moves through
// the game. 14 is her own body at the ray's start, 1 a flat plane under the
// whole field
void SurveyLayers(const Vec4& at) {
    void* world = *reinterpret_cast<void**>(g_base + kGroundWorldRva);
    if (!world) return;
    alignas(16) const float from[4] = {at.v[0], at.v[1] + 0.5f, at.v[2], 1.0f};
    alignas(16) const float to[4] = {at.v[0], at.v[1] - 3.0f, at.v[2], 1.0f};
    const auto ray = reinterpret_cast<GroundRayFn>(g_base + kGroundRayRva);
    char line[512];
    int n = std::snprintf(line, sizeof line, "  layers under her at %.2f %.2f %.2f:", at.v[0], at.v[1], at.v[2]);
    for (int bit = -1; bit < 32 && n < static_cast<int>(sizeof line) - 24; ++bit) {
        alignas(16) float hit[16]{};
        if (!(ray(world, nullptr, from, to, hit, bit < 0 ? -1 : 1 << bit) & 0xFF)) continue;
        // a point and a normal come back and which is which isn't pinned: the one under her
        float y = NAN;
        for (int k : {1, 5})
            if (std::fabs(hit[k] - at.v[0]) < 0.5f && std::fabs(hit[k + 2] - at.v[2]) < 0.5f) y = hit[k + 1];
        n += bit < 0 ? std::snprintf(line + n, sizeof line - n, " all %.2f", y)
                     : std::snprintf(line + n, sizeof line - n, " %d %.2f", bit, y);
        // past the point and the normal there's more, and somewhere in it
        // should be whose shape it hit: what names the piece a layer found
        if (bit >= 0 && bit != 1 && bit != 14) {
            const auto* raw = reinterpret_cast<const DWORD*>(hit);
            char words[160];
            int w = 0;
            for (int k = 0; k < 16; ++k) w += std::snprintf(words + w, sizeof words - w, " %08lx", raw[k]);
            Log("    layer %d hit:%s", bit, words);
        }
    }
    Log("%s", line);
    LogOverHer(at);
}

// standing on something the level moves: the same ray through the half metre
// either side of her feet. No world to ask means no answer, not a no. In a
// field nothing's posed, those pieces sit where the rebuild puts them, which
// is what a load lands on: the Ch 9 slab held every pre-button load
bool OnMovingPiece(const Vec4& at) {
    if (!g_fieldAnimates) return false;
    void* world = *reinterpret_cast<void**>(g_base + kGroundWorldRva);
    if (!world) return false;
    alignas(16) const float from[4] = {at.v[0], at.v[1] + 0.5f, at.v[2], 1.0f};
    alignas(16) const float to[4] = {at.v[0], at.v[1] - 0.5f, at.v[2], 1.0f};
    alignas(16) float hit[16]{};
    return reinterpret_cast<GroundRayFn>(g_base + kGroundRayRva)(world, nullptr, from, to, hit, kNotFloorLayers) & 0xFF;
}

// whether Save goes on the bar, and why not when it doesn't
const char* Withheld() {
    if (!CanSave()) return "this area's save point isn't loaded";
    if (g_worldCheckWanted) return "the field's still being put back after the load";
    if (g_lostLanding)
        return "the load dropped her through the spot's floor, so Save waits till she's at a station (load that save "
               "again and it starts on ground she walked)";
    if (g_sanctioned.story < 0) return "you haven't been at a station since this field started";
    static Progress now;
    ReadProgress(now);
    // a sequence still running, whatever came before
    if (now.ui != g_sanctioned.ui) return "a sequence has the field UI off";
    if (std::memcmp(now.menu, g_sanctioned.menu, sizeof now.menu) != 0) return "a sequence has the menus locked";
    // a story beat or a scene since the last station used to be the end of
    // it, because a load only rebuilds the station's side of the story. The
    // journal carries everything the scripts did to the field since, and a
    // load puts back what the rebuild misses (Vile Peaks, Nov 15: every
    // difference a terminal's own reload left was one of those). So now it's
    // only the few things it can't put back yet, and the zone's own flags,
    // which no save keeps
    if (g_sceneSince >= 0 || now.story != g_sanctioned.story) {
        if (CharasMoved())
            return "since you were last at a station a scene made or let go of a character, which a load can't put "
                   "back yet";
        if (g_lockedNotes.load() != g_lockedAtSanction)
            return "since you were last at a station a scene changed who you can talk to or where the AI can walk, "
                   "which a load can't put back yet";
        if (std::memcmp(now.zone, g_sanctioned.zone, sizeof now.zone) != 0)
            return "a zone flag changed since you were last at a station, and no save keeps those";
    }
    if (g_rode)
        return g_rodeByScene ? "a scene moved her and she hasn't walked off where it left her yet"
                             : "she rode a lift or a platform and hasn't walked off it yet";
    // near the floor only counts with ground walked since the last scene or
    // station behind it: if the spot's floor is gone on load, that's where she
    // lands instead of a station that may sit on the far side of the scene
    if (!g_idleSolid && !(g_nearFloor && g_trailCount > 0))
        return "she isn't on the level's own floor (a lift, a platform, mid-air, or not moved since loading)";
    if (g_trailCount == 0 && (g_sceneSince >= 0 || now.story != g_sanctioned.story))
        return "she hasn't stood on the level's floor since the scene";
    float rise = 0;
    if (!Steady(rise)) return "you were going up or down (a lift, a fall or a jump)";
    if (Carried()) return "something was carrying you (a moving platform)";
    // last, being calls into the engine
    Vec4 at{};
    if (!Position(PlayerHandle(), at)) return nullptr;
    constexpr const char* kOnPiece =
        "she's standing on something the level moves (a lift, the slab beside one), which a load can't set her back on";
    // her controller knows what she's on. The ray only knows layer 16, which
    // Gran Pulse and Nautilus put under plain ground all over, and Save went
    // missing there in patches for no reason (Nov 27)
    const auto* ctrl = *reinterpret_cast<const unsigned char**>(g_base + kFieldPlayerRva);
    if (ctrl && *reinterpret_cast<const float*>(ctrl + kControllerGround) != 1.0f) return kOnPiece;
    if ((!ctrl || g_cutscenePosed) && OnMovingPiece(at))
        return "she's over something a cutscene moved here (the ray reads it under her), which a load can't set her "
               "back on";
    return nullptr;
}

void SetPhase(Phase phase) {
    g_phase = phase;
    g_phaseStart = GetTickCount64();
}

ULONGLONG PhaseMs() { return GetTickCount64() - g_phaseStart; }

void StartSave() {
    const DWORD handle = PlayerHandle();
    if (!ReadMatrix(handle, g_savedMatrix)) {
        Log("no player position, not saving");
        return;
    }
    g_getSavePoint(g_savedPoint.bytes);
    ReadProgress(g_atSave);
    g_savedWorld = WorldNow();
    g_savedZone = g_worldZone;
    g_savedMapSet = MapSetName();
    std::copy(g_trail, g_trail + g_trailCount, g_savedTrail);
    g_savedTrailCount = g_trailCount;
    FindWalk(Translation(g_savedMatrix.m), g_savedWalk);
    // the pause screen only closes through its own Resume, so it gets one:
    // a frame of Start, the button that resumes. Firing the zone's PAUSE_REQ
    // instead moved the state machine on but left the screen up
    g_pressStart = true;
    SetPhase(Phase::Resuming);
    Log("save requested from pause");
}

void FinishSave() {
    EnterCriticalSection(&g_fileLock);
    const SlotHash written = g_written;
    g_written = {};
    LeaveCriticalSection(&g_fileLock);
    g_ourSave = false;
    if (written.slot < 0) {
        Log("save screen closed without saving");
        return;
    }
    // nothing runs behind the save screen, so this never should. If it did,
    // the save stands as a plain one and loads at its station
    static Progress after;
    ReadProgress(after);
    if (const char* moved = Drifted(after, g_atSave)) {
        Log("slot %d saved, but %s while saving, so it keeps no spot", written.slot, moved);
        return;
    }
    WriteWhere(written);
}

void TickSave(int state) {
    if (g_swallow > 0) --g_swallow;
    switch (g_phase) {
    case Phase::Idle:
        break;
    case Phase::Resuming:
        if (state == kStateFieldIdle) {
            // the world ran again for the frames it took to resume, so the
            // guard's asked once more right before the save screen opens
            if (const char* why = Withheld()) {
                Log("save dropped, %s", why);
                SetPhase(Phase::Idle);
                break;
            }
            g_ourSave = true;
            g_zoneFire(Zone(), nullptr, kTrgSaveReq);
            SetPhase(Phase::Requested);
        } else if (PhaseMs() > 5000) {
            Log("never got back to free roam (state %d), save dropped", state);
            SetPhase(Phase::Idle);
        }
        break;
    case Phase::Requested:
        if (state == kStateSave) {
            SetPhase(Phase::Saving);
        } else if (PhaseMs() > 3000) {
            g_ourSave = false;
            Log("the game didn't take the save request (state %d)", state);
            SetPhase(Phase::Idle);
        }
        break;
    case Phase::Saving:
        if (state != kStateSave) SetPhase(Phase::Settling);
        break;
    case Phase::Settling:
        // give the write a moment to land before hashing it
        if (PhaseMs() > 500) {
            FinishSave();
            SetPhase(Phase::Idle);
        }
        break;
    }
}

int StartMarker() {
    auto* field = static_cast<unsigned char*>(*g_fieldStart);
    void* holder = field ? *reinterpret_cast<void**>(field + 0x40) : nullptr;
    return holder ? g_startMarker(holder, nullptr) : -1;
}

// a missing floor is a whole piece missing (a lift's apron, a bridge, a
// platform), so ground within this of a spot that failed is on it too. Ch 9:
// the three newest trail spots were all on the apron by the button
constexpr float kSamePiece = 5.0f;

std::vector<Vec4> ReadLost(int slot) {
    std::vector<Vec4> lost;
    FILE* file = nullptr;
    if (fopen_s(&file, LostPath(slot).c_str(), "r") != 0 || !file) return lost;
    Vec4 v{};
    while (lost.size() < 8 && fscanf_s(file, "%f %f %f", &v.v[0], &v.v[1], &v.v[2]) == 3) lost.push_back(v);
    fclose(file);
    return lost;
}

void LoseSpot(int slot, const Vec4& spot) {
    FILE* file = nullptr;
    if (fopen_s(&file, LostPath(slot).c_str(), "a") != 0 || !file) return;
    fprintf(file, "%.6f %.6f %.6f\n", spot.v[0], spot.v[1], spot.v[2]);
    fclose(file);
}

bool OnLostPiece(const std::vector<Vec4>& lost, const Vec4& at) {
    for (const Vec4& l : lost)
        if (std::hypot(at.v[0] - l.v[0], at.v[2] - l.v[2]) < kSamePiece) return true;
    return false;
}

// moves the spawn off a spot that dropped her before, onto the newest trail
// spot off that stretch, keeping her heading. False when there's none
bool AvoidLostSpots(int slot, Matrix& saved) {
    const std::vector<Vec4> lost = ReadLost(slot);
    if (!OnLostPiece(lost, Translation(saved.m))) return true;
    for (int i = 0; i < g_fallbackCount; ++i) {
        if (OnLostPiece(lost, g_fallback[i])) continue;
        Log("slot %d's spot dropped her before, starting where she walked at %.2f %.2f %.2f instead", slot,
            g_fallback[i].v[0], g_fallback[i].v[1], g_fallback[i].v[2]);
        for (int k = 0; k < 3; ++k) saved.m[12 + k] = g_fallback[i].v[k];
        return true;
    }
    return false;
}

// the first lookup of the field's start marker after a save of ours was read
// decides it: the save's .where has to match (bytes, save point, a real spot)
// or nothing's armed and every answer stays the game's. Anything off and you
// start at the station, same as vanilla
void TryArm(int marker) {
    EnterCriticalSection(&g_fileLock);
    const SlotHash load = g_loaded;
    LeaveCriticalSection(&g_fileLock);
    // a save read a while back that never reached a field isn't this one
    if (load.slot < 0 || GetTickCount64() - load.at > 120000 || !g_enabled || marker != StartMarker()) return;
    EnterCriticalSection(&g_fileLock);
    g_loaded = {};
    LeaveCriticalSection(&g_fileLock);
    g_loadedSlot = load.slot;
    g_worldCheckWanted = true;
    g_roamMs = 0;
    g_realFrom = ~0u;
    g_spotUsed = false;
    g_lostLanding = false;
    g_pendingWanted = false;
    g_pendingMapSet = "-";
    g_pendingZone = -1;
    g_mapSetSwapLeft = true;

    unsigned long long stored = 0;
    SavePoint point{}, now{};
    Matrix saved{};
    int format = 0;
    std::vector<Entry> world;
    Walk walk;
    const bool ours = ReadWhere(load.slot, stored, point, saved, &format, g_fallback, &g_fallbackCount, &world, &walk);
    // the field's put back whenever the save is ours, whether or not the spot
    // can be used: a save made after a story beat needs its field back even
    // if it ends up starting at the station
    if (ours && load.hash == stored && format >= 6) {
        g_pending = std::move(world);
        g_pendingWanted = true;
    }
    if (!ours) {
        Log("loaded slot %d, which has no spot of ours", load.slot);
    } else if (load.hash != stored) {
        Log("slot %d was saved over since its spot was kept, starting at the station", load.slot);
    } else if (g_getSavePoint(now.bytes), !(now == point)) {
        Log("slot %d spawns at a different save point than it was saved at, starting at the station", load.slot);
    } else if (saved.m[12] == 0.0f && saved.m[13] == 0.0f && saved.m[14] == 0.0f) {
        Log("slot %d's spot reads as the origin, starting at the station", load.slot);
    } else if (const Vec4 spot = Translation(saved.m); !AvoidLostSpots(load.slot, saved)) {
        Log("slot %d's spot dropped her before and nothing she walked is off that stretch, starting at the station",
            load.slot);
    } else {
        // turned into the spawn's matrix on the first answer, against the station's
        // a spot moved off by AvoidLostSpots isn't the one she walked in to
        const Vec4 now = Translation(saved.m);
        const bool same = std::hypot(now.v[0] - spot.v[0], now.v[2] - spot.v[2]) < 0.01f;
        if (!same) walk.count = 0;
        g_spawn = {true, marker, GetTickCount64() + 20000, saved, load.slot, 0, walk};
        g_holdWalk.count = 0;
        if (walk.count)
            Log("  walked in from %.2f %.2f %.2f: starting her %.2f m back along it, at the heights she walked it",
                walk.at[0].v[0], walk.at[0].v[1], walk.at[0].v[2], kEaseInRun);
        else
            Log("  no straight walk in on file: starting her %.2f m back along her heading, level with the spot",
                kEaseInRun);
        g_landTarget = Translation(saved.m);
        g_savedSpot = g_landTarget;
        g_spotUsed = true;
        g_landCheckAt = GetTickCount64() + 2500;
        g_landGiveUp = GetTickCount64() + 15000;
        g_landSeenAt = 0;
        // made under the full guard, so it's a state a station could save
        if (format >= 4) g_sanction = kOurs;
        Log("slot %d: start marker %d answers with %.2f %.2f %.2f, %u things to check the field for once it's up",
            load.slot, marker, saved.m[12], saved.m[13], saved.m[14], static_cast<unsigned>(g_pending.size()));
    }
}

// the leader's matrix becomes the save's heading, upright, a step ahead of
// the spot, and the other two move by the same amount so the party stays
// where it always is around the leader
int __fastcall MarkerLookupHook(void* manager, void* edx, int marker, float* out0, float* out1, float* out2) {
    const int count = g_markerLookup(manager, edx, marker, out0, out1, out2);
    if (count <= 0 || !out0) return count;
    // the game's own answer for where this field starts, before ours replaces it
    if (g_startWanted && marker == StartMarker()) {
        g_startPos = Translation(out0);
        g_startWanted = false;
    }
    if (!g_spawn.armed) TryArm(marker);
    if (!g_spawn.armed || marker != g_spawn.marker) return count;
    if (GetTickCount64() > g_spawn.until) {
        g_spawn.armed = false;
        return count;
    }
    const Vec4 station = Translation(out0);
    if (g_spawn.answered == 0) {
        g_stationPos = station;
        g_spawn.matrix = SpawnMatrix(g_spawn.matrix, out0, g_spawn.walk);
        g_holdWalk = g_spawn.walk;
        const float* m = g_spawn.matrix.m;
        const float x = m[12] - kEaseInFrom * m[8], z = m[14] - kEaseInFrom * m[10];
        // the height the hold starts her at, not the marker's
        g_seedFrom = {{x, WalkHeight(g_holdWalk, x, z), z, 1.0f}};
        g_seedPath = true;
    }
    float delta[3];
    for (int i = 0; i < 3; ++i) delta[i] = g_spawn.matrix.m[12 + i] - station.v[i];
    std::memcpy(out0, g_spawn.matrix.m, sizeof g_spawn.matrix.m);
    float* party[] = {out1, out2};
    for (int member = 0; member < 2 && member + 1 < count; ++member)
        if (party[member])
            for (int i = 0; i < 3; ++i) party[member][12 + i] += delta[i];
    if (g_spawn.answered++ < 6) {
        const DWORD from = static_cast<DWORD>(reinterpret_cast<std::uintptr_t>(__builtin_return_address(0)) - g_base);
        Trace("  marker %d asked from %#lx (%d matrices): station %.2f %.2f %.2f, answered with the save's", marker,
            from, count, station.v[0], station.v[1], station.v[2]);
    }
    return count;
}

void __fastcall AreaEnterHook(void* self, void* edx, void* a, void* point) {
    g_areaEnter(self, edx, a, point);
    // taken on the game thread's next tick, where the state can be read
    g_areaEntered = true;
}

Progress g_restartAt;

int __cdecl RestartSaveHook(void* vm) {
    const int out = g_restartSave(vm);
    // scripts run on the game thread, so this is exactly what the snapshot holds
    ReadProgress(g_restartAt);
    g_restartPoint = true;
    return out;
}

// keeps a state as the sanctioned one, saying so only when it's news
// a restart point keeps the chara and talk baseline where it was: a load of
// ours still rebuilds from the station, not the restart point, so a chara
// the sequence made before the point is still one the station never makes
void Sanctify(const char* where, bool baseline = true) {
    static Progress now;
    ReadProgress(now);
    const bool news = g_sanctioned.story < 0 || Moved(now) || g_sceneSince >= 0;
    g_sanctioned = now;
    g_sceneSince = -1;
    if (baseline) {
        EnterCriticalSection(&g_worldLock);
        g_lockedAtSanction = g_lockedNotes.load();
        g_charaNet.clear();
        g_sceneLetGo.clear();
        g_rideParty.clear();
        LeaveCriticalSection(&g_worldLock);
    }
    g_trailCount = 0;
    g_pathCount = 0;
    g_rode = false;
    g_lostLanding = false;
    if (news) Log("story %d at %s", now.story, where);
}

// every frame from her first real frame after the spawn, once the field's put
// back. If the floor she saved on is there she stands on it; if it isn't (a
// lift back at its start, a platform elsewhere on its path, a save in mid
// air) she drops straight through, and nothing can catch her: the spot's
// marked so the next load starts somewhere she walked, and Save stays off.
// Right after a load the handle points at a stand-in for a few seconds, so
// only reads by the target count
void TickLoad(int state) {
    if (!g_landCheckAt || state != kStateFieldIdle || GetTickCount64() < g_landCheckAt) return;
    const ULONGLONG now = GetTickCount64();
    Vec4 at{};
    const DWORD handle = PlayerHandle();
    const bool read = Position(handle, at);
    // the stand-in sits at 0 1.5 -10 whatever the field
    const bool standIn = std::fabs(at.v[0]) < 0.01f && std::fabs(at.v[1] - 1.5f) < 0.01f && std::fabs(at.v[2] + 10.0f) < 0.01f;
    const float flat = read && !standIn ? std::hypot(at.v[0] - g_landTarget.v[0], at.v[2] - g_landTarget.v[2]) : 1e9f;
    const float drop = g_landTarget.v[1] - at.v[1];
    if (g_worldCheckWanted) return;
    // straight down: a walk or a hop off a step covers more ground than
    // height, and 0.25.13 read a jump next to the spot as a fall (Nov 23)
    if (flat < 3.0f && drop > 1.5f && flat < 0.5f * drop) {
        Log("dropped to %.2f %.2f %.2f, the floor at the spot isn't there", at.v[0], at.v[1], at.v[2]);
        LoseSpot(g_loadedSlot, g_landTarget);
        Log("slot %d's next load starts on ground she walked, off that stretch", g_loadedSlot);
        g_lostLanding = true;
        g_landCheckAt = 0;
        g_spawn.armed = false;
        return;
    }
    if (flat < 3.0f) {
        if (!g_landSeenAt) g_landSeenAt = now;
        // stood there a few seconds without dropping: the floor's real
        if (now - g_landSeenAt < 4000) return;
        Log("landed at %.2f %.2f %.2f, %.2f across and %.2f up from where it aimed", at.v[0], at.v[1], at.v[2], flat,
            at.v[1] - g_landTarget.v[1]);
        // no fall, but not on it either: into the floor, where Save's off and the next load shouldn't go
        if (drop > 0.3f) {
            Log("that's inside the floor at the spot, not on it");
            LoseSpot(g_loadedSlot, g_landTarget);
            Log("slot %d's next load starts on ground she walked, off that stretch", g_loadedSlot);
            g_lostLanding = true;
        }
    } else if (read && Distance(at, g_stationPos) < 5.0f) {
        Log("started at the station (%.2f %.2f %.2f) after all", at.v[0], at.v[1], at.v[2]);
    } else if (now < g_landGiveUp) {
        return;
    } else {
        Log("never saw the player near the spot or the station (last read %.2f %.2f %.2f)", at.v[0], at.v[1], at.v[2]);
    }
    g_landCheckAt = 0;
    g_spawn.armed = false;
}

// XIII reads pads through DirectInput and folds the keyboard in through its
// own bindings, and on the pause screen the save button never showed up in
// that mask. So the press is also read straight off the hardware: the key the
// game binds to it, and the same button on any XInput pad. Each source keeps
// its own last state so a hold is one press
using XInputGetStateFn = DWORD(WINAPI*)(DWORD, void*);
XInputGetStateFn g_xinputGetState = nullptr;
int g_saveKey = 'E';
bool g_keyWas = false;
bool g_padWas[4]{};
int g_padIdle[4]{};
int g_sourceLines = 0;

struct XInputState {
    DWORD packet;
    WORD buttons;
    BYTE leftTrigger, rightTrigger;
    SHORT thumbs[4];
};

// XInput's bit for each of XIII's mask bits that has one
WORD XInputBit(int bit) {
    switch (bit) {
    case 5: return 0x1000;
    case 7: return 0x2000;
    case 6: return 0x4000;
    case 4: return 0x8000;
    case 15: return 0x0010;
    case 14: return 0x0020;
    default: return 0;
    }
}

void LoadXInput() {
    for (const wchar_t* name : {L"xinput1_4.dll", L"xinput1_3.dll", L"xinput9_1_0.dll"}) {
        if (HMODULE module = LoadLibraryW(name)) {
            g_xinputGetState = reinterpret_cast<XInputGetStateFn>(reinterpret_cast<void*>(GetProcAddress(module, "XInputGetState")));
            if (g_xinputGetState) return;
        }
    }
}

bool GameHasFocus() {
    DWORD pid = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    return pid == GetCurrentProcessId();
}

// a fresh press from the keyboard or an XInput pad, or what's still held,
// read only on the pause screen and while saving: XInputGetState on an empty
// slot is slow, so it's never polled out in the field
const char* RawPress(bool& held) {
    const char* source = nullptr;
    held = false;
    const bool key = GameHasFocus() && (GetAsyncKeyState(g_saveKey) & 0x8000);
    if (key && !g_keyWas) source = "keyboard";
    held = held || key;
    g_keyWas = key;
    const WORD want = XInputBit(g_button);
    for (DWORD user = 0; want && g_xinputGetState && user < 4; ++user) {
        // an empty slot costs real time to ask about, so one that came back
        // empty is left a couple of seconds before it's asked again
        if (g_padIdle[user] > 0) {
            --g_padIdle[user];
            continue;
        }
        XInputState state{};
        const DWORD result = g_xinputGetState(user, &state);
        if (result == ERROR_DEVICE_NOT_CONNECTED) g_padIdle[user] = 120;
        const bool down = result == ERROR_SUCCESS && (state.buttons & want);
        if (down && !g_padWas[user] && !source) source = "controller";
        held = held || down;
        g_padWas[user] = down;
    }
    return source;
}

// Debug only, on the after-the-load lines: whether anyone's touching the
// stick or the movement keys while she drifts, so a drift isn't taken for
// the game's when it was the player's
void InputNow(char* out, size_t size) {
    XInputState state{};
    int lx = 0, ly = 0;
    if (g_xinputGetState && g_xinputGetState(0, &state) == ERROR_SUCCESS) {
        lx = state.thumbs[0];
        ly = state.thumbs[1];
    }
    char keys[8]{};
    int n = 0;
    for (int key : {'W', 'A', 'S', 'D'})
        if (GetAsyncKeyState(key) & 0x8000) keys[n++] = static_cast<char>(key);
    std::snprintf(out, size, "stick %d %d, keys %s", lx, ly, n ? keys : "-");
}

// a line that says where and when, at the moments the journal is about: a
// scene handing back to free roam, walking into a station, a save, a load.
// With Debug on it lists what the field gained since the last one too, so a
// story beat's changes read as one block without knowing where to look
std::vector<Entry> g_lastMoment;
bool g_wasScene = false;

void Moment(const char* what) {
    const std::vector<Entry> now = WorldNow();
    Vec4 at{};
    Position(PlayerHandle(), at);
    unsigned char* party = *g_party;
    const int scenario = party ? *reinterpret_cast<int*>(party + kWork) : -1;
    std::vector<Entry> moved;
    for (const Entry& e : now)
        if (std::none_of(g_lastMoment.begin(), g_lastMoment.end(), [&](const Entry& x) { return SameEffect(x, e); }))
            moved.push_back(e);
    Log("moment: %s. Zone %d, story %d, scenario %d, map set %s, at %.1f %.1f %.1f, %u field changes kept, %u new "
        "since the last moment", what, ZoneNumber(), g_story(), scenario, MapSetName().c_str(), at.v[0], at.v[1],
        at.v[2], static_cast<unsigned>(now.size()), static_cast<unsigned>(moved.size()));
    for (size_t k = 0; g_debug && k < moved.size() && k < 60; ++k) {
        char text[96];
        Describe(moved[k], text, sizeof text);
        Log("    + %s", text);
    }
    g_lastMoment = now;
}

// once a load's rebuild is done: a save of ours gets back whatever the
// station's load script didn't do, and with Debug on a station save's load
// says what the game's own rebuild missed, which is what this is all for
void CheckWorld() {
    Moment("loaded, the station's rebuild is done");
    const std::vector<Entry> have = WorldNow();
    int other = 0;
    if (g_pendingWanted) {
        g_pendingWanted = false;
        // the map set doesn't come into it: areas and boxes belong to the
        // zone and the game swaps sets under them as you walk, so a step
        // across a swap box before this ran used to skip the whole replay.
        // A different zone would mean the journal isn't this field's at all
        if (g_pendingZone != g_worldZone) {
            Log("saved in zone %d but this is zone %d, leaving the field as the game built it", g_pendingZone,
                g_worldZone);
            g_pending.clear();
            return;
        }
        // the area boxes' groups are the stretch of level around the spot, so
        // only there, on the map set it was saved on. Started at the station
        // instead, the rebuild's own groups are the right ones
        Vec4 at{};
        const bool read = Position(PlayerHandle(), at);
        const bool boxes = g_spotUsed && g_realSet == g_pendingMapSet && read &&
                           std::hypot(at.v[0] - g_savedSpot.v[0], at.v[2] - g_savedSpot.v[2]) < kBoxReach;
        if (!boxes)
            Log("not at the spot on map set %s (came up on %s), the area boxes' groups stay as the rebuild made them",
                g_pendingMapSet.c_str(), g_realSet.c_str());
        const std::vector<Entry> missing = Missing(g_pending, have, &other, boxes, g_realFrom);
        LogMissing("putting the field back the way it was saved", missing, other);
        for (const Entry& e : missing) Replay(e);
        g_pending.clear();
        return;
    }
    if (!g_debug || g_loadedSlot < 0) return;
    FILE* file = nullptr;
    if (fopen_s(&file, StationWorldPath(g_loadedSlot).c_str(), "r") != 0 || !file) return;
    std::vector<Entry> want;
    int zone = -1;
    const bool ok = ReadWorld(file, want, &zone);
    fclose(file);
    if (ok && zone == g_worldZone)
        LogMissing("a station load, against what was live when it saved", Missing(want, have, &other), other);
}

unsigned char* g_pauseWidget = nullptr;
ULONGLONG g_pauseSetAt = 0;
int g_pauseLooks = 0;
int g_idLines = 0;
void LogSlots(const char* when);

void Tick() {
    const bool pressed = g_pressed;
    g_pressed = false;
    const int state = State();
    g_stateForFiles = state;
    if (state < 0) return;

    const ULONGLONG now = GetTickCount64();
    const int stale = g_staleSlot.exchange(-1);
    if (stale >= 0) Log("slot %d saved at a station, its save anywhere spot is gone with the old save", stale);
    if (state != kStateFieldIdle) g_idleSince = 0;
    else if (!g_idleSince) g_idleSince = now;
    if (state == kStateFieldIdle) {
        if (g_roamAt) g_roamMs += now - g_roamAt;
        g_roamAt = now;
    } else {
        g_roamAt = 0;
        if (state != kStateFieldPause && state != kStateFieldPauseWait && state != kStateFieldPauseEnd) g_roamMs = 0;
    }
    // a new field's coordinates aren't the old one's
    if (state == 1 || state == 2) {
        g_trailCount = 0;
        g_pathCount = 0;
        g_idleSolid = false;
        g_nearFloor = false;
        g_rode = false;
        g_runDy = 0;
        g_runMs = 0;
        // a field change after the point means it's not this field's any more
        g_restartPoint = false;
    }
    const Sample& prev = g_track[(g_trackNext + 15) % 16];
    if (state == kStateFieldIdle && now - prev.at >= 40) {
        Matrix m{};
        if (ReadMatrix(PlayerHandle(), m)) {
            Sample next{now, Translation(m.m), {{m.m[0], m.m[2]}, {m.m[8], m.m[10]}}};
            for (auto& a : next.axis) {
                const float len = std::max(std::hypot(a[0], a[1]), 1e-6f);
                a[0] /= len;
                a[1] /= len;
            }
            // walking pace, level: that's her moving herself, so it says which way she faces
            const float dx = next.pos.v[0] - prev.pos.v[0], dz = next.pos.v[2] - prev.pos.v[2];
            const float pace = std::hypot(dx, dz) * 1000.0f / static_cast<float>(now - prev.at);
            if (prev.at && now - prev.at < 200 && pace > 1.5f && pace < 12.0f &&
                std::fabs(next.pos.v[1] - prev.pos.v[1]) < 0.05f && g_axisVotes[0] + g_axisVotes[1] < 100000)
                for (int a = 0; a < 2; ++a) g_axisVotes[a] += Along(next, a, dx, dz) > 0.9f;
            g_track[g_trackNext++ % 16] = next;
            const float stepDy = next.pos.v[1] - prev.pos.v[1];
            const ULONGLONG dt = now - prev.at;
            // a gap is a pause, a fight or a scene, and a jump of metres is a
            // teleport (a load, a rescue): neither is a ride. A hop is up then
            // down inside a few hundred ms, so it never builds a run
            const bool rising = prev.at && dt <= 1000 && std::hypot(dx, dz) < 0.5f * std::fabs(stepDy) &&
                                std::fabs(stepDy) * 1000.0f > 0.1f * static_cast<float>(dt) &&
                                std::hypot(std::hypot(dx, dz), stepDy) < 2.0f;
            if (!rising || (g_runDy > 0) != (stepDy > 0)) {
                g_runDy = 0;
                g_runMs = 0;
            }
            if (rising) {
                g_runDy += stepDy;
                g_runMs += dt;
            }
            // a load settling her onto the spot isn't a ride either
            if (g_landCheckAt) {
                g_runDy = 0;
                g_runMs = 0;
            }
            const bool carried = Carried();
            // a platform the ray lost mid-leg still moves her through her own
            // controller (Palumpolum Ch 7, Nov 26: a save on one floated her on
            // load). Every carried step moves the latch along, so 6 m off is
            // measured from where the ride ended, whatever it was
            if (auto* ctrl = *reinterpret_cast<const unsigned char**>(g_base + kFieldPlayerRva)) {
                const auto* step = reinterpret_cast<const float*>(ctrl + kControllerCarry);
                if (std::fabs(step[0]) + std::fabs(step[1]) + std::fabs(step[2]) > kCarryMoving) {
                    if (!g_rode || g_rodeByScene)
                        Log("something the level moves is carrying her (%.3f %.3f %.3f a frame), Save waits till she's "
                            "off it",
                            step[0], step[1], step[2]);
                    g_rode = true;
                    g_rodeByScene = false;
                    g_rodeAt = next.pos;
                }
            }
            if (g_runMs >= 2000 && std::fabs(g_runDy) >= 2.0f) {
                if (!g_rode) Log("riding a lift (%.1f %s in %llu ms), Save waits till she's off it", std::fabs(g_runDy),
                                 g_runDy > 0 ? "up" : "down", g_runMs);
                g_rode = true;
                g_rodeByScene = false;
                g_rodeAt = next.pos;
            }
            float rise = 0;
            Footing footing;
            // not in the first beat back from a pause either, before the game has
            // put the bit back. The save fires right in that beat
            if (now - g_idleSince >= 100) {
                const bool read = ReadFooting(PlayerHandle(), footing);
                g_idleSolid = read && footing.solid;
                g_nearFloor = read && !footing.solid &&
                              std::hypot(next.pos.v[0] - footing.ground.v[0], next.pos.v[2] - footing.ground.v[2]) <
                                  kNearFloorAcross &&
                              std::fabs(next.pos.v[1] - footing.ground.v[1]) < kNearFloorUp;
            }
            // after a scene too, now a save can come after one: the trail is
            // where a load with no floor at the spot lands you, so it has to be
            // floor you've walked since
            const bool offRide = !g_rode || std::hypot(next.pos.v[0] - g_rodeAt.v[0], next.pos.v[2] - g_rodeAt.v[2]) >=
                                                  (g_rodeByScene ? kOffScene : kOffRide);
            if (Steady(rise) && !carried && g_idleSolid && offRide) {
                if (g_rode)
                    Log("off %s, at %.2f %.2f %.2f", g_rodeByScene ? "where the scene left her" : "the ride", next.pos.v[0],
                        next.pos.v[1], next.pos.v[2]);
                g_rode = false;
                if (g_sanctioned.story >= 0) Remember(next.pos);
                if (g_seedPath.exchange(false)) {
                    g_pathCount = 0;
                    NotePath(g_seedFrom);
                }
                NotePath(next.pos);
            } else {
                g_pathCount = 0;
            }
        }
    }
    if (InScene(state) && g_sceneSince < 0 && g_sanctioned.story >= 0) {
        g_sceneSince = state;
        g_notesAtScene = g_notes.load();
        // ground walked before a scene may be the wrong side of whatever it
        // changed, so the trail starts again after it
        g_trailCount = 0;
        g_pathCount = 0;
        // against where it started, not the last station: a chest or a ledge
        // jump on the way flips flags, and those were fine before the scene
        ReadProgress(g_sceneStart);
        g_sceneFromRead = Position(PlayerHandle(), g_sceneFrom);
        Log("scene (state %d)", state);
    }
    // only a scene's own let-gos ride on the chocobo it brings in: free roam
    // starts the list again every frame
    if (state == kStateFieldIdle) {
        EnterCriticalSection(&g_worldLock);
        g_sceneLetGo.clear();
        LeaveCriticalSection(&g_worldLock);
    }
    if (InScene(state)) g_wasScene = true;
    else if (g_wasScene && state == kStateFieldIdle && g_idleSince && now - g_idleSince > 500) {
        g_wasScene = false;
        Moment("back in free roam after a scene");
        // a terminal's own menu is an event state too, so just using one
        // turned Save off until you walked back into its area (Nov 15). A
        // scene that left the story, every flag, every script variable, the
        // zone's flags and the field itself exactly as they were is no scene
        // as far as a save can tell
        if (g_sceneSince >= 0) {
            static Progress after;
            ReadProgress(after);
            if (g_notes.load() == g_notesAtScene && !Moved(after) && !Drifted(after, g_sceneStart)) {
                Log("that scene (state %d) left everything as it was, save anywhere is back", g_sceneSince);
                g_sceneSince = -1;
            }
        }
        // a load's own scene isn't one: the quit to title, the station's load
        // script and the spawn all run as scenes, and they move her by design
        Vec4 at{};
        if (g_sceneSince >= 0 && !g_worldCheckWanted && g_sanctioned.story >= 0 && g_sceneFromRead &&
            Position(PlayerHandle(), at) &&
            (std::hypot(at.v[0] - g_sceneFrom.v[0], at.v[2] - g_sceneFrom.v[2]) > 2.0f ||
             std::fabs(at.v[1] - g_sceneFrom.v[1]) > 1.0f)) {
            Log("the scene moved her to %.2f %.2f %.2f, Save waits till she's walked off it", at.v[0], at.v[1], at.v[2]);
            g_rode = true;
            g_rodeByScene = true;
            g_rodeAt = at;
        }
    }
    if (g_areaEntered.exchange(false) && state == kStateFieldIdle) {
        Moment("walked into a station's area");
        Sanctify("a station");
        // a load that started you inside the area is settled by this too
        if (g_sanction == kLoad) g_sanction = kNone;
    }
    // a restart point counts once you're back in free roam with nothing that
    // persists moved since. A scene that ran on past the point and changed
    // things means free roam isn't what the snapshot holds, so it waits
    if (state == kStateFieldIdle && g_restartPoint.exchange(false)) {
        static Progress now;
        ReadProgress(now);
        if (const char* why = Drifted(now, g_restartAt))
            Log("restart point at story %d, but %s before free roam, not taking it", g_restartAt.story, why);
        else
            Sanctify("a restart point", false);
    }
    // whatever was sanctioned before belongs to the field that's gone
    if (g_sanction == kLoad || g_sanction == kOurs) g_sanctioned.story = -1;
    // settled for 2 s past the stand-in, the way the land check waits. Free
    // roam that a pause only holds: pausing to look for Save right after a
    // load kept starting the 2 s over (Vile Peaks, Nov 16)
    if (g_sanction != kNone && state == kStateFieldIdle && g_roamMs > 2000) {
        if (g_sanction == kStation) {
            Log("saved at a station (state %d)", g_stationSaveState.load());
            Moment("saved at a station");
            Sanctify("the last station save");
            g_sanction = kNone;
        } else if (g_sanction == kOurs) {
            // not off the stand-in's idle: the station's load script and its
            // own scene come after that, and a sanction taken first left the
            // load's scene standing against it, Save off until the next
            // station after every load of ours (Vile Peaks, Nov 15). Once the
            // field's been put back, the state's the one that was saved
            if (!g_worldCheckWanted) {
                Sanctify("a save anywhere load");
                g_sanction = kNone;
            }
        } else {
            // a plain load counts once you're seen standing at the station it
            // started you at. The stand-in at 0 1.5 -10 isn't you yet
            Vec4 at{};
            const bool read = Position(PlayerHandle(), at);
            const bool standIn = std::fabs(at.v[0]) < 0.01f && std::fabs(at.v[1] - 1.5f) < 0.01f &&
                                 std::fabs(at.v[2] + 10.0f) < 0.01f;
            if (read && !standIn) {
                if (std::hypot(at.v[0] - g_startPos.v[0], at.v[2] - g_startPos.v[2]) < 6.0f &&
                    std::fabs(at.v[1] - g_startPos.v[1]) < 4.0f)
                    Sanctify("the station this load started at");
                else
                    Log("loaded away from a station, save anywhere waits until you reach one");
                g_sanction = kNone;
            } else if (g_roamMs > 15000) {
                Log("never saw where this load started, save anywhere waits until you reach a station");
                g_sanction = kNone;
            }
        }
    }
    // not the first free roam after a load: that's the stand-in at 0 1.5 -10,
    // before the station's load script has even run (Nov 15, a replay there
    // hung the load for good). The script runs, hands off through its own
    // scene, and only then is it you, in free roam, with the calls gone quiet
    // A field whose scripts never go quiet (a loop toggling something every
    // second) still gets its turn once free roam has run a while
    if (g_worldCheckWanted && g_realFrom == ~0u && state == kStateFieldIdle) {
        Vec4 at{};
        if (Position(PlayerHandle(), at) && !StandIn(at)) {
            g_realFrom = g_notes.load();
            g_realSet = MapSetName();
            // a fall gets caught in the first metre, before she lands in a
            // fight on the floor below. 2.5 s in she'd already dropped 11 m
            if (g_landCheckAt) g_landCheckAt = now;
            // the ease-in starts in her next few frames, and the hold's on from the first
            if (g_landCheckAt && g_setMatrix && g_holdWalk.count >= 2) {
                g_holdUntil = now + 5000;
                g_holdFrames = 0;
                g_holdAtSpot = 0;
                g_holdPullWay = g_holdPullSpot = 0;
                g_holding = true;
            }
            g_heightTraceUntil = g_debug ? now + 4000 : 0;
            g_watchLines = 0;
            g_ctrlGot = 0;
            g_ctrlDiffed = !g_debug;
            if (auto* ctrl = *reinterpret_cast<unsigned char**>(g_base + kFieldPlayerRva))
                g_ctrlGot = Readable(ctrl, g_ctrlStart, sizeof g_ctrlStart);
        }
    }
    // the hold and the setMatrix watch run alongside, on whoever the leader is right now
    if (g_setMatrix && (g_holding || (g_heightTraceUntil && now < g_heightTraceUntil))) {
        unsigned char* registry = *g_actorRegistry;
        const DWORD handle = PlayerHandle();
        if (registry && handle != 0xFFFFFFFF) {
            g_lock(registry + 0x6C0, nullptr, nullptr);
            g_watchActor = g_resolveActor(registry, nullptr, static_cast<int>(handle));
            g_unlock(registry + 0x6C0, nullptr);
        }
        g_watchUntil = g_heightTraceUntil;
    }
    // and once it's over, every dword of the controller that changed through it
    if (!g_ctrlDiffed && g_heightTraceUntil && now >= g_heightTraceUntil) {
        g_ctrlDiffed = true;
        static unsigned char end[kFieldPlayerSpan];
        auto* ctrl = *reinterpret_cast<unsigned char**>(g_base + kFieldPlayerRva);
        const size_t got = ctrl ? std::min(g_ctrlGot, Readable(ctrl, end, sizeof end)) : 0;
        int lines = 0;
        for (size_t at = 0; at + 4 <= got && lines < 80; at += 4) {
            DWORD a, b;
            std::memcpy(&a, g_ctrlStart + at, 4);
            std::memcpy(&b, end + at, 4);
            if (a == b) continue;
            float fa, fb;
            std::memcpy(&fa, &a, 4);
            std::memcpy(&fb, &b, 4);
            Log("    controller +%#05x: %08lx -> %08lx (%g -> %g)", static_cast<unsigned>(at), a, b, fa, fb);
            ++lines;
        }
        Log("  controller through the trace: %d of %u dwords changed", lines, static_cast<unsigned>(got / 4));
    }
    if (g_heightTraceUntil && now < g_heightTraceUntil && now - g_heightTraceAt >= 250 && state == kStateFieldIdle) {
        Vec4 at{};
        char input[48];
        InputNow(input, sizeof input);
        DWORD flags = 0;
        if (auto* ctrl = *reinterpret_cast<unsigned char**>(g_base + kFieldPlayerRva))
            Readable(ctrl + kFieldPlayerFlags, reinterpret_cast<unsigned char*>(&flags), sizeof flags);
        if (Position(PlayerHandle(), at))
            Log("  after the load: %.2f %.2f %.2f (%s, controller %08lx)", at.v[0], at.v[1], at.v[2], input, flags);
        g_heightTraceAt = now;
    }
    if (g_worldCheckWanted && state == kStateFieldIdle && g_roamMs > 1000 &&
        (now - g_lastNoteAt.load() > 1000 || g_roamMs > 6000)) {
        Vec4 at{};
        if (Position(PlayerHandle(), at) && !StandIn(at)) {
            g_worldCheckWanted = false;
            CheckWorld();
        }
    }
    if (state == kStateFieldPause && g_lastState != kStateFieldPause && g_phase == Phase::Idle && g_enabled) {
        if (const char* why = Withheld()) Log("save not offered: %s", why);
        if (g_debug) {
            static Progress pausedAt;
            ReadProgress(pausedAt);
            LogProgress("paused", pausedAt);
            DumpActor();
            Vec4 at{};
            if (Position(PlayerHandle(), at)) SurveyLayers(at);
        }
    }

    const bool watching = state == kStateFieldPause || g_phase != Phase::Idle;
    bool rawHeld = false;
    const char* raw = watching ? RawPress(rawHeld) : nullptr;
    // only with Save on the bar: with Quit's confirm box up, the Start we press
    // to resume would land on that instead
    if (state == kStateFieldPause && g_phase == Phase::Idle && g_enabled && (g_decorated || !g_prompt) &&
        (pressed || raw)) {
        if (g_sourceLines < 6) {
            ++g_sourceLines;
            Log("save pressed (%s)", pressed ? "game pad mask" : raw);
        }
        if (const char* why = Withheld()) Log("save pressed, but %s", why);
        else StartSave();
    }
    // the press that started a save would otherwise land in the field as it
    // resumes, where Y opens the main menu. So from the save starting until
    // it's let go, and a beat after, that button's presses are eaten. Only
    // ours: a Y in normal play never gets near this
    if (g_phase == Phase::Resuming || g_phase == Phase::Requested) g_eatUntilUp = true;
    else if (!rawHeld && !g_heldLastFrame) g_eatUntilUp = false;
    if (g_eatUntilUp) g_swallow = 15;
    g_heldLastFrame = false;
    // the bar again a beat after it was built and a second after, in case
    // something sets it again behind the mode setter
    if (g_pauseLooks > 0 && g_pauseWidget) {
        const ULONGLONG since = GetTickCount64() - g_pauseSetAt;
        if ((g_pauseLooks == 2 && since > 150) || (g_pauseLooks == 1 && since > 1200)) {
            LogSlots(g_pauseLooks == 2 ? "0.15s" : "1.2s");
            --g_pauseLooks;
        }
    }
    TickSave(state);
    if (g_phase == Phase::Idle && g_enabled) TickLoad(state);
    g_lastState = state;
}

// XIII's text glyphs are 0xF1 then 0x40 + the button's place in its prompt
// order: confirm, cancel, face left, face top, menu. Keyed by pad mask bit
int GlyphFor(int bit) {
    switch (bit) {
    case 5: return 0;
    case 7: return 1;
    case 6: return 2;
    case 4: return 3;
    case 15: return 4;
    default: return -1;
    }
}

// what the pause bar holds, for the log: the same two virtual hops the mode
// setter takes to each slot's label, and the text pointer its text box keeps
using VirtualFn = void*(__fastcall*)(void*, void*);

void* Virtual(void* object, int offset) {
    if (!object) return nullptr;
    auto* table = *static_cast<VirtualFn**>(object);
    return table[offset / 4](object, nullptr);
}

void LogSlots(const char* when) {
    auto* slots = g_pauseWidget ? *reinterpret_cast<unsigned char**>(g_pauseWidget + 0x34) : nullptr;
    if (!slots) return;
    for (int slot : {4, 8, 0xC, 0x10}) {
        void* component = *reinterpret_cast<void**>(slots + slot);
        if (!component) {
            Trace("  %s slot +%#x: none", when, slot);
            continue;
        }
        auto* label = static_cast<unsigned char*>(Virtual(Virtual(component, 0x10), 0x44));
        const char* text = label ? *reinterpret_cast<const char**>(label + 0xE0 + 0x1A8) : nullptr;
        char shown[160]{};
        int at = 0;
        for (int i = 0; text && text[i] && i < 40 && at < 150; ++i) {
            const unsigned char c = static_cast<unsigned char>(text[i]);
            at += c >= 0x20 && c < 0x7F ? std::snprintf(shown + at, sizeof shown - at, "%c", c)
                                        : std::snprintf(shown + at, sizeof shown - at, "<%02X>", c);
        }
        Trace("  %s slot +%#x: component %p vt %p label %p text %p \"%s\"%s", when, slot, component,
            *static_cast<void**>(component), label, text, shown, text == g_saveLabel ? " (ours)" : "");
    }
}

unsigned short& TextFlags(unsigned char* text) { return *reinterpret_cast<unsigned short*>(text + 0xC); }

// the pause bar is one string, "$pause_03": "[Back]Quit   [Start]Resume",
// right-aligned. The other label, "$pause_01", is a lone "[Start]Resume"
// sitting right on top of that string's tail, which is where everything we
// put there before ended up drawn. So Save goes on the front of the bar's own
// string, three spaces like the game's, and its layout spaces all three.
// The text is set once, in the game's own pass
void __fastcall TextSetIdHook(unsigned char* text, void* edx, const char* id, int arg) {
    if (g_pauseWidget && g_idLines < 24 && GetTickCount64() - g_pauseSetAt < 50) {
        ++g_idLines;
        Trace("  text id \"%s\"%s", id ? id : "(null)", g_relabel ? " (relabelling)" : "");
    }
    if (!g_relabel || !id) return g_textSetId(text, edx, id, arg);
    // bit 1 is "owns a text resource": a fresh string never does, and ours
    // never should, since the destructor frees what that bit says it owns
    if (std::strcmp(id, "$pause_03") == 0) {
        // the mode setter reuses one string for both labels, so this one
        // still holds Resume's resource. The game's own lookup lets that go
        // and takes the bar's; the bar's is copied, then let go the way the
        // destructor would, and the string reset for ours
        g_textSetId(text, edx, id, arg);
        const char* bar = (TextFlags(text) & 1)
            ? *reinterpret_cast<const char**>(text + 4) + *reinterpret_cast<short*>(text + 0xE)
            : nullptr;
        if (!bar) return;
        std::snprintf(g_barLabel, sizeof g_barLabel, "%s   %s", g_saveLabel, bar);
        g_textDtor(text, nullptr);
        g_textCtor(text, nullptr);
        *reinterpret_cast<const char**>(text + 4) = g_barLabel;
        *reinterpret_cast<const char**>(text + 8) = id;
        *reinterpret_cast<unsigned short*>(text + 0xE) = 0;
        // the state the game's own direct table lookup leaves: valid, not owned
        TextFlags(text) = (TextFlags(text) | 1) & ~2;
        g_relabelled = 1;
        return;
    }
    g_textSetId(text, edx, id, arg);
}

// the setter skips the rebuild when the mode hasn't changed, so with Save on
// the screen last time, or wanted this time, it's forced to rebuild from the
// game's own text ids first. Save only goes on in a field pause, with the
// station this save would load at in what's loaded
void __fastcall PauseModeHook(unsigned char* widget, void* edx, int mode) {
    const int state = State();
    const bool want = g_enabled && g_prompt && g_phase == Phase::Idle &&
                      (state == kStateFieldPause || state == kStateFieldPauseWait) && !Withheld();
    if (want || g_decorated) *reinterpret_cast<int*>(widget + 0x70) = -1;
    g_relabel = want && mode != 0;
    g_relabelled = 0;
    // the widget is only kept for the traces that read the bar back
    const bool logging = g_debug && g_pauseLogLines < 8;
    if (logging) {
        g_pauseWidget = widget;
        g_pauseSetAt = GetTickCount64();
        Trace("pause labels: widget %p mode %d (was %d), state %d, want %d", widget, mode,
            *reinterpret_cast<int*>(widget + 0x70), state, want);
    }
    g_pauseMode(widget, edx, mode);
    g_relabel = false;
    g_decorated = g_relabelled == 1;
    if (logging) {
        ++g_pauseLogLines;
        Trace("  relabelled %d, save %s", g_relabelled, g_decorated ? "shown" : "not shown");
        LogSlots("set");
        g_pauseLooks = 2;
    }
}

LONGLONG g_costMax = 0, g_costSum = 0;
int g_costFrames = 0;
ULONGLONG g_costSince = 0;

void Cost(LONGLONG ticks) {
    g_costMax = std::max(g_costMax, ticks);
    g_costSum += ticks;
    ++g_costFrames;
    const ULONGLONG now = GetTickCount64();
    if (!g_costSince) g_costSince = now;
    if (now - g_costSince < 60000) return;
    LARGE_INTEGER rate;
    QueryPerformanceFrequency(&rate);
    const double us = 1e6 / static_cast<double>(rate.QuadPart);
    Log("cost over the last minute: %.1f us a frame on average, %.1f us at most, %d frames",
        g_costSum * us / g_costFrames, g_costMax * us, g_costFrames);
    g_costMax = g_costSum = 0;
    g_costFrames = 0;
    g_costSince = now;
}

// runs for every pad, every frame. Presses from any of them count, and the
// frame's logic runs once, on whichever pad the game updated first
void __fastcall PadUpdateHook(void* self, void* edx, void* arg) {
    g_padUpdate(self, edx, arg);
    auto* pad = static_cast<unsigned char*>(self);
    if (!g_leadPad) g_leadPad = self;
    if (g_debug && g_lastState == kStateFieldPause && g_phase == Phase::Idle) LearnButtons(pad);
    if (g_swallow == 0) g_pressed = g_pressed || Pressed(pad);
    g_heldLastFrame = g_heldLastFrame || ((Mask(pad, kPadHeld) >> g_button) & 1);
    if (self == g_leadPad) {
        if (g_debug) {
            // what the add-on costs a frame, for anyone wondering if it's the lag
            LARGE_INTEGER a, b;
            QueryPerformanceCounter(&a);
            Tick();
            QueryPerformanceCounter(&b);
            Cost(b.QuadPart - a.QuadPart);
        } else {
            Tick();
        }
    }
    if (g_swallow > 0) Swallow(pad);
    // Start for one frame, held and pressed, on the pad the frame's logic
    // runs on. Next frame the hardware says it's up and it reads as a tap
    if (g_pressStart && self == g_leadPad) {
        g_pressStart = false;
        constexpr DWORD start = 1u << 15;
        Mask(pad, kPadHeld) |= start;
        Mask(pad, kPadPressed) |= start;
        Mask(pad, kPadRepeat) |= start;
        Mask(pad, kPadRepeatToo) |= start;
    }
}

void ReadSettings() {
    const std::wstring ini = g_dir + L"\\xiii-save-anywhere.ini";
    g_enabled = GetPrivateProfileIntW(L"SaveAnywhere", L"Enabled", 1, ini.c_str()) != 0;
    g_button = static_cast<int>(GetPrivateProfileIntW(L"SaveAnywhere", L"SaveButton", 4, ini.c_str()));
    if (g_button < 0 || g_button > 15) g_button = 4;
    wchar_t key[8]{};
    GetPrivateProfileStringW(L"SaveAnywhere", L"SaveKey", L"E", key, 8, ini.c_str());
    // one letter or digit, as printed on the key. Virtual key codes for those
    // are their uppercase ASCII
    const wchar_t k = static_cast<wchar_t>(std::towupper(key[0]));
    g_saveKey = (k >= L'A' && k <= L'Z') || (k >= L'0' && k <= L'9') ? k : 'E';
    g_prompt = GetPrivateProfileIntW(L"SaveAnywhere", L"Prompt", 1, ini.c_str()) != 0;
    g_debug = GetPrivateProfileIntW(L"SaveAnywhere", L"Debug", 0, ini.c_str()) != 0;
    const int glyph = GlyphFor(g_button);
    if (glyph >= 0) std::snprintf(g_saveLabel, sizeof g_saveLabel, "\xF1%cSave", 0x40 + glyph);
    else std::snprintf(g_saveLabel, sizeof g_saveLabel, "Save");
}

bool Matches(DWORD rva, const unsigned char* bytes, size_t size) {
    return std::memcmp(reinterpret_cast<const void*>(g_base + rva), bytes, size) == 0;
}

bool Verify() {
    g_base = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
    wchar_t exe[MAX_PATH]{};
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    const wchar_t* name = std::wcsrchr(exe, L'\\');
    if (!name || _wcsicmp(name + 1, L"ffxiiiimg.exe") != 0) {
        Log("not XIII's exe, staying off");
        return false;
    }
    struct Site {
        DWORD rva;
        const unsigned char* bytes;
        size_t size;
    };
    const Site sites[] = {
        {kPadUpdateRva, kPadUpdateHead, sizeof kPadUpdateHead},
        {kGetStateRva, kGetStateHead, sizeof kGetStateHead},
        {kZoneFireRva, kZoneFireHead, sizeof kZoneFireHead},
        {kGetSavePointRva, kGetSavePointHead, sizeof kGetSavePointHead},
        {kFindSavePointRowRva, kFindSavePointRowHead, sizeof kFindSavePointRowHead},
        {kResolveActorRva, kResolveActorHead, sizeof kResolveActorHead},
        {kLockRva, kLockHead, sizeof kLockHead},
        {kUnlockRva, kUnlockHead, sizeof kUnlockHead},
        {kPlayerHandleRva, kPlayerHandleHead, sizeof kPlayerHandleHead},
        {kGroundRayRva, kGroundRayHead, sizeof kGroundRayHead},
        {kSetMatrixRva, kSetMatrixHead, sizeof kSetMatrixHead},
        {kZoneLoadRva, kMovEcxMem, sizeof kMovEcxMem},
        {kPauseModeRva, kPauseModeHead, sizeof kPauseModeHead},
        {kTextSetIdRva, kTextSetIdHead, sizeof kTextSetIdHead},
        {kTextCtorRva, kTextCtorHead, sizeof kTextCtorHead},
        {kTextDtorRva, kTextDtorHead, sizeof kTextDtorHead},
        {kSaveWriteRva, kSaveWriteHead, sizeof kSaveWriteHead},
        {kSaveReadRva, kSaveReadHead, sizeof kSaveReadHead},
        {kMarkerLookupRva, kMarkerLookupHead, sizeof kMarkerLookupHead},
        {kAreaEnterRva, kAreaEnterHead, sizeof kAreaEnterHead},
        {kRestartSaveRva, kRestartSaveHead, sizeof kRestartSaveHead},
        {kStartMarkerRva, kStartMarkerHead, sizeof kStartMarkerHead},
        {kStoryRva, kStoryHead, sizeof kStoryHead},
        {kEventFlagRva, kEventFlagHead, sizeof kEventFlagHead},
        {kFieldUiRva, kFieldUiHead, sizeof kFieldUiHead},
        {kMenuMaskRva, kMenuMaskHead, sizeof kMenuMaskHead},
        {kMenuMaskBitsRva, kMenuMaskBitsHead, sizeof kMenuMaskBitsHead},
        {kWorkRva, kWorkHead, sizeof kWorkHead},
        {kZoneFlagRva, kZoneFlagHead, sizeof kZoneFlagHead},
        {kAreaOnRva, kAreaOnHead, sizeof kAreaOnHead},
        {kAreaFadeRva, kAreaFadeHead, sizeof kAreaFadeHead},
        {kAreaOffRva, kAreaOffHead, sizeof kAreaOffHead},
        {kObjectRva, kObjectHead, sizeof kObjectHead},
        {kBgAnimRva, kBgAnimHead, sizeof kBgAnimHead},
        {kLayoutRva, kLayoutHead, sizeof kLayoutHead},
        {kEncountRva, kEncountHead, sizeof kEncountHead},
        {kMapSetRva, kMapSetHead, sizeof kMapSetHead},
        {kRetryRva, kRetryHead, sizeof kRetryHead},
        {kCreateCharaRva, kCreateCharaHead, sizeof kCreateCharaHead},
        {kReleaseCharaRva, kReleaseCharaHead, sizeof kReleaseCharaHead},
        {kTalkOnRva, kTalkOnHead, sizeof kTalkOnHead},
        {kTalkOffRva, kTalkOffHead, sizeof kTalkOffHead},
        {kPathEdgeRva, kPathEdgeHead, sizeof kPathEdgeHead},
        {kCutsceneRva, kCutsceneHead, sizeof kCutsceneHead},
        {kCharaSetInRva, kLoaderHead, sizeof kLoaderHead},
        {kCharaSetOutRva, kLoaderHead, sizeof kLoaderHead},
        {kCharaInRva, kLoaderHead, sizeof kLoaderHead},
        {kCharaOutRva, kLoaderHead, sizeof kLoaderHead},
    };
    // SteamStub decrypts the code before the game's entry point, and NovaFix
    // loads us after that, but waiting on the bytes costs nothing if not
    for (int tries = 0;; ++tries) {
        bool all = true;
        for (const Site& site : sites) all = all && Matches(site.rva, site.bytes, site.size);
        if (all) break;
        if (tries == 200) {
            for (const Site& site : sites)
                if (!Matches(site.rva, site.bytes, site.size)) Log("unexpected bytes at %#lx", site.rva);
            Log("this isn't the Steam build this was made for, staying off");
            return false;
        }
        Sleep(50);
    }
    g_getState = reinterpret_cast<GetStateFn>(g_base + kGetStateRva);
    g_zoneFire = reinterpret_cast<ZoneFireFn>(g_base + kZoneFireRva);
    g_getSavePoint = reinterpret_cast<GetSavePointFn>(g_base + kGetSavePointRva);
    g_findSavePointRow = reinterpret_cast<FindSavePointRowFn>(g_base + kFindSavePointRowRva);
    g_resolveActor = reinterpret_cast<ResolveActorFn>(g_base + kResolveActorRva);
    g_lock = reinterpret_cast<LockFn>(g_base + kLockRva);
    g_unlock = reinterpret_cast<UnlockFn>(g_base + kUnlockRva);
    g_actorRegistry = reinterpret_cast<unsigned char**>(g_base + kActorRegistryRva);
    g_playerHandle = reinterpret_cast<PlayerHandleFn>(g_base + kPlayerHandleRva);
    g_startMarker = reinterpret_cast<StartMarkerFn>(g_base + kStartMarkerRva);
    g_fieldStart = reinterpret_cast<void**>(g_base + kFieldStartRva);
    g_story = reinterpret_cast<StoryFn>(g_base + kStoryRva);
    g_party = *reinterpret_cast<unsigned char***>(g_base + kStoryRva + 10);
    g_menu = *reinterpret_cast<unsigned char***>(g_base + kMenuMaskRva + 1);
    g_textCtor = reinterpret_cast<TextThisFn>(g_base + kTextCtorRva);
    g_textDtor = reinterpret_cast<TextThisFn>(g_base + kTextDtorRva);
    g_zoneSlot = *reinterpret_cast<void***>(g_base + kZoneLoadRva + 2);
    return true;
}

bool Hook(LPVOID target, LPVOID detour, LPVOID* original) {
    return MH_CreateHook(target, detour, original) == MH_OK && MH_EnableHook(target) == MH_OK;
}

template <typename Fn>
bool HookAt(DWORD rva, Fn detour, Fn& original) {
    return Hook(reinterpret_cast<LPVOID>(g_base + rva), reinterpret_cast<LPVOID>(detour),
                reinterpret_cast<LPVOID*>(&original));
}

void LetGo(const float* at, const char* why) {
    g_holding = false;
    Log("let go of her at %.2f %.2f %.2f after %d frames (%s). The controller wanted her up to %.2f m off on the way "
        "in and %.2f m off on the spot", at[0], at[1], at[2], g_holdFrames, why, g_holdPullWay, g_holdPullSpot);
    // Debug: two more seconds of who moves her, to see walking keep her on the floor
    if (g_debug) {
        g_heightTraceUntil = GetTickCount64() + 2000;
        g_watchUntil = g_heightTraceUntil;
        g_watchLines = 0;
    }
}

// the controller's write of her through the ease-in, at the height she walked
// wherever it has her along the walk in, and into its own state so the next
// frame starts from there. Left alone the ease-in runs her at the marker's
// height, or the level floor's under the marker, whatever's under her: Lake
// Bresha's waves aren't that floor, and down one in Ch 3 it ended 0.9 m into
// the ice, up one 0.2, and she walked out under the level (Nov 26). False
// once it lets go, and then the write goes through untouched
bool HoldToWalk(const float* m, float* out) {
    const Vec4& spot = g_holdWalk.at[g_holdWalk.count - 1];
    float side = 0, left = 0;
    float y = WalkHeight(g_holdWalk, m[12], m[14], &side, &left);
    const float off = std::hypot(m[12] - spot.v[0], m[14] - spot.v[2]);
    // an ease-in that stops a hair short still counts, and one that runs out
    // of time near enough gets pinned where it is rather than let go to settle
    if (g_holdAtSpot < 3 && GetTickCount64() > g_holdUntil && off < 0.3f) {
        g_holdAtSpot = 3;
        Log("the ease-in stopped %.2f m off the spot, keeping her at its height there", off);
    }
    if (g_holdAtSpot >= 3) {
        // and on the spot till she walks off it. Standing, it settles her
        // toward the floor under where it eased her to, so letting go there
        // sank her 1.38 m into a wave she'd walked up (0.25.35, Nov 26).
        // Walking follows the floor she's on
        y = spot.v[1];
        if (off > 0.3f) {
            LetGo(m + 12, "she walked off the spot");
            return false;
        }
        g_holdPullSpot = std::max(g_holdPullSpot, std::fabs(m[13] - y));
    } else if (side > 0.5f || left < -0.5f) {
        // the stick: past here it's her walking, not the ease-in
        LetGo(m + 12, "she walked off the walk in");
        return false;
    } else if (GetTickCount64() > g_holdUntil) {
        LetGo(m + 12, "the ease-in never got her near the spot");
        return false;
    } else {
        g_holdPullWay = std::max(g_holdPullWay, std::fabs(m[13] - y));
    }
    std::memcpy(out, m, 16 * sizeof(float));
    out[13] = y;
    // only when it's the controller's own numbers coming through, which they always were
    if (auto* ctrl = *reinterpret_cast<unsigned char**>(g_base + kFieldPlayerRva)) {
        auto* at = reinterpret_cast<float*>(ctrl + kControllerAt);
        if (std::fabs(at[0] - m[12]) < 0.01f && std::fabs(at[2] - m[14]) < 0.01f) {
            at[1] = y;
            *reinterpret_cast<float*>(ctrl + kControllerAimY) = y;
        }
    }
    ++g_holdFrames;
    // a few writes on the spot, so its own last step down to it is ours too
    if (g_holdAtSpot < 3 && (left <= 0.02f || off < 0.05f) && ++g_holdAtSpot == 3)
        Log("held the ease-in to her walk in for %d frames, on the spot at %.2f %.2f %.2f till she moves", g_holdFrames,
            out[12], out[13], out[14]);
    return true;
}

int __fastcall SetMatrixHook(void* self, void* edx, const float* m, int warp, int lock) {
    if (!m || self != g_watchActor.load()) return g_setMatrix(self, edx, m, warp, lock);
    const DWORD from = static_cast<DWORD>(reinterpret_cast<std::uintptr_t>(__builtin_return_address(0)) - g_base);
    float held[16];
    if (g_holding && from == kControllerWriteRva && HoldToWalk(m, held)) m = held;
    if (GetTickCount64() < g_watchUntil.load()) {
        static DWORD lastFrom = 0;
        static float last[3]{};
        const bool moved = std::fabs(m[12] - last[0]) + std::fabs(m[13] - last[1]) + std::fabs(m[14] - last[2]) > 0.05f;
        // a new writer always, the same one each 5 cm it moves her
        if ((from != lastFrom || moved) && g_watchLines++ < 120) {
            lastFrom = from;
            std::memcpy(last, m + 12, sizeof last);
            Log("    set from %#lx: %.2f %.2f %.2f, warp %d", from, m[12], m[13], m[14], warp);
        }
    }
    return g_setMatrix(self, edx, m, warp, lock);
}

bool HookWorld() {
    return HookAt(kAreaOnRva, &AreaOnHook, g_areaOn) && HookAt(kAreaFadeRva, &AreaFadeHook, g_areaFade) &&
           HookAt(kAreaOffRva, &AreaOffHook, g_areaOff) && HookAt(kObjectRva, &ObjectHook, g_object) &&
           HookAt(kBgAnimRva, &BgAnimHook, g_bgAnim) && HookAt(kLayoutRva, &LayoutHook, g_layout) &&
           HookAt(kEncountRva, &EncountHook, g_encount) && HookAt(kMapSetRva, &MapSetHook, g_mapSet) &&
           HookAt(kRetryRva, &RetryHook, g_retry) && HookAt(kCreateCharaRva, &CreateCharaHook, g_createChara) &&
           HookAt(kReleaseCharaRva, &ReleaseCharaHook, g_releaseChara) &&
           HookAt(kTalkOnRva, &TalkOnHook, g_talkOn) && HookAt(kTalkOffRva, &TalkOffHook, g_talkOff) &&
           HookAt(kPathEdgeRva, &PathEdgeHook, g_pathEdge) && HookAt(kCutsceneRva, &CutsceneHook, g_cutscene) &&
           HookAt(kCharaSetInRva, &CharaSetInHook, g_charaSetIn) &&
           HookAt(kCharaSetOutRva, &CharaSetOutHook, g_charaSetOut) && HookAt(kCharaInRva, &CharaInHook, g_charaIn) &&
           HookAt(kCharaOutRva, &CharaOutHook, g_charaOut);
}

// the log appends every session, so past 1 MB the old one steps aside as .1
// (replacing the one before it) and this session starts a fresh file
void RotateLog() {
    const std::wstring log = g_dir + L"\\xiii-save-anywhere.log";
    WIN32_FILE_ATTRIBUTE_DATA info{};
    if (!GetFileAttributesExW(log.c_str(), GetFileExInfoStandard, &info)) return;
    if (info.nFileSizeHigh == 0 && info.nFileSizeLow < 1024 * 1024) return;
    MoveFileExW(log.c_str(), (log + L".1").c_str(), MOVEFILE_REPLACE_EXISTING);
}

DWORD WINAPI Worker(LPVOID) {
    RotateLog();
    Log("xiii_save_anywhere 0.25.45, pid %lu", GetCurrentProcessId());
    if (!Verify()) return 0;
    ReadSettings();
    Tidy();
    LoadXInput();
    if (MH_Initialize() != MH_OK ||
        !Hook(reinterpret_cast<LPVOID>(g_base + kSaveWriteRva), reinterpret_cast<LPVOID>(&SaveWriteHook),
              reinterpret_cast<LPVOID*>(&g_saveWrite)) ||
        !Hook(reinterpret_cast<LPVOID>(g_base + kSaveReadRva), reinterpret_cast<LPVOID>(&SaveReadHook),
              reinterpret_cast<LPVOID*>(&g_saveRead)) ||
        !Hook(reinterpret_cast<LPVOID>(g_base + kMarkerLookupRva), reinterpret_cast<LPVOID>(&MarkerLookupHook),
              reinterpret_cast<LPVOID*>(&g_markerLookup)) ||
        !Hook(reinterpret_cast<LPVOID>(g_base + kAreaEnterRva), reinterpret_cast<LPVOID>(&AreaEnterHook),
              reinterpret_cast<LPVOID*>(&g_areaEnter)) ||
        !Hook(reinterpret_cast<LPVOID>(g_base + kRestartSaveRva), reinterpret_cast<LPVOID>(&RestartSaveHook),
              reinterpret_cast<LPVOID*>(&g_restartSave)) ||
        !Hook(reinterpret_cast<LPVOID>(g_base + kPadUpdateRva), reinterpret_cast<LPVOID>(&PadUpdateHook),
              reinterpret_cast<LPVOID*>(&g_padUpdate)) ||
        !Hook(reinterpret_cast<LPVOID>(g_base + kPauseModeRva), reinterpret_cast<LPVOID>(&PauseModeHook),
              reinterpret_cast<LPVOID*>(&g_pauseMode)) ||
        !Hook(reinterpret_cast<LPVOID>(g_base + kTextSetIdRva), reinterpret_cast<LPVOID>(&TextSetIdHook),
              reinterpret_cast<LPVOID*>(&g_textSetId)) ||
        !HookWorld()) {
        // all or nothing: the input side without the save hooks would keep
        // spots it can never match to a slot
        MH_DisableHook(MH_ALL_HOOKS);
        Log("couldn't hook the game, save anywhere is off this run");
        return 0;
    }
    // not worth stopping for: without it a load still lands, the ease-in just
    // isn't held to the walk in
    if (!HookAt(kSetMatrixRva, &SetMatrixHook, g_setMatrix)) {
        g_setMatrix = nullptr;
        Log("couldn't hook her matrix, loads won't hold the ease-in to where she walked");
    }
    Log("ready, save is button %d or the %c key%s", g_button, g_saveKey, g_xinputGetState ? "" : " (no XInput)");
    return 0;
}

const NovaFixPluginDescriptor kDescriptor = [] {
    NovaFixPluginDescriptor d{};
    d.structSize = sizeof d;
    d.apiVersion = NOVAFIX_PLUGIN_ABI_VERSION;
    d.id = "armyofone.xiii-save-anywhere";
    d.name = "Save Anywhere";
    d.author = "KarpTheMagikarp";
    d.version = "0.25.45";
    d.description = "Save from the field pause screen, and load back where you stood.";
    return d;
}();

}  // namespace

NOVAFIX_PLUGIN_EXPORT const NovaFixPluginDescriptor* NOVAFIX_PLUGIN_CALL
NovaFixPlugin_Query(const NovaFixPluginHost*) {
    return &kDescriptor;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(module);
    InitializeCriticalSection(&g_fileLock);
    InitializeCriticalSection(&g_worldLock);
    wchar_t path[MAX_PATH]{};
    GetModuleFileNameW(module, path, MAX_PATH);
    g_dir = path;
    g_dir.resize(g_dir.find_last_of(L"\\/"));
    DeleteFileW((g_dir + L"\\xiii-save-anywhere.log").c_str());
    // never in the loader lock: MinHook suspends threads to patch
    HANDLE thread = CreateThread(nullptr, 0, Worker, nullptr, 0, nullptr);
    if (thread) CloseHandle(thread);
    return TRUE;
}
