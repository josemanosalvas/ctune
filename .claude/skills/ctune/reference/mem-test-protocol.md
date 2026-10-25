# Test protocol on Linux

ctune runs every test itself, so the user runs nothing by hand. Long tests run detached: poll `test_status`, and tell the user before starting one. Each result lands in the journal.

- **Screen** (every mem step, 25 min warm). `mem_test` screen runs stressapptest over the free RAM while memtest_vulkan heats the case, so the DIMMs warm up the way they do in a game.
- **Fabric screen** (FCLK steps, and the re-check after a BIOS flash). `mem_test` fabric runs the screen with NVMe reads for 90 min, then leaves the machine idle for 20 min. Random reboots at idle are the classic FCLK symptom.
- **Sign-off** (end of a campaign, about 4 h). `signoff` runs RAM, y-cruncher, the per-core Curve Optimizer cycle, the GPU, a combined RAM + GPU + NVMe load and an idle soak. The user's normal use over the following days is the last check.
- **Coverage.** A test takes the free RAM minus 1.5 GB, and `coverage_pct` reports the share of the 32 GB it covered. Before a sign-off, ask the user to close large apps (browser, games) so coverage passes 80 %. A large `swapped_out_mb` means the test pushed the desktop into zram. The test is still valid; the desktop was only sluggish.
- **VRAM.** GPU tests and the GPU heat load need 2 GB of free VRAM. Below that, `gpu_test` refuses to start and `mem_test` runs without the heat load, and both name the processes holding VRAM. Ask the user to close them (an LLM server, a game) before a GPU step or a sign-off.
- **After every test**, wait at least 300 s, then call `errors`. After a crash, read the previous boot's kernel log (`journalctl -k -b -1`), then the user runs `sudo btrfs scrub start -B /sysroot` and `sudo ostree fsck`.
- memtest86+ needs a boot from USB, so ctune cannot run it. stressapptest finds overclocking errors faster. memtest86+ adds coverage of the memory the running OS holds, so offer it only when the user wants a full-coverage pass.
