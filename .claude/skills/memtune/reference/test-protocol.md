# Test protocol on Linux

memtune has no stress runner yet. The user runs these by hand, or from a distrobox for tools that Bazzite lacks.

- **Screen** (every step, 20 to 30 minutes, warm). Size stressapptest from `MemAvailable` minus about 1.5 GB, for example `stressapptest -M <MB> -s 1800 -W`. Run a GPU load (a game or a GPU stress test) at the same time so the DIMMs heat up. Turn zram off or lower `vm.swappiness` first, so the test exercises DRAM rather than swap.
- **Fabric screen** (FCLK steps). Run a combined CPU, GPU and NVMe load for 1 to 2 hours, then leave the machine idle for a while. Random reboots at idle are the classic FCLK symptom.
- **Sign-off** (end of a campaign). The user's usual multi-hour mixed suite (stressapptest, y-cruncher, OCCT memory and Large AVX2), then memtest86+ overnight from USB.
- **After every test**, wait at least 300 s, then call `errors`. After a crash, read the previous boot's kernel log (`journalctl -k -b -1`), then run `sudo btrfs scrub start -B /sysroot` and `sudo ostree fsck`.
