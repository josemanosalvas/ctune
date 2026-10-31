# Design

This note explains why ctune persists GPU settings the way it does, what limits its passwordless root path, how it keeps crash evidence, and how the BIOS decoder keeps working after a BIOS update. [research/reuse-tools.md](research/reuse-tools.md) has the tool facts these choices rely on, with their sources.

## 1. GPU settings: what persists

**Only signed-off settings persist.** ctune trusts a setting only after `signoff` and a few days of real use (the sign-off in `.claude/ctune/campaign.md`). A tool that re-applies settings at every boot keeps them active during later RAM and CPU campaigns, so an undervolt that fails at its edges would show up as noise in the other domains' tests. Experiments therefore stay runtime-only, and a reboot clears them.

**Design.** Runtime steps go through ctune's own guarded root helper. After a green sign-off of a GPU campaign, `gpu_persist` writes the signed-off settings to `/etc/ctune/gpu.json`. A root-owned boot unit then applies them at every boot with nvidia_oc (Dreaming-Codes/nvidia_oc, MIT), a stateless NVML CLI. `nvidia-smi` sets the power limit (`-pl`) and the clock lock (`-lgc`) but not the clock offsets an undervolt needs. nvidia_oc sets all four values, with the same NVML calls as ctune's helper. The code is in `ctune/gpu/persist.py`, `install.py` and `boot_apply.py`, and the ctune-gpu skill's [persistence reference](../.claude/skills/ctune-gpu/reference/persist.md) describes how to use it.

The boot-side code is root-owned and lives outside the repo, in `/usr/local/libexec/ctune`, with its settings in `/etc/ctune`. If the user could write code that runs as root at boot, that code would be a privilege-escalation path.

**Safeguards.**
- `plan()` requires a full-length gpu phase in a green sign-off, so experiments never persist.
- The boot apply sets the power limit and the clock lock before the offsets, and returns the GPU to stock on any failure.
- The resume unit re-applies the settings only when the boot unit applied them in the same boot. Whether NVML settings survive suspend is untested.
- The crash-loop guard counts Xid errors after the apply.
- The units have a start timeout, and the `ctune.gpu=off` kernel argument skips the apply.
- A failed persist removes its config.

**Alternatives not chosen.**
- **LACT as the only NVML writer** (Flatpak or COPR `lact-headless`), with ctune talking to its socket. LACT is maintained and re-applies its settings after boot, resume and driver reloads. It adds a confirm-or-revert timer, a `lact-reset` kernel argument, a GUI, a VF-curve editor and fan curves. These points count against it:
  - Its socket (0660) accepts passwordless writes from any process running as the user, including the agent. Those writes include NvAPI voltage boost, VF-curve points and `enable_overdrive`, which stages kernel arguments as root. The Flatpak's root daemon also searches the user-writable `~/.local/share/flatpak` for extensions.
  - During a test, any DRM uevent (a monitor hotplug or sleep) makes LACT reset the clocks to stock and re-apply them. A readback cannot see that brief drop. The confirm timer is global, blocks other edits and can stall readbacks, so holding a change pending for the length of a test is not how LACT is meant to be used.
  - Automatic updates (Bazzite's daily `uupd`, for example) replace LACT with no version pin, and LACT re-applies a confirmed setting on a new driver without a re-test.
  - LACT zeroes another writer's offsets, so LACT and ctune's helper cannot both write.

  Anyone who wants LACT's GUI and VF curve anyway should set `daemon.disable_nvapi: true`, pin its version (`flatpak mask`), and keep it out of RAM and CPU campaigns.
- **A hybrid**, with LACT holding the signed-off baseline and ctune experimenting while LACT is detached (`detach_gpu`). This has two writers, and lactd keeps the detach only in memory, so a lactd restart loses it. It is the most fragile option.

## 2. Passwordless root path

**What it is.** `ctune passwordless` asks for one password and installs root-owned copies in `/usr/local/libexec/ctune`: `gpu-runtime-apply` (`ctune/gpu/runtime_apply.py`), `cpu/apply.py` with `smu.py`, and `mem/zen3_am4/collect_root.py` with the modules it loads. A polkit rule, `/etc/polkit-1/rules.d/49-ctune.rules`, lets one user run exactly those three programs as root through pkexec with no password. ctune uses them when they are installed, so GPU apply and reset, CPU probe, apply and reset, and `mem_snapshot` run unattended.

**Accepted risk.** Any process running as that user can change the GPU offsets, clock lock and power limit and the CPU margins within ctune's caps, and can read what a RAM snapshot reads. The CPU guardrails limit each call on its own, so a caller that loops can move a core to any margin from -30 to +30. That range is the cap.

**What each helper accepts.**
- The GPU helper accepts numbers only, never a path or a tool argument. It loads `gpu-boot-apply` from its fixed root-owned path, never from the repo. ctune's caps apply, and the power limit stays between the GPU's minimum and its default. The helper writes no file, and a failed apply or readback returns the GPU to stock.
- The installed CPU helper keeps its state in the root-owned `/run/ctune/cpu-co.json` and refuses `--state` and `--owner`, because a caller could otherwise forge the probe result or the last-known margins. It takes an exclusive lock, and the margin guardrails stay in the helper.
- The RAM collector only reads. It reads SMN registers from a fixed list, runs dmidecode, and reads `/dev/mem` at addresses from the firmware's own tables. It accepts only `--skip-devmem`, runs one copy at a time in a private temporary directory under `/run/ctune`, and prints everything as JSON. It skips the bulk ACPI NVS dumps, which can hold other firmware data once the kernel runs with `iomem=relaxed`. A snapshot that needs those dumps runs `ram_snapshot.py` as root behind the password. `ram_snapshot.py` writes the snapshot as the user and refuses a returned path outside the snapshot.

**Hardening.**
- The polkit rule names the resolved path, because pkexec hands polkit `/var/usrlocal/...` on image-based systems. The rule also requires that the target user is root.
- The shebangs pass `-IB` as one flag group, because Linux passes everything after the interpreter as one argument.
- The installer creates its temporary files exclusively, with mode 0600 under umask 022, because pkexec keeps the caller's umask.
- Every helper creates `/run/ctune` with mode 0755.

**What still asks for a password.** `gpu_persist`, `gpu_unpersist` and the installer keep their password prompt, because they install root-owned code from the repo, and the user can write the repo. Compare the sha256 hashes the installer reports with the repo's files.

**Removal.** `ctune passwordless --remove` removes the path, and so does `ctune gpu unpersist --uninstall`.

## 3. Crash evidence

A failed RAM, fabric or Curve Optimizer step often ends in a freeze or a reset, and the evidence is in the crashed boot's log. ctune keeps it without extra installs:
- Every test writes a crash marker and fsyncs the marker's directory. `check_interrupted` turns a marker left by an earlier boot into a red result. The result holds the crashed boot's newest error lines and an unfiltered tail, plus the next boot's hardware-error and `Previous system reset reason` lines. It reads the crashed boot's own log, which is not always `-b -1`, and reports `journal_lost` when that boot has rotated out of the journal.
- ctune records its own errors and stops in its journal.
- `ctune boot-check --install` adds a user unit that runs at every boot, records a test that a crash cut short, and saves that boot's hardware-error lines under `data/boots/`. It needs no root and changes nothing system-wide, and `systemctl --user disable ctune-boot-check` removes it. It matters because a service that logs heavily can write hundreds of thousands of lines an hour, so even a 1 GB journal may hold only hours of the previous boot. Rate-limiting such a service protects every log, not only ctune's. The options are `LogLevelMax=` on its unit, a lower log level, or a rotated file log driver for a container.

**Platform options.** These catch what the journal cannot. Each one is a system change outside ctune.

| Option | Catches | Costs and limits |
|---|---|---|
| ramoops (on Bazzite: `ujust toggle-save-panics enable`, which adds two kernel arguments and `kernel.panic=5`) | The panic or oops text that journald cannot write once the kernel stops. Memory instability without ECC often ends that way. `kernel.panic=5` turns a panic into a reboot, so an unattended sign-off comes back. | It reserves 64 KiB of RAM. It catches nothing for sync-flood, watchdog or shutdown resets or for hard hangs, which are the usual Curve Optimizer and FCLK failures. With `kernel.panic_on_oops=0`, an oops alone neither reboots nor dumps. The firmware decides whether the board keeps the reserved RAM through a reset. systemd-pstore can race the module load, because the stock unit waits only for efi_pstore, so a drop-in has to order `systemd-pstore.service` after `modprobe@ramoops.service`. |
| Hardware watchdog (sp5100_tco plus systemd `RuntimeWatchdogSec=`) | It turns a hard hang during an unattended run into a reset, which the next boot labels as a watchdog reset. | A false trigger reboots the machine. Some images blacklist sp5100_tco (Bazzite's `deck-blacklist.conf`, a Steam Deck default), so check whether the module is loaded, and if not, why. |
| rasdaemon | A durable SQLite record of machine-check, AER and memory-failure events that does not depend on journal retention. | It starts too late to record the leftover banks the kernel logs at boot after a crash, and it misses fatal errors that panic first. Fedora's 0.8.0 does not show the SMCA error text in `--errors`. It needs three extra packages. |
| efi_pstore instead of ramoops (`efi_pstore.pstore_disable=0`) | It survives power loss and does not depend on RAM surviving a reset. | It writes UEFI NVRAM at panic time, and firmware bugs can corrupt NVRAM. Only one pstore backend runs at a time. |
| kdump | The full crash dump. | It needs kexec-tools, and the NVIDIA driver complicates it. Not recommended. |

**Recommended order.** Install `boot-check` first, and rate-limit any service that floods the journal. Both are cheap, and they keep the evidence that most often goes missing. Add ramoops next, and keep it only if a deliberate test panic survives a reset on the board. Run that test once the BIOS settings are final, because the firmware decides whether RAM survives a warm reset:
1. Check that `dmesg | grep -i ramoops` shows the reserved region.
2. Save your work and stop services that write to disk.
3. Run `sync`, then `echo c | sudo tee /proc/sysrq-trigger`. The kernel panics and reboots after 5 s.
4. After the boot, look in `/var/lib/systemd/pstore`. A new directory there holding `dmesg-ramoops-*` means ramoops works on this board. If there is none, ramoops does not work on it. On Bazzite, remove it with `ujust toggle-save-panics disable` and delete the systemd-pstore drop-in.

Consider the hardware watchdog for unattended sign-offs. Skip rasdaemon and kdump.

## 4. BIOS setup decoder (mem domain)

**ctune keys settings by location, not by byte offset.** A BIOS update can move questions inside a varstore. On the B550 AORUS PRO, F19c grows the Setup (Tweaker) varstore from 646 to 650 bytes and shifts 418 questions by 4 or 5 bytes. CAS Latency moves from Setup@368 to @373, tRFC from 394 to 399, and DRAM Voltage from 508 to 513. A decoder keyed by offset would attach values to the wrong settings without any error. So `KEY` and `GBT_VOLT` in `bios_settings_decode.py` use the varstore, menu path and prompt as the key, and so does `verify.py`.
- When several storage locations sit at one menu location, `find_row` returns an error instead of guessing. An op or width filter tells them apart.
- The decoder lists any key setting it cannot find, and `mem_snapshot` returns the list as `bios_key_problems`.
- The per-core Curve Optimizer questions are not in a BIOS image's forms, because AGESA adds them at boot. Only a live snapshot can resolve them.
- `tests/test_bios_keys.py` checks both layouts (`tests/fixtures/bios_key_rows.json`).
- `mem_checklist` reads the last snapshot taken before the flash, so a layout change does not affect it.

**ctune never hard-codes tuned values as checks.** Such a check would fail after every "Load Optimized Defaults". `mem_verify` compares the BIOS configuration with the running hardware instead.

**Possible cross-checks.**
- **IFRExtractor-RS** (LongSoft) could serve as an offline reference, run once per BIOS version. It is an independent parser of the same HiiDB dump, and it catches string-ID, opcode-field and missed-question errors. It builds no form paths, joins no varstores, evaluates no conditions and does not show value widths, so it checks only the structural layer. It pairs every form package with every string package, so the dump has to be split first. It exits 0 even when some opcodes fail to parse, and its binary is unsigned. Key the check on the BIOS version, not the HiiDB hash, because the runtime HiiDB embeds live values. It cannot replace ctune's parser, because it emits text, and ctune would still need everything it does not provide: paths, conditions, defaults and values.
- **Completeness checks** in each snapshot could test for zero refused pages in `dump_fw_mem.log`, package-list lengths that add up to the HiiDB size, and an END package in each list. They catch a truncated or zero-filled dump, which would otherwise decode without an error.
- `ctune/mem/platforms/zen3_am4/tools/verify_hii_settings.py` is a second, independent in-house parser (`CTUNE_SNAPSHOT=<snapshot dir>`).

**Known limitation.** A form reached through several REF paths keeps only the first parent's conditions (`HiiDb._build_paths` in `hii_ifr.py`). The form should be visible when any of its incoming paths is visible. No external tool evaluates visibility, so no cross-check catches this.
