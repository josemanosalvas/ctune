# Test protocol (Linux)

memtune has no stress runner yet. Run these by hand, or from a distrobox for packages missing on Bazzite.

- **Screen (each step, 20–30 min, warm):** stressapptest sized from `MemAvailable` minus ~1.5 GB, for example `stressapptest -M <MB> -s 1800 -W`. Run a GPU load (a game, or a GPU stress test) alongside so the DIMMs heat up. Turn zram off or lower `vm.swappiness` first, so the test hits DRAM rather than swap.
- **Fabric / FCLK steps:** combined CPU + GPU + NVMe I/O load for 1–2 h, then an idle soak (idle reboots are the classic FCLK symptom).
- **Sign-off (end of a campaign):** the user's usual multi-hour mixed suite (stressapptest, y-cruncher, OCCT memory and Large AVX2), and memtest86+ overnight from USB.
- **After any test:** wait ≥ 300 s, then call `errors`. After a crash, check the previous boot (`journalctl -k -b -1`), `sudo btrfs scrub start -B /sysroot` and `sudo ostree fsck`.
