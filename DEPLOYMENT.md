# Deploying / moving the QLC+ web-control build to another PC + network

The build is a **self-contained folder** (`C:\qlcplus`): Qt runtime, OpenSSL, plugins, and the
MinGW runtime are all bundled. Moving it needs **no install, no build, no MSYS2** on the target.
The control endpoint is `wss://<domain>/qlcplusWS` (QLC+ Web Access with TLS — see
[SALESFORCE_QLCPLUS_INTEGRATION.md](SALESFORCE_QLCPLUS_INTEGRATION.md)).

## What's fixed vs. what changes per move

| Item | Travels with the folder? | Notes |
|---|---|---|
| App + Qt + OpenSSL + plugins | ✅ yes | copy the whole folder |
| TLS certificate (`cert.pem`/`key.pem`) | ✅ yes | **bound to the domain name, not the PC/IP** — keeps working anywhere as long as the domain resolves to the new location, until it expires (~90 days) |
| Login file (`webpass.txt`), show (`show.qxw`) | ✅ yes | keep them in the folder |
| Router **port-forward** | ❌ redo per network | new router = new forward |
| **DNS** pointer (public IP) | ❌ redo per network | unless using DDNS |
| Windows Firewall rule | ❌ redo per PC | run `setup-new-pc.bat` once |

## Move checklist (minimal work)

### On the OLD PC (once)
1. Make sure these are in `C:\qlcplus`: `cert.pem`, `key.pem`, `webpass.txt`. The
   `start-qlcplus.bat`, `setup-new-pc.bat`, `win-acme\` (the cert tool), and the project itself
   (`SaveFile\Main Project.qxw`, auto-loaded) are **placed there automatically by the build/install**
   (`ninja -C build-mingw install` / `cmake --install build-mingw`, from `deploy/` and `SaveFile/`
   in the repo) — no manual copy needed.
2. Copy the **entire `C:\qlcplus` folder** to the new PC (USB, network share, etc.). Put it
   anywhere — the `.bat` files use relative paths, so the drive/path doesn't matter.

### On the NEW PC (once)
3. Right-click **`setup-new-pc.bat` → Run as administrator** (opens firewall for 9999 + 80).
4. Note the PC's LAN IP: `ipconfig` → IPv4 Address (e.g. `192.168.1.83`). Ideally set it static
   or make a DHCP reservation so it doesn't change.

### On the NEW network (once per venue)
5. **Port-forward** on that router: public **TCP 443 → <PC LAN IP>:9999** (clean URL), and
   **TCP 80 → <PC LAN IP>:80** (for cert renewal). [AT&T gateways: `192.168.1.254` →
   Firewall → NAT/Gaming → Custom Services.]
6. **Point the domain at this network's public IP:**
   - Find the public IP: open `https://whatismyipaddress.com` on that network.
   - Squarespace → Domains → `mayansvip.com` → DNS → set the **A record** `lights` to that IP.
   - *(Or set up DDNS once — see below — and never touch DNS again.)*

### Run
7. Double-click **`start-qlcplus.bat`**. It auto-uses `cert.pem`/`key.pem` (HTTPS/WSS),
   `webpass.txt` (login), and `show.qxw` (project) if present.
8. Test from cellular: `https://lights.mayansvip.com` and `wss://lights.mayansvip.com/qlcplusWS`.

## Make it even more hands-off

- **DDNS (skip DNS edits on every move):** instead of an A record, set the Squarespace record as
  a **CNAME** `lights` → a DDNS host (e.g. `mayansvip.duckdns.org`), and enable that DDNS provider
  in each venue **router's** built-in DDNS client. The router keeps the IP current, so step 6
  becomes automatic. (Runs on the router — nothing extra on the PC.)
- **Certificate (fully automated):** the **win-acme folder already ships inside this folder** as
  `win-acme\` (bundled by the build, so it travels with everything). `setup-new-pc.bat` runs win-acme unattended
  with `--pemfilespath` set to **this folder's current location**, which both issues/refreshes the
  cert here (`lights.mayansvip.com-chain.pem` + `-key.pem`) and registers the auto-renewal to keep
  writing here. Move the folder → re-run `setup-new-pc.bat` → the renewal auto-re-points. No manual
  win-acme, no path to remember.
- **No conversion, no restart on renewal:** `start-qlcplus.bat` auto-uses those PEM files, and QLC+
  **auto-reloads** them when they change. `setup-new-pc.bat` also flips the win-acme task to "run if
  a start was missed," so a powered-off PC catches up at boot. win-acme renews ~30 days before
  expiry (HTTP-01, needs port 80); if the PC is off through the whole window it self-heals on next
  run, or force with `wacs.exe --renew --force`.
- The cert step needs **DNS + port 80 reachable** when it runs, so run `setup-new-pc.bat` *after*
  the port-forward + DNS are set for that venue.
- **CGNAT warning:** if a venue's ISP doesn't give a real public IP (public IP on
  whatismyipaddress ≠ router WAN IP), port-forwarding can't work there; that network would need
  an outbound tunnel instead (extra software on the PC).

## Security (do not skip when public)
Always run with `webpass.txt` present (login required) and a strong password. The endpoint is
internet-reachable; without auth anyone with the URL can control the lights.
