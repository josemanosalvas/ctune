# The GPU screen

`gpu_test` screen (about 44 min) runs these parts in order. Each has its own telemetry, with `at_lock_share` once a `max_clock` is applied this boot. `limiter_share.power cap` near 1 means the power limit sets the clock; an undervolt lowers it.

- **`fp32`** (600 s): gpu-burn SGEMM with results compared bitwise, so an undervolt that computes wrong without crashing turns the step red. A power-limited card runs it at the power cap in every step (`at_lock_share` near 0), so an undervolt shows here as more MHz at the same heat. Its median is the score (`score_gflops`).
- **`fp64`** (600 s): the load that runs at the lock below the cap (`at_lock_share` near 1). It tests the top V/F point, and it is where an undervolt shows as less heat and noise: compare `power_w_avg`, `temp_c_end` and `fan_pct_end` between steps. `*_end` averages the part's last 60 s, once the cooler has caught up.
- **`tc`** (120 s): tensor cores, with results compared. It runs at the power cap.
- **`transient`** (180 s): FP32 with the container frozen 1 s in every 10. In each pause the GPU idles (about 120 W) and boosts to the lock; each thaw steps the load straight back to full at that clock, where undervolts tend to crash. `transient.pauses` counts the cycles, and a run that never paused is inconclusive. `telemetry.paused.core_mhz_avg` is the clock the load came back at.
- **`graphics`** (300 s): Quake II RTX path tracing on the RT cores, headless in gamescope at 2560×1440, the part closest to a game. It runs at the lock and reaches the power cap, so like FP32 it shows an undervolt as clock rather than heat. Nothing it renders is checked: it is red only on `Device lost!`, a crash, a hang or an Xid. `fps_median` is for information.
- **`raster`** (300 s): Unigine Superposition at Extreme shaders, OpenGL, headless at 2560×1440. It loads the ROPs and texture units that gpu-burn and Quake II RTX leave idle, and is the part closest to most games. It reaches the power cap and is judged like `graphics`; `score_median` and `fps_median` are for information.
- **`tessellation`** (300 s): Unigine Heaven at its Extreme preset (Ultra quality, extreme tessellation, 8x MSAA), OpenGL, headless at 2560×1440, for the geometry and tessellation units and the ROPs under MSAA. Judged like `raster`.
- **`vram`** (300 s): memtest_vulkan.

The graphics, raster and tessellation parts cover the RT cores, texture units, ROPs and tessellation units, and none checks what it draws, so games stay the last check.

## Reading a result
- **`clock_stretch`** compares GFLOPS per MHz of `fp32`, `fp64` and `tc` with the last green result at the same `max_clock`. A part in `stretched` did more than 1 % less work per MHz.
- **`blocked_by`** names what made a part inconclusive: VRAM another app holds (ask the user to close it), another ctune test on the GPU (wait for it), a missing Quake II RTX (`ctune gpu q2rtx-setup` once), or a missing Superposition or Heaven (`ctune gpu unigine-setup` once).
