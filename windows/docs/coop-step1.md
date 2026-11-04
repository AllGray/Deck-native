# Co-op step 1: Link Play between two instances, with our own module

2026-11-03. Step 1 of [COOP-PLAN](COOP-PLAN.md): the shared module in `src/coop/` (MIT, our own code), unit tests on Windows and the Mac, the
Mac research host wired to it, and two game instances on the rented Mac joining each other through Link Play → Local connection over loopback.
Evidence and design background: [research/06](../research/06-multiplayer.md) (§3.2 has the measured join).

## Result

| | status |
|---|---|
| Module (`ldn` + `vnet` + `mpnet`), portable C11, Winsock and BSD sockets | done, 1,576 lines |
| Unit tests (host and stations in one process over loopback, UDP and TCP-only data paths) | **pass** on Windows (clang 23.1.4, MSVC runtime) and the Mac (Apple clang 21, plain `cc`): 1,061 checks |
| Mac research host (`~/mp/nxnative-mp`) routed to the module under `DM_COOP` | done; `DM_MPFAKE=1` without `DM_COOP` still hosts as in step 0 |
| Host instance: Invite Others → Local | **works**: "You're now connected!", hosting menu, back in town while hosting |
| Visitor instance: Visit a Friend → Local | **scan works**: "Choose a Destination" lists the host's town; "Do you want to head to this destination?" shows Visitors 0/3 |
| LDN connect and the game's own UDP traffic | **works**: Connect succeeds and the two games exchange their own datagrams through the module |
| Visitor arrives in the host's town | **yes, with two different saves** (2026-11-04): the visitor travels to the host's town, both screens show both characters, movement syncs. With two copies of **one** save the host's game refuses the visitor by design: application error 9005 = `KTGL_ONLINE_E_APPLICATION_REJECT_JOIN_SESSION_EXIST_SAME_USER_ID_USER`, keyed on a 16-byte user ID stored in the save (see [Why one save is refused](#why-one-save-is-refused)). Nothing in the module was at fault |

## Design

```
 game (unchanged) ──nn::ldn────▶ coop_ldn.c   state machine, the 0x480 NetworkInfo, scan / connect over the link
                  ──nn::socket─▶ coop_vnet.c  virtual UDP on 169.254.S.0/24, broadcast fan-out, per-socket queues
                                     │
                                     ▼
                                 coop_mpnet.c  TCP control link per station + UDP game datagrams, star around the host
                                 coop_plat.c   Winsock / BSD sockets, Win32 / pthreads, time, randomness
```

The HLE glue stays per port; the module never sees guest-specific types. Public API: `src/coop/coop.h`. Each process makes one node
(`coop_env_node`), tests make several. Every entry point takes the node's lock; each node runs one I/O thread.

### `ldn` (coop_ldn.c)

- **States** as nn::ldn: None 0, Initialized 1, AccessPoint 2, AccessPointCreated 3, Station 4, StationConnected 5. Results are the SDK's
  (module 203): InvalidState 0x40CB, ConnectNotFound 0x82CB, ConnectTimeout 0x84CB, ConnectRejected 0x86CB, VersionTooLow 0xE2CB,
  VersionTooHigh 0xE4CB, TooManyPlayers 0x120CB, InvalidArgument 0xC0CB; Initialize answers DeviceDisabled 0x2CCB if the transport cannot
  start (port taken).
- **Calls:** Initialize, Finalize, GetState, Open/CloseAccessPoint, Create/DestroyNetwork, SetAdvertiseData (≤ 384 bytes), Open/CloseStation,
  Scan (ScanFilter flags LocalCommunicationId, SessionId, NetworkType, Bssid, Ssid, SceneId), Connect, Disconnect, GetNetworkInfo,
  GetIpv4Address, GetNetworkConfig, GetSecurityParameter, GetDisconnectReason, SetStationAcceptPolicy (RejectAll refuses joins), Reject.
- **State callback:** `hooks.on_state(state)` fires on every state change and every NetworkInfo change (join, leave, advertise data), outside
  the lock. The Mac glue signals the event the game attached, as nn::ldn does; Pokopia attaches none and polls.
- **The host builds NetworkInfo** from its own state: IntentId as the game gave it (`-1` passes through; `cfg.lcid` can replace it), a random
  SessionId, Ssid = the SessionId in hex, Bssid = node 0's MAC, channel 1 when the game asks for automatic, LinkLevel 3, NetworkType 2,
  random ServerRandom and Challenge, SecurityMode from the SecurityConfig, NodeCountMax from the NetworkConfig, one NodeInfo per node (IPv4,
  MAC `02:h:h:h:S:id+1` from a hash of the Uid, node id, connected, the UserConfig name, Platform 1, the node's LocalCommunicationVersion),
  and the advertise data untouched. Stations get it through SCAN_RESP, ACCEPT and SYNC.
- **Addresses:** subnet byte S = 1 + SessionId[0] mod 254; node k is 169.254.S.(k+1), mask 255.255.255.0. Host-order u32, as nn::ldn keeps them.
- **Connect** sends the scanned NetworkId, the UserConfig, the version argument and a 64-bit FNV-1a hash of the SecurityConfig (the passphrase
  itself never leaves the node). The host refuses another NetworkId (ConnectNotFound), another passphrase hash or a RejectAll policy
  (ConnectRejected), a lower or higher LocalCommunicationVersion (VersionTooLow / VersionTooHigh), a full network (TooManyPlayers). It does
  not compare Uids: duplicate users are the game's own check.
- **ACCEPT waits for the host's game** (at most 300 ms) until it has read a NetworkInfo that lists the new station. KTGL identifies senders by
  its member table, and the visitor sends its first datagram as soon as Connect returns. The host polls every frame, so this costs one frame.
- **Disconnect reasons:** Disconnect → DisconnectedByUser 1; DestroyNetwork, CloseAccessPoint or the host's Finalize → DestroyedByUser 3 on the
  stations; Reject → Rejected 5; a lost link → SignalLost 6. The station falls back to Station 4.

### `vnet` (coop_vnet.c)

- Virtual descriptors 0x100–0x11F per node. Only `Socket(AF_INET, SOCK_DGRAM[|NonBlock], 0 or 17)` made while the LDN is initialized is
  virtual; everything else gets ENETDOWN, as before. Bind only records the port (INADDR_ANY or the node's own address), so every instance
  binds 49530 on one machine.
- SendTo: a station's address or the host's, 169.254.S.255 or 255.255.255.255. Anything outside the /24 is ENETUNREACH, so the game reaches
  session members only. Broadcasts reach every other node, never the sender. SO_BROADCAST is recorded and logged, not enforced.
- Datagrams ≤ 4,096 bytes (EMSGSIZE above); ≤ 256 queued per socket, the oldest dropped. RecvFrom fills the sender's virtual address and port
  (BSD sockaddr_in, sin_len 16); empty with MSG_DONTWAIT or a non-blocking socket → EAGAIN (35). MSG_PEEK works.
- Poll: POLLIN when a datagram waits, POLLOUT always. Select over Horizon's 1024-bit fd_set. Waits go through `hooks.sleep_ns` in 1 ms slices
  (the HLE's own guest sleep), with nxnative's 100 ms cap for infinite timeouts. GetSockOpt (SO_TYPE, SO_ERROR, SO_BROADCAST), GetSockName,
  Fcntl (F_GETFL / F_SETFL O_NONBLOCK).
- FreeBSD errno values throughout; results are `-errno` and the glue sets the HLE's last error.

### `mpnet` (coop_mpnet.c): why TCP control plus UDP data, and no ENet

- **Control:** one TCP link from each station to the host, length-prefixed frames: HELLO / WELCOME (magic, protocol 1, the station's Uid and
  platform; the host's UDP port), SCAN_REQ / SCAN_RESP, JOIN / ACCEPT / REFUSE, SYNC, LEAVE, KICK, PING / PONG every second; a link silent for
  10 s is lost. TCP gives ordering, reliability and loss detection for free on every platform, and these messages are rare.
- **Game datagrams:** UDP between each station and the host. The game has its own resends and ordering (KTGL's RESEND_COUNT,
  COMMUNICATION_QUALITY), so the transport must not add head-of-line stalls. A station proves its UDP endpoint with the 64-bit token from
  ACCEPT (UHELLO / UACK, 100 ms retries for 3 s) and uses its TCP link for datagrams until then; `cfg.tcp_only` keeps them on TCP for good
  (firewalls; the second unit-test pass).
- **Star:** stations send everything to the host; the host delivers to its own sockets and relays to the stations a datagram is for,
  rewriting the source to the sender's virtual address.
- **No ENet:** no new third-party downloads in this step. TCP + UDP covers what research/06 §5.4 wanted from ENet (connections, keepalive,
  a reliable control channel, an unreliable data channel) in about 400 lines. Fragmentation is not needed: datagrams stay ≤ 4,124 bytes.
  Encryption and NAT traversal are later steps.
- **Step 1 binds and dials 127.0.0.1 only**; DM_COOP refuses any join address outside 127.0.0.0/8. Nothing leaves the machine.
- **Threads:** one I/O thread per node owns accepting, dialing, reading, timers and closing. Game threads only append to a link's send buffer
  (flushed at once when the socket takes it) and send UDP directly, under the node lock. Packets are assembled in a per-node buffer, not on
  the game thread's stack (KTGL's threads may have small stacks).

### Configuration

- `DM_COOP=host:<port>` listens on 127.0.0.1:<port> (TCP control, UDP datagrams on the same number); `DM_COOP=join:127.0.0.1:<port>` dials it.
  The station keeps dialing every 500 ms while its LDN is initialized, so either side may start first.
- `DM_COOP_TCP=1` (step 1b) sets `tcp_only`: game datagrams over the control link, for a TCP-only path such as an SSH tunnel.
- `DM_UID=<32 hex digits>` (first 16 digits = Uid word 0) gives the instance its own `nn::account::Uid` when DM_COOP is set. Unset: nxnative's
  constant, logged. Pokopia does not need it: its same-user check uses the save's own user ID ([Why one save is refused](#why-one-save-is-refused)).
- Logs (`[coop]` lines on the Mac): state changes, links, joins and refusals, every SetSockOpt, the first 8 datagrams per socket and
  direction with their first 192 bytes in hex, then every 500th.

## Unit tests (`src/coop/tests/coop_test.c`)

A host and three stations in one process over loopback, through the API the hooks call, run twice (UDP data path, then `tcp_only`). The host
game's frame loop is a thread reading GetNetworkInfo every 5 ms. Covered: configuration parsing (loopback only, Uid parsing, the invalid Uid);
the game's own call order (socket and bind before the network); CreateNetwork and NetworkInfo fields; Scan finds the host's NetworkInfo with
its advertise data, other scenes and sessions are filtered out; VersionTooLow, VersionTooHigh, a wrong passphrase (ConnectRejected), a stale
session (ConnectNotFound), TooManyPlayers; unicast both ways with the sender's virtual address; station to station through the relay;
broadcast from the host and from a station, never heard by the sender; a 4,096-byte datagram, EMSGSIZE, ENETUNREACH outside the /24 and for
non-members; Poll and Select readiness; queue overflow keeping the newest 256; MSG_PEEK; advertise-data updates reaching stations with a state
callback; Disconnect; Reject; DestroyNetwork; the host's Finalize; a host that never reads its NetworkInfo (ACCEPT after 300 ms); the host
coming back on the same port; the UDP endpoints proven and the data path counters.

Windows (separate build directory, 4 jobs, low priority; the toolchain as `scripts/build.cmd`):

```
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set PATH=C:\Program Files\LLVM\bin;C:\msys64\mingw64\bin;%PATH%
cmake -S src\coop -B build\coop -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang
start "" /b /low /wait cmake --build build\coop -j 4
ctest --test-dir build\coop --output-on-failure
```

```
1/1 Test #1: coop.loopback ....................   Passed    4.21 sec
100% tests passed out of 1
```

The Mac (no CMake there), from a copy of `src/coop` in `~/mp/coop-test/coop`:

```
cc -std=c11 -D_DARWIN_C_SOURCE -O1 -g -Wall -Wextra -Wno-unused-parameter -pthread -Icoop \
   coop/coop_ldn.c coop/coop_vnet.c coop/coop_mpnet.c coop/coop_plat.c coop/tests/coop_test.c -o coop_tests && ./coop_tests
== game datagrams over UDP
== game datagrams over the control links (tcp_only)
1061 checks, 0 failed
```

The top-level `CMakeLists.txt` also adds `src/coop`, so the full build builds and runs it with the other tests.

## Mac integration (research copy only)

`research/mac-coop/apply_mp_patch.py` (a copy lives in `~/mp/macsrc/` with `coop/`, the module) extends step 0's patch. It is idempotent and
gives the same tree from a fresh copy of `~/nxnative-trace` or from step 0's patched copy (checked by diffing both):

- copies the module in as `dm/host/hle_coop_{ldn,vnet,mpnet,plat}.c` plus `coop.h` and `coop_int.h` (the Makefile's `hle_*.c` wildcard);
- replaces `hle_offline.c`'s ldn section with `ldn_coop.c`: every hook calls the module when DM_COOP is set, else step 0's lone access point
  (DM_MPFAKE=1), else the cut;
- puts `sock_coop.c` after `sk_fail` in `hle_net.c` and replaces `dm_sk_poll` / `dm_sk_select` (`sock_poll.c`). Socket, Bind, Close,
  GetSockName, Get/SetSockOpt, Fcntl, Recv, RecvFrom, Send (typed and `nnsocket*` forms, `hle_net.c`) and SendTo, RecvFrom, GetLastErrno and
  the SDK 11 plain overloads (`hle_offline.c`) go through `mpsk_*`: the module under DM_COOP, step 0's lone node under DM_MPFAKE, else
  ENETDOWN;
- `hle_account.c`: `k_uid` becomes `DM_UID` under DM_COOP;
- `hle_misc.c`: pctl "allowed" under DM_MPFAKE or DM_COOP;
- `hle_mptrace.c` (step 0's tracer) also dumps `err::ShowApplicationError`'s argument (code and message texts).

```
cd ~/mp && python3 macsrc/apply_mp_patch.py ~/mp/nxnative-mp ~/mp/macsrc
cd ~/mp/nxnative-mp/dm/host && DM_GAMES_DIR=$HOME/installer-test/build201-clean/profile make GAME=0400A3A01EE4C000 dmhost
```

The module compiles there without warnings. The step 0 binary is kept as `dmhost_step0`.

## The two-instance test

Runs `~/mp/runs/coop1{h,v}`, `coop2{h,v}`, `coop3{h,v}` (different Uids) and `coop3s` (the visitor with the host's Uid), each booting its own
copy of `~/mp/saves/level2-1103`; `coop4f` is the DM_MPFAKE regression; `coop5`–`coop7` give the visitor a save with another user ID (below).
Traces (SecurityConfig cut to its first 4 bytes) and screenshots: `_local/mp/mac/coop1/` (game-derived, not in git).

### Measured join sequence with two copies of one save (coop3; coop1 and coop2 agree)

Host: Link Play → Invite Others → Local → "Would you like to set a Link Code?" No → "Please choose how you want to play." (3 dialogs) →
Withered Wasteland Settings (Four, Spectator On, locked) → Plus.
1. `pctl::TryBeginFreeCommunication(true)` → allowed; `ldn::Initialize`; `Socket(AF_INET, DGRAM|NonBlock)`; `Bind(INADDR_ANY:49530)`;
   `RecvFrom` (EAGAIN); `OpenAccessPoint`; `CreateNetwork` (NodeCountMax 4, LocalCommunicationVersion 301); `SetAdvertiseData` (377 B);
   `GetIpv4Address` (169.254.S.1); `GetNetworkInfo`; `SetAdvertiseData` (379 B). "You're now connected!" (`h_connected`).
2. Every frame: `RecvFrom`, `GetNetworkInfo`, `GetState`. A then B B returns to the town, still hosting (`h_town`).

Visitor: Link Play → Visit a Friend → tutorial (B) → Local.
3. `TryBeginFreeCommunication` → allowed; `Initialize`; `Socket`; `Bind(49530)`; `OpenStation`; `Scan` ×3, each finding the host's
   NetworkInfo; `CloseStation`. "Choose a Destination" lists the host's town (`v_list`).
4. A: "Do you want to head to this destination?", Visitors 0/3 (`v_head`). Yes: two Spectator Mode notices ("When playing with others in
   this town, everyone will be restricted to playing in Spectator Mode." / "Certain moves and items can't be used while playing in Spectator
   Mode.").
5. `OpenStation`; `GetNickname`; `Connect(info, sec, user, 301, 0)`. The host's game reads a 2-node NetworkInfo, ACCEPT goes out, Connect → 0.
   `GetIpv4Address` (169.254.S.2); `GetNetworkInfo` (2 nodes); `GetNickname` ×2; `SendTo(169.254.S.1:49530, 174 B)`.
6. Host game: `RecvFrom` → 174 B from 169.254.S.2:49530; `SendTo(169.254.S.2:49530, 66 B)`.
7. Visitor: `RecvFrom` → 66 B from 169.254.S.1:49530; `SendTo(host, 44 B)`; `Disconnect`; `CloseStation`;
   `err::ShowApplicationError`: code **9005** (0x232D), en-US, "Communication error. Please wait and try again. If this error appears
   repeatedly, please check your connection." On screen: **"A communication error occurred."** (`v_result`), then back to the destination
   list. The host's game keeps hosting and shows nothing.

### Why one save is refused

**The error, decoded statically (2.0.1 main):**
- **Error display:** `err::ShowApplicationError` is called from main+0x1bce544. That function turns a KTGL error word into the application error
  number: (bits 20–27 > 10 → 9000) + (bits 8–11 × 100) + the low byte.
- **Descriptors:** each `KTGL_ONLINE_E_*` error has a 12-byte name getter followed by a 12-byte code getter (`mov`/`movk`/`ret`). Scanning main
  for these pairs gives 218 name↔code pairs (`_local/mp/coop1b/errtab.txt`).
- **The one behind 9005:** exactly one maps to 9005: **0x87F7F005 `KTGL_ONLINE_E_APPLICATION_REJECT_JOIN_SESSION_EXIST_SAME_USER_ID_USER`**
  (code getter main+0x66a844). It is an "application" (category 0x7F) reject, which the game sends back to a joiner. Its neighbours are
  FULL_MEMBER (9003), REJECT_JOIN_SESSION_UGC_CHANGE_OWNER (9004) and REJECT_JOIN_SESSION_OTHER_ERROR (9006).

**The exchange, decoded from that:**
1. visitor → host, 174 bytes: the join request;
2. host → visitor, 66 bytes: the host game's reject, reason "a user with the same user ID is already in the session";
3. visitor → host, 44 bytes: the visitor's leave.

The bodies stay opaque. Main imports only SHA-1 and SHA-256 from the SDK's `nn::crypto` (8 symbols); no AES or HMAC. The 318 crypto functions
are the SDK's own exports. So KTGL's packet protection lives inside main, and hooking the SDK's crypto would not show plaintext. The decoded
error made that unnecessary.

**The "user ID" is a 16-byte value in the save:**
- **Where it lives:** the 16-byte per-save value from the host's advertise data (research/06 §3.1) is stored in `S00/SYDAT.BIN` at offset
  0x61. That file is 2,009 bytes, with no checksum; its first word `87762231` is a magic every save file shares. main also holds save-field
  names `m_userID`, `m_userIDVer2` and `m_MyNPLNUserID`.
- **The account `Uid` is not it.** In coop3s (one save, the host's own Uid) the visitor was refused just as in coop1–3 (one save, different
  Uids). In coop6 (different save IDs, the host's own Uid) the visitor joined.
- **Experiments:**

| run | visitor's save | visitor's Uid | result |
|---|---|---|---|
| coop1–3 | copy of `level2-1103` (same ID as the host) | different | refused, 9005 |
| coop3s | copy of `level2-1103` | the host's | refused, 9005 |
| coop5 | `~/mp/saves-derived/level2-1103-uid9b`: a copy with **one bit** of that ID flipped, nothing else | different | **arrives** in the host's town; ~3,500 datagrams each way, movement syncs |
| coop6 | the same derived copy | the host's | **arrives** |
| coop7 | **`~/mp/saves/player2-level2`**, the second Level 2 save (its own ID; another character) | different | **arrives**; both screens show both characters, movement syncs |

- **What a second save must differ in:** the 16-byte user ID in `S00/SYDAT.BIN` at 0x61.
  - A separately started save has its own (player2-level2's differs).
  - A copy of someone else's save keeps theirs and is refused by that player's host.
  - This applies to one player's own devices too: a save synced between the Mac and the iPhone gives both the same ID.
  - To compare two saves: `xxd -s 0x61 -l 16 S00/SYDAT.BIN`. Keep the bytes local; they identify the save.
- **No fault in our layer:** the NetworkInfo, platform byte, MAC/IP, channel, LocalCommunicationId −1, the missing GetSecurityParameter, timing,
  ordering and source rewriting were all unchanged between the refused and the successful runs.

**No broadcast-relay mode:** no `SetSockOpt` and no broadcast, in the refused handshakes and in the full sessions of coop5 and coop7 (thousands of
datagrams each way). Every datagram is unicast to port 49530, with no 4-byte 169.254 destination header (research/06 §2.4).

**The host loses a visitor cleanly:** killing the visitor's process (coop5) drops its link; the host removes node 1, its game reads a 1-node
NetworkInfo and keeps playing (`coop5h/h_lost.jpg`).

### Screenshots (`_local/mp/mac/coop1/`)

- host connected: `coop1h/h_p2.jpg`, `coop2h/h_connected.jpg`; host in town while hosting: `coop3h/h_town.jpg`, `coop7h/h_town.jpg`
- visitor's scan finding the host: `coop1v/v_scan3.jpg`, `coop3v/v_list.jpg`; the join prompt: `coop1v/v_join3.jpg`, `coop7v/v_head.jpg` (Visitors 0/3)
- Spectator notices: `coop1v/v_go3.jpg`, `coop1v/v_go4.jpg`
- one save, refused: `coop1v/v_go5.jpg`, `coop3v/v_result.jpg`, `coop3s/v_result.jpg`
- **the visitor in the host's town:** traveling `coop5v/v_g4.jpg`; arrived `coop5v/v_g5.jpg` + `coop5h/h_g5.jpg`, `coop5h/h_s7.jpg` (coop6),
  **`coop7v/v_arr.jpg` + `coop7h/h_arr.jpg`** (the second save); moving `coop7v/v_move.jpg` + `coop7h/h_move.jpg`
- DM_MPFAKE regression: `coop4f/f_connected.jpg`

## Open issues

1. **Product:** every player needs their own save; two instances of one save cannot meet. Saves synced between one player's devices count as
   the same player.
2. Error 9005's exact sender-side condition (which field the host compares, `m_userID` or `m_userIDVer2`) was not traced in code. The save
   experiment pins it to the value at `SYDAT.BIN`+0x61.
3. Not exercised with the game yet: Reject, a host leaving with visitors, member-to-member relay (two visitors), broadcasts, the TCP fallback
   path. The unit tests cover them.
4. The Platform byte (1) and channel (1) are guesses that the game accepted for scanning and Connect; MACs and the Ssid are ours.
5. LocalCommunicationId stays `-1` in NetworkInfo (as step 0, and as Ryujinx does unless its backend needs the real ID); `cfg.lcid` can switch.
6. No discovery, encryption, compatibility key or NAT handling yet (step 1c); `join:` accepts loopback only.

## Reproduce

```
# PC: unit tests (see above). Then on the Mac:
cd ~/mp && python3 macsrc/apply_mp_patch.py ~/mp/nxnative-mp ~/mp/macsrc
cd ~/mp/nxnative-mp/dm/host && DM_GAMES_DIR=$HOME/installer-test/build201-clean/profile make GAME=0400A3A01EE4C000 dmhost
cd ~/mp
SAVE=~/mp/saves/level2-1103 nohup ./ia.sh coop3h 1800 ~/installer-test/build201-clean ~/mp/nxnative-mp/dm/host/dmhost \
  DM_COOP=host:47600 DM_UID=4f9c2a71d03e8b5617ac94e2b8d05c3f DM_MPTRACE=1 DM_NICK=Host &
SAVE=~/mp/saves/player2-level2 nohup ./ia.sh coop3v 1800 ~/installer-test/build201-clean ~/mp/nxnative-mp/dm/host/dmhost \
  DM_COOP=join:127.0.0.1:47600 DM_UID=9a3e60c4f1b7d2852e0f4c9b7a16d3e8 DM_MPTRACE=1 DM_NICK=Visitor &   # its own save: arrives
# (with SAVE=~/mp/saves/level2-1103 for the visitor too, the host refuses it: error 9005)
B="wait:30 A wait:4 A wait:4 A wait:4 A wait:4 A wait:4 A wait:4 A wait:4 A wait:4 wait:10"
./k.sh coop3h $B & ./k.sh coop3v $B; wait
./k.sh coop3h LLeft~0.2 wait:0.5 LUp~0.15 wait:1 A wait:3 Down wait:0.5 A wait:4 A wait:3 Down wait:0.4 A wait:3 Down wait:0.4 A wait:5 \
  A wait:3 A wait:2 A wait:2 Plus wait:6 shot:h_connected A wait:3 B wait:3 B wait:3 shot:h_town
./k.sh coop3v LLeft~0.2 wait:0.5 LUp~0.15 wait:1 shot:v_face      # the "Check" prompt must show before A
./k.sh coop3v A wait:3 Down wait:0.5 A wait:4 Down wait:0.5 A wait:3 B wait:2 Down wait:0.4 A wait:7 shot:v_list \
  A wait:2 shot:v_head A wait:2 A wait:2 A wait:4 shot:v_result
grep '\[coop\]' runs/coop3h/host.log runs/coop3v/host.log
```

After `v_result`, wait about 20 s for the travel screen. The arrival shows both characters at the PC on both screens.

The stick nudge toward the PC is timing-sensitive:
- with step 0's `LLeft~0.15 LUp~0.12` the visitor of coop2 did not turn;
- player2-level2's character needed `LLeft~0.3 LUp~0.2`.

So check for the "Check" prompt in `v_face` before pressing A. If Down is missed in the connection menu, Online gets picked and its notice
and 6-character code keyboard appear (cancelled); B back out.

## Windows hookup (next co-op step, 1b)

Done in step 1b: [coop-step1b.md](coop-step1b.md). The plan as written before it:

The nxrt hook files are ports of nxnative's and mirror the Mac files, so the hookup is the same routing as `research/mac-coop/`, written
into them directly instead of patched. Not done in this step.

- **Build:** `target_link_libraries(nxrt_core PRIVATE coop)`; the `coop` target already links `ws2_32`.
- **`src/nxrt/hle/hle_offline.c`, ldn section (lines 18–47):** each `h_ldn_*` calls `coop_ldn_*` on `coop_env_node(&hooks)` when it is
  non-NULL, else today's cut: Initialize, Finalize, GetState, OpenStation, CloseStation, OpenAccessPoint, CloseAccessPoint, Disconnect,
  DestroyNetwork, Scan, ScanPrivate, CreateNetwork, Connect, GetNetworkInfo, GetIpv4Address, GetSecurityParameter, SetAdvertiseData,
  GetDisconnectReason, SetStationAcceptPolicy (`p & 0xFF`), Reject, GetNetworkConfig. AttachStateChangeEvent keeps the bind and remembers the
  event; `hooks.on_state` calls `hle_signal_bound_system_event` on it; `hooks.sleep_ns` is `dm_guest_sleep_ns`; `hooks.log` is the run log.
- **`hle_offline.c`, socket overloads:** GetLastErrno (the thread-local error from `hle_net.c`), `Socket(int,int,int)`, `Bind(sockaddr)`, both
  SendTo forms, `RecvFrom(…sockaddr…)`, GetSockName2, GetSockOpt2, SetSockOpt2, Fcntl2, Recv2, Send2; Poll and Select2 already go to
  `dm_sk_poll` / `dm_sk_select`.
- **`src/nxrt/hle/hle_net.c`:** Socket, Bind, Close, GetSockName, GetSockOpt, SetSockOpt, Fcntl, Recv, RecvFrom, Send in the typed and
  `nnsocket*` forms call `coop_vnet_*` when `coop_vnet_is(fd)` (Socket: when the node exists), else `sk_fail()`; a negative result sets
  `t_skerr = -r` and returns -1. `dm_sk_poll` / `dm_sk_select` hand descriptor sets that are all virtual to `coop_vnet_poll` /
  `coop_vnet_select` (a non-virtual descriptor ≥ 0 stays ENETDOWN). Fcntl gets its variadic third argument as a third `int` (it arrives in x2);
  a changed signature means rerunning `gen/gen_hle.py` for the adapters.
- **`src/nxrt/hle/hle_account.c`:** `k_uid` (line 10) becomes `DM_UID` through `coop_env_uid` when DM_COOP is set.
- **`src/nxrt/hle/hle_misc.c`:** CheckFreeCommunicationPermission, TryBeginFreeCommunication and IsFreeCommunicationAvailable (lines 177,
  178, 186) answer allowed when DM_COOP is set.
- **Winsock:** `coop_create` calls `WSAStartup` itself (counted, paired with `WSACleanup` in `coop_destroy`), so nxrt needs no init of its own.
  The UDP socket turns off `SIO_UDP_CONNRESET` (a departed peer's ICMP would fail later receives); the listener uses `SO_EXCLUSIVEADDRUSE`.
  `Sleep` granularity (15.6 ms without a raised timer resolution) only lengthens the module's waits, which go through `dm_guest_sleep_ns`.
- **Firewall:** loopback (step 1) never prompts. Hosting on a LAN address later makes Windows Defender Firewall ask on the first inbound
  TCP/UDP; it must be allowed on Private networks, a Public profile blocks it silently (detect and explain). Joining is outbound only and
  needs no rule; so does the relay mode.
