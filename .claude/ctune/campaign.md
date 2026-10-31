# ctune campaigns

The frame the ctune-mem, ctune-cpu and ctune-gpu skills share. The tools are the `ctune` MCP server, or `bin/ctune <command>` from the repo root.

## Campaign
A **campaign** tunes one domain (mem, cpu or gpu) through a chain of **steps**. A step changes one variable or one coupled group, and is planned, applied, verified, tested and recorded. The **baseline** is the last green configuration and its result; every step is judged against it. One domain per campaign.

## Journal
- Before a change, a `plan` entry with `data.domain`: the setting, its current and new value, the reason, and the rollback value.
- After the test, a `result` entry with the verdict and its source.
- Facts the tools cannot show (limits found, dropped sets, BIOS behaviour, cooling) go to `data/this-machine.md`, local and gitignored. On a new machine, create it from [the template](this-machine.template.md). Replace a stale fact rather than appending, and read live values from the tools.

## Verdicts
- **Green**: no crash, zero corrected machine-check errors in `errors` 300 s after the test ends, no new Xid or load error, and a score within about 1 % of the baseline's. Green becomes the new baseline.
- **Red**: anything else. Record the error source, roll back to the baseline value, and record the limit in `data/this-machine.md`.
- **Inconclusive**: the test could not run or was too short to judge; `blocked_by` or the result's `inconclusive` field says why. Fix the cause and run it again, with no rollback.

Each skill names its domain's score and red signals.

## Long tests
Tests run detached as systemd user units, so they outlive the session and hold a sleep lock. When one starts, tell the user what runs and when it ends, wait for it in the background, and report the verdict when it arrives (`test_status`; `test_stop` stops it). Each result lands in the journal.
- **One test at a time.** The GPU is shared, so a test refuses to start while another ctune test runs, whichever data directory it uses. For a development run, set `CTUNE_DATA` to a scratch directory and start it detached.
- **Root tools** run unattended once `ctune passwordless` is installed. Otherwise each opens a password prompt on the machine's screen, so tell the user before calling one.

## Test conditions
- **Coverage.** A RAM test takes the free RAM minus 1.5 GB, and `coverage_pct` reports the share of the total it covered. Before a sign-off, ask the user to close large apps (browser, games) so coverage passes 80 %. A large `swapped_out_mb` means the test pushed the desktop into zram; the test stays valid.
- **VRAM.** GPU tests and the GPU heat load need 2 GB of free VRAM. Below that, `gpu_test` refuses to start and `mem_test` runs without the heat load, and both name the processes holding VRAM. Ask the user to close them (an LLM server, a game). When memtest_vulkan cannot allocate its buffer anyway (exit 68, no output), it retries up to three budgets, each 1 GiB smaller; `alloc_retries` lists the failed ones.
- **A quiet GPU.** Idle GPU utilisation (`nvidia-smi`) should be about 0 % before a GPU test or a sign-off. Another program drawing on the GPU (live monitoring graphs, video, a game) lowers throughput at the same clock, which `clock_stretch` reads as a stretched clock.
- **A quiet CPU.** A benchmark or a scrub running beside a test lowers its score; run the test again on a quiet machine before judging the score.
- Quake II RTX, Superposition and Heaven run in the GPU screen and the sign-off. Install them once with `ctune gpu q2rtx-setup` and `ctune gpu unigine-setup`; without them those parts end inconclusive.

## Sign-off
A campaign ends with a **sign-off** of its final configuration, then a few days of the user's real workload: games, and the machine left idle overnight.
- `signoff` (about 5 h 30 min plus TM5) runs RAM, TM5, y-cruncher, the per-core Curve Optimizer cycle (mprime SSE, then AVX2), the GPU, a combined RAM, GPU and NVMe load and an idle soak. Its 90 min GPU phase is the full GPU screen (ctune-gpu describes its parts).
- Each finished phase is written to the journal as a note, so a stop (verdict `stopped`) or a crash keeps the phases already done. An inconclusive phase does not stop the run; the sign-off stays inconclusive until that phase is run again green.
- Persisted GPU settings stay active during RAM and CPU campaigns, so a crash there is not a GPU result.

## After a crash
- Read the red record's kernel-line fields, then run its `next_step` (the crashed boot's own log, which is not always `-b -1`). If `journal_lost` is true, the journal has rotated; check `saved_boot_files`.
- A crash during a cpu or gpu step clears the runtime settings on reboot; record it as red. When the crash interrupts `cpu_test`, the next cpu call writes that red result.
- Unstable RAM can corrupt files. On Bazzite and other ostree systems on btrfs, the user checks the filesystem through a writable mount: `sudo btrfs scrub start -B /var`, then `sudo ostree fsck`.
