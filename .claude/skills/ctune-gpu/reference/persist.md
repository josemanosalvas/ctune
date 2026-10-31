# Boot persistence

After a green sign-off and a few hours of the user's games, `gpu_persist` keeps the settings across reboots. It runs as root and always asks for a password.
- It writes the settings applied now to `/etc/ctune/gpu.json`. A root-owned boot unit re-applies them at boot and after resume with nvidia_oc, a pinned build in `/usr/local/libexec/ctune`.
- It refuses unless the latest sign-off with exactly these settings and a full gpu phase (90 min, with Quake II RTX, Superposition and Heaven), on this GPU and driver, was green. `dry_run` shows the reasons.
- The unit sets the power limit and the clock lock first, then the offsets, and reads them back. On any failure the GPU goes back to stock.
- The unit skips the settings in four cases, and `gpu_status` says why in `persistence.this_boot`:
  - **A new driver.** `gpu_apply` the settings, run `signoff`, then `gpu_persist` again.
  - **A value over ctune's caps.**
  - **The crash-loop guard.** In two boots in a row the GPU failed after the apply: a crash within 10 minutes, or an Xid. Find the cause, then clear it with `gpu_persist` `reset_guard`.
  - **Resume after a boot that did not apply them.**
- **Escape at the boot menu:** `ctune.gpu=off` on the kernel command line.
- `gpu_unpersist` stops it; the settings applied now stay until reboot. `gpu_reset` and `gpu_apply` still change the running values on top of the persisted ones.
