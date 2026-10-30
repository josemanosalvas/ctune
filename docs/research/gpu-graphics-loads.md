# Graphics, raster and ray-tracing loads: Quake II RTX, Unigine Superposition and Heaven

This note draws on web pages, source files, test profiles and the first bytes of each archive. Each fact below has its source, and "unconfirmed" means no primary source was found. The Decision section adds what the downloads and example runs on an RTX 3080 Ti under Bazzite (Fedora 44) showed. The Open list has the questions that remain.

## Decision
ctune runs three graphics loads in the screen and the sign-off's gpu phase, because each loads hardware the others leave idle:
- `graphics`: Quake II RTX (`ctune/gpu/q2rtx.py`) loads the RT cores. It traces even its primary rays, so it barely touches the ROPs. It has no licence question, and it reports a lost device itself (`Device lost!`, exit 1).
- `raster`: Unigine Superposition 1.1 (`ctune/gpu/unigine.py`) rasterises every frame through OpenGL 4.5 with heavy shading and post-processing: shaders, texture units and ROPs.
- `tessellation`: Unigine Heaven 4.0 (same module) at its Extreme preset: extreme tessellation (geometry units) and 8x MSAA (ROPs).

The Unigine licence question below stays open. ctune drives the Basic editions through their engine binaries, as PTS does, for personal use, and never redistributes them.

None of the three checks what it renders, so gpu-burn remains the test of correct results, and each graphics part is red only on a crash, a lost device, a missing result, a hang or an Xid error.

Checked for the Unigine loads on Bazzite (Fedora 44), on an RTX 3080 Ti at a 1905 MHz lock, a +55 MHz offset and a 330 W limit:
- Both `.run` files match the FreeBSD ports' SHA256 and size: Superposition 1.1 at 1,642,169,125 bytes, Heaven 4.0 at 286,332,610 bytes (PTS still lists Heaven at 286,329,188 bytes, an older packing).
- Both Makeself payloads match their archive's own MD5 field when read past the header (588 lines for Superposition, 589 for Heaven) with nothing executed. Superposition's `postinstall.sh` only writes desktop entries and icons, so skipping it loses nothing. Heaven's script is `echo`.
- Every library of `bin/superposition` and `bin/heaven_x64` resolves on the host with `LD_LIBRARY_PATH` set to their `bin/` (and `bin/x64/` for Heaven). No runtime container is needed.
- Both run headless in `gamescope --backend headless` through its Xwayland, with no window on the desktop.
- **Superposition** with the PTS arguments works unchanged on 1.1 (`-mode 2 -preset 0` runs the benchmark once and quits). At 2560×1440, `-shaders_quality 3 -textures_quality 2` (Extreme shaders, high textures) one pass took 197 s with loading, scored 7164 (53.6 FPS, min 44.3, max 61.7) and ran at the 330 W cap at 1785 to 1800 MHz, 78 °C. The score file is `$HOME/.Superposition/automation/log-<date>_<time>.txt`. With `-sound_app openal` and no sound device the engine printed about 9,500 `OpenAL error: invalid value` lines per pass; `-sound_app null` is used.
- **Heaven** with the PTS arguments and the `PHORONIX` define runs the benchmark once (260 s), prints `Time:`, `Frames:` and `FPS:` and quits. The launcher's Extreme preset is the defines `QUALITY_ULTRA,TESSELLATION_EXTREME` with `-video_multisample 3` (`data/launcher/js/browser.js`). At 2560×1440 with them one pass took 269 s and ran at 150 FPS at the 330 W cap at about 1840 MHz, 79 °C; with no quality defines it ran at 247 FPS, also at the cap.
  - Heaven has no `config_readonly` and rewrites its engine config at exit. It resolves every path from `bin/`, so an absolute `-engine_config` fails; a path relative to `bin/` works, so ctune copies the shipped config into its data directory before each pass.

Checked on Bazzite (Fedora 44), without running it:
- The tarball's SHA256 matches.
- It holds `baseq2/pak0.pak` (1106 files, with `demos/q2demo1.dm2` and `maps/demo1-3.bsp`), `q2rtx_media.pkz` (907 MB), `shaders.pkz`, `q2rtx.sh` and `find-retail-paks.sh`. It unpacks to 1.1 GB.
- **The binary does not run on Fedora 44 as shipped.** It needs `libssl.so.1.1` and `libcrypto.so.1.1` (`readelf -d`), and Fedora 44 has only OpenSSL 3.
  - It is built with Steam's sniper SDK, so ctune runs it in the standalone Steam Linux Runtime sniper: `SteamLinuxRuntime_sniper/run -- ./q2rtx …`.
  - ctune uses the runtime snapshot 3.0.20260805.254768 from https://repo.steampowered.com/steamrt3/images/3.0.20260805.254768/, 184 MB, SHA256 `e264f063…3d94` from that snapshot's `SHA256SUMS`. It unpacks to 794 MB.
  - Its manifest lists `libssl1.1` 1.1.1w-0+deb11u8. Inside it, `ldd q2rtx` resolves every library, and pressure-vessel supplies `libvulkan.so.1` from the host's driver.
  - `bwrap` is on the host, so no package needs layering.
- `ctune gpu q2rtx-setup` downloads both, checks both hashes, unpacks them into `tools/q2rtx/` and `tools/steamrt-sniper/`, and writes `tools/q2rtx/VERSION`.

Example runs on an RTX 3080 Ti from a systemd user unit, all with a 1905 MHz lock, a +70 MHz offset and a 400 W limit:
- `gamescope --backend headless -w W -h H -W W -H H -- SteamLinuxRuntime_sniper/run -- q2rtx …` works on the NVIDIA driver, with no window on the desktop. pressure-vessel passes `XDG_DATA_HOME` through to the game, so `console.log` is written under ctune's data directory. The log said `Using VK_KHR_ray_query`.
- One `q2demo1` pass, with the gamescope and runtime start included, took 15 s at 3440×1440 (70.7 FPS), 12.8 s at 2560×1440 (92.9 FPS) and 10 s at 1920×1080 (152 FPS). FPS varied by about 2 % between passes.
- At 3440×1440 the GPU ran at 1882 MHz and 382 W, at the power cap the whole time, with `at_lock_share` 0.52.
- At 2560×1440 it ran at 1896 MHz and 381 W, with `at_lock_share` 0.90, 72 °C and the fan at 53 %. ctune uses this size, because it keeps the clock closest to the lock at high power.
- At 1920×1080 the load was bursty, with utilisation of 90 % or more only about 30 % of the time.
- There were no Xid errors, crashes or hangs in 33 passes.

## Unigine Superposition (Basic, Linux)

### Licence and edition limits
- **Basic is free. It allows personal use only.** The EULA lets you install Basic on any number of computers (§2.1). Basic and Advanced are for "private individuals ... solely for their own use" (§2.1.1). You may copy it only for archive or backup (§2.2). Any other copying or distribution needs Unigine's written consent (§4.2). The EULA bans decompiling and derivative works (§3.1, §3.3). Source: https://benchmark.unigine.com/policies/superposition-eula (last updated 2025-09-15).
  - Keeping it unpacked in the gitignored `tools/` and never committing it fits these terms.
- **What Basic lacks.** These points come from the editions table on https://benchmark.unigine.com/superposition and the editions table in the 1.1 manual (https://benchmark.unigine.com/docs/superposition/Superposition_Benchmark_User_Manual.pdf, "Editions"):
  - Basic has: single run, Custom settings, GPU monitoring, interactive mode and VR.
  - "Looped Stress Test" needs Advanced ($19.95). Even there, the user starts it from the launcher GUI: pick STRESS, set the duration and click RUN (manual, "Stress Test [Advanced and Pro versions only]").
  - "Command Line Automation", CSV reports and commercial use need Pro ($495). The CLI is `superposition_cli` with `-mode`, `-mode_duration`, `-iterations`, `-log_txt` and `-log_csv` (manual, "Automation [Pro version only]").
- **Licence blocker.** Phoronix Test Suite runs the Basic build unattended by starting the engine binary `bin/superposition` directly and skipping the launcher (see below). The EULA neither allows nor forbids this. Unigine sells command-line automation as a Pro feature. Whether doing it under Basic is within the licence is unconfirmed, and only Unigine (licensing@unigine.com) can answer.

### Download
- **Current version: 1.1 (2019).** The URL is https://assets.unigine.com/d/Unigine_Superposition-1.1.run. The file is 1,642,169,125 bytes, and its Last-Modified date is 2025-11-21 (HTTP HEAD). The download page says 1564 MB. Unigine also offers a `.torrent` (FreeBSD port Makefile L4).
- **Checksum.** Unigine publishes none. The FreeBSD port pins SHA256 `7494e1c73bf59ef2161513d5d5cb9d9bffbd8479929d4976ac53b6955de580f8` at the same size (https://cgit.freebsd.org/ports/plain/benchmarks/linux-unigine-superposition/distinfo, timestamp 2026-01-18). This is a third-party hash.
  - Unigine has replaced its files before without changing the name. PTS had to update the 1.0 Linux hashes in 2018 and again in 2020 (https://github.com/phoronix-test-suite/test-profiles/issues/37; changelog of `pts/unigine-super-1.0.7`). Pin the hash, and expect Unigine to change the file under it.
  - The 1.0 file is still served: https://assets.unigine.com/d/Unigine_Superposition-1.0.run, 1,641,749,718 bytes, Last-Modified 2018-07-11. PTS still uses it (`pts/unigine-super-1.0.8/downloads.xml` L6-8). PTS's last hash for it is SHA256 `ba828e48…5d8a32` (`unigine-super-1.0.6/downloads.xml`).
- **Format: a Makeself 2.4.0 archive** (`file` on the first 24 KB). The header shell script is 588 lines long (header L499). A gzip tar follows, 2,829,336 KB unpacked (header L502). Its embedded checksums are MD5 `895cd9ca9ac9c06a06a41de96b928635` and CRC 367360281. The SHA field is all zeros, so the script does not check it (header L10-12). The default target is `Unigine_Superposition-1.1`, and the script then runs `./postinstall.sh` (header L17-23).
  - It supports `--noexec` (skip postinstall), `--target dir`, `--nox11` (no xterm), `--check`, `--list` and `--tar` (header L142-163). PTS runs it with `--nox11` (`install.sh` L4).
  - **The archive can be extracted without running the vendor script**, with `tail -n +589 Unigine_Superposition-1.1.run | tar xzf - -C <dir>`. The FreeBSD port does the same with `tail -n 5401121 … | tar xzf -` (Makefile L37). Verify the sha256 first, because makeself's own `--check` needs the script to run.
  - What `postinstall.sh` does is unconfirmed, because it is inside the payload.
- **Contents** (FreeBSD `pkg-plist`):
  - the launcher `Superposition` and `bin/launcher`;
  - the engine `bin/superposition` with `libUnigine_x64.so`, `libGPUMonitor_x64.so`, `libopenal.so` and `libopenvr_api.so`;
  - `bin/superposition_cli` (the Pro CLI, shipped in Basic too);
  - a bundled Qt 5 with only the xcb (X11) platform plugin;
  - `data/{core,data,superposition}.ung`.
- **System libraries.** The manual asks for glibc 2.15+ ("System Requirements") and OpenGL 4.5 Core ("Tips: Linux"). The FreeBSD port depends on dbus, dri (GL), openal-soft and the X.org libraries (Makefile L26). On Bazzite (Fedora 44) `ldconfig -p` has libGL, libEGL, libX11, libXext, libXrandr, libXinerama, libXi, libXrender, libXcursor, libxcb, libxkbcommon, libdbus-1, fontconfig and freetype. The exact NEEDED list of `bin/superposition` is unconfirmed (see Open).

### Starting a run without clicks
- **PTS command.** PTS `pts/unigine-super-1.0.8` (phoronix-test-suite commit be12163, `install.sh` L9) runs:
  `cd Unigine_Superposition-1.0 && ./bin/superposition -sound_app openal -system_script superposition/system_script.cpp -data_path ../ -engine_config ../data/superposition/unigine.cfg -video_mode -1 -project_name Superposition -video_resizable 1 -console_command "config_readonly 1 && world_load superposition/superposition" -mode 2 -preset 0 <options>`
  - The options come from `test-definition.xml` L26-85:
    - `-video_width W -video_height H`;
    - `-video_fullscreen 1|0` (fullscreen or windowed);
    - quality as `-shaders_quality 0..3 -textures_quality 0..2` for Low, Medium, High and Ultra, with Ultra as shaders 3 and textures 2;
    - `-video_app opengl` (`direct3d11` is the Windows option).
  - The test needs a display (`RequiresDisplay TRUE`, L20).
  - No source found documents what `-mode 2 -preset 0` select. Whether the same arguments work on 1.1 is unconfirmed.
- **APIs on Linux.** Linux has only OpenGL. DirectX is the Windows choice (manual, "Performance Test": "DirectX or OpenGL"; "Tips: Linux": OpenGL 4.5 Core).
- **Presets.** 720p Low, 1080p Medium/High/Extreme, 4K Optimized, 8K Optimized and Custom (manual, "Performance Test"). How the engine's `-shaders_quality` values map to these is unconfirmed.
- **Window.** Windowed or fullscreen through `-video_fullscreen`. The resolution is free with `-video_mode -1` (PTS L9 and options). The Pro CLI also has a borderless mode (`-fullscreen 2`).
- **Looping.** Basic has no built-in loop. Looping needs the Advanced stress test (GUI) or the Pro CLI (`-mode_duration`, `-iterations`, and samples such as `loop_run_high_30min.xml`; manual "XML Testing Routines"). In Basic, the only way to loop is to relaunch.
- **Length of one run.** OpenBenchmarking puts the average at 3 min 17 s per run, and 10 min for 3 runs at 1080p High OpenGL (https://mail.openbenchmarking.org/test/pts/unigine-super-1.0.8). These are PTS figures, which include loading. The benchmark is 5400 frames in 18 scenes (manual, "Command Line Parameters": `frame [0..5400]`, `scene [0..17]`).

### How failure shows
- **The score.** PTS deletes `~/.Superposition/automation/log*.txt`, runs the benchmark, then reads that file (`install.sh` L8, L10). The file holds `FPS:`, `Min FPS:`, `Max FPS:` and `Score:` lines (`results-definition.xml` L5-8). The manual gives `~/.Superposition/automation` as the report directory ("Report Files").
- **No result checking is documented.** The Advanced stress test reports only "the total test duration time, GPU characteristics, and the mark that the test was passed" (manual, Stress Test "Results"). A pass means only that it ran to the end. Nothing in the manual compares frames.
- No source covers exit codes, the engine log path, or what happens on a GL error or an Xid error. All three are unconfirmed (see Open).

### What hardware it uses
- **Raster only.** It renders through OpenGL 4.5 on Linux. Its "SSRTGI" is screen-space ray-traced GI computed in shaders (manual, "Key Features"). Version 1.0 came out in April 2017 (manual, "Version History"), before GPUs had RT cores, and core OpenGL has no hardware ray-tracing API. **It does not use the RT cores.** That is an inference, and no Unigine statement says so.
- It does load the shaders, TMUs and ROPs, which gpu-burn does not.
- **Speed on an RTX 3080 Ti.** OpenBenchmarking lists the RTX 3080 Ti at 165 ± 8 FPS on average at 1080p High OpenGL (14 public results, same page). No primary source gives power or clock measurements.

### Running from a systemd user unit
- **X11 only, so it runs through Xwayland.** The launcher's Qt has only `libqxcb.so` (FreeBSD pkg-plist). The engine probably uses X11 and GLX too, but that is unconfirmed. The user manager has `DISPLAY=:0` and `WAYLAND_DISPLAY=wayland-0` (`systemctl --user show-environment`).
- **Vsync.** No source documents a vsync option for the 2017 engine. For GLX, the NVIDIA driver honours `__GL_SYNC_TO_VBLANK=0` (NVIDIA README "OpenGL environment variables", https://download.nvidia.com/XFree86/Linux-x86_64/580.95.05/README/openglenvvariables.html). Whether it removes the cap under Xwayland is unconfirmed.
- **Sound.** PTS uses `-sound_app openal`. Current Unigine docs list `-sound_app null` for "no sound" (https://raw.githubusercontent.com/unigine-engine/ai-docs/main/ai_docs/md_docs/code/command_line.md, "Sound Settings"). Whether this works in the 2017 engine is unconfirmed.
- **Network.** The software may connect to the Internet for update checks, anonymous statistics and licence validation (EULA §5). Running offline is untested.

## Quake II RTX (NVIDIA, native Linux, Vulkan ray tracing)

### Licence and data
- **The engine is GPL v2** (`readme.md` L18-21, `license.txt`; tag v1.8.1, commit 2ab526a).
- **The bundled media is not free.** The Quake II textures and derivatives are "Copyright (c) 2019 id Software LLC ... All Rights Reserved" (`notice.txt` L50-52). Nobody may redistribute the retail pak files (`readme.md` L23-25).
- **No purchase needed.** It is free and "includes the 3 levels from the original shareware distribution" (https://store.steampowered.com/app/1089130/). Buying the full game unlocks the remaining levels and multiplayer.
  - The Windows installer has a "Quake II Shareware Demo" section that installs `baseq2/shareware/pak0.pak` (`setup/setup.nsi` L91-100).
  - The engine detects shareware data (`fs_shareware`) and then blocks multiplayer (`src/client/main.c` L504-507).
- **The repository is archived.** v1.8.1 (2025-12-11) is "the final official release" (GitHub release notes; the repo API reports `archived: true`). Future driver regressions will get no fixes.

### Download
- **Release.** https://github.com/NVIDIA/Q2RTX/releases/download/v1.8.1/q2rtx-1.8.1-linux.tar.gz, 1,058,767,022 bytes. Its SHA256 is `31dfd0a924366272161d3e1ff728e8f1ae8304142e3ce954d0fa73d9ff5b7562`, from GitHub's asset digest, and the AUR `q2rtx` PKGBUILD pins the same value. It is a plain gzip tarball with a top directory `q2rtx/`. The first 8 MB, read by HTTP range, list `q2rtx/license.txt` and then `q2rtx/baseq2/blue_noise.pkz`.
- **The shareware data is in the tarball.** No source lists the tarball's contents, so two independent sources support this:
  - The AUR `q2rtx-data` PKGBUILD installs `q2rtx/baseq2/*.pak` and `*.pkz` from this tarball.
  - PTS `pts/quake2rtx-1.8.0` extracts the tarball and runs `+demo q2demo1.dm2` with no data step (`install.sh` L8-25). `q2demo1.dm2` is the demo inside id's shareware `pak0.pak`; PTS `yquake2-1.3.0` takes it from `q2-314-demo-x86.exe`.
  - A full listing would confirm it (see Open).
- **Requirements.** The NVIDIA driver must be version 460.82 or later (`readme.md` L99), and the engine checks the version (`src/refresh/vkpt/main.c` L1181). It is built in the Steam Runtime "sniper" SDK (`.github/workflows/build.yml` L54) with SDL2, OpenAL, curl and zlib from submodules. At run time it needs `libvulkan.so.1`, which the NVIDIA driver's Vulkan loader setup provides. Its exact NEEDED list is unconfirmed.

### Starting a run without clicks
- **PTS command** (`quake2rtx-1.8.0/install.sh` L25):
  `cd q2rtx && ./q2rtx +set ray_tracing_api <nv|pipeline|query> +demo q2demo1.dm2 +timedemo 1 +set r_mode -1 +set r_customwidth W +set r_customheight H +set vid_fullscreen 1 +set pt_num_bounce_rays <0|0.5|1|2> +set flt_enable <0|1> +set nextserver quit`
  - `nv` no longer exists, because 1.6.0 removed `VK_NV_ray_tracing` (`changelog.md` L137).
  - `r_mode` and `r_customwidth` are old Quake 2 cvars. They do not appear in `src/client/refresh.c`, so they are probably ignored.
- **Resolution.**
  - Fullscreen: `vid_fullscreen N` picks entry N of `vid_modelist`, a list like `"1920x1080 2560x1440"` where the `desktop` keyword means the current mode. `vid_fullscreen 0` is windowed (`doc/client.md` "Video Modes"; `refresh.c` L78-130).
  - Windowed: the size is `vid_geometry WxH[+X+Y]`.
  - `viewsize` sets the resolution scale (default 100). Dynamic resolution, `drs_enable`, defaults to 0 (`client.md`).
- **Forcing RT.**
  - `vid_rtx` defaults to 1, the Vulkan path tracer; 0 is the OpenGL renderer (`refresh.c` L323-328).
  - `ray_tracing_api` is `auto`, `pipeline` or `query`. Auto picks `VK_KHR_ray_query` on the NVIDIA proprietary driver (`vkpt/main.c` L1103-1135, L3868). The choice is logged as `Using VK_KHR_ray_query` or `…ray_tracing_pipeline` (L1164).
  - Without either extension, it stops with "No ray tracing capable GPU found." (L1142). It never falls back to rendering without RT.
  - The heaviest settings are `pt_num_bounce_rays 2` (GI High), with `flt_enable 1` and `pt_reflect_refract 2` at their defaults (`client.md`).
- **Timedemo.**
  - `+timedemo N` plays the demo N times back to back in one process (`demo.c` L909-919, L1262-1278).
  - Each run renders one frame per demo packet, as fast as possible (`demo.c` L1304-1308; `main.c` L3205-3208).
  - The results print only after all N runs, one line each: `<frames> frames, <s> seconds: <fps> fps` (`demo.c` L1264-1270).
  - `timedemo` is a cheat cvar (`common.c` L906), but PTS sets it on the command line, and that works.
  - When the demo ends, `nextserver` runs (PTS sets `quit`). With `nextserver` empty, the client prints "Demo finished" and stays in the menu (`demo.c` L633-655).
  - There is no other built-in benchmark. Only `q2demo1.dm2` is confirmed in the shareware data. Other demo files are unconfirmed.
- **Length of one run.** OpenBenchmarking puts the average at 1 min 10 s per run, and 2 min for 3 runs at 4K with GI and denoiser off (https://mail.openbenchmarking.org/test/pts/quake2rtx-1.8.0). Because timedemo is uncapped, a faster GPU finishes sooner.
- **Display server.** It uses SDL2 2.32.10 (`changelog.md` L27). In SDL2 the X11 driver comes before Wayland (SDL `release-2.32.10` `src/video/SDL_video.c` L74-83), so with `DISPLAY` set it runs on Xwayland. `SDL_VIDEODRIVER=wayland` should force native Wayland. Whether the release build includes the SDL Wayland backend is unconfirmed. The game has Wayland-specific code and logs `Using SDL video driver: <name>` (`src/unix/video/sdl.c` L419-449).

### How failure shows
- **Exit codes.**
  - A normal quit, SIGTERM, SIGINT and SIGHUP end with `exit(EXIT_SUCCESS)` (`src/unix/system.c` L90-96, L159-164, L558-566).
  - A fatal error (`Com_Error(ERR_FATAL)`) logs `FATAL: …` and ends with `exit(EXIT_FAILURE)` (`common.c` L493-570; `system.c` L309-341).
  - SIGSEGV, SIGILL, SIGFPE and SIGTRAP print the signal name and exit 1 (`system.c` L166-178, L291-298).
- **Vulkan device lost.** `vkWaitForFences` returning `VK_ERROR_DEVICE_LOST` prints `Device lost!` and calls `exit(1)` (`vkpt/main.c` L3487-3492). Other Vulkan results go through `_VK()`, which ignores them in release builds (`vkpt.h` L57-67).
- **Log file.**
  - The path is `$XDG_DATA_HOME/quake2rtx/baseq2/logs/console.log`, or `~/.local/share/...` when XDG_DATA_HOME is unset (`system.c` L252-283).
  - Logging is on by default (`logfile 1`, `logfile_flush 1`; `common.c` L891-894).
  - Mode 1 rewrites the file at every start; `logfile 2` appends (`common.c` L232).
  - PTS reads the FPS from the `frames` line of this file (`install.sh` L6, L29-31).
- **No result checking.** Nothing in the timedemo or renderer code compares images. The only signals are a crash, an exit code and a hang.

### What hardware it uses
- **RT cores.** The renderer is a path tracer in four stages: primary rays, reflection and refraction, direct lighting and indirect lighting. It finds the primary visible surface by tracing rays, not by rasterising (`src/refresh/vkpt/shader/path_tracer.h` L29-46). It uses `VK_KHR_ray_tracing_pipeline`, or with `ray_query` it traces from compute shaders (`path_tracer.h` L123-124; `vkpt/main.c` L438-445).
  - Khronos says these APIs can run "using existing GPU compute or dedicated ray tracing cores" (https://khronos.org/blog/vulkan-ray-tracing-final-specification-release).
  - NVIDIA's driver is expected to run the traversal on the RT cores for both `query` and `pipeline`, but no NVIDIA source confirms it.
- **Little raster or ROP work.** Rasterisation is limited to the UI (`stretch_pic`) and the final blit (shader list in `vkpt.h` L72-75). Most of the remaining work is compute: the ASVGF denoiser, god rays and tone mapping (`path_tracer.h` L60-64; `readme.md` L65-68). The ray and compute shaders sample textures, so the TMUs work too.
- **Speed and power.** Phoronix measured the 3080 Ti's power across its whole Linux suite as "comparable to that of the RTX 3090" and gave no Q2RTX figure (https://www.phoronix.com/review/nvidia-rtx3080ti-linux/6, 2021-06-02, driver 465.31). No primary source gives per-test power or clock data.

### Running from a systemd user unit
- **Focus does not matter in timedemo.** Without timedemo, an unfocused window is capped at 60 FPS and a minimised one renders nothing (`main.c` L3209-3216, L3256-3259).
- **Vsync is off by default.** `vid_vsync` is 0, which gives `IMMEDIATE` present mode, or `MAILBOX` when IMMEDIATE is missing (`vkpt/main.c` L676-690, L3770).
- **Sound.** `+set s_enable 0` turns it off (`client.md` "Sound Subsystem"). Since 1.4.0, it does not crash without a sound device (`changelog.md` L265).
- **Isolation.** Setting `XDG_DATA_HOME` for the unit keeps its config and logs out of the user's own Q2RTX data (`system.c` L257-277).

## Pitfalls
- **Q2RTX fatal errors block the process.** `Sys_Error` opens a modal `SDL_ShowSimpleMessageBox` before it exits (`system.c` L322-326). An unattended run can hang on the dialog. SIGTERM only sets a flag that the blocked main loop never reads (L159-164, L558). ctune therefore kills a pass that runs past `PASS_TIMEOUT_S` (120 s, about the length of 8 passes) with SIGTERM, then SIGKILL after 10 s.
  - "Device lost" exits without a dialog.
  - `com_fatal_error 1` turns ERR_DROP errors, such as a demo read failure, into fatal ones (`common.c` L916, L549-551).
- **Do not start Q2RTX through `q2rtx.sh`.** On first launch it runs `find-retail-paks.sh`, which asks a zenity question (`setup/q2rtx.sh` L23-27; `setup/find-retail-paks.sh` L29-56). Run the `q2rtx` binary directly, as PTS does. Whether the tarball ships `q2rtx.sh` is unconfirmed.
- **Set `nextserver quit`.** Without it, Q2RTX does not exit when the demo ends.
- **`+timedemo N` prints FPS only at the end.** A crash in a long N-run process leaves no per-run numbers. Relaunching with `+timedemo 1` per pass gives one exit code and one FPS line each time.
- **Superposition 1.0 and 1.1.** The PTS arguments were written for 1.0, which Unigine has repacked twice. Version 1.1 packs its data in `.ung` archives. The PTS `-data_path` and `-engine_config` paths may not work on 1.1.
- **Superposition cannot loop in Basic.** Looping means relaunching. A relaunch reloads the scene, so the load has gaps.
- **Neither tool checks its output.** A pass means only that the run did not crash or hang. Errors in RT-core or ROP results go unseen unless they crash the driver (Xid), so `gpu_xid` and the kernel log remain the main signal. gpu-burn remains the only test that checks results.
- **Screen blanking.** GNOME may blank or lock the screen during a multi-hour run. Whether rendering continues then is unconfirmed (see Open). ctune holds `gnome-session-inhibit --inhibit suspend` (`_inhibit_sleep` in `ctune/core/stress.py`); `--inhibit idle` is a valid action (`gnome-session-inhibit --help`, gnome-session 50.1).

## Comparison

| | Superposition Basic 1.1 | Quake II RTX 1.8.1 |
|---|---|---|
| RT cores | No (OpenGL 4.5 raster, screen-space GI) | Yes, `VK_KHR_ray_query` by default on NVIDIA, or `ray_tracing_pipeline` |
| Raster / ROP | Heavy | Light (UI and final blit; primary rays are traced) |
| CLI start | Engine binary with engine args (PTS); official CLI is Pro only | Yes, `+` cvars and commands |
| Loop | Relaunch only (stress loop: Advanced GUI or Pro CLI) | `+timedemo N` in one process, or relaunch |
| Licence | Proprietary EULA, personal use; no redistribution; automation through the engine binary is unclear | GPL v2 engine; free shareware data from NVIDIA; no purchase |
| Failure signal | Crash or hang; score file `~/.Superposition/automation/log*.txt`; exit codes unconfirmed | Exit 1 on fatal error or device lost (fatal error first shows a blocking dialog); `console.log` with `Device lost!`, `FATAL:` and the FPS line |
| Checks results | No | No |
| Download | 1.64 GB `.run` (Makeself 2.4.0, gzip tar, 2.7 GB unpacked) | 1.06 GB `.tar.gz` |

## Open
The live runs (see Decision) answered these questions. Q2RTX runs on Bazzite in the sniper runtime, headless in gamescope from a user unit, with no desktop window and no dependence on the screen lock. They also measured the pass length, and the power, clocks and `at_lock_share` at the 400 W limit.

These questions are still open:
- Superposition 1.1 (not used): whether the PTS engine arguments work on 1.1, `-mode 2 -preset 0`, `-sound_app null`, exit codes and the engine log, `superposition_cli` without a Pro key, `postinstall.sh`, and the licence question for scripted runs under Basic.
- Q2RTX:
  - Whether the `Sys_Error` dialog appears under gamescope's headless backend, or the process just exits. Either way the pass timeout ends it.
  - Whether `SDL_VIDEODRIVER=wayland` works. A headless run does not need it.
- Whether any counter ctune can read shows the RT-core load. NVML has no RT-core utilisation, so this would need Nsight.
