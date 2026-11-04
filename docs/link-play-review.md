# Link Play on the local network: changes for review

Not released and not in the README yet. What changed in this repository for Link Play ("Visit a Friend" / "Invite Others" → **Local
connection**) between the iPhone app, the Mac app and the Windows build, on one Wi-Fi network. The design, the tests and the test steps are in
the Windows port's `docs/coop-step1c.md`.

## Files

- `host/nxnative-switch2.patch` (34 → 46 files; the 32 sections Link Play does not touch are unchanged byte for byte):
  - new: `dm/host/coop.h`, `coop_int.h`, `hle_coop_ldn.c`, `hle_coop_vnet.c`, `hle_coop_mpnet.c`, `hle_coop_plat.c`, `hle_coop_disc.c`. The co-op
    module, our own MIT code, the same files the Windows build uses;
  - `dm/host/hle_offline.c`: `nn::ldn` goes to the module while Link Play is on (else cut, as before); the game's identity for the join check is
    main's build ID and the cheats that are on;
  - `dm/host/hle_net.c`: the game's UDP sockets become the module's virtual sockets while Link Play is on;
  - `dm/host/hle_misc.c`: parental controls answer "allowed" only while Link Play is on;
  - `dm/games/settings.tsv`: the launcher's **Link Play on this network** switch (`DM_COOP` = `auto` / `off`, off by default), iPhone and Mac;
  - `dm/ios/project.yml`, `dm/mac/pack_app.py`: `NSLocalNetworkUsageDescription` ("Link Play uses your local network to find and visit other
    players' towns.") and `NSBonjourServices` (`_pokopia-ldn._udp`);
  - `docs/env_switches.tsv`: `DM_COOP`, `DM_COOP_DISCOVERY`, `DM_COOP_TCP`, `DM_UID`.
- `host/windows/build_app.patch`: the same two Info.plist keys in IPAs built on Windows.
- `host/UPSTREAM.md`: the patch's description.

## What a player sees

- Off by default. With the switch on, the first Link Play use asks for local network access once.
- Hosting and visiting work as on the console: the visitor's "Local connection" list shows towns hosted on the same network (found with
  DNS-SD/Bonjour). Where a router blocks that, `DM_COOP=join:<host's IPv4>` in `Documents/env.txt` joins by address.
- Only devices on the local network can connect (private and link-local addresses); the host listens only while it hosts; both sides must run the
  same game build and the same cheats.
- Each player needs their own save: the game refuses a visitor whose save is a copy of the host's.

## Checked

- Built on the Mac with `nxs2.py setup` and `nxs2.py build … --ipa` (the module builds without warnings); two Mac instances found each other
  and the visitor arrived in the host's town. nxnative's `test_launcher_settings` and `test_lint_c` pass; `test_env_switches` and `test_docs` fail
  as they did before this change.
- Not yet: the Windows builder with the new patch (setup re-applies it on the next run), and the iPhone itself.
