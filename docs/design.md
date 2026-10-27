# Design

Why ctune persists GPU settings the way it does, how its passwordless root path is bounded, how it keeps crash evidence, and how the BIOS decoder survives a BIOS update. The tool facts these rely on, with their sources, are in [research/reuse-tools.md](research/reuse-tools.md).

## 1. GPU settings: what persists

**Policy: only signed-off settings persist.** A setting is trusted only after `signoff` and a few days of real use (the skill's step 6). A tool that re-applies settings at every boot keeps them active during later RAM and CPU campaigns, so an undervolt that fails at its edges would show up as noise in other domains' tests. Experiments therefore stay runtime-only, and a reboot clears them.

**Design.** Runtime steps go through ctune's own guarded root helper. After a green sign-off of a GPU campaign, `gpu_persist` writes the signed-off settings to `/etc/ctune/gpu.json`, and a root-owned boot unit applies them at every boot with nvidia_oc (Dreaming-Codes/nvidia_oc, MIT), a stateless NVML CLI. `nvidia-smi` sets the power limit (`-pl`) and the clock lock (`-lgc`) but not the clock offsets an undervolt needs, so nvidia_oc does all four, with the same NVML calls as ctune's helper. The code is `ctune/gpu/persist.py`, `install.py` and `boot_apply.py`; the skill's [gpu-nvidia reference](../.claude/skills/ctune/reference/gpu-nvidia.md) describes its use.

The boot-side code is root-owned and lives outside the repo (`/usr/local/libexec/ctune`, settings in `/etc/ctune`). Code the user can write that runs as root at boot would be a privilege-escalation path.

**Safeguards.**
- `plan()` requires a full-length gpu phase in a green sign-off, so experiments never persist.
- The power limit and the clock lock are applied before the offsets, with a rollback to stock on any failure.
- Resume follows the boot result. Whether NVML settings survive suspend is untested.
- The guard counts Xid errors after the apply.
- A start timeout, and the `ctune.gpu=off` kernel argument to skip the apply.
- A failed persist removes its config.

**Alternatives not chosen.**
- **LACT as the only NVML writer** (Flatpak or COPR `lact-headless`), with ctune talking to its socket. LACT is maintained, re-applies after boot, resume and driver reloads, and adds a confirm-or-revert timer, a `lact-reset` kernel argument, a GUI, a VF-curve editor and fan curves. Against it:
  - Security: its socket (0660) accepts passwordless writes from any process running as the user, including the agent. That covers NvAPI voltage boost, VF-curve points and `enable_overdrive`, which stages kernel arguments as root. The Flatpak's root daemon also searches the user-writable `~/.local/share/flatpak` for extensions.
  - During tests: any DRM uevent (monitor hotplug or sleep) resets the clocks to stock and re-applies them, a brief drop a readback cannot see. The confirm timer is global, blocks other edits and can stall readbacks, so holding a change pending for a test is off-label.
  - Updates: automatic updates (Bazzite's daily `uupd`, for example) replace LACT with no pin, and a confirmed setting is re-applied on a new driver without a re-test.
  - Coexistence: LACT zeroes another writer's offsets, so it and ctune's helper cannot both write.

  Anyone who wants LACT's GUI and VF curve anyway should set `daemon.disable_nvapi: true`, pin its version (`flatpak mask`), and keep it out of RAM and CPU campaigns.
- **A hybrid**, with LACT holding the signed-off baseline and ctune experimenting while LACT is detached (`detach_gpu`). Two writers, and the detach lives only in memory, so it is lost when lactd restarts. The most fragile option.

## 2. Passwordless root path

**What it is.** `ctune passwordless` (one password prompt) installs root-owned copies in `/usr/local/libexec/ctune`: `gpu-runtime-apply` (`ctune/gpu/runtime_apply.py`), `cpu/apply.py` with `smu.py`, and `mem/zen3_am4/collect_root.py` with the modules it loads. A polkit rule, `/etc/polkit-1/rules.d/49-ctune.rules`, lets one user run exactly those three programs, as root, through pkexec with no password. The tools use them when they are installed, so GPU apply and reset, CPU probe, apply and reset, and `mem_snapshot` run unattended.

**Accepted risk.** Any process running as that user can change the GPU offsets, clock lock and power limit and the CPU margins within ctune's caps, and read what a RAM snapshot reads. The CPU guardrails bind each call, so a caller that loops can walk a core across -30..30; that range is the cap.

**What each helper accepts.**
- GPU: numbers only (no path or tool arguments). `gpu-boot-apply` is loaded from its fixed root-owned path. ctune's caps apply, and the power limit stays between the GPU's minimum and its default. No file is written, and a failed apply or readback returns to stock.
- CPU: the installed helper keeps its state in the root-owned `/run/ctune/cpu-co.json` and refuses `--state` and `--owner`, because a caller could otherwise forge the probe or the last-known margins. It takes an exclusive lock, and the margin guardrails stay in the helper.
- RAM: the collector only reads: SMN registers from a fixed list, dmidecode, and `/dev/mem` at addresses from the firmware's own tables. It takes only `--skip-devmem`, runs one at a time in a private temp directory under `/run/ctune`, and prints everything as JSON. It skips the bulk ACPI NVS dumps, which can hold other firmware data once the kernel runs with `iomem=relaxed`; a snapshot that needs them runs `ram_snapshot.py` as root behind the password. `ram_snapshot.py` writes the snapshot as the user and refuses a returned path outside it.

**Hardening.**
- The rule names the resolved path, because pkexec hands polkit `/var/usrlocal/...` on image-based systems, and it requires the target user root.
- The shebangs are `-IB` as one flag group, because Linux passes everything after the interpreter as one argument.
- The installer creates its temp files exclusively at 0600 under umask 022, because pkexec keeps the caller's umask.
- Every helper creates `/run/ctune` with mode 0755.

**What stays prompted.** `gpu_persist`, `gpu_unpersist` and the installer keep their password prompt: they install root-owned code from the repo, which the user can write. Compare the sha256s the installer reports with the repo's files.

**Removal.** `ctune passwordless --remove`; `ctune gpu unpersist --uninstall` removes it too.

## 3. Crash evidence

A failed RAM, fabric or Curve Optimizer step often ends in a freeze or a reset, and the evidence lives in the crashed boot's log. ctune keeps it without extra installs:
- Every test writes a crash marker, and the marker's directory is fsynced. `check_interrupted` turns a marker left by an earlier boot into a red result with the crashed boot's newest error lines and an unfiltered tail, plus the next boot's hardware-error and `Previous system reset reason` lines. It reads the crashed boot's own log (not always `-b -1`) and reports `journal_lost` when that boot has rotated out of the journal.
- ctune's own errors and stops are recorded in its journal.
- `ctune boot-check --install` adds a user unit that runs at every boot, records a test cut short by a crash, and saves that boot's hardware-error lines under `data/boots/`. It needs no root and changes nothing system-wide; `systemctl --user disable ctune-boot-check` removes it. It matters because a chatty service can log hundreds of thousands of lines an hour, so even a 1 GB journal may hold only hours of the previous boot. Rate-limiting such a service (`LogLevelMax=` on its unit, a lower log level, or a rotated file log driver for a container) protects every log, not just ctune's.

**Platform options** for what the journal cannot catch. They are system changes outside ctune:

| Option | Catches | Costs and limits |
|---|---|---|
| **ramoops** (on Bazzite: `ujust toggle-save-panics enable`, two kernel arguments plus `kernel.panic=5`) | The panic or oops text journald cannot write once the kernel stops. Non-ECC memory instability often ends that way. `kernel.panic=5` turns a panic into a reboot, so an unattended sign-off comes back. | 64 KiB of RAM. Nothing for sync-flood, watchdog or shutdown resets or hard hangs, the usual Curve Optimizer and FCLK failures. With `kernel.panic_on_oops=0` an oops alone neither reboots nor dumps. Whether the board keeps the reserved RAM through a reset is up to the firmware. systemd-pstore can race the module load: the stock unit waits only for efi_pstore, so a drop-in has to order `systemd-pstore.service` after `modprobe@ramoops.service`. |
| **Hardware watchdog** (sp5100_tco plus systemd `RuntimeWatchdogSec=`) | Turns a hard hang during an unattended run into a reset, which the next boot labels as a watchdog reset. | A false trigger reboots the machine. Some images blacklist sp5100_tco (Bazzite's `deck-blacklist.conf`, a Steam Deck default), so check whether the module is really loaded and why. |
| **rasdaemon** | A durable SQLite record of machine-check, AER and memory-failure events, independent of journal retention. | Starts too late to record the leftover banks the kernel logs at boot after a crash, and misses fatal errors that panic first. Fedora's 0.8.0 does not show the SMCA error text in `--errors`. Three extra packages. |
| **efi_pstore** instead of ramoops (`efi_pstore.pstore_disable=0`) | Survives power loss and does not depend on RAM surviving a reset. | Writes UEFI NVRAM at panic time, and firmware bugs can corrupt NVRAM. Only one pstore backend runs at a time. |
| **kdump** | The full crash dump. | Needs kexec-tools, and the NVIDIA driver complicates it. Not recommended. |

**Recommended order.** First `boot-check`, and rate-limiting any service that floods the journal: both are cheap and keep the evidence that is actually lost. Then ramoops, kept only if a deliberate test panic survives a reset on the board. Run that test once the BIOS settings are final, because the firmware decides whether RAM survives a warm reset:
1. Check that `dmesg | grep -i ramoops` shows the reserved region.
2. Save your work and stop services that write to disk.
3. Run `sync`, then `echo c | sudo tee /proc/sysrq-trigger`. The kernel panics and reboots after 5 s.
4. After the boot, a new directory in `/var/lib/systemd/pstore` holding `dmesg-ramoops-*` means ramoops works on this board. Nothing there means it does not: on Bazzite, remove it with `ujust toggle-save-panics disable` and delete the systemd-pstore drop-in.

Consider the hardware watchdog for unattended sign-offs. Skip rasdaemon and kdump.

## 4. BIOS setup decoder (mem domain)

**Settings are keyed by location, not by byte offset.** A BIOS update can move questions inside a varstore. On the B550 AORUS PRO, F19c grows the Setup (Tweaker) varstore from 646 to 650 bytes and shifts 418 questions by 4 or 5 bytes: CAS Latency moves from Setup@368 to @373, tRFC from 394 to 399, and DRAM Voltage from 508 to 513. A decoder keyed by offset would attach values to the wrong settings without any error. So `KEY` and `GBT_VOLT` in `bios_settings_decode.py` are keyed by varstore, menu path and prompt, as `verify.py` is.
- `find_row` refuses a location that matches several storage locations rather than guessing; an op or width filter separates them.
- The decoder lists any key setting it cannot find, and `mem_snapshot` returns it as `bios_key_problems`.
- The per-core Curve Optimizer questions are not in a BIOS image's forms, because AGESA adds them at boot, so they resolve only from a live snapshot.
- `tests/test_bios_keys.py` checks both layouts (`tests/fixtures/bios_key_rows.json`).
- `mem_checklist` reads the last pre-flash snapshot, so it is not affected by a layout change.

**Tuned values are never hard-coded as checks.** They fail by design after "Load Optimized Defaults". `mem_verify` compares the BIOS configuration with the running hardware instead.

**Possible cross-checks.**
- **IFRExtractor-RS** (LongSoft) as an offline oracle, run once per BIOS version: an independent parser of the same HiiDB dump that catches string-ID, opcode-field and missed-question errors. It builds no form paths, joins no varstores, evaluates no conditions and does not show value widths, so it checks only the structural layer. It pairs every form package with every string package, so the dump has to be split first; it exits 0 even when some opcodes fail to parse; and its binary is unsigned. Key the check on the BIOS version, not the HiiDB hash, because the runtime HiiDB embeds live values. Replacing ctune's parser with it was rejected: it emits text, and ctune would still need everything it does not provide (paths, conditions, defaults, values).
- **Completeness checks** in each snapshot: zero refused pages in `dump_fw_mem.log`, package-list lengths summing to the HiiDB size, and an END package per list. They catch a truncated or zero-filled dump, which would otherwise decode silently.
- `ctune/mem/platforms/zen3_am4/tools/verify_hii_settings.py` is a second, independent in-house parser (`CTUNE_SNAPSHOT=<snapshot dir>`).

**Known limitation.** A form reached through several REF paths keeps only the first parent's conditions (`HiiDb._build_paths` in `hii_ifr.py`); visibility should OR over every incoming path. No external tool evaluates visibility, so no cross-check catches it.
