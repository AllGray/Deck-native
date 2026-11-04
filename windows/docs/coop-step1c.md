# Co-op step 1c (first part): Link Play on the LAN, Windows and iPhone

2026-11-04. The first part of step 1c in [COOP-PLAN](COOP-PLAN.md): the shared module `src/coop/` joins real LAN addresses with protection, finds
hosts with DNS-SD, and runs in the Windows build (nxrt) and in pokopia-native's iPhone and Mac apps (nxnative). Built on steps 1 and 1b
([coop-step1.md](coop-step1.md), [coop-step1b.md](coop-step1b.md)); design background in [research/06](../research/06-multiplayer.md) §5.5–5.8.
In the working trees for review: nothing is committed in either repository.

## Result

| | status |
|---|---|
| LAN joining with protection: listening only while hosting, peers from loopback / RFC 1918 / 169.254 only, a build and mods check before a join, bounded parsing, rate limits, timeouts | done (`src/coop/`, 2,700 lines with DNS-SD) |
| DNS-SD discovery: Windows `DnsServiceRegister` / `DnsServiceBrowse`, Apple `dns_sd`, a no-op elsewhere; `DM_COOP=auto` | done; service type `_pokopia-ldn._udp` |
| Join by address: `DM_COOP=join:<LAN IPv4>[:<port>]` | done; on the iPhone through `Documents/env.txt` |
| Unit tests, including malformed, truncated, oversized and flooding input against a host and a fake host against a station | **1,327 checks pass** on Windows (clang 23.1.4) and on the Mac (Apple clang 21); ctest in `build\coop` 3/3, co-op tests in `build\c1c` 7/7 |
| Windows ↔ Windows on this PC over its LAN address (192.168.x.y, not loopback), two different saves | **the visitor arrives in the host's town**, found by DNS-SD and again by address; movement syncs; the host closing sends the visitor home and stops listening |
| Mac build of pokopia-native with the module (`~/c1c`) | builds without warnings from the module; two instances meet through DNS-SD; the host refuses the Mac's own public address |
| iPhone IPA, unsigned | `<work>\c1c-ipa\build\out\PokemonPokopia-unsigned.ipa` (500 MiB, 154 of 154 run-time shaders) |
| PC ↔ iPhone on the user's Wi-Fi | **not tested**: needs the user ([the test plan](#test-plan-pc--iphone-on-your-wi-fi)) |

## Configuration

| setting | what it does |
|---|---|
| `DM_COOP=auto` (`auto:<port>`) | hosts on every interface, port 47600, while the game hosts (CreateNetwork to DestroyNetwork / CloseAccessPoint / Finalize), and advertises it with DNS-SD; browses with DNS-SD while the game scans. 47600 taken (a second instance on one PC): the system picks a port, DNS-SD says which |
| `DM_COOP=join:<IPv4>[:<port>]` | dials that host only (port 47600 when left out). Loopback, RFC 1918 and 169.254/16 addresses only; anything else is refused with a line saying internet joining is not built |
| `DM_COOP=host:<port>` | step 1's loopback host (127.0.0.1 only), now also listening only while the game hosts: the SSH tunnel's end |
| `DM_COOP=off`, `0`, unset | Link Play stays offline; parental controls answer "not allowed" as before |
| `DM_COOP_TCP=1` | the game's datagrams over the control link (a TCP-only path), as in step 1b |
| `DM_COOP_DISCOVERY=0` | auto without DNS-SD: visitors join by address |
| `DM_UID=<32 hex>` | as in step 1b (nxrt's account Uid; two instances on one PC) |

- **Windows:** environment variables. `play.ps1` passes an inherited `DM_COOP` through when neither `-CoopHost` nor `-CoopJoin` is given:
  `$env:DM_COOP = "auto"` before `_local\play-local.ps1`. Flags for `auto` belong in `play.ps1`, which is M6 phase B's file now; not added.
- **iPhone and Mac apps:** the launcher's **Link Play on this network** switch (Game section; on the Mac among the advanced settings) sets
  `DM_COOP=auto` or `off`, off by default. It comes from nxnative's own settings list (`dm/games/settings.tsv`). `Documents/env.txt` overrides it
  (`DM_COOP=join:192.168.x.y`); the launcher then shows the switch greyed, "Set by an env file".

## Design as built

```
 game ──nn::ldn──▶ coop_ldn.c   state machine, NetworkInfo, Scan over every known host, Connect to the scanned one, the build and mods check
      ──nn::socket▶ coop_vnet.c  virtual UDP on 169.254.S.0/24 (unchanged)
                      │
                      ▼
                  coop_mpnet.c  host: a listener and a UDP socket while hosting; station: one link per known host; limits on everything received
                  coop_disc.c   DNS-SD: advertise while hosting, browse while scanning (Apple dns_sd, Windows DnsService*, none)
                  coop_plat.c   sockets, threads, the route address, Windows' network profile
```

### Listening only while hosting

- CreateNetwork opens the listener and the host's UDP socket, binding every interface for `auto`, 127.0.0.1 for `host:`. DestroyNetwork,
  CloseAccessPoint and Finalize send KICK to the joined stations, close every station link after its goodbye (≤ 300 ms), stop advertising, and
  close both sockets. The I/O thread closes sockets a game thread lets go only after its poll returns, so a descriptor is never reused under it.
- Initialize no longer opens anything. A station dials only while its LDN is initialized, and dials DNS-SD hosts only while browsing.
- Measured: after "Close the Connection" a connect to 192.168.x.y:47600 is refused (Windows run below); `coop.lan` and `coop.loopback` check
  that nothing listens before CreateNetwork and after DestroyNetwork.

### Who may connect

- Address classes (`coop_addr4_class`, `coop_addr6_class`): loopback; private (10/8, 172.16/12, 192.168/16; IPv6 fc00::/7); link-local
  (169.254/16; fe80::/10); everything else public, carrier NAT 100.64/10 included; 0/8, multicast and broadcast invalid. The transport is IPv4.
- Scope LAN accepts loopback, private and link-local; scope loopback only loopback. A refused TCP link is closed right after accept (its handshake
  completes in the kernel first), a refused UDP packet is dropped; both are counted, with one log line per 5 s.
- The bound address is every interface (one listener covers the Wi-Fi and Ethernet on a PC and follows address changes); the source check is what
  keeps the internet out. On the rented Mac, whose only address is public, connections from that address were refused (Mac run below).
- A visitor's UDP endpoint (UHELLO) must come from the same address as its control link.
- Internet join-by-address and a relay are not built. They would be another scope, chosen explicitly, with its own checks (encryption at least).

### Compatibility before a join (research/06 §5.6)

- **Identity:** main's build ID (first 16 hex digits; hooks.build) and a 64-bit fingerprint of the active gameplay mods (hooks.mods; 0 = none).
  nxrt has no cheats: its fingerprint is 0. pokopia-native hashes the names of the cheats that are on (`hle_cheats.c`, when its master switch is on).
  Camera and input patches are not mods.
- **Host:** checks JOIN in order: the network (NotFound), the passphrase hash and accept policy (Rejected), LocalCommunicationVersion
  (VersionTooLow / VersionTooHigh, the game's own check), the build and the mods (Rejected, with a `compat:` line naming both sides), room
  (TooManyPlayers). Both are self-reported: a compatibility check, not anti-cheat.
- **Station:** WELCOME and every SCAN_RESP carry the host's identity; a host whose build or mods differ is asked at each scan but not listed (one
  line when it changes), a host speaking another coop protocol is not dialled again. The mods are read again before each scan and Connect.

### Untrusted input

Every frame, packet and record from the network is length-checked before it is read; a malformed frame closes its link and is counted.

| limit | value |
|---|---|
| frame length | 1 … 0x4000; unread bytes per link ≤ one frame + 8 KiB (the buffer cannot be grown by a peer) |
| HELLO / WELCOME | within 3 s of connecting; a short one, another magic or another protocol closes the link (the other protocol is told ours first) |
| new links | 20 a second; ≤ 4 per address still owing their HELLO; 16 links |
| frames per link | 2,000 a second (burst 4,000); beyond, datagrams are dropped, other frames ignored |
| game datagrams per link | 1,000 a second and 4 MiB a second (bursts twice that), TCP and UDP together; the game sends about 50 a second per player |
| SCAN_REQ / JOIN | 20 / 5 a second per link; a JOIN past that is refused (ConnectRejected) unread |
| send buffer | 4 MiB; a peer that stops reading is dropped |
| silence | 10 s ends a link (PING every second) |
| UDP | ≤ 512 packets per wakeup; short, over-long, unknown token or not the proven endpoint: dropped and counted |
| NetworkInfo from a host (SCAN_RESP, ACCEPT, SYNC) | NetworkType 2, Ssid ≤ 32, 1 ≤ NodeCountMax ≤ 8, 1 ≤ NodeCount ≤ NodeCountMax, AdvertiseDataSize ≤ 384, node 0 at 169.254.S.1 with S 1–254, every connected node at its own id and address with a terminated name, and exactly NodeCount of them; unused node slots and advertise bytes past the size are zeroed. ACCEPT must also name this station's node and the scanned network; SYNC must keep the network and this station |
| REFUSE | only nn::ldn's Connect results reach the game; anything else becomes ConnectRejected |
| KICK reason | 1–6, else SignalLost |
| datagram from the host | its source must lie in the network's /24 |
| DNS-SD | names printable and < 64 bytes; TXT strings inside the record, `p` 1–65535, `b` 16 hex digits or `-`, `m` 16 hex digits, `a` a dotted quad; LAN addresses only; at most 16 hosts kept |

Tests (`coop.hostile`): against a host, a frame length of 0xFFFFFFFF, 0 and 64 KiB, a frame before HELLO, a bad magic, a short HELLO, protocol 1,
a frame that never completes (closed after 3 s, not before), a short JOIN, a JOIN with a bad build ID, a stale network, ten JOINs at once, a
version of 0xFFFF, unknown types and early datagrams, 200 SCAN_REQs at once (answered at most 30 times; the link stays up), ten half-open links
from one address (at most four kept), and UDP junk (short, foreign, an unknown token, 9,000 bytes); then an honest station still joins and talks.
Against a station, a fake host: another protocol in WELCOME, a NetworkInfo with AdvertiseDataSize 0xFFFF (not listed), every NetworkInfo field
above broken one at a time, an ACCEPT naming node 9, a REFUSE of 0xDEADBEEF, an insane SYNC, 5,000 datagram frames at once (≥ 1,000 dropped by the
rate limit, ≤ 256 queued), and a frame length of 0xFFFFFFFF (the station leaves the network, SignalLost).

### Discovery (research/06 §5.5)

- **Service:** `_pokopia-ldn._udp` (unchanged from research/06; the control link is TCP, which DNS-SD convention would call `_tcp`, but the name
  works either way and is what iOS's `NSBonjourServices` lists). Instance `Pokopia-<8 random hex>`, port = the control port (UDP on the same
  number or another the WELCOME names).
- **TXT:** `p=2` (coop protocol), `b=<16 hex>` (main's build, `-` unknown), `m=<16 hex>` (mods), `a=<IPv4>` (the host's LAN address as it
  routes; only a LAN address is published). No player, town or computer names.
- **Windows** (measured with a scratch program first): `DnsServiceBrowse` keeps calling back with PTR, SRV and TXT records; for a registration on
  the same PC it never delivers an A record, and `DnsServiceResolve` / `DnsQuery_W` never resolve its host name. So the host publishes `a=`, and the
  browser takes the A record when a responder sends one along (Apple's do), else `a=`. The browse is restarted every 15 s and an entry not seen
  for 35 s is dropped; a goodbye (TTL 0) drops it at once. Cancel ends in an `ERROR_CANCELLED` callback, so each operation's context lives until
  then. dnsapi.dll's functions are loaded at run time: before Windows 10 1809 there is a log line and join by address.
- **Apple:** one shared `DNSServiceCreateConnection`, polled by the I/O thread; Register (interface any, host the device's own name), Browse,
  Resolve, GetAddrInfo (IPv4). The A record wins; `a=` is used when none answers within 1 s. `kDNSServiceErr_PolicyDenied` logs where the iPhone's
  Local Network switch is.
- A Scan right after browsing starts waits up to 1.5 s from its start for a host to appear; then 0.5 s for links and 0.5 s for answers, as before.
- A station keeps a link to every host it knows (DNS-SD's, or the configured one) while browsing or initialized, asks each in Scan, and Connects to
  the one whose NetworkId the game picked.

### Protocol 2

HELLO is unchanged (23 bytes); WELCOME is 32 bytes (magic, protocol, UDP port, build, mods); SCAN_RESP is a flag, the identity (24 bytes), then
the NetworkInfo; JOIN is 0x72 bytes (step 1's 0x5A plus the identity). A step 1b build is told protocol 2 and closed with a log line on both sides.

### The ports

- **nxrt** (`src/nxrt/hle/hle_offline.c`): `dm_coop()`'s hooks gain `build` (`dm_main_build()`); one line changed, none added, so the generated HLE
  inventory is unchanged (`nxrt.generated` passes). `tests/test_coop.c` reads the host's port after CreateNetwork. Nothing else in nxrt changed.
- **pokopia-native** (`host/nxnative-switch2.patch`, made by `src/coop/nxnative/apply_coop.py` + `make_patch.py`):
  - the module as `dm/host/coop.h`, `coop_int.h`, `hle_coop_{ldn,vnet,mpnet,plat,disc}.c` (byte-identical copies; the Makefile's `hle_*.c`
    wildcard builds them for the Mac and the iPhone);
  - `hle_offline.c` routes nn::ldn to the module under `DM_COOP`, with the build and the cheats fingerprint as the identity, and its socket
    overloads go through `mpsk_*` only then; `hle_net.c` gets the `mpsk_*` block, Poll / Select that know virtual descriptors, and the socket hooks
    (as `research/mac-coop`, without the tracer and without `DM_MPFAKE`);
  - `hle_misc.c`: parental controls answer "allowed" only while the node exists (`DM_COOP` set and not off);
  - the launcher switch (`settings.tsv`), `NSLocalNetworkUsageDescription` ("Link Play uses your local network to find and visit other players'
    towns.") and `NSBonjourServices` (`_pokopia-ldn._udp`) in the iPhone app (`project.yml`), the Mac app (`pack_app.py`) and the Windows builder's
    IPA (`host/windows/build_app.patch`), and rows for the four switches in nxnative's `docs/env_switches.tsv`.
  - The 32 sections the co-op changes do not touch are copied through byte for byte; the patch applies to a clean c1df4dd and reproduces the tree.

## Windows: the firewall and the network profile

- **The prompt:** the first time a build hosts with `auto`, Windows shows "Windows Security Alert" for that `nxrt.exe` (per path). Answer
  **Allow** with **Private networks** ticked and Public not ticked.
- **The profile:** this PC's Ethernet is set to **Public** (read with `Get-NetConnectionProfile`; nothing changed). On a Public network Windows
  keeps other devices out even with the prompt answered for Private, and the phone may not see the PC's DNS-SD answers. A host now checks the
  profile (Network List Manager, read only) and logs: "this PC's network is set to Public: Windows Firewall keeps other devices out … Make it
  Private". The user changes it: Settings → Network & internet → Ethernet → Network profile type → **Private**.
- **What the test left:** when `build\c1c\bin\nxrt.exe` first listened, Windows added two inbound **Block** rules for that path on the Public
  profile (seen with `Get-NetFirewallApplicationFilter`, read only). They only concern that test binary; the user may keep or delete them
  (Windows Defender Firewall with Advanced Security → Inbound Rules → nxrt.exe). The prompt itself may still be open on screen.
- **A visitor** needs no rule: it dials out, and the host's UDP replies come back on that flow.

## Tests

### Module

```
build\coop (cmake -S src\coop, clang 23.1.4, 2 jobs, low priority):
== config / addr / txt
   network profile: a connected network is Public (a LAN host would log the hint)
== loopback: game datagrams over UDP
== loopback: game datagrams over the control links (tcp_only)
== lan: auto hosts, the listening window, the default port, build and mods
== hostile: raw links against a host
   host after the hostile links: 14 malformed inputs, 192 rate drops, 6 joins refused
== hostile: a fake host against a station
1327 checks, 0 failed

ctest --test-dir build\coop
1/3 Test #1: coop.loopback ....................   Passed    9.62 sec
2/3 Test #2: coop.lan .........................   Passed    6.37 sec
3/3 Test #3: coop.hostile .....................   Passed    5.78 sec
100% tests passed, 0 tests failed out of 3

The Mac (~/c1c/test, cc -Wall -Wextra, no warnings): 1327 checks, 0 failed
```

`COOP_TEST_MDNS=1` (or `coop_tests mdns`) advertises and browses on the real LAN:

```
Windows: found "Pokopia-b18c826b" at 192.168.x.y:47935 after 31 ms (build 76a489f3c0ffee00, protocol 2); the station saw the host's goodbye
Mac:     found "Pokopia-95907c0e" at 127.0.0.1:47948 after 267 ms (build 76a489f3c0ffee00, protocol 2); the station saw the host's goodbye
```

(mDNSResponder resolves an instance of the same Mac on lo0, so the station's link there is loopback.)

### nxrt (`build\c1c`: HEAD 68aa462 plus only the co-op files, so M6 phase B's work in progress is not in it)

```
ctest --test-dir build\c1c -R "coop|generated"
1/7 nxrt.coop_off  Passed   2/7 nxrt.coop_host  Passed   3/7 nxrt.coop_join  Passed   4/7 nxrt.generated  Passed
5/7 coop.loopback  Passed   6/7 coop.lan  Passed   7/7 coop.hostile  Passed
100% tests passed, 0 tests failed out of 7
```

Only the targets these need were built (`nxrt`, `nxrt-tests-coop`, `coop_tests`), at 2 jobs, low priority. The co-op files add no warnings.

### Windows ↔ Windows over the LAN address

- **Setup:** `_local\c1c\run.ps1` (m6-run.ps1 with `build\c1c\bin\nxrt.exe`, below-normal priority, refusing to start while another nxrt.exe
  runs); host `c1c-h1` on a copy of `save-sync\backups\20261103-214949-level2`, visitors `c1c-v1` / `c1c-v2` on copies of
  `…\20261104-001844-player2-level2`; `DM_FIXED_TIME=1793793600`, `TZ=UTC`; driven by `_local\coop1b\k.py`.

| host's log, s | |
|---:|---|
| 114.7 | CreateNetwork: "hosting on every interface:47600 … links from loopback, private (RFC 1918) and link-local addresses only" |
| 115.4 | "disc: advertising "Pokopia-d3a28017" (_pokopia-ldn._udp) on port 47600" |
| 167.1 | "station link 0 up from 192.168.x.y:53085" (visitor `c1c-v1`, `DM_COOP=auto`: "disc: found Pokopia-d3a28017 at 192.168.x.y:47600", 12 ms after browsing started) |
| 184.9 | node 1 joins; ACCEPT after the game read the NetworkInfo; "node 1 datagrams over UDP" |
| 185–255 | the session: the visitor arrives, both screens show both characters, the visitor walks off and the host's view follows; > 3,000 datagrams each way; 59.4–59.9 presents/s on the host |
| 255.0 | the visitor's process killed: "station link 0 down", "node 1 left", the host plays on |
| 357.8 | visitor `c1c-v2`, `DM_COOP=join:192.168.x.y`: link up, Scan, Connect, UDP; arrives the same way |
| 507.5 | the host chooses Close the Connection: the game sends its own leave, "node 1 left", CloseAccessPoint: "stopped listening; 1 link(s) closed", "disc: stopped advertising"; the visitor waves and travels home; 192.168.x.y:47600 refuses connections |

Screenshots (`_local\c1c\shots\`, game-derived, not in git): `h1\h_face`, `v1\v_list` (the host's town listed), **`v1\v_arr` + `h1\h_arr`**
(arrival), `v1\v_move` + `h1\h_move`, `h1\h_lost`, **`v2\v_arr` + `h1\h_arr2`** (by address), `h1\h_c1` (Close the Connection),
`v2\v_c2` (the visitor waving), `v2\v_home` (back home).

### Mac (`~/c1c`, pokopia-native's patch with the module)

- `nxs2.py setup` + `apply_coop.py` in `~/c1c/installer`; the build folder `~/c1c/build` holds APFS clones of `build201-clean`'s inputs (not its
  Xcode project, outputs or logs). The shader chain version is unchanged (`cac032d8de97`): 4,816 Metal libraries reused, none compiled. The corpus
  was regenerated by the repository's newer tools, so the 154 run-time shaders were added back from the recorded dumps (152) and two control
  sections copied from `build201-clean` (their dumps are no longer on disk); the shader map then equals `build201-clean`'s (3,967 keys).
- Two instances (`~/c1c/ia.sh`, copies of `~/mp/saves/level2-1103` and `player2-level2`, `DM_COOP=auto`, the fixed clock): the host hosts on every
  interface and advertises; the visitor "found Pokopia-f555ba55 at 127.0.0.1:47600", scans, connects, "datagrams over UDP", and **arrives**: both
  screens show both characters; its walk shows on the host. Shots: `_local\c1c\shots\mac\` (`v_list`, `v_arr`, `h_arr`, `v_move`, `h_move`).
- The Mac's only non-loopback address is public, so no LAN test was possible there; instead a TCP connect and a UDP packet from
  that address to the host: "refused <its public address>: not a loopback, private or link-local address", while 127.0.0.1 was accepted.
- nxnative's own checks in the patched tree: `test_launcher_settings` OK, `test_lint_c` OK; `test_env_switches` and `test_docs` fail exactly as
  without the co-op changes (switches and a patch index the Switch 2 patch already leaves out of nxnative's docs).

## The iPhone IPA

`<work>\c1c-ipa\build\out\PokemonPokopia-unsigned.ipa`: built on the Mac by `nxs2.py build … --ipa` from
`~/c1c/build`; bundle `org.nxnative.0400a3a01ee4c000` (the user's app: installing keeps `Documents`), 2.0.1, 154 of 154 run-time shaders, no game
files inside (the phone's `Documents/romfs.img` is used), Info.plist with both keys, the launcher's Link Play switch (off). Unsigned: no profile,
no `_CodeSignature`; the executable carries the two memory entitlements ad hoc, as every unsigned IPA of the builder. The folder is laid out as a
build folder, so the builder's Sign IPA takes it as it is.

## Test plan: PC ↔ iPhone on your Wi-Fi

You need: the PC and the iPhone on the same home network, your signing certificate (.p12) and development profile, a USB cable.

1. **Sign:** open Pokopia Native Builder on the PC, set **Build folder** to `<work>\c1c-ipa\build`, press
   **Sign IPA**, choose your .p12, your .mobileprovision and type the password. The result is `c1c-ipa\build\out\PokemonPokopia-signed.ipa`.
2. **Install** with the phone on USB: `py -3 -m pymobiledevice3 apps install "<work>\c1c-ipa\build\out\PokemonPokopia-signed.ipa"`.
   It replaces the app and keeps its Documents (save, romfs.img).
3. **Save 2 on the phone** (the PC and the phone must not run the same save: the game refuses that, error 9005). Close Pokopia on the phone, then:
   `py -3 -W ignore <work>\save-sync\pokopia_save_sync.py --to-phone --eden-save <work>\c1c-ipa\save-player2-level2`
   and type `yes`. That folder is a copy of `backups\20261104-001844-player2-level2`. The tool backs both sides up first and keeps the phone's
   current save as `Documents/save.replaced-<time>`. To go back later: the same tool with `--to-phone` from your main save.
4. **The PC's network:** Settings → Network & internet → Ethernet → Network profile type → **Private** (it is Public now, which keeps the phone out).
5. **PC game:** in PowerShell, `$env:DM_COOP = "auto"`, then play as usual (`powershell -ExecutionPolicy Bypass -File _local\play-local.ps1`). It
   runs your main Level 2 save. The exe must contain this step: `build\Release` after the next `scripts\build.cmd`, or now `build\c1c\bin\nxrt.exe`
   through play.ps1's `-Exe`. The first time it hosts, Windows asks about the firewall: tick **Private networks** only, **Allow**.
6. **Phone game:** start Pokopia; in the launcher turn on **Link Play on this network**, press Play. The first time Link Play is used, iOS asks
   for local network access: **Allow** (later: Settings → Privacy & Security → Local Network → Pokemon Pokopia).
7. **PC hosts, phone visits:** PC: Pokémon Center PC → Link Play → Invite Others to Visit → Local connection → Link Code: No → confirm → +
   ("You're now connected!"). Phone: PC → Link Play → Visit a Friend → Local connection → your town → Yes → the two notices → travel.
8. **Phone hosts, PC visits:** both leave Link Play (Close the Connection on the PC), then the same with the roles swapped.
9. **If the town does not appear** (DNS-SD blocked on some routers): join by address. Find the host's IPv4 (PC: `ipconfig`; phone: Settings →
   Wi-Fi → (i)). Phone visiting the PC: put `DM_COOP=join:<PC's IPv4>` in the app's `Documents/env.txt` (Finder or the Apple Devices app: the
   app's files) and restart the app. PC visiting the phone: `$env:DM_COOP = "join:<phone's IPv4>"` before starting the game.
10. **Report, with screenshots:** the destination list showing the other town; both screens after arrival (each should show both characters);
    one after walking around; any error message and its number. From the PC: the run's log (`[coop]` lines; `play.ps1` prints where). From the
    phone: `Documents/log.txt` (`tools\phone_pull.py`, or the Files app). Say which direction worked and whether DNS-SD or the address was used.

## Not done, not verified

- **PC ↔ iPhone has not run.** Everything iPhone-specific is untested on a phone: the local network prompt, Bonjour on iOS, the launcher switch,
  hosting from the phone, Wi-Fi latency and power saving, the app going to the background (iOS suspends it; the game disconnects itself).
- **The Mac had no LAN:** its Link Play ran over loopback (DNS-SD resolved to lo0); only the refusal was tested on its public address.
- **Windows DNS-SD towards another device** was not observed: whether Windows answers the phone's queries for the registration (and on a Public
  network at all) is unknown; `a=` covers the address, the A record is a bonus.
- **Windows Firewall with a Private profile and a real remote device** was not exercised (local traffic skips the firewall). The prompt's
  outcome was read from the rules Windows created, not seen.
- IPv6 is classified but not transported; a phone on an IPv6-only network would need it.
- No encryption on the LAN (as before): anyone on the same Wi-Fi can read or inject Link Play traffic. The token proves a UDP endpoint, not a person.
- The mods fingerprint is self-reported; cheats toggled after joining are not re-checked.
- `play.ps1` has no `-Coop auto` flag (M6 phase B owns it now).
- nxnative's `test_env_switches` / `test_docs` still fail as before (the Switch 2 patch's own documentation gaps, not this step).

## Decisions for the user

- Whether to set the PC's Ethernet to Private (needed for the phone to reach a PC host).
- Whether to keep the two Block rules Windows made for `build\c1c\bin\nxrt.exe`.
- The service name `_pokopia-ldn._udp` (kept; `_tcp` would be the stricter DNS-SD convention). Changing it later means a new IPA.
- The launcher switch's default (off) and its wording.
- Pushing pokopia-native: the patch and `host/windows/build_app.patch` changed (see its `docs/link-play-review.md`).

## Reproduce

```
rem the module (PC)
cmake -S src\coop -B build\coop -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang
start "" /b /low /wait cmake --build build\coop -j 2
ctest --test-dir build\coop --output-on-failure
build\coop\coop_tests.exe mdns
rem nxrt from HEAD plus the co-op files (git archive HEAD into _local\c1c\src, then the co-op files copied over it)
cmake -S _local\c1c\src -B build\c1c -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ ^
  -DNXRT_SDL3_DIR=<work>/toolchains/SDL3-3.4.18 -DNXW_LLVM_DIR=<work>/toolchains/llvm-23.1.4 -DNXRT_NXNATIVE=<work>/installer/nxnative
start "" /b /low /wait cmake --build build\c1c -j 2 --target nxrt nxrt-tests-coop coop_tests
ctest --test-dir build\c1c -R "coop|generated"
rem two instances (no other nxrt.exe running)
powershell -File _local\c1c\run.ps1 -Name c1c-h1 -Coop auto -Uid 4f9c2a71d03e8b5617ac94e2b8d05c3f -Nick Host -Save <level2 backup> -Cache _local\c1c\cache-h
powershell -File _local\c1c\run.ps1 -Name c1c-v1 -Coop auto -Uid 9a3e60c4f1b7d2852e0f4c9b7a16d3e8 -Nick Visitor -Save <player2-level2 backup> -Cache _local\c1c\cache-v -Pair
rem then coop-step1b.md's k.py token lists
```

```
# the Mac
cd ~/c1c/installer && ~/venv/bin/python nxs2.py setup && ~/venv/bin/python ~/c1c/coop/nxnative/apply_coop.py nxnative
~/venv/bin/python nxs2.py build "$HOME/dumps/<the 2.0.1 dump>" --out ~/c1c/build --ipa
~/venv/bin/python ~/c1c/coop/nxnative/make_patch.py nxnative host/nxnative-switch2.patch ~/c1c/nxnative-switch2.patch.new
SAVE=~/mp/saves/level2-1103 ./ia.sh m1h 1800 ~/c1c/build ~/c1c/build/host/dmhost DM_COOP=auto DM_FIXED_TIME=1793793600 TZ=UTC   # and m1v
./k.sh m1h ${=B}   # zsh: split the token list
```
