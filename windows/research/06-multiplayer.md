# 06 — Multiplayer: cross-device local co-op (research and feasibility design)

This note asks whether Pokopia's **local wireless co-op** can be restored across our native ports (Mac, iPhone, Windows, later Android), and how. It covers research and design only; no product code was written. Every claim is marked as one of:
- **measured**: from the game's binaries or a run on the Mac;
- **public**: from a cited web page;
- **inferred**: reasoned from the above, not verified.

Builds examined:
- **2.0.1:** main `76a489f3…`, the census build of 04.
- **1.0.5:** main `816c5358…`.

Its network imports and its LDN code constants are identical to 2.0.1's.

**Verdict: feasible with caveats.** Details are in §7.
- Local co-op runs on Nintendo's local-wireless API (`nn::ldn`) plus plain UDP sockets, through Koei Tecmo's own session library. There is no Pia and no NEX, and no Nintendo service sits on that path.
- Our HLE can emulate the whole local stack with a virtual LDN network over LAN or a relay.
- Every online feature (Online Link Play, Cloud Islands, GameChat GameShare) runs on Nintendo's NPLN servers and stays out of scope.
- **Verified at run time (§3.1, §3.2):** hosting, scanning, and two instances on one Mac playing Link Play Local together through our module, provided each player has their own save.

---

## 1. What multiplayer Pokopia has (public sources)

| mode | players | transport | requirements | where |
|---|---|---|---|---|
| **Link Play, Local Connection** ("Invite Others to Visit" / "Visit a Friend") | 2–4 (host + up to 3 visitors) | local wireless (LDN) | each player needs their own copy and a Switch 2; **no** Nintendo Switch Online and no internet | PC → Link Play; optional 4-digit Link Code; host settings: show code, player count (≤ 4), Spectator Mode |
| Link Play, Online Connection | up to 4 | internet | NSO membership, own copy, latest update | same menu |
| **Cloud Island** | up to 4 | online only | NSO; anyone with the address and the Link Code can join, even when the owner is offline | Pokémon Center PC; roles owner / host / visitor |
| Virtual Cloud Island visits (Mysterious Goggles) | — | online | NSO; Environment Level 3; goggles cost 100 Life Coins | read-only "virtual mode" |
| GameShare with nearby players | 2 | local wireless (system streaming) | one copy; host on Switch 2, guest on Switch 2 or Switch | Palette Town only |
| GameShare via GameChat | 2 | online | one copy; GameChat (NSO) | Palette Town only |

How Link Play works:
- **Unlock:** Link Play unlocks at about 30 minutes of play, when the Withered Wasteland reaches **Environment Level 2**.
- **What is shared:** visitors play on the host's island.
  - Changes to the host's island persist, and all progress and rewards go to the host.
  - Visitors cannot change terrain outside Palette Town, keep a separate inventory and take no items home.
  - Visitors can register Pokémon in their own Pokédex, order items from the PC Shop and bring one Pokémon ("Follow me!").
  - The session lasts only while the host plays. The host can kick visitors.
- **Versions:** "All players must be using the same version of the software to play together" (Nintendo's update page).
  - Online play also requires the latest update.
  - Updates: 1.0.1 (4 Mar 2026) … 2.0.0 (5 Aug 2026), 2.0.1 (8 Sep 2026).
- **eShop listing:** local wireless 2–4 players, online 1–4. GameShare is supported "with users in a GameChat session and with local users". Save Data Cloud needs NSO.
- **Format:** the physical release is a game-key card, which is a download licence. Nothing indicates that the key card matters to local play.

Sources:
- [Nintendo Support: Link Play](https://en-americas-support.nintendo.com/app/answers/detail/a_id/71349)
- [Nintendo: How to update Pokopia](https://www.nintendo.com/en-gb/Support/Purchases-Subscriptions/Games/How-to-Update-Pokemon-Pokopia-3041475.html)
- [eShop listing](https://ec.nintendo.com/apps/0400a3a01ee4c000/AU?lang=en)
- [Game8 multiplayer guide](https://game8.co/games/Pokemon-Pokopia/archives/582101)
- [Vandal](https://vandal.elespanol.com/en/pokemon-pokopia/multiplayer-in-pokemon-pokopia-how-it-works-cloud-island-and-limitations.html)
- Key card: [Nintendo Life](https://www.nintendolife.com/news/2025/11/pokemon-pokopia-is-seemingly-nintendos-first-game-key-card-release), [VGC](https://www.videogameschronicle.com/news/pokemon-pokopia-appears-to-be-the-first-nintendo-published-switch-2-game-to-be-on-a-game-key-card)

**The target:** our target is the first row only, Link Play with Local Connection. It is the one mode that needs no Nintendo service. It already requires every player to own the game, which our installer model enforces anyway, since each user dumps their own copy.

---

## 2. What the game's code uses for networking (measured)

### 2.1 Imports from sdk (main, 2.0.1; 1.0.5 identical)

Counts are per namespace, using the census method of 04. The script and outputs are `_local/mp/imports-main-201.dem.txt` and `net-imports-201.txt`.

| namespace | n | what main imports (abridged) | role |
|---|---:|---|---|
| `nn::ldn` | **17** | Initialize, Finalize, GetState, OpenAccessPoint, CloseAccessPoint, CreateNetwork, OpenStation, CloseStation, Scan, Connect, Disconnect, GetNetworkInfo, GetIpv4Address, SetAdvertiseData, Reject, MakeSubnetMask, MakeBroadcastAddress | local wireless; **no** AttachStateChangeEvent, DestroyNetwork, private (Create/Connect)Private or NodeLatestUpdate: the game **polls** GetState / GetNetworkInfo |
| `nn::socket` + `nnsocket*` | 40 + 30 | Socket, Bind, SendTo, RecvFrom, SetSockOpt, Poll, Select, GetAddrInfo, EventFd… | the LDN data plane (§2.4) and the online stack |
| `nn::nifm` | 8 | Initialize, SubmitNetworkRequest, IsNetworkAvailable, HandleNetworkRequestErrorResult, GetCurrentPrimaryIpAddress, GetCurrentProxySetting… | internet only |
| `nn::ssl` + `nnssl*` | 12 + 23 | Context, Connection (OpenSSL/gRPC/curl over the system TLS) | internet only |
| `nn::account` | 18 | GetUserId, GetNickname, TryOpenPreselectedUser, **EnsureNetworkServiceAccountAvailable**, GetNetworkServiceAccountId, (Ensure/Load)NetworkServiceAccountIdTokenCache, LoadNetworkServiceLicenseInfoAsync, ShowLicenseRequirementsForNetworkService | user identity; NSA / tokens for NPLN |
| `nn::friends` | 16 | GetProfileList, StartFriendInvitation, StartSendingFriendInvitation, GetFriendInvitationNotificationEvent… | online invites |
| `nn::streamplay` | 18 | Initialize, CreateIndividualDisplays, GetGuestPadStates, WriteGuestAudioStream, OpenSessionWithMatchmakeSupport… | **GameShare** (Switch 2 streaming) |
| `nn::pctl` | 2 | TryBeginFreeCommunication, EndFreeCommunication | parental "communication with others" gate |
| `nn::bcat` 19, `nn::nsd` 2, `nn::err` 12, `nn::prepo` 13, `nn::time` 26 (incl. `AsyncEnsureNetworkClockAvailabilityTask`) | | | news delivery, environment, error applet, telemetry, network clock |

**Absent:** there are **no** imports from `nn::npln`, `nn::nex` or `nn::pia`.
- NPLN is linked into main itself (§2.2).
- `main.npdm` grants 46 services, including `ldn:u`, `nifm:u`, `bsd:u`, `ssl`, `acc:u0`, `friend:u`, `bcat:u`, `nsd:u`, `sfdnsres`, `chat:u`, `pctl`, `prepo:u` and `ngc:u`. It also grants two Switch 2 services: `stpl:u` (presumably streamplay) and `banana` (unknown).
- The local communication ID in `control.nacp` is the title ID, `0x0400A3A01EE4C000` (first of 8 slots; the rest are 0).

### 2.2 Libraries linked into main

The NSO's API-info list shows the middleware linked into main:
- `NintendoSDK_gRPC_For_NPLN`
- `OpenSSL_For_NPLN`
- `NPLN-1_41_4`
- `Google_APIs_For_NPLN`
- `libcurl-20_5_25`
- `libz`
- `WebRTC_For_NPLN`

The strings confirm it. Each count below is case-insensitive.

| marker | count |
|---|---:|
| `npln` | 338 |
| `grpc` | 307 |
| `webrtc` | 79 |
| `cricket` (libjingle ICE) | 66 |
| `stun` | 59 |
| `pia::` / `nn::pia` / `nex::` / `quazal` / `rendezvous` | **0** |

So there is **no Pia and no NEX**. Online play runs on **NPLN**, Nintendo's newer gRPC platform. Its gRPC services appear in main:
- `nn.npln.auth.v1.Auth/*`
- `matchmaking.v1.GameSessionService/*`, including `AllocateIceServerSet`
- `matchmaking.v1.Matchmaker/*`
- `gamesync.v1.Gamesync/*`, which handles documents and transactions (presumably Cloud Islands)
- `ugcstore.v1.*`
- `friends.v1.*`
- `maintenance.v1.*`

Two hostnames appear: `gamesync.npln.nintendo.net` and `%s.%s%s.t.npln.srv.nintendo.net:443`.

Online P2P uses WebRTC ICE: `nn::npln::ice::TransportDispatcher`, `cricket::P2PTransportChannel`, `nn::npln::p2p::StunChannelP2pPlugin`. **All of this is off limits and stays cut.**

### 2.3 The game's own session layer: KTGL Online

Koei Tecmo's engine wraps everything in one abstraction. Its class names come from 12-byte name-getter functions found through `_local/mp/scripts/strref.py`.

| backend | classes | getters | transport |
|---|---|---|---|
| **Local wireless** | `CNnLdnOnlineSessionP2p`, `…Platform`, `…Member` | main+0x1b57248, 0x1b571e8, 0x1b57524 | `nn::ldn` + UDP |
| Online | `CNnNplnOnlineSessionP2p`, `…Platform`, `…Member` | main+0x1b2b128 … | NPLN matchmaking + WebRTC |
| Dev server | `CDevSrvOnlineSessionP2p`, `CDevSrvOnlineSessionP2pPlatform_Nn` | main+0x1b62aac, 0x1b5a634 | UDP port **35000** to a Koei Tecmo development server (not used in retail as far as we can tell) |

Shared pieces:
- **Common classes:** `COnlineSessionP2p`, `COnlineSessionP2pManager`, `COnlineSessionP2pMember`, `CNnOnlineNetworkDevice` (device modes "internet" vs "ad hoc").
- **Error enum:** `KTGL_ONLINE_E_*`, 300+ names. The ones that matter here:
  - `…DEVICE_FAILURE_OPEN_ADHOC_NETWORK`, `…DEVICE_ADHOC_NETWORK_CLOSED`, `…DEVICE_DIFFERENT_COMM_MODE`
  - `…APPLICATION_REJECT_JOIN_SESSION_EXIST_SAME_USER_ID_USER` and `…SELF_DISCONNECT_EXIST_SAME_USER_ID_USER` (a joiner with the **same user ID** as a member is rejected)
  - `…SELF_DISCONNECT_COMMUNICATION_QUALITY`, `…SESSIONP2P_HOST_TIMEOUT`, `…SELF_DISCONNECT_RESEND_COUNT` (the game has its own reliability and quality timeouts over UDP)
  - `…SELF_DISCONNECT_RETURN_FROM_BG` (disconnects after the app was in the background)
  - `…SESSIONP2P_TOO_LARGE_UNRELIABLE_PACKET`
  - `…FAILURE_NEED_MULTIPLAY_PRIVILAGE` (an NSO check; online)

**Device bring-up paths (measured, static):**
- **Internet device-up** (main+0x1ac23f8) starts three tasks through function pointers:
  - main+0x1ac3134: `nifm::Initialize` / `SubmitNetworkRequest`;
  - main+0x1ac34c0: `account::EnsureNetworkServiceAccountAvailable` / `GetNetworkServiceAccountId` for every user;
  - main+0x1ac3660.
- **Ad-hoc device-up** (main+0x1ac3f54) starts main+0x1ac42a0. That calls `ldn::Initialize` (main+0x1abd0f0) and spawns the LDN worker main+0x1ac59a0 → main+0x1ac5e20.
- **The ad-hoc path calls no nifm, NSA, friends or NPLN function.** That is the main reason for the verdict.

**The user ID** (inferred): KTGL's user-ID accessor (main+0x1adfbe0) returns `nn::account::GetUserId` of the device's user handle. That is the 16-byte `Uid`. The "same user ID" rejection therefore most likely compares account `Uid`s.

### 2.4 The local-wireless backend in detail (measured, static; 2.0.1 offsets)

**LDN worker** (main+0x1ac5e20) is a state loop over the requested mode (0 none, 1 access point, 2 station):
- **To host:** `OpenAccessPoint` → `CreateNetwork`.
- **To join:** `OpenStation` → (Scan / Connect elsewhere).
- **To stop:** `CloseAccessPoint` / `CloseStation`.
- **State:** it waits on `GetState()`, polled.

**NetworkConfig:**
- `IntentId.LocalCommunicationId` comes from KTGL's config (the app ID);
- `SceneId` = **0**;
- `Channel` = 0 (auto);
- `NodeCountMax` = the requested player count (a byte);
- `LocalCommunicationVersion` = a u16 from KTGL's config (+0x228). Its value is set at run time and not yet observed.

**SecurityConfig:** mode 1 (product), with a **fixed 64-byte binary passphrase** from main's read-only data (location not published). This is LDN-layer only; an emulation can ignore it, or compare it as a sanity check, and must not publish it.

**UserConfig:** the user name, ≤ 32 bytes, copied with `strncpy` from KTGL's user profile (the account nickname).

**Scan:** up to **24** networks (0x6c00-byte buffer), channel 0, with a ScanFilter from the session object. Scan results are decoded by the session layer:
- the advertise data holds a key `__ssd` (session search data);
- each member's address travels as a property `nx_ipaddr`.

**Advertise data:** built by main+0x1b54d88 (KTGL property map → `SetAdvertiseData`). It must fit LDN's 384 bytes.

**Data plane:** the KTGL LDN platform uses **plain UDP sockets, not Pia**.
- **Socket and port:** main+0x1b525cc creates the socket with `Socket(AF_INET, SOCK_DGRAM | NonBlock, 0)` and binds it to `INADDR_ANY:49530` (0xc17a). Both versions do this, with 2 sites each.
- **Broadcast mode:** a KTGL config flag (+0x290) sets `SO_BROADCAST`.
- **Send** (main+0x1b56100):
  - The payload must be < 4,093 bytes (0xffd).
  - In the flag's **broadcast-relay mode**, the game prefixes a 4-byte destination IPv4. It sends to `MakeBroadcastAddress(own IP, MakeSubnetMask(24))`, which is **a hard-coded /24**, port 49530.
  - Otherwise it unicasts to the member's IP on port 49530.
- **Receive** (main+0x1b52860):
  - Each read is `RecvFrom(fd, buf, 0x1000, MSG_DONTWAIT=0x80, …)` in a polling loop.
  - In broadcast-relay mode it drops packets whose 4-byte header names another node.
  - Senders are identified **by source IPv4**, looked up in the member table. The source address must equal the node's IP in `NetworkInfo`.
- Whether retail sets the broadcast-relay flag is unknown, because the default constructor (main+0x1abcf48) writes 0. The emulation must handle both modes.

### 2.5 What nxnative answers today (our MIT code, `installer/nxnative/dm/host`)

| area | answer |
|---|---|
| `nn::ldn` (`hle_offline.c`) | `Initialize` → `RES_FAIL`, `GetState` → 0, every other call fails, Scan finds 0 |
| `nn::nifm` (`hle_net.c`) | Initialize OK; the request is "on hold" once, then unavailable; `IsNetworkAvailable` → false; no IP |
| `nn::socket` / `nnsocket*` | Initialize OK; Socket/Bind/SendTo/… return −1 / ENETDOWN (50); Poll/Select with no descriptors sleep the timeout |
| `nn::ssl` | Initialize OK, everything else fails |
| `nn::account` (`hle_account.c`) | one fixed local user: **`Uid` = {0x78626c0000000001, 0x53574e4154495645}, the same constant on every install**; nickname from `DM_NICK` (default "Player"); every NSA/token call fails |
| `nn::pctl` (`hle_misc.c`) | `TryBeginFreeCommunication` → **not allowed** (no UI) |
| `nn::friends` | no friends, async contexts done/failed |
| `nn::streamplay` | stubs: success, no session, no guests |

**The "Offline" label:** the game's menus show **Offline** with a Wi-Fi icon (§3). That is the internet status from nifm; local play does not depend on it.

---

## 3. What happens at run time (Mac, measured)

**Setup:**
- **Host:** a copy of nxnative, `~/mp/nxnative-mp` (from `~/nxnative-trace`), with one new research-only file, `hle_mptrace.c`.
  - With `DM_MPTRACE=1`, every guest call of 202 network, account, pctl, friends, streamplay, ssl, bcat, nsd and err hooks is logged: x0–x5, the guest lr, the result, and struct dumps for ldn and socket calls.
  - It works through a trampoline stub chosen in `hlecount_wrap`.
  - `DM_MPFAKE=1` adds a lone-access-point `nn::ldn`: Create, GetNetworkInfo and GetIpv4Address succeed, and Scan returns nothing.
  - Sources: `_local/mp/macsrc/`.
- **Build:** `~/installer-test/build201-clean`, read only.
- **Drivers:** `~/mp/mpdrive.py`, `ia.sh` (interactive, `DM_CTL`) and `k.sh`.
- **Run folders:** `~/mp/runs/{base1,walk1,lp0}`. Screenshots and logs are copied to `_local/mp/mac/`.

| run | save | route | result |
|---|---|---|---|
| `base1` | copy of run-4 | boot, title, Plus menu, Minus | stock host, `DM_HLECOUNT` |
| `walk1` | copy of run-4 | walk toward the Pokémon Center | the tutorial stops the player ("Whoooaaa there… you've wandered awfully far away!"): run-4 is minutes into the game |
| `lp0` | **copy of the user's Eden save** (`~/mp/saves/eden1103`, the most progressed save found) | boot, walk to the Pokémon Center PC, open it, select the locked tiles | `DM_MPTRACE=1` |

**Calls at boot (`lp0`; `base1`'s counts agree):**
- `err::InitializeContextWriterApi`, `pctl::Initialize`;
- `account::Initialize` ×2, `TryOpenPreselectedUser`, `GetNickname` ("Player"), `GetUserId` ×4 (the constant `Uid` above);
- **KTGL network init** (lr main+0x1abd034 and +0x1abd2f8): `socket::Initialize` (Config dump in `_local/mp/mac/lp0/mp-lines.txt`), `ssl::Initialize`, `nifm::Initialize`;
- `ssl::Context::Destroy` (fails), `bcat::Initialize` (fails);
- `streamplay::GetRequiredMemorySize` ×2 (1 MiB), `CreateIndividualDisplays(1)`, `GetNativeWindowHandle(0)`;
- `friends::GetFriendInvitationNotificationEvent` about once per frame (5,384 calls in 95 s).

**No `ldn`, no `nifm::SubmitNetworkRequest`, no socket creation and no NSA call** at boot, in the Plus menu or in the PC menu.

**What the screens show:**
- **Plus menu and PC header:** "Offline" next to a Wi-Fi icon.
- **PC menu:** "Withered Wasteland — Environment Level 1". Challenges, Shop and Get Items are available; two tiles are locked ("???"), and selecting them does nothing.
- **Link Play:** one of the locked tiles; it unlocks at Environment Level 2.

**Why the co-op menu was not reached:**
- **Saves:** every save on the PC and the Mac is at Environment Level 1, by CMNDAT size and the screens above. That covers run-4, the user's Eden save, the phone backups and the Eden backup.
- **Scripted play:** raising the level takes about 30 minutes of guided play.
- **Memory editing:** DM_CTL has peek and memdump but no poke, and the level lives in `m_environmentSaveData`, whose layout is unknown.
- **Consequence:** the Local Connection path was **not exercised**.

**Inferred** from §2.3 and §2.5: with today's nxnative, choosing Local Connection would run the ad-hoc device-up. `ldn::Initialize` returns `RES_FAIL`, KTGL reports a device error (`…FAILURE_OPEN_ADHOC_NETWORK` or similar), and the game shows a communication error.

**The harness is ready:** with a save at Environment Level ≥ 2, one `ia.sh` session with `DM_MPTRACE=1 DM_MPFAKE=1` will record the real values. See Q1 in §7. Done in §3.1.

### 3.1 Step 0 with a Level 2 save (measured, 2026-11-03)

**Setup:**
- **Save:** the user's Eden save at Withered Wasteland Environment Level 2, backed up on the PC and copied to the Mac as `~/mp/saves/level2-1103`. Every run boots a fresh copy.
- **Host:** the research copy with two more research-only changes, both active only with `DM_MPFAKE=1` (`apply_mp_patch.py`):
  - pctl answers "allowed";
  - `sock_fake.c` provides lone-node virtual UDP sockets:
    - descriptors start at 0x100;
    - Bind, options and GetSockName succeed;
    - sends are counted and dropped, and never reach a real network;
    - receives return EAGAIN;
    - Poll/Select report virtual sockets as writable only.
- **Runs:** `~/mp/runs/lp2a`–`lp2f`. Redacted traces (passphrase removed) and the scripts are in `_local/mp/mac/lp2/`.

| run | what | result |
|---|---|---|
| `lp2a` | stock pctl ("never"), Link Play tile | the tile calls `pctl::TryBeginFreeCommunication(true)` and the PC menu stays put, with no message |
| `lp2b` | pctl allowed, Invite Others → Local → no code → confirm | the menus open; after `ldn::Initialize`, `Socket` fails (ENETDOWN), then `err::ShowApplicationError` and `ldn::Finalize`, back to the menu |
| `lp2c` | + virtual sockets, 4 players, Spectator On | **"You're now connected!"**: hosting works on a lone node |
| `lp2d` | Spectator Off attempt; then Check Settings while hosting | Off could not be selected (see below); while hosting, changes are refused |
| `lp2e` | Visit a Friend → Local | scans, finds nothing, then "That destination couldn't be found." |
| `lp2f` | Link Code `1234` (`DM_KBD`), 2 players | hosting works; locates the code and the player count |

**The Link Play menus (2.0.1):**
- **Main menu:** Invite Others to Visit / Visit a Friend / Play on a Cloud Island / Check Settings / Get Friends' Stuff (greyed).
- **Hosting:** Invite → Online / Local / Cancel → "Would you like to set a Link Code?". Yes opens the system keyboard: "Please enter the Link Code.", exactly 4 characters. Then come the town's settings:
  - Display Link Code: Yes / No, active only when a code is set;
  - Number of Players: Two / Three / Four;
  - Spectator Mode: On / Off.
- **Confirming:** + confirms. Changed settings first ask "Ready to go with these settings?".
- **While hosting:** the menu is Close the Connection / Check Settings / Get Friends' Stuff.

**Spectator Mode:**
- **What the game says:** the game's own visitor restriction. The first-time tutorial says that everyone in this town plays in Spectator Mode, where certain moves and items can't be used.
- **Locked On:** in Withered Wasteland at Level 2, both On and Off are drawn in the disabled grey, as Display Link Code is without a code. Right and A do nothing.
- **Fixed at creation:** while hosting, Check Settings refuses changes ("This can't be changed when you're already connected!"), and Number of Players is greyed. The setting is part of the session from CreateNetwork on.
- **Still unknown:**
  - When Off becomes available (a later level or another town). The message text is compressed inside RomFS and was not unpacked.
  - Which advertise byte carries the setting, because it could not be toggled.

**Hosting sequence** (`lp2c`, identical in `lp2f`):
1. `account::GetUserId` ×2, then `ldn::Initialize`.
2. `socket::Socket(AF_INET, SOCK_DGRAM | NonBlock = 0x20000002, 0)`, then `Bind(INADDR_ANY:49530)`. The sockaddr has `sin_len` 0.
3. `GetNickname`, then a first `RecvFrom(fd, buf, 0x1000, MSG_DONTWAIT)`.
4. `OpenAccessPoint`, then `CreateNetwork`.
5. `SetAdvertiseData` (378 bytes), `GetIpv4Address`, `GetNetworkInfo`, then `SetAdvertiseData` again (380 bytes).
6. Then every frame: `RecvFrom` (EAGAIN), `GetNetworkInfo` and `GetState`.

**What does not happen while hosting alone:**
- no `SetSockOpt`, so no `SO_BROADCAST`;
- no `SendTo`;
- no `Poll`/`Select`.

A lone host only listens: first contact comes from the visitor. Whether the broadcast-relay mode ever turns on can only be seen once a member joins (step 1).

**NetworkConfig:**
- `LocalCommunicationId` = 0xFFFFFFFFFFFFFFFF, the SDK's "this application's own ID";
- `SceneId` 0;
- `Channel` 0;
- **`NodeCountMax` = the Number of Players setting** (4 for Four, 2 for Two);
- **`LocalCommunicationVersion` = 0x012D (301)** on 2.0.1.

**SecurityConfig:** mode 1 with the fixed 64-byte passphrase (§2.4). It is **byte-identical with and without a Link Code**.

**UserConfig:** the account nickname.

**Advertise data** (378 / 380 of LDN's 384 bytes). Offsets are approximate, because the leading block's length varies by a byte between sessions:
- a leading block of ~55 bytes that differs every session (random or hashed);
- the player's character name from the save (~0x50);
- a town name, "Palette Town" (~0xB8);
- a 16-byte value that is constant across sessions of the same save (~0xE2);
- **the Link Code as 4 ASCII digits** (~0xF2), zero without a code;
- a flag (~0xFB), 0 → 1 with a code;
- 0x012D again (~0xFC);
- the account nickname, after 4 constant bytes (~0x10C);
- a tail of `02 06 04` + 16 bytes, repeated, that differs per session;
- a short list that grows from the first call to the second (count 2 → 4).

So **the Link Code is matched by the game from the scanned advertisement, not by LDN security**. The emulation passes advertise data through untouched and needs nothing for codes.

**Visitor sequence** (`lp2e`):
1. `ldn::Initialize`, then Socket and Bind on 49530 (as when hosting).
2. `OpenStation`.
3. `Scan(out, &count, 24, filter, channel 0)` about once a second. ScanFilter:
   - `LocalCommunicationId` −1;
   - `SceneId` 0;
   - `NetworkType` 2 (Ldn);
   - flags 0x21 (LocalCommunicationId | SceneId).
4. An empty scan ends with "That destination couldn't be found."

A first-time tutorial adds that visitors can help build when the host is in Palette Town.

### 3.2 Step 1: two instances over loopback (measured, 2026-11-03)

**Setup:** the research copy routes nn::ldn, the game's UDP sockets and the account `Uid` to our module `src/coop/` under `DM_COOP`
([docs/coop-step1.md](../docs/coop-step1.md)). Two `ia.sh` sessions on the Mac, each with its own copy of `~/mp/saves/level2-1103`: host
`DM_COOP=host:47600`, visitor `DM_COOP=join:127.0.0.1:47600`, different `DM_UID`s, `DM_MPTRACE=1`. Runs `~/mp/runs/coop1*`–`coop7*` (coop5–7 with a visitor save of its own, see below);
redacted traces and screenshots in `_local/mp/mac/coop1/`.

**Menus, more precisely than §3.1:**
- **Host:** after "No" to the Link Code, "Please choose how you want to play." takes three A presses before the town settings. After "You're
  now connected!", A opens the hosting menu (Close the Connection / Check Settings / Get Friends' Stuff); B B returns to the town, hosting.
- **Visitor:** a found host gives "Choose a Destination" (the host's character name and town, "Withered Wasteland"). A asks "Do you want to
  head to this destination?" with **Visitors 0/3**, read from NodeCount and NodeCountMax. Yes shows two Spectator Mode notices ("When playing
  with others in this town, everyone will be restricted to playing in Spectator Mode." / "Certain moves and items can't be used while playing
  in Spectator Mode."), then connects.

**Visitor's calls:** `Scan` ×3 (each finding the host), `CloseStation`, the destination prompts, then `OpenStation`, `GetNickname`,
`Connect(info, sec, user, 301, ConnectOption 0)`, `GetIpv4Address`, `GetNetworkInfo`, `GetNickname` ×2, and at once
`SendTo(host:49530, 174 B)`. The visitor scans with its own station and closes it before asking; it opens a new one to connect.

**The join handshake:**
1. visitor → host, 174 bytes; the host's game receives it from 169.254.S.2:49530;
2. host → visitor, 66 bytes;
3. visitor → host, 44 bytes; the visitor calls `Disconnect`, `CloseStation` and `err::ShowApplicationError`: application error code **9005**,
   "Communication error. Please wait and try again. If this error appears repeatedly, please check your connection."; on screen "A
   communication error occurred.", then the destination list again. The host keeps hosting and shows nothing.

**Packet shape:** a 24- or 28-byte head, `02 06 04` and a 16-byte per-session sender ID (the host's is the one in its advertise data's tail),
then an opaque, random-looking body (presumably encrypted). The host's answer body is 19 bytes, the visitor's last message 1 byte.

**Ruled out:**
- **The account `Uid`:** `GetUserId` returns each instance's `DM_UID`. With the host's own `Uid` on the visitor (`coop3s`) the exchange and error
  are identical.
- **A race in the emulation:** with ACCEPT held until the host's game had read a NetworkInfo listing the visitor (`coop3`), the result is the same.

**The cause (measured, 2026-11-04): the game refuses a second copy of the same save.**
- **Error 9005 decoded:** it is KTGL error **0x87F7F005, `KTGL_ONLINE_E_APPLICATION_REJECT_JOIN_SESSION_EXIST_SAME_USER_ID_USER`**. Every
  KTGL error has a name getter and a code getter (main+0x66a838 / +0x66a844 for this one), and main+0x1bce544 turns codes ≥ category 11 into
  9000 + the low byte. The neighbours are 9003 JOIN_SESSION_FULL_MEMBER, 9004 REJECT_JOIN_SESSION_UGC_CHANGE_OWNER and 9006
  REJECT_JOIN_SESSION_OTHER_ERROR. The 66-byte answer is the host game's application-level reject, and the 44-byte message is the visitor's leave.
- **The "user ID":**
  - It is not the account `Uid`. It is the 16-byte per-save value from the advertise data (§3.1), stored in the save's `S00/SYDAT.BIN` at 0x61
    (no checksum on that file). main also names save fields `m_userID`, `m_userIDVer2` and `m_MyNPLNUserID`.
  - A visitor copy with **one bit** of that value flipped (coop5, coop6, the latter with the host's own `Uid`) **joins and arrives in the
    host's town**.
  - So does the user's second Level 2 save, `player2-level2`, which has its own value (coop7).
- **Sessions that arrive:** both screens show both characters, movement syncs both ways, thousands of datagrams pass, and losing the visitor's
  process just drops it from the host's network.
- **Our layer was not involved:** NetworkInfo, platform byte, addresses, channel, LCID −1, timing and ordering were the same in refused and
  successful runs.
- **Crypto:** main imports only SHA-1 and SHA-256 from the SDK's `nn::crypto` (8 symbols, no AES or HMAC), so KTGL's packet protection is
  inside main; the bodies were not decrypted.

**Broadcast-relay mode:** no `SetSockOpt` and no broadcast in any run, including full two-player sessions (coop5, coop7). Every datagram is
unicast to port 49530 with no 4-byte destination header: retail runs KTGL's LDN transport in plain unicast mode.

---

## 4. How emulators make local wireless work (Ryujinx, MIT; ldn_mitm docs)

These are facts from Ryujinx's LDN HLE and Ryubing's LdnServer, both MIT, read for design only. Paths are under `src/Ryujinx.HLE/HOS/Services/Ldn/`. Only ldn_mitm's README was read, since its code is GPL. Full sources are in PROVENANCE.

**Guest state machine** (`NetworkState`): None 0, Initialized 1, AccessPoint 2, AccessPointCreated 3, Station 4, StationConnected 5, Error 6.
- Transitions: Initialize, Open/CloseAccessPoint, Create/DestroyNetwork, Open/CloseStation, Connect/Disconnect, Finalize.
- Losing the backend drops AccessPointCreated or StationConnected to Initialized and sets a DisconnectReason: DestroyedBySystem 4, Rejected 5, SignalLost 6, …
- One state-change event fires on every state change **and** every NetworkInfo update.
- **Results (module 203):** InvalidState 0x40CB, ConnectNotFound 0x82CB, ConnectTimeout 0x84CB, ConnectRejected 0x86CB, VersionTooLow 0xE2CB, VersionTooHigh 0xE4CB, TooManyPlayers 0x120CB.

**Structs** (they agree with switchbrew):
- **NetworkInfo (0x480):** NetworkId (IntentId + SessionId[16]) at 0x00, Common at 0x20, Ldn at 0x50.
- **CommonNetworkInfo:** Bssid, Ssid, Channel u16 at 0x28, LinkLevel at 0x2A, NetworkType 2 at 0x2B.
- **LdnNetworkInfo** (offsets within it):
  - ServerRandom, SecurityMode at 0x10, AcceptPolicy at 0x12;
  - NodeCountMax at 0x16, NodeCount at 0x17;
  - Nodes[8] × 0x40 at 0x18;
  - AdvertiseDataSize at 0x21A, AdvertiseData[0x180] at 0x21C;
  - Challenge at 0x428.
- **NodeInfo:** Ipv4, MAC, NodeId, IsConnected, UserName[33], Platform at 0x2D (Switch 2 era), LocalCommunicationVersion at 0x2E. Node 0 is the access point.
- **IP order:** IPs are host-order u32.

**LAN backend ("LdnMitm", ldn_mitm-compatible):**
- **Transport:** UDP + TCP on port 11452, with a 12-byte header (magic 0x11451400) and zero-run RLE compression.
- **Packets:** Scan is a broadcast answered by ScanResponse (NetworkInfo); Connect and SyncNetwork go over TCP.
- **Addresses:** node IPs are the **real LAN IPs**, and game traffic uses the host's real sockets.
- **Limits:** no version or passphrase checks, needs one L2 broadcast domain, and stations are never told when the host leaves.

**Relay ("RyuLDN"):**
- **Transport:** TCP to port 30456, header "RLDN", with messages Initialize, CreateAccessPoint, Scan/ScanReply, Connect, SyncNetwork, Proxy*, Disconnect and Ping.
- **Virtual addressing:** 10.114.0.0/16 from a virtual DHCP. Every guest IPv4 socket becomes a proxy socket once the network exists, and the relay rewrites and checks source IPs.
- **Version check:** the server compares LocalCommunicationVersion with the host's and answers VersionTooLow/High.
- **Room filter:** passphrase equality.
- **NAT:** UPnP for an optional P2P proxy; otherwise everything is relayed over TCP.
- **Terms:** the relay's README says it is for **Ryujinx clients only**, so we must not use `ldn.ryujinx.app`.

**Pia:** emulators do nothing special for Pia; it is just guest UDP. Pokopia has no Pia, and its KTGL transport is simpler (§2.4).

**What carries over to Pokopia:**
1. Pokopia computes broadcast as a hard-coded /24, so the virtual subnet **must be /24**. RyuLDN's /16 would put broadcasts on 10.114.0.255.
2. Source addresses must equal NodeInfo IPs.
3. Advertise-data and NetworkInfo changes must reach stations promptly.
4. A relay over TCP works, but adds head-of-line latency.

---

## 5. Feasibility design: cross-device local co-op in the shared HLE

### 5.1 Shape

```
 game (unchanged)                    our HLE, shared C (Mac / iPhone / Android native, Windows nxrt)
 ─────────────────                   ──────────────────────────────────────────────────────────────
 KTGL LDN session ──nn::ldn──▶ ldn_hle   state machine, NetworkInfo builder, scan/connect, identity
          │                      │
          └──nn::socket (UDP)──▶ vnet   virtual /24 network: virtual sockets, broadcast fan-out
                                 │
                                 ▼
                              mpnet  one real UDP socket per device; session protocol (reliable control
                                     + unreliable datagrams), version gate, encryption
                                 │                  │
                     LAN discovery (mDNS)     optional relay (rooms, forwarding; self-hostable)
```

The game's own code is not touched. Everything sits at the `nn::ldn` / `nn::socket` API level, which is how nxnative already works.

The same C module serves:
- **Mac and iPhone:** nxnative hooks.
- **Windows:** the nxrt HLE port.
- **Android:** a future native build.

Platform code is limited to discovery, permissions and the UDP socket.

### 5.2 The LDN API to implement (`ldn_hle`)

| API (game imports) | behaviour |
|---|---|
| Initialize / Finalize | None ↔ Initialized; start/stop mpnet; fail with DeviceDisabled (0x2CCB) when the user turned multiplayer off |
| GetState | the state above (the game polls it; no event needed, but keep one for other games) |
| OpenAccessPoint / CloseAccessPoint | Initialized ↔ AccessPoint (Close also tears the network down) |
| CreateNetwork(cfg, sec, user) | AccessPoint → AccessPointCreated. Store the IntentId, NodeCountMax and LocalCommunicationVersion. Set SessionId to 16 random bytes. Take the /24 virtual subnet (§5.3); node 0 is self. Start advertising (§5.5). |
| SetAdvertiseData(p, n ≤ 384) | store; push SyncNetwork to stations; refresh discovery |
| GetNetworkInfo(out) | build the 0x480 NetworkInfo from the session table (Ssid, Bssid/MACs derived from each node's Uid, Channel 1/6/11, LinkLevel 3, NodeCount, Nodes[], AdvertiseData) |
| GetIpv4Address(ip, mask) | own virtual IP, mask 255.255.255.0 |
| OpenStation / CloseStation | Initialized ↔ Station |
| Scan(out, n, max ≤ 24, filter, ch) | ask the discovered hosts for their NetworkInfo (≤ 1 s, as hardware); filter by LCID / SceneId / NetworkType / SessionId as ScanFilter says; **hide incompatible builds** (§5.6) |
| Connect(info, sec, user, ver, opt) | Station → StationConnected after the host's Accept (timeout ~4 s → ConnectTimeout); map host refusals to ConnectRejected / VersionTooLow / VersionTooHigh / TooManyPlayers |
| Disconnect | StationConnected → Station |
| Reject(ip) | host drops that node (DisconnectReason Rejected on the station) |
| MakeSubnetMask / MakeBroadcastAddress | pure SDK helpers; let the SDK run |

**Loss of the peer or host:** StationConnected → Station or Initialized, with SignalLost or DestroyedBySystem. The game's polling notices the change.

**Also hook** (present in nxnative today for other games): AttachStateChangeEvent, DestroyNetwork, GetDisconnectReason, GetNetworkConfig, SetStationAcceptPolicy and GetSecurityParameter. CreateNetworkPrivate and ConnectPrivate can stay cut.

### 5.3 Virtual network and sockets (`vnet`)

**Subnet:** 169.254.*S*.0/24, as real LDN uses link-local addresses. *S* is one byte derived from the SessionId, and nodes are .1 to .8 (node id + 1). So:
- `MakeBroadcastAddress(ip, MakeSubnetMask(24))` yields 169.254.*S*.255;
- GetIpv4Address, NodeInfo.Ipv4 and the recvfrom source addresses all agree.

**Virtual sockets:** while an LDN network exists, a guest UDP socket is virtual. This applies to sockets created after Initialize, or bound to `INADDR_ANY` or the virtual IP. The game creates no other sockets offline; any other socket keeps today's ENETDOWN behaviour.

The virtual subset needed is small:

| call | behaviour |
|---|---|
| `Socket(AF_INET, DGRAM[\|NonBlock], 0/17)` | create the virtual socket |
| `Bind` | port 49530 per node: **no host port is used**, so two instances on one machine do not clash |
| `SetSockOpt(SOL_SOCKET, SO_BROADCAST)` | record it |
| `SendTo` | unicast to a node, to 169.254.*S*.255 or to 255.255.255.255 |
| `RecvFrom(MSG_DONTWAIT)` | must return −1 with **EAGAIN (35)**, not ENETDOWN, when empty |
| `Close`, `GetSockName`, `Poll` / `Select` | on virtual fds |

**Delivery:**
- **Broadcast:** delivered by fan-out to every other node's virtual socket on that port. It never becomes a real broadcast. That avoids iOS's multicast entitlement and Wi-Fi broadcast loss.
- **TCP:** not needed (the game uses none on this path); answer EOPNOTSUPP.

**Limits:** cap datagrams at 4,096 bytes of payload (the game's limit is 4,093). Queue up to ~256 datagrams per socket, dropping the oldest, as UDP does.

### 5.4 Session protocol (`mpnet`)

**Step 1 chose otherwise:** our own TCP control link plus UDP game datagrams, without ENet ([docs/coop-step1.md](../docs/coop-step1.md)). The
design below is the original proposal.

**Transport:** one real UDP socket per device, on an ephemeral port.

**Library:** use **ENet**, which is MIT licensed, portable C, and used by many games. It provides:
- connection management and keepalive;
- a reliable ordered channel for control;
- an unreliable channel for game datagrams;
- fragmentation.

**Topology** is a star around the host (the LDN access point). Stations talk only to the host, which forwards station-to-station datagrams. That costs one extra hop on LAN (< 1 ms) and makes NAT and relay handling identical.

Control messages, reliable:

| message | from | content |
|---|---|---|
| `Hello` | joiner | protocol version, compat key (§5.6), Uid, nickname, platform, LocalCommunicationVersion, session id wanted |
| `ScanReq` / `ScanResp` | any / host | host's current NetworkInfo (0x480, RLE- or zlib-compressed) |
| `Accept` / `Refuse(reason)` | host | node id + virtual IP + full NetworkInfo / ConnectRejected, VersionTooLow/High, TooManyPlayers, SameUser |
| `Sync` | host | NetworkInfo after every join, leave or advertise change |
| `Leave` / `Kick` | either / host | Disconnect / Reject |

**Data messages**, unreliable and unsequenced: `Dgram{src vIP:port, dst vIP:port, payload}`. The game has its own resend and ordering, so the transport must not add head-of-line blocking.

**Timeouts:** ENet's default peer timeout (5–30 s) is bounded to about 10 s. On timeout the host sends `Sync` without that node, and the station's state drops with SignalLost.

### 5.5 Discovery and relay

**LAN:**
- The host advertises a DNS-SD service `_pokopia-ldn._udp`. The final name needs care, since iOS requires it in `NSBonjourServices`.
- The TXT record carries: protocol version, compat key, title ID, display version, nickname, players/max, link-code flag.
- Scan browses the service (≤ 1 s), sends `ScanReq` to each host and returns their NetworkInfo to the game.
- **Fallback:** "Join by address" (IP:port, or a short code shown by the host) for networks that block mDNS, such as guest Wi-Fi and some routers.

**As built in step 1c** ([docs/coop-step1c.md](../docs/coop-step1c.md)):
- The type stayed `_pokopia-ldn._udp`. TXT: `p` (coop protocol 2), `b` (main's build, 16 hex digits), `m` (mods fingerprint), `a` (the host's LAN
  IPv4 as it routes). No nickname, players or link-code flag: the NetworkInfo from the host's link carries what the game shows.
- **Windows 10/11, measured on this PC:** `DnsServiceBrowse` calls back repeatedly with PTR, SRV and TXT records; for a registration on the same
  machine it never delivers an A record, and neither `DnsServiceResolve` nor `DnsQuery_W` resolves the registered host name (9005 / timeout).
  Hence `a=`. Cancelling a browse ends in an `ERROR_CANCELLED` callback; register and deregister each call back once.
- **macOS:** mDNSResponder resolves an instance on the same Mac to 127.0.0.1 on lo0.

**Internet (optional relay):**
- A small self-hostable server keeps rooms keyed by a room code that the host displays.
- It forwards ENet packets between members and sees only encrypted payloads (§5.7).
- It can also exchange observed public addresses so peers try direct UDP hole punching first, falling back to forwarding.
- The game host remains a player's device; the relay never runs the game.
- **Scope:** no persistence, no accounts, and never anything resembling Nintendo's services.
- **Estimated cost:** about 300–600 lines (Go or C).

**As built in step 1d** ([docs/coop-step1d.md](../docs/coop-step1d.md)): `tools/coop-relay`, about 530 lines of C11. Rooms are keyed by a 16-byte id
derived one-way from the room code (HKDF); a host keeps a control link open, the relay announces each visitor, the host dials one link for it, and the
relay pairs the two and copies bytes. Game datagrams use the TCP links through it. No hole punching or address exchange yet: port forwarding or the
relay. Limits on rooms, links, addresses, rates, bytes and time; rooms expire; 127.0.0.1 by default.

### 5.6 Session identity and version matching

**Compat key** = SHA-256 over:
- the title ID;
- the **main module build ID** (2.0.1 = `76a489f3…`, 1.0.5 = `816c5358…`);
- the sdk/subsdk build IDs;
- our mpnet protocol version;
- a **gameplay-mod fingerprint**: active cheats (`hle_cheats.c`) and any runtime patch that changes game state. Look and camera patches are excluded.

**Rules:**
- Scan hides hosts with a different key. Optionally they are listed as "different version (2.0.1)" in a native overlay.
- `Hello` with a different key is refused with VersionTooLow or VersionTooHigh, by comparing display versions, so the game shows its own version message.
- **LocalCommunicationVersion** is compared exactly as the system does (Ryujinx's relay does too). The build-ID key also covers updates that do not change it. Nintendo's rule is the same version for local play (§1).

**Identity:**
- Every install gets a **random persistent account `Uid`**, replacing nxnative's constant. Without that, the second player hits `…EXIST_SAME_USER_ID_USER` (§2.3).
- Randomizing is safe for saves. The phone runs the Eden-created save, made under Eden's user `681FFDE0…`, with nxnative's different constant `Uid`. So the game does not bind its save to the `Uid` (measured indirectly).
- A user-set nickname feeds `GetNickname` and UserConfig.
- MAC addresses are locally administered (`02:…`) and derived from the `Uid`.

**As built in step 1c:** no SHA-256 key. Each side carries main's build ID (first 16 hex digits) and a 64-bit mods fingerprint (FNV-1a over the
names of the cheats that are on; 0 without). The host refuses a JOIN whose build or mods differ (ConnectRejected, after the game's own
LocalCommunicationVersion check); a station hides such hosts from Scan. The `Uid` stays as step 1 left it.

### 5.7 NAT, latency, security

**NAT:**
- **LAN:** none.
- **Internet:** UDP hole punching through the relay, or forwarding.
- Pokopia's LDN mode was designed for local wireless. Its timeouts (`HOST_TIMEOUT`, `RESEND_COUNT`, `COMMUNICATION_QUALITY`) are unknown and may be tuned for low latency.

**Latency** is the main unknown for internet play:
- **Measure:** round-trip time vs disconnects in the prototype, with injected delay of 20, 50, 100 and 200 ms.
- **Expectation:** LAN (Wi-Fi, 1–10 ms) should be fine; a relay across a continent may not be.

**Security:**
- **Authentication and encryption:** sessions use a random key from the host, ENet with an AEAD layer (e.g. libsodium `crypto_secretstream` or Noise_NN plus a room code), and per-peer replay counters.
- **Relay:** forwards ciphertext only.
- **Containment:** virtual sockets can reach **only** session members' virtual IPs, never a real address. There is no SSRF or LAN-scan surface, and nothing is exposed to the guest except the virtual /24.
- **Untrusted input:** game packets from peers feed the game's own parsers, as on hardware, so cap sizes and rates.
- **Strangers:** joining needs the room code or the game's own Link Code (which works unchanged, as it is in-game data). Show nicknames before accepting. The host can kick (Reject).
- **Cheats:** a cheating visitor or host can alter the host's persistent island. Refuse sessions whose mod fingerprints differ, and warn when cheats are on.

**As built in step 1c (LAN only):** the host listens only from CreateNetwork to DestroyNetwork / CloseAccessPoint; links and UDP packets from
anything but loopback, RFC 1918 and 169.254/16 are refused; every frame and record is bounded, malformed input closes its link, and accepts,
frames, scans, joins and datagrams are rate-limited. No encryption yet: anyone on the same LAN can read the traffic.

**As built in step 1d (internet play, an opt-in):** a 16-symbol room code (80 bits) proven both ways by HMAC-SHA256 over fresh nonces, per-link keys
from HKDF, a 128-bit tag on every frame (over an implicit frame count) and datagram (with a sequence number and a 64-entry replay window), failed
proofs limited per address and in all. Integrity and authentication only: still no encryption and no PAKE (a recorded handshake allows offline
guessing; 80-bit codes make that impractical). The relay forwards authenticated traffic it cannot forge but can read.

### 5.8 What each platform needs

**iOS / iPadOS:**
- `NSLocalNetworkUsageDescription` (prompt) and `NSBonjourServices` (our service type).
- Unicast UDP plus Bonjour needs **no** multicast entitlement. Raw broadcast or multicast would need `com.apple.developer.networking.multicast`, a restricted entitlement a personal team cannot get, which is why vnet never broadcasts for real.
- Sessions end when the app goes to the background: iOS suspends it, and the game self-disconnects on return (`…RETURN_FROM_BG`).

Sources: [Local Network Privacy FAQ](https://developer.apple.com/forums/thread/663875), [multicast entitlement thread](https://developer.apple.com/forums/thread/655920).

**macOS:**
- The same Info.plist keys; macOS 15 and later ask for local-network permission too.
- The application firewall may prompt once.

**Android:**
- `INTERNET`, `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE`.
- `NsdManager` for DNS-SD, which needs no runtime permission.
- `WifiManager.WifiLock` (`WIFI_MODE_FULL_LOW_LATENCY`, API 29+) during a session, against Wi-Fi power-save latency.
- `CHANGE_WIFI_MULTICAST_STATE` and a `MulticastLock` only if raw multicast is ever used. `NEARBY_WIFI_DEVICES` is not needed for LAN sockets.

**Windows:**
- **Firewall:** Windows Defender Firewall prompts the first time the app receives unsolicited inbound UDP, so a host on LAN must be allowed on Private networks. Hosting on a "Public" network profile fails silently; detect it and explain.
- **Relay mode** needs no inbound rule, since all traffic is outbound.
- **mDNS:** use `DnsServiceRegister` / `DnsServiceBrowse` (Windows 10 1809+), or a small embedded mDNS library.

- **Measured in step 1c:** this PC's Ethernet is in the Public profile. When a build first listened, Windows added inbound Block rules for that
  exe on Public; traffic to the PC's own LAN address is not filtered, so a same-PC test passes either way. A host now logs a hint when a connected
  network is Public (Network List Manager, read only).

**All platforms:**
- A "Multiplayer" settings page (nickname, LAN on/off, relay address, join by code).
- `ldn::Initialize` returns DeviceDisabled when multiplayer is off.

### 5.9 Does anything assume real hardware or Nintendo services? (blockers)

Each item below is measured statically unless marked otherwise.

| item | finding | impact |
|---|---|---|
| Nintendo services on the local path | none: the ad-hoc device-up calls no nifm, NSA, NPLN or friends (§2.3) | not a blocker; to confirm at run time |
| NSO "multiplay privilege" | `…NEED_MULTIPLAY_PRIVILAGE` belongs to the online session errors | inferred online-only; confirm |
| pctl `TryBeginFreeCommunication` | **measured (§3.1):** the Link Play tile calls it with show-UI true; nxnative's "never" leaves the PC menu with no message | **blocker, confirmed**: answer "allowed" without UI in MP builds (the user owns the device); the research copy does |
| Sockets before CreateNetwork | **measured (§3.1):** KTGL opens and binds its UDP socket before `OpenAccessPoint`; ENETDOWN → application error, `ldn::Finalize` | covered by `vnet` (§5.3); a lone-node version already works in the research copy |
| Same `Uid` on every install | the game rejects a second member with the same user ID | **blocker until changed**: per-install random `Uid` (§5.6) |
| LDN 802.11 layer (channels, action frames, Switch 2 `SetProtocol`, Platform byte, LDN encryption) | hidden below the `nn::ldn` API we replace | not a blocker; set NodeInfo.Platform to 1 ("Ounce", the Switch 2) to match hardware |
| Hard-coded /24 broadcast, fixed port 49530, source-IP identity | §2.4 | design constraints (§5.3), not blockers |
| Determinism across Mac, iPhone and Android (native) vs Windows (recompiled) | KTGL replicates state (host-authoritative messages, `…BLOCK_REPLICATION`) rather than running lockstep | low risk; the recompiler must match ARM FP semantics anyway (05: FPCR 0 everywhere) |
| GameShare (`nn::streamplay`) | separate system streaming feature; stubs suffice | not needed |
| Real Switch 2 consoles in the same session | impossible without 802.11 LDN and Nintendo's keys | out of scope by design |

### 5.10 Size estimate and prototype plan

| part | estimate (our code) |
|---|---:|
| `ldn_hle` (state machine, NetworkInfo, scan/connect, identity) | 700–900 lines C |
| `vnet` (virtual UDP sockets, fan-out, poll/select integration; nxnative's `dm_sk_poll` / `dm_sk_select` already exist) | 500–800 |
| `mpnet` (ENet sessions, control protocol, compat gate, crypto) | 900–1,300 + ENet (~4k, MIT) + libsodium or Monocypher |
| Discovery: Apple dns_sd, Android NSD (JNI), Windows DnsService, join-by-address | 450–700 |
| Relay server | 300–600 |
| Settings UI and permissions (per app) | 200–500 each |
| Two-instance test harness and logging | 300 |
| **Total** | **≈ 3.5–5k lines** |

**Time:** about 2 weeks to two Macs on LAN, and 5–8 weeks to all three shipping platforms plus the relay, for one experienced developer.

**Prototype order:**
0. **Measure (≈ 1 day; needs a save at Environment Level ≥ 2). Done 2026-11-03, §3.1.** Use the existing `DM_MPTRACE=1 DM_MPFAKE=1` host:
   - Choose Link Play → Invite Others → Local, and read the CreateNetwork NetworkConfig (NodeCountMax, LocalCommunicationVersion) and the advertise data.
   - Watch the socket calls (broadcast-relay mode or not), the pctl and account calls, and what the game shows with a lone access point.
   - Repeat with Visit a Friend → Local to see the scan path.
1. **Two instances on one Mac (≈ 1 week):**
   - **Code:** `ldn_hle` + `vnet` + `mpnet` over 127.0.0.1, with no discovery (join by port), each instance with its own save copy and `Uid`. Virtual sockets make the fixed port 49530 a non-issue.
   - **Result:** this proves the game-level protocol end to end.
   - **Machine:** the rented M4 can run two hosts.
2. **Mac ↔ iPhone on LAN:** Bonjour, permissions, Wi-Fi latency.
3. **Windows:** port the same C into nxrt and add the firewall handling.
4. **Relay** with injected latency.
5. **Android**, once its runtime exists.

---

## 6. Legal and ethical boundaries

**In bounds:**
- Emulating local wireless as an API-level reimplementation in our own code (MIT). This is what Ryujinx and ldn_mitm do.
- Designing from facts read in MIT code and public docs, with no code copied.
- Every player runs their own legally dumped copy. That matches Nintendo's local-play rule that each player owns the game.
- Using our own relay.

**Off limits, and kept cut:**
- Anything that talks to Nintendo:
  - NPLN (`*.npln.srv.nintendo.net`, gamesync, matchmaking, ugcstore, friends);
  - NSA and ID tokens (`EnsureNetworkServiceAccountIdTokenCacheAsync` etc.);
  - friend invitations, BCAT, play reports, the network clock;
  - NSO checks, GameChat.
- **Cloud Islands and online Link Play** are server-side Nintendo services. Re-implementing them would mean emulating Nintendo's online platform: not done.
- **Anti-cheat and reporting:** KTGL's block/ban lists and the "report player" flow are online-only and stay cut.
- **Interoperating with real consoles:** would need Nintendo's LDN keys and crypto; not attempted.

**Borderline, flagged:**
1. **GameShare across devices** would let someone without the game play, by streaming the host's second view. Nintendo's own feature allows that, but in our ports it would hand a stream to a non-owner. Recommendation: do not build it.
2. **A public relay** for a commercial game draws attention. Keep it optional, self-hostable and private-room only, with no player directory.
3. **The fixed LDN passphrase** in main's read-only data is not needed by the design. Do not publish it.
4. **Ryujinx's relay** terms forbid non-Ryujinx clients. Do not connect to `ldn.ryujinx.app` and do not reuse its protocol identity.
5. **Cheats in multiplayer** alter another player's island. Gate them with the mod fingerprint (§5.6).
6. **The user's real save** was copied to the rented Mac for this research (`~/mp/saves/eden1103`, also in `~/mp/runs/lp0/save*`). It stays on the PC and the Mac as the rules allow. The user may want it removed when done; nothing here deletes it. The Level 2 save (`~/mp/saves/level2-1103`, and the copies in `~/mp/runs/lp2*/save*`) was copied with the user's OK for testing.

---

## 7. Verdict, blockers, open questions

**Feasible with caveats.**
- **Why feasible:**
  - Local co-op is an `nn::ldn` network plus UDP between KTGL session objects, and nxnative already intercepts both API surfaces.
  - No Pia, NEX, NPLN or account service is on the ad-hoc path.
  - The data plane is small and well-defined: one UDP port, ≤ 4 KB datagrams, /24 broadcast, sources identified by IP.
  - One shared C implementation serves every platform.
- **Caveats:**
  1. **Confirmed at run time (§3.2):** two instances on one Mac play Link Play Local together through our module. Each player needs their own
     save: the game refuses a visitor whose save has the host's 16-byte user ID (error 9005), including saves synced between one player's
     devices.
  2. pctl "allowed" is mandatory (confirmed). A per-install `Uid` is not needed by this game: the same-user check uses the save's ID (§3.2).
  3. Internet play depends on latency tolerances that are unknown until measured.
  4. iOS needs the Bonjour + unicast design, not raw broadcast.
  5. The Windows firewall has to be handled for LAN hosting.

**Open questions:**
1. **Answered (§3.2):** two copies of one save cannot meet: the host refuses with `…APPLICATION_REJECT_JOIN_SESSION_EXIST_SAME_USER_ID_USER`
   (9005). The visitor arrives with the user's second Level 2 save, or with a copy whose save user ID differs by one bit.
2. **Answered:** NodeCountMax is the Number of Players setting, and LocalCommunicationVersion is 301 on 2.0.1 (§3.1). The broadcast-relay
   flag (+0x290) stays off in retail: full two-player sessions use unicast only, with no `SO_BROADCAST` and no destination header (§3.2).
3. **Answered:** the Link Play tile reaches `TryBeginFreeCommunication`, and local play requires it. The same-user check compares the save's
   16-byte user ID (`S00/SYDAT.BIN` +0x61), not the account `Uid`: equal `Uid`s with different save IDs join (§3.2). Which named field it is
   (`m_userID` or `m_userIDVer2`) was not traced.
4. What are KTGL's LDN-mode timeouts? This decides whether internet play over a relay is practical.
5. What is the `banana` service in `main.npdm`? It was not called in any run.
6. Discovery name, relay hosting and who runs it are product decisions for the user.
7. **Spectator Mode:**
   - When does Off become selectable?
   - What exactly does On block for visitors?
   - Which advertise byte carries it?
   This is the game's own visitor restriction, and Route A's natural target (COOP-PLAN).
8. **Answered (§3.2):** the host's 66-byte answer is the application reject 0x87F7F005 (same user ID). The packet bodies themselves were
   not decrypted: KTGL's packet protection is inside main, not in the SDK's `nn::crypto`.

**Files:**
- **Scripts:** `_local/mp/scripts/`:
  - analysis (PC): `strings_dump.py`, `xref.py`, `dis.py`, `strref.py`, `callers.py`, `ldrref.py`, `vtdump.py`, `vt.py`;
  - Mac drivers: `mpdrive.py`, `ia.sh`, `k.sh`, `view.sh`.
- **Mac research host sources:** `_local/mp/macsrc/` (`hle_mptrace.c`, `ldn_fake.c`, `apply_mp_patch.py`). The step-0 versions, with the pctl change and `sock_fake.c`, are in `_local/mp/mac/lp2/macsrc/`. Step 1's patch, which also carries step 0's: `research/mac-coop/`.
- **Analysis outputs (game-derived, local only):** `_local/mp/*.txt` and `*.json`.
- **Run artifacts:** `_local/mp/mac/`.
- **Ryujinx scratch clones:** `_local/mp/ext/`.
- **On the Mac:** `~/mp/` (copy host `nxnative-mp`, runs, saves). `~/nxnative`, `~/installer` and `~/installer-test/*` were not modified.
