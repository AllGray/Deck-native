// Mario Kart World 1.8.0's game profile as nxnative's dm/games/game.py would generate it (same macro names, so the ported files read it unchanged).
// Values from BananaNative's dm/games/0400C3F00006E000/game.tsv; keys that profile does not set take game.py's defaults.
#pragma once

#define DM_GAME_ID "0400C3F00006E000"
#define DM_GAME_NAME "Mario Kart World"
#define DM_GAME_PROGRAM_ID 0x0400C3F00006E000ull
#define DM_GAME_MODULES {"main", "subsdk0", "subsdk1", "sdk"}
#define DM_GAME_NMOD 4
#define DM_GAME_MEM_RESERVE 0x300000000ull   // mem_reserve: 256 MB in front + 4 GB alias + 256 MB stack + 7.75 GB heap region
#define DM_GAME_MEM_ALIAS 0x100000000ull     // mem_alias: the SDK's node area (0x3fe00000) and the regions the game maps physical memory into
#define DM_GAME_MEM_STACK 0x10000000ull      // mem_stack
#define DM_GAME_APP_POOL 0x240000000ull      // app_pool: the Switch 2's 9 GB
#define DM_GAME_MEM_USED 0x4000000ull        // mem_used: game.py's default
#define DM_GAME_SYS_RESOURCE 0x4000000ull    // sys_resource: the NPDM's 64 MB, virtual address memory on
#define DM_GAME_ARGS ""
#define DM_GAME_ENV ""
#define DM_GAME_MEM_MAP_LIMIT 0
#define DM_GAME_STDALLOC_CAP 0x0ull
#define DM_GAME_CORE_PREEMPT 0
#define DM_GAME_ALLOC_BIG_ALIGN 0
// The build the profile knows (main's build ID, first 16 bytes)
#define DM_GAME_BUILD_180 "d00e4f399e411d0613aa8d26b1c02fef"
// Read by the ports of hle_oe.c, hle_sdk16.c and hle_hid.c (values as game.py generates them for this profile)
#define DM_GAME_ROMFS_MARKER "System/Resource/ResourceSizeTable.Product.100.Nin_Ounce_NVN2.rsizetable.zs"
#define DM_GAME_DISPLAY_VERSIONS {"d00e4f399e411d0613aa8d26b1c02fef", "1.8.0"}
#define DM_GAME_PAD_LAYOUT 1                 // pad_layout: full
#define DM_GAME_LOOK_RATE 0.0f               // no pointer look in this game
#define DM_GAME_LOOK_DEAD 0.5f
#define DM_GAME_LOOK_CAM ""
#define DM_GAME_OPMODE 1                     // op_mode: console (game.py's default)
#define DM_GAME_PERF_BOOST 0                 // perf_mode: follow
