---
name: laptop-network
description: How the show laptop's Wi-Fi and Ethernet dongle are addressed, and the static-IP trap that broke IPv4 off-venue
metadata:
  type: project
---

Show laptop (LAPTOP-58LCT300) network setup, as of 2026-09-28:

- **Wi-Fi** (Intel AX201, MAC `3C-21-9C-E9-CE-38`): **DHCP** for IPv4 and DNS.
  At the venue, the router has a **DHCP reservation** so the laptop still gets
  `192.168.4.32` on `192.168.4.0/22` (gateway `192.168.4.1`). The show files bind the OSC
  input/feedback to the interface named `192.168.4.32` (e.g. `SaveFile/Main Project.qxw`),
  and TouchOSC targets that address.
- **USB Ethernet dongle**: static `192.168.5.10/24`, no gateway, no DNS. This is the Art-Net
  side (node `192.168.5.162`); see [[club-rig-mayans]].

**Why:** from Jun 25 to Sep 28, 2026 the Wi-Fi had a *static* `192.168.4.32/22` + gateway
`192.168.4.1` (+ DNS `192.168.1.1`). Windows applies a static IP per adapter, not per SSID,
so on any other network (home AT&T "Soju" = `192.168.1.0/24`, gw `.254`) IPv4 was dead and
the laptop was online over IPv6 only. Google etc. still loaded; IPv4-only sites
(gdtf-share.com, github.com) timed out.

**How to apply:** if "some sites load, others don't" on this laptop, check the Wi-Fi IPv4
first (`ipconfig /all`: DHCP Enabled, gateway pingable). Keep Wi-Fi on DHCP and pin the
venue address with a router reservation, not a static IP. Note that `192.168.4.0/22` covers
`192.168.5.x`, so with the dongle plugged in, traffic to `192.168.5.x` (e.g. OSC feedback
to a tablet at `192.168.5.3`) leaves via the dongle's `/24` route, not Wi-Fi.
