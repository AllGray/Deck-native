// Pokémon Pokopia's game profile as nxnative's dm/games/game.py would generate it (same macro names, so the ported files read it unchanged).
// Values from installer\profile\0400A3A01EE4C000\game.tsv; keys the profile does not set take game.py's defaults.
#pragma once

#define DM_GAME_ID "0400A3A01EE4C000"
#define DM_GAME_NAME "Pokemon Pokopia"
#define DM_GAME_PROGRAM_ID 0x0400A3A01EE4C000ull
#define DM_GAME_MODULES {"main", "subsdk0", "sdk"}
#define DM_GAME_NMOD 3
#define DM_GAME_MEM_RESERVE 0x2c0000000ull   // mem_reserve: the 11 GB guest block
#define DM_GAME_MEM_ALIAS 0x10000000ull      // mem_alias
#define DM_GAME_MEM_STACK 0x10000000ull      // mem_stack
#define DM_GAME_APP_POOL 0x280000000ull      // app_pool: Pokopia asks for total minus ~2.1 GiB and panics below ~7.9 GiB
#define DM_GAME_MEM_USED 0x5600000ull        // mem_used
#define DM_GAME_SYS_RESOURCE 0x0ull          // sys_resource: no virtual address memory
#define DM_GAME_ARGS ""
#define DM_GAME_ENV ""
#define DM_GAME_MEM_MAP_LIMIT 0
#define DM_GAME_STDALLOC_CAP 0x0ull
#define DM_GAME_CORE_PREEMPT 0
#define DM_GAME_ALLOC_BIG_ALIGN 0
// The two builds the profile knows (main's build ID, first 16 bytes)
#define DM_GAME_BUILD_105 "816c5358e8178d802fcec4a7ce5a741a"
#define DM_GAME_BUILD_201 "76a489f3bd3642fa5e18bd83d32c98ba"
// Read by the ports of hle_oe.c, hle_sdk16.c and hle_hid.c (values as game.py generates them for this profile)
#define DM_GAME_ROMFS_MARKER "motor_asset_re/KIDS_PLATFORM_OUNCE/0x38f972af.fdata"
#define DM_GAME_DISPLAY_VERSIONS {"816c5358e8178d802fcec4a7ce5a741a", "?"}, {"76a489f3bd3642fa5e18bd83d32c98ba", "2.0.1"}
#define DM_GAME_PAD_LAYOUT 0
#define DM_GAME_LOOK_RATE 110.0f
#define DM_GAME_LOOK_DEAD 0.52f
#define DM_GAME_LOOK_CAM "816c5358e8178d80 34513d0 b0 b4 e4 e8 121/bfff;76a489f3bd3642fa 3561948 b0 b4 e4 e8 121/bfff"
#define DM_GAME_OPMODE 1                     // op_mode: console (game.py's default; the Mac oracle runs docked)
#define DM_GAME_PERF_BOOST 0                 // perf_mode: follow
