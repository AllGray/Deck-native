# Co-op step 1b: the Windows hookup, and Windows ↔ Mac

2026-11-04. Step 1b of [COOP-PLAN](COOP-PLAN.md): nxrt's hooks call the shared module `src/coop/` (step 1, [coop-step1.md](coop-step1.md)), so the
Windows build hosts and joins Link Play "Local connection" sessions. Tested with two Windows instances on one PC and, through an SSH tunnel, with the
Mac research host in both directions. In the working tree for review (nothing committed).

## Result

| | status |
|---|---|
| nxrt's `nn::ldn`, socket, pctl and account hooks routed to the module under `DM_COOP` | done; without `DM_COOP` every hook answers exactly as before |
| `DM_COOP_TCP=1`: game datagrams over the control link (the module's `tcp_only`, now reachable from the environment) | done (module: 4 lines in `coop_env_node`) |
| `play.ps1` / `play.cmd`: `-CoopHost <port>`, `-CoopJoin 127.0.0.1:<port>`, `-CoopTcp` | done |
| Tests | 3 new (`nxrt.coop_off`, `nxrt.coop_host`, `nxrt.coop_join`, 169 checks); the suite is **42/42** with `NXW_DUMP` set |
| Two Windows instances, loopback | **the visitor arrives in the host's town**; both screens show both characters; movement syncs; killing the visitor drops it cleanly from the host's network |
| Windows visitor → Mac host (SSH `-L`, `DM_COOP_TCP=1`, ~145 ms RTT) | **arrives**; movement syncs both ways; no timeouts |
| Mac visitor → Windows host (SSH `-R`; the Mac binary has no `DM_COOP_TCP`, so its UDP proof fails and it stays on TCP) | **arrives**; movement syncs. This is also the first game run of the module's TCP fallback path |

## What was wired (mirrors `research/mac-coop/`)

- **`src/nxrt/hle/hle_offline.c`, ldn section.**
  - `dm_coop()` makes the process's node on first use through `coop_env_node` (NULL without a valid `DM_COOP`). Hooks: `log` → the run log (`[coop]` lines),
    `sleep_ns` → `dm_guest_sleep_ns`, `on_state` → `hle_signal_bound_system_event` on the event the game attached.
  - Every `h_ldn_*` calls `coop_ldn_*` when the node exists, else today's cut. That covers Initialize, Finalize, GetState, Open/CloseStation,
    Open/CloseAccessPoint, Disconnect, DestroyNetwork, Scan, ScanPrivate, CreateNetwork, Connect, GetNetworkInfo, GetIpv4Address,
    GetSecurityParameter, SetAdvertiseData, GetDisconnectReason, SetStationAcceptPolicy (`p & 0xFF`), Reject and GetNetworkConfig.
  - AttachStateChangeEvent binds the event as before and remembers it. CreateNetworkPrivate, ConnectPrivate and AddAcceptFilterEntry stay cut.
- **`hle_net.c`.** `mpsk_*` routing after `sk_fail`:
  - a descriptor `coop_vnet_is` accepts goes to `coop_vnet_*`; a negative result sets `t_skerr` and returns -1;
  - Socket goes to `coop_vnet_socket` when the node exists (only AF_INET datagram sockets after `ldn::Initialize` become virtual);
  - everything else is `sk_fail()` (-1, ENETDOWN), as today.
  - Socket, Bind, Close, GetSockName, Get/SetSockOpt, Fcntl, Recv, RecvFrom and Send route in the typed and `nnsocket*` forms.
  - `dm_sk_poll` / `dm_sk_select` hand a set whose descriptors are all virtual to `coop_vnet_poll` / `coop_vnet_select`. A non-virtual descriptor
    ≥ 0 still fails with ENETDOWN; an empty set still waits out the timeout.
- **`hle_offline.c`, socket overloads.** Under `DM_COOP` (only then, so the offline answers keep their exact form) these go through `mpsk_*`:
  GetLastErrno (the thread's last error), `Socket(int,int,int)`, `Bind(sockaddr)`, both SendTo, `RecvFrom(…sockaddr…)`, GetSockName2, Get/SetSockOpt2,
  Fcntl2, Recv2 and Send2.
- **Fcntl's third argument.** Fcntl is variadic; its third argument arrives in x2. All three forms gained `int v`. `gen/gen_hle.py` was rerun:
  - the adapters changed only for those three entries (`int a2 = (int)x[2]`);
  - `hle_registry.gen.c` changed only in the "why" texts;
  - `hle_inventory.tsv` changed the three signatures, and line numbers in the four files. No entry was added or removed, and no status changed,
    so the registry order and the game DLL's imports are unchanged.
- **`hle_misc.c`.** CheckFreeCommunicationPermission, TryBeginFreeCommunication and IsFreeCommunicationAvailable answer "allowed" only while the node
  exists (`DM_COOP` set and valid); otherwise 0, as before.
- **`hle_account.c`.** `k_uid` becomes `DM_UID` (through `coop_env_uid`) when both `DM_COOP` and `DM_UID` are set, else nxnative's constant. Pokopia
  does not compare it (coop-step1.md); it costs 12 lines and mirrors the Mac.
- **Build.** `nxrt_core` links `coop` (PRIVATE; `coop` brings `ws2_32`, and `coop_create` calls `WSAStartup` itself).
- **The module.** `coop_env_node` reads `DM_COOP_TCP` (non-empty and not `0` sets `tcp_only`) and logs it. The host needs nothing: it sends to a
  station over UDP only after that station's UHELLO proved its endpoint, so it follows a TCP-only station automatically.

## Running it

```
rem the host (its own save copy and work folder)
scripts\play.cmd -ExeFS <ExeFS> -Dll <game.dll> -RomFS <RomFS.img> -Save D:\pk\host-save -Work D:\pk\host-work -CoopHost 47600
rem the visitor: another save of its own (not a copy of the host's: the game refuses that with 9005)
scripts\play.cmd -ExeFS <ExeFS> -Dll <game.dll> -RomFS <RomFS.img> -Save D:\pk\visitor-save -Work D:\pk\visitor-work -CoopJoin 127.0.0.1:47600
```

- **In the game:**
  - Host: the Pokémon Center PC → Link Play → Invite Others to Visit → Local connection → Link Code: No → three A → + ("You're now connected!"),
    then A, B, B back to town.
  - Visitor: PC → Link Play → Visit a Friend → (tutorial: B) → Local connection → the host's town → Yes → two Spectator Mode notices (A, A) → about
    20 s of travel.
- **Another machine, for now:** forward a local port over SSH and join it with `-CoopTcp`, since `-L` / `-R` carry TCP only.
  - Visitor on Windows: `ssh -N -L 47600:127.0.0.1:47600 <host machine>`, then `-CoopJoin 127.0.0.1:47600 -CoopTcp`.
  - Host on Windows: the other machine runs `ssh -N -R 47600:127.0.0.1:47600 <this PC>`, or the PC runs that `-R` towards it.
  - Both ends stay bound to loopback; no port is opened to a network. Real LAN / internet addresses are step 1c.
- **Environment, without the scripts:** `DM_COOP=host:<port>` or `join:127.0.0.1:<port>`, optionally `DM_COOP_TCP=1`, `DM_UID=<32 hex>`, `DM_NICK=<name>`.
- **A second instance on one PC** needs its own `-Save` and `-Work`: the pipeline cache and logs live in `-Work`.

## Tests (`src/nxrt/tests/test_coop.c`)

Every call goes through the forced entry's generated adapter (`nxrt_hle_find(...)->adapter`), as the game's calls do. One process per mode, since
`DM_COOP` is read once:

| test | what it proves | checks |
|---|---|---:|
| `nxrt.coop_off` | no `DM_COOP`: pctl's three queries 0; the account Uid is the constant; ldn Initialize 0x2A2, GetState 0, Scan fails with 0 found, GetIpv4Address / GetNetworkInfo / OpenAccessPoint fail; every socket form (typed, plain, `nnsocket*`) -1 with ENETDOWN from all three error getters; Poll / Select with a descriptor -1, with nothing to wait on 0 | 37 |
| `nxrt.coop_host` | `DM_COOP=host:0`, `DM_UID` set: pctl allowed; the Uid from `DM_UID`; sockets offline before Initialize; the game's order (Socket, Bind, RecvFrom EAGAIN in all three error getters, OpenAccessPoint, CreateNetwork, SetAdvertiseData); Fcntl's third argument in all three forms (F_SETFL 0 / 4 read back with F_GETFL); SO_BROADCAST, SO_TYPE, GetSockName; a non-virtual descriptor (Bind, Poll) stays ENETDOWN. A station made with the module directly scans (it sees the advertise data set through the hook), connects (2 nodes through the hook), sends; the hook's Poll and plain RecvFrom read it with the sender's virtual address; both SendTo overloads reach the station; Select over Horizon's fd_set; Disconnect drops the host back to 1 node; Close, DestroyNetwork, CloseAccessPoint, Finalize | 82 |
| `nxrt.coop_join` | a host made with the module; `DM_COOP=join:127.0.0.1:<its port>`, `DM_COOP_TCP=1` set at run time: pctl allowed; Initialize, plain Socket / Bind, OpenStation, Scan (the advertise data), CloseStation / OpenStation, Connect (StationConnected, 169.254.S.2); the typed SendTo reaches the host; the plain-pollfd Poll and the typed RecvFrom read the host's answer; `coop_get_stats`: no UDP link, no UDP datagrams, TCP both ways (so `DM_COOP_TCP` reached the module); Disconnect (reason 1), CloseStation, Close, Finalize | 50 |

```
scripts\build.cmd            (4 jobs, low priority; build\Release keeps -DNXRT_PDB=ON)
set NXW_DUMP=<2.0.1 ExeFS>
ctest --test-dir build\Release --output-on-failure
...
23/42 Test #23: nxrt.coop_off ....................   Passed    0.05 sec
24/42 Test #24: nxrt.coop_host ...................   Passed    0.06 sec
25/42 Test #25: nxrt.coop_join ...................   Passed    0.06 sec
26/42 Test #26: nxrt.generated ...................   Passed    0.50 sec
...
42/42 Test #42: coop.loopback ....................   Passed    4.24 sec
100% tests passed out of 42
Total Test time (real) =  61.30 sec
```

The build adds no warnings: the 11 it prints are the existing `strdup` and `test_input.c` ones.

## Two Windows instances on this PC (loopback)

- **Setup:** `_local\m6-run.ps1`, below-normal priority, each with its own run folder, save copy and cache copy (`_local\coop1b\cache-{h,v}`, from
  `_local\m6-cache`).
  - **Host** `coop1b-h2`: a copy of `save-sync\backups\20261103-214949-level2`, `DM_COOP=host:47600`.
  - **Visitor** `coop1b-v2`: a copy of `...\20261104-001844-player2-level2`, `DM_COOP=join:127.0.0.1:47600`.
  - Both with different `DM_UID`s, `DM_FIXED_TIME=1793793600`, `TZ=UTC`.
- **The driver:** `_local\coop1b\k.py`, k.sh's token form over DM_CTL; `title` / `field` wait on m6drive's regions. The first attempt (`coop1b-h1/v1`)
  used the real clock. After 1 a.m. the title is the night variant, so m6drive's daytime reference never matched, and the runs idled at the title until
  their cap. The fixed clock fixes that; k.py's title threshold is also 25 now.
- **Timeline (host's log):**

| s | |
|---:|---|
| 101 | Link Play opens (pctl allowed); the node is made |
| 168 | `ldn::Initialize`, Socket + Bind 49530, OpenAccessPoint, CreateNetwork (NodeCountMax 4, version 301, 169.254.72.0/24), "You're now connected!" |
| 219 | the visitor's scanner link (the visitor's Scan) |
| 249 | node 1 joins ("Visitor", 301) as 169.254.72.2; ACCEPT after the host's game read the 2-node NetworkInfo; node 1 datagrams over UDP |
| 249–318 | the session: the host sent > 3,000 datagrams, received > 2,500 |
| 318 | the visitor's process killed: link closed, node 1 left, the host's game reads a 1-node NetworkInfo and keeps playing |

- **The join handshake** goes past step 1's refused point: visitor 174 B → host 162 + 42 + 20 B → visitor 44, 7, 33 B → … and the travel screen.
  With two saves of their own there is no 9005.
- **Performance:** both instances held 59.7 / 60.0 presents/s while connected.

Screenshots (`_local\coop1b\shots\`, game-derived, not in git; each `.png` has a half-size `.jpg`):

| | host (`h2\`) | visitor (`v2\`) |
|---|---|---|
| hosting | `h_connected` ("You're now connected!"), `h_town` (back in town, hosting) | |
| scan and join | | `v_list` (Choose a Destination: the host's town), `v_head` (Visitors 0/3), `v_go1` (Spectator notice), `v_go4` (travelling) |
| **arrival** | **`h_go5`** | **`v_go5`**: both characters at the PC on both screens |
| **after the visitor moves** | **`h_move2`** (the host's camera turned 150°: the visitor at the wall) | **`v_move`** |
| visitor gone | `h_lost` | |

## Windows ↔ Mac through an SSH tunnel

- **Mac side:** the research host (`~/mp/nxnative-mp/dm/host/dmhost`, unchanged since step 1; its module copy `~/mp/macsrc/coop` matched `src/coop` at
  HEAD byte for byte), driven with `~/mp/ia.sh` / `k.sh` and screenshots fetched by `_local\coop1b\mk.sh`.
- **Clock:** both ends ran with `DM_FIXED_TIME=1793793600 TZ=UTC`.
- **Saves:** `ia.sh` copies them into the run folder.
- **Link:** this PC to the rented Mac, ICMP RTT 144–147 ms.

**Windows visitor → Mac host** (Mac `win1h`: `DM_COOP=host:47600` on `~/mp/saves/level2-1103`; PC `ssh -N -L 47600:127.0.0.1:47600`; Windows
`coop1b-tv1`: a copy of player2-level2, `DM_COOP=join:127.0.0.1:47600`, `DM_COOP_TCP=1`):

| Windows log, s | |
|---:|---|
| 210.448 | the visitor's `ldn::Initialize`: dialing 127.0.0.1:47600 (the tunnel) |
| 210.757 | host link up: **309 ms**, about two round trips (SSH channel open, then HELLO / WELCOME) |
| 211.248 | Scan answered (inside the module's 500 ms window), CloseStation; the host's town is listed |
| 235.775 → 235.957 | OpenStation → Connect joined: **182 ms** |
| 236–353 | the session: > 5,500 datagrams delivered to the visitor, > 3,000 sent, all over the control link |

- **Result:** the visitor arrives; both screens show both characters. The visitor walked away and back, and the Mac's screen followed it both times.
- **Timing:** no timeout from the game or the module; KTGL's resends absorbed the ~150 ms.
- **Performance:** Windows stayed at 59.6–60.0 presents/s.
- **Leaving:** killing the Windows visitor drops it on the Mac host ("station link 0 down", "node 1 left", 1-node NetworkInfo).
- **Not measured:** how much later a move shows on the other screen.

**Mac visitor → Windows host** (Windows `coop1b-th1`: a copy of the level2 backup, `DM_COOP=host:47600`; PC `ssh -N -R 47600:127.0.0.1:47600`; Mac
`win1v`: `~/mp/saves/player2-level2`, `DM_COOP=join:127.0.0.1:47600`, no `DM_COOP_TCP`, which the Mac binary predates):

- **The UDP proof never passes.** The Mac station's UHELLOs went to its own 127.0.0.1:47600, where nothing listens, since `-R` carries no UDP.
  After 3 s it stayed on its TCP link for good, and the Windows host never logged "datagrams over UDP".
- **Result:** the visitor arrives; both screens show both characters; movement syncs.
- **Traffic:** the Mac visitor closed with 2,595 sent and 3,298 received.
- **So `DM_COOP_TCP` is a convenience, not a requirement**, for a TCP-only path: the fallback reaches the same place 3 s later.
- **Windows performance:** this run held 37–48 presents/s from its first minute on, well before Link Play opened at 118 s. Something else was
  loading the PC at the time; it was not the session.

Screenshots: Windows `_local\coop1b\shots\tv1\` and `th1\`; the Mac's as half-size JPEGs in `_local\coop1b\shots\mac-win1h\` and `mac-win1v\` (PNGs on the
Mac in `~/mp/runs/win1h`, `win1v`).

| | Windows | Mac |
|---|---|---|
| Win visitor → Mac host: hosting | | `mac-win1h\m_connected` |
| scan over the tunnel | `tv1\v_list` | |
| **arrival** | **`tv1\v_arr`** | **`mac-win1h\m_arr`** |
| **visitor moves** | **`tv1\v_move`, `v_move2`** | **`mac-win1h\m_move`, `m_move2`** |
| visitor gone | | `mac-win1h\m_lost` |
| Mac visitor → Win host: scan | | `mac-win1v\mv_list` |
| **arrival** | **`th1\h_arr`** | **`mac-win1v\mv_arr`** |
| **visitor moves** | **`th1\h_move`** | `mac-win1v\mv_move` |

Everything started was stopped: both tunnels (only those two ssh processes), the Windows instances and the Mac's two `dmhost` runs.

## Open issues

1. **Joining is still loopback only** (the module's parser, and `-CoopJoin`'s check). Another machine means an SSH tunnel today. Opening a real
   address, discovery, a compatibility key and encryption are step 1c.
2. **Not exercised with the game:** AttachStateChangeEvent (Pokopia attaches none), Reject, a host leaving with visitors, two visitors (member-to-member
   relay), broadcasts. The module's unit tests cover them; `nxrt.coop_*` covers the hook routing but not the event signal (binding needs a guest
   thread).
3. **The Mac research binary has no `DM_COOP_TCP`.** It works anyway through the 3 s fallback. Rebuilding it through `apply_mp_patch.py` with the new
   module would add it; that was not done.
4. **Latency:** only measured through one ~145 ms link, for a few minutes per direction. No packet loss was injected; KTGL's tolerance for worse links is
   unknown.
5. **`play.ps1` from the agent's shell:** `$PSScriptRoot` came out empty in `-Exe`'s default when the script ran under `powershell -File` there, so
   `-Exe` had to be given. This predates this step and was not investigated.
6. **The `[coop]` log:** it dumps the first 192 bytes of each socket's first 8 datagrams, as on the Mac. Run logs stay in `_local\` (not in git).

## Reproduce

```
scripts\build.cmd
ctest --test-dir build\Release            (NXW_DUMP set)
powershell -File _local\m6-run.ps1 -Name coop1b-h2 -Seconds 1500 -Ctl -Cache _local\coop1b\cache-h -Save <level2 backup> ^
  -Env @{ DM_COOP = "host:47600"; DM_UID = "4f9c2a71d03e8b5617ac94e2b8d05c3f"; DM_NICK = "Host"; DM_FIXED_TIME = "1793793600"; TZ = "UTC" }
powershell -File _local\m6-run.ps1 -Name coop1b-v2 -Seconds 1500 -Ctl -Cache _local\coop1b\cache-v -Save <player2-level2 backup> ^
  -Env @{ DM_COOP = "join:127.0.0.1:47600"; DM_UID = "9a3e60c4f1b7d2852e0f4c9b7a16d3e8"; DM_NICK = "Visitor"; DM_FIXED_TIME = "1793793600"; TZ = "UTC" }
K="python -I _local\coop1b\k.py"
%K% _local\nxrt-run\coop1b-h2\ctl.txt _local\coop1b\shots\h2 title wait:2 A field wait:4 LLeft~0.2 wait:0.5 LUp~0.15 wait:1 shot:h_face ^
  A wait:3 Down wait:0.5 A wait:4 A wait:3 Down wait:0.4 A wait:3 Down wait:0.4 A wait:3 A wait:2 A wait:2 A wait:2 Plus wait:6 shot:h_connected ^
  A wait:3 B wait:3 B wait:3 shot:h_town
%K% _local\nxrt-run\coop1b-v2\ctl.txt _local\coop1b\shots\v2 title wait:2 A field wait:4 LLeft~0.3 wait:0.5 LUp~0.2 wait:1 shot:v_face ^
  A wait:3 Down wait:0.5 A wait:4 Down wait:0.5 A wait:3 B wait:2 Down wait:0.4 A wait:8 shot:v_list A wait:2 A wait:2 A wait:2 A wait:30 shot:v_arr
```

- **The "Check" prompt:** check for it in `*_face` before the A. Player2-level2's character sometimes needs one more `LLeft~0.15`; it did through the
  tunnel run.
- **Ending a run:** `bash _local/coop1b/stop.sh <run>` ends that run's nxrt; a timed-out wait should end both instances.
- **The tunnel runs:** the same token lists, with `-L` / `-R` started first, `DM_COOP_TCP=1` on the Windows visitor, and `_local\coop1b\mk.sh <mac tag>
  <tokens>` for the Mac (`ia.sh win1h` / `win1v`, see coop-step1.md for `ia.sh`).
