# Open decisions: GPU persistence, crash forensics, BIOS decoder

Research from 2026-10-25. Each topic had one researcher and three skeptics, and the skeptics' corrections are folded in. Tool facts already acted on are in [reuse-tools.md](reuse-tools.md). Each topic ends with a recommendation; the choice is the user's.

## 1. Applying and persisting GPU settings

**The question before choosing a tool: what may persist?** The campaign model says a setting is trusted only after `signoff` and days of real use (SKILL.md, step 6). A tool that re-applies settings at every boot makes them active during later RAM and CPU campaigns too. If an undervolt fails at the edges, it then shows up as noise in other domains' tests. Proposed policy: only signed-off settings persist; experiments stay runtime-only and a reboot clears them.

| Option | Pros | Cons |
|---|---|---|
| **A. LACT** (Flatpak or COPR `lact-headless`) as the only NVML writer. ctune talks to its socket. | Maintained, and makes the same kind of NVML calls (per-pstate offsets through the newer `nvmlDeviceSetClockOffsets`). Re-applies at boot, after resume and after driver reloads. Has a confirm-or-revert timer and a `lact-reset` kernel argument. Adds a GUI, VF-curve editor and fan curves. No password prompts. | **Security:** the socket (0660) accepts passwordless writes from any process running as the user, including the agent. It also allows NvAPI voltage boost and VF-curve points, and `enable_overdrive`, which stages `rpm-ostree kargs` as root. The Flatpak's root daemon also searches the user-writable `~/.local/share/flatpak` for extensions (untested whether that allows injection). **During tests:** any DRM uevent (monitor hotplug or sleep) runs `reset_clocks` and then re-applies, a brief drop to stock that a readback cannot see. Holding a change pending for a test is off-label: the timer is global, blocks other edits, and can stall readbacks while pending. **Updates:** Bazzite's daily `uupd` updates the Flatpak or the layered RPM with no pin. A confirmed setting is re-applied on a new driver without a re-test. **Coexistence:** LACT and ctune's helper cannot both write; LACT zeroes the other writer's offsets. |
| **B. ctune's pkexec helper plus a root-owned boot unit** that applies the last signed-off settings. | Keeps ctune's caps and the per-step password prompt, which the skill treats as consent. Only signed-off values persist. No third-party root daemon. Can reuse vendor tools: `nvidia-smi -pl` (power) and `-lgc` (clock lock); only the offset needs NVML. | New privileged code. It must be root-owned and outside the repo (`/usr/local/libexec` plus a settings file in `/etc`), or it becomes a passwordless privilege-escalation path. Needs a crash-loop guard (a boot counter, or `systemd.mask=` on the kernel command line), a resume hook (untested whether NVML settings survive suspend), and a driver-update re-test policy. No GUI or VF curve. |
| **C. nvidia_oc** (Dreaming-Codes/nvidia_oc, MIT), a stateless NVML CLI for power limit, offsets and min/max clock, in the boot unit of B. | Reuses an existing tool for the boot unit, and is documented for exactly that use. | Small project; quality and maintenance unverified. No caps, no re-apply after resume. Still needs B's root-owned placement and guards. |
| **D. Hybrid:** LACT holds only the signed-off baseline; ctune experiments with LACT detached (`detach_gpu`, v0.10.0). | Persistence comes from LACT; experiments keep the prompt. | Two writers. Detach lives only in memory and is lost when lactd restarts. Relies on APIs about three months old. The passwordless socket still exists. Both skeptics rated it the most fragile option. |

**Recommendation: B, with C or `nvidia-smi` doing the boot-time apply, and only signed-off settings persist.** Choose A only if you want LACT's GUI and VF curve, and accept passwordless GPU and kernel-argument changes from your user account. If you choose A, set `daemon.disable_nvapi: true`, pin the version (`flatpak mask`), and keep LACT out of RAM and CPU campaigns. Nothing is urgent: the GPU campaign can run on runtime-only settings, which is how ctune works today.

## 2. Crash forensics

Already done in ctune, no install needed:
- `check_interrupted` records the crashed boot's newest error lines and an unfiltered tail, plus the next boot's hardware-error and `Previous system reset reason` lines. It says `journal_lost` when the crashed boot has rotated out, and it no longer reads `-b 0` by mistake.
- The marker now fsyncs its directory.
- Ctune errors and stops are recorded in the journal.

| Option | Pros | Cons |
|---|---|---|
| **Enable `ctune boot-check --install`** (built): a user unit that runs at each boot, records a cut-short test, and saves that boot's hardware lines to `data/boots/`. | No root and no system change; a user unit, removable with `systemctl --user disable`. Evidence survives the journal flood (next row). | One more unit at boot. It captures only what the kernel logged, and only from the next boot on. |
| **Quiet the ollama container's logging** (podman `k8s-file` log driver with rotation, a lower llama-server log level, or `LogLevelMax=` on its unit). | This is the real reason evidence disappears: up to about 750k lines an hour in bursts, so even with the 1 GB cap `journalctl -b -1` holds only hours. Fixing it protects every log, not just ctune's. | Changes your ollama setup; its logs move to a file or get shorter. |
| **ramoops** (`ujust toggle-save-panics enable`): two kernel arguments plus `kernel.panic=5`, and a reboot. | Bazzite's own one-command recipe, with a disable path; costs 64 KiB of RAM. Saves the panic or oops text that journald cannot write after the kernel stops. Non-ECC memory instability often ends in exactly that kind of oops or panic. `kernel.panic=5` turns a panic into a reboot, so an unattended sign-off comes back. | Captures nothing for sync-flood, watchdog or shutdown resets or hard hangs: the usual Curve Optimizer and FCLK failures. `kernel.panic_on_oops=0` here, so an oops alone neither reboots nor dumps. Unverified whether this board keeps the reserved RAM through a reset; the only test is a deliberate panic (`sync` first, stop the database containers). systemd-pstore can race the module load without an extra drop-in. |
| **Hardware watchdog:** sp5100_tco plus systemd `RuntimeWatchdogSec=`. | Turns a hard hang during an unattended run into a reset, which the next boot labels as a watchdog reset. No install. | System config change. `/etc/modprobe.d/deck-blacklist.conf` blacklists sp5100_tco (a Steam Deck default), yet the module is loaded here (`watchdog0`, 60 s); find out why before relying on it. A false trigger reboots the machine. |
| **rasdaemon** (layered package). | Durable SQLite record of machine-check, AER and memory-failure events, independent of journal retention. | Starts too late to record the leftover banks the kernel logs at boot after a crash, and misses fatal errors that panic first. Adds three layered packages (rasdaemon, perl-DBD-SQLite, perl-DBI). Fedora's 0.8.0 does not show the SMCA error text in `--errors`. (The "cpu +1" report against it is a misreading: the field is the CPU vendor.) |
| **efi_pstore** instead of ramoops (`efi_pstore.pstore_disable=0`). | Survives power loss, and does not depend on the board keeping RAM through a reset. | Writes UEFI NVRAM at panic time; firmware bugs can corrupt NVRAM. Only one pstore backend can run at a time. |
| **kdump**. | The standard full crash dump. | Needs kexec-tools layered, and the NVIDIA driver complicates it. Rejected. |

**Recommendation:** enable `boot-check` and quiet ollama's logging first. Both are cheap and fix the evidence actually lost. Then turn on ramoops, and keep it only if a deliberate test panic survives on this board; it helps the RAM campaign most. Skip rasdaemon and kdump. Consider the hardware watchdog for unattended sign-offs once the blacklist is checked.

## 3. BIOS setup decoder (mem domain)

**Fixed on 2026-10-25, before the flash.** F19c's Setup (Tweaker) varstore grows from 646 to 650 bytes, and 418 questions shift by +4 or +5 bytes. For example, CAS Latency moves from Setup@368 to @373, tRFC from 394 to 399, and DRAM Voltage from 508 to 513. `KEY` and `GBT_VOLT` in `bios_settings_decode.py` were keyed by F16b offsets and would have attached values to the wrong settings without any error.
- **The fix.** They are now keyed by varstore, menu path and prompt, as `verify.py` already was. `find_row` refuses a location that matches several storage locations rather than guessing; an op or width filter separates them.
- **Retired.** `CHECKS` hard-coded this machine's tuned values (tCL 14, tRFC 288), which fail by design after "Load Optimized Defaults". `verify.py`'s live-hardware check (`mem_verify`) covers the same settings.
- **Checked.**
  - On the F16b snapshot, all 118 entries resolve to their old offsets, and the decoded questions, settings and focus tables are unchanged.
  - On the F19c image (Setup, the 0x7a19 CBS, AOD and PBS forms carved from the LZMA volume at 0x1b10e88), 102 entries resolve: the Tweaker ones moved +5 (60) or +4 (3), and CBS and AOD did not move. The other 16 are the per-core CO questions, which are not in the image's forms because AGESA adds them at boot.
  - Every prompt `verify.py` uses exists on F19c.
- **Tests.** `tests/test_bios_keys.py` with both layouts in `tests/fixtures/bios_key_rows.json`.
- **After the flash.** The decoder lists any key setting it cannot find, and `mem_snapshot` returns it as `bios_key_problems`.
- **Not affected.** `mem_checklist` reads the pre-flash snapshot.

| Option | Pros | Cons |
|---|---|---|
| **Cross-check with IFRExtractor-RS** (LongSoft, v1.6.1 release binary in `tools/`), once per BIOS version, as an offline oracle. | An independent parser of the same HiiDB dump, run unprivileged and offline. Catches string-ID, opcode-field and missed-question errors on a new BIOS. | Builds no form paths, joins no varstores, evaluates no conditions, and does not show value widths, so it checks only the structural layer. It pairs every form package with every en-US string package (117 outputs for 9 real pairs), so the dump must be split first, without relying on ctune's own list walker. Exits 0 even when some opcodes fail to parse. Unsigned binary with no attestation. Key the check on the BIOS version, not the HiiDB hash, because the runtime HiiDB embeds live values. |
| **Replace ctune's parser** with IFRExtractor-RS. | Less IFR code to maintain. | It emits text, so ctune would need a new text parser and still keep everything it does not provide: paths, conditions, defaults, values. Rejected by every reviewer. |
| **Cheap built-in completeness checks** in each snapshot: zero refused pages in `dump_fw_mem.log`, package-list lengths summing to the HiiDB size, an END package per list. | No new tool; catches a truncated or zero-filled dump, which would otherwise decode silently. | Covers the dump only, not the parser. |
| **Retire or parametrize `tools/verify_hii_settings.py`** (a second in-house parser with a hard-coded scratch path). | Removes duplicate parsing, or makes the check repeatable on F19c. | Loses an independent parser if it is retired without IFRExtractor-RS taking its place. |

**Recommendation:**
1. ~~Re-key `KEY`, `CHECKS` and `GBT_VOLT` by menu path plus prompt before the flash.~~ Done.
2. Add the completeness checks.
3. Adopt IFRExtractor-RS as a per-BIOS-version cross-check that replaces `verify_hii_settings.py`.

Separately, the visibility bug: a form reached through several REF paths keeps only the first parent's conditions (`hii_ifr.py` L617-640). Visibility has to OR over every incoming path. Neither external tool evaluates visibility, so no cross-check will catch it.
