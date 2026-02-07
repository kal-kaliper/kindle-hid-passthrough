# Build Notes - ARM Binary for Kindle

## Target
Kindle Paperwhite with kernel 4.9.77-lab126, glibc 2.20, ARMv7 hard-float.

## Working Solution
**Nuitka standalone + bundled ld-linux + bundled glibc from Debian Bookworm.**

The C wrapper invokes the bundled dynamic linker directly, completely bypassing the Kindle's ancient system libraries:

```
./kindle-hid-passthrough  →  dist/ld-linux-armhf.so.3 --library-path dist dist/main.bin
```

### Output Structure
```
kindle-hid-passthrough       # Static C wrapper (entry point)
libsyscall_wrapper.so        # Shim for preadv2/pwritev2 (missing on kernel 4.9)
config.ini
hid-passthrough.upstart
dist/
  ld-linux-armhf.so.3        # Bundled dynamic linker (glibc 2.36)
  libc.so.6, libm.so.6, ...  # Bundled glibc 2.36 core libs
  main.bin                    # Nuitka standalone Python binary
  *.so                        # Python C extension shared libraries
```

### Build
```bash
docker build --platform linux/arm/v7 -f .github/Dockerfile.arm -t builder --load .
```
CI runs on `ubuntu-24.04-arm` GitHub runners.

## Key Constraints

### ARM32 Branch Range
ARM32 conditional branches limited to ±32MB. `bumble.hci` exceeds this.
Fix: `CFLAGS="-mword-relocations -mlong-calls -ffunction-sections -fdata-sections"`

### Kindle Filesystem
- `/mnt/us` — FUSE, very slow writes, can store files but staticx can't extract here
- `/tmp` — 64MB tmpfs, usually full from system processes
- `/dev/shm` — 237MB tmpfs, empty — best for testing

### Syscall Compatibility
Kindle kernel 4.9.77 lacks `preadv2`/`pwritev2` (added in 4.16). A shared library shim
loaded via `LD_PRELOAD` falls back to `preadv`/`pwritev`.

## What Doesn't Work

| Approach | Why it fails |
|---|---|
| Dynamic linking (any) | Kindle's glibc 2.20 too old for modern builds |
| staticx + Nuitka onefile | staticx bootloader fails on kernel 4.9 ("couldn't find attached data header") |
| staticx + Nuitka standalone | staticx only wraps main.bin, misses all .so deps in dist/ |
| staticx --strip | Destroys Nuitka's appended onefile payload |
| Bundled ld-linux + Kindle's glibc | ld-linux 2.36 silently rejects glibc 2.20 libs |
| Nuitka onefile + compression | OOM on GitHub ARM runners during zstd packaging |
| PyInstaller | Failed (reason undocumented) |
| Hetzner CAX41 runners | OOM during Docker builds |

## Critical Lessons
- **ld-linux version must match bundled glibc** — version mismatch causes silent "file not found" errors
- **Nuitka standalone doesn't include glibc core libs** — must copy libc, libm, libpthread, etc. manually
- **Use `cp -L`** when bundling libs — many are symlinks that `cp` or `find -type f` will skip
- **`LD_DEBUG=libs`** is essential for diagnosing library loading issues on the Kindle

## Last Updated
2026-02-07
