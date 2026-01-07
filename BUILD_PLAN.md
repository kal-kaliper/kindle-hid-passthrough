# Build Plan: Exclude Bumble from Nuitka Compilation

## Goal
Fix ARM "conditional branch out of range" error by excluding the large `bumble` library from Nuitka's C compilation. Bumble will be included as Python bytecode instead.

## Background
The `bumble.hci` module generates ~800K lines of assembly when compiled by Nuitka, exceeding ARM's conditional branch range limits. By excluding bumble from compilation, it stays as compact bytecode while our code gets compiled.

## Implementation Steps

### 1. Update Dockerfile.arm
Add `--nofollow-import-to=bumble` to Nuitka command:

```dockerfile
RUN python3 -m nuitka \
    --mode=onefile \
    --jobs=$(nproc) \
    --lto=no \
    --low-memory \
    --show-progress \
    --output-filename=kindle-hid-passthrough \
    --include-package=kindle_hid_passthrough \
    --include-package=bumble \
    --nofollow-import-to=bumble \
    --include-data-file=kindle_hid_passthrough/config.ini=kindle_hid_passthrough/config.ini \
    --nofollow-import-to=pytest \
    --nofollow-import-to=unittest \
    --nofollow-import-to=setuptools \
    kindle_hid_passthrough/main.py
```

Key changes:
- `--include-package=bumble`: Include bumble package files
- `--nofollow-import-to=bumble`: Don't compile bumble to C, keep as bytecode

### 2. Update build-arm.yml
Use native ARM64 runner for faster builds:
- Change `runs-on: ubuntu-latest` to `runs-on: ubuntu-24.04-arm`
- Remove QEMU setup steps
- Add `--platform linux/arm/v7` to docker build

### 3. Simplify CFLAGS
With bumble excluded, we may not need aggressive optimization reduction:
```dockerfile
ENV CFLAGS="-mword-relocations"
# CCFLAGS and LDFLAGS may not be needed anymore
```

### 4. Test
- Push branch and create PR
- Verify CI builds successfully
- Download artifact and test on Kindle

## Expected Outcome
- Faster compilation (bumble.hci won't be compiled to C)
- Smaller generated C code
- No branch range errors
- Binary still works (bumble runs as bytecode)

## Commands to Execute
```bash
cd /home/lzampier/Clone/kindle-hid-exclude-bumble
# Edit Dockerfile.arm and build-arm.yml as described above
git add -A && git commit -s -m "Exclude bumble from Nuitka compilation"
git push -u origin build/exclude-bumble
gh pr create --title "Build: Exclude bumble from Nuitka compilation" --body "..."
```

---

## Results (2026-01-07)

### What Worked ✅
- Successfully excluded bumble from Nuitka C compilation using `--nofollow-import-to=bumble`
- Fixed patchelf 0.12 extraction (directory name mismatch - used wildcard `cd patchelf-0.12*`)
- Fixed OpenSSL 32-bit ARM compilation (switched from `./config` to `./Configure linux-generic32`)
- Removed slow optimization flags (`--enable-optimizations` for Python, `--low-memory` for Nuitka)
- Native ARM64 runner configuration working

### What Failed ❌
**GitHub Actions 6-hour timeout exceeded**

Building from source on Debian Jessie (for glibc 2.19/2.20 compatibility) under QEMU ARMv7 emulation is too slow:

**Build time breakdown:**
- patchelf compilation: ~1-2 minutes
- OpenSSL 1.1.1w compilation: ~30-40 minutes
- Python 3.10.14 compilation: ~2-3 hours (without PGO)
- Nuitka compilation + packaging: ~2+ hours
- **Total: 5-6+ hours**

**Two build attempts both hit 6-hour timeout:**
- First attempt: 6h0m23s
- Second attempt: 6h0m25s (with optimization flags removed)

### Why QEMU Emulation is Slow
The `ubuntu-24.04-arm` runner is ARM64, but Kindle requires ARMv7 32-bit. Docker's `--platform linux/arm/v7` uses QEMU user-mode emulation which adds ~5-10x overhead for CPU-intensive compilation.

### Recommended Alternative Approaches

**1. Cross-compilation** (Recommended - ~1-1.5 hours)
- Use `arm-linux-gnueabihf-gcc` toolchains on x86_64 runners
- Cross-compile OpenSSL, Python, and final binary
- No emulation overhead
- Estimated build time: 1-1.5 hours

**2. Pre-built Python binaries** (~2-3 hours)
- Use python-build-standalone for pre-compiled Python
- Only compile OpenSSL and Nuitka binary
- Eliminates longest build stage
- Still uses emulation but much faster

**3. PyInstaller instead of Nuitka** (Unknown - likely still too slow)
- Faster to build than Nuitka
- Larger binaries
- May still exceed 6-hour limit with from-source Python

**4. Self-hosted ARM runner** (No timeout)
- Use physical ARMv7 hardware or ARM cloud instance
- No emulation overhead
- No GitHub Actions timeout limits

**5. Local Docker build** (30-60 minutes)
- Build locally where time limits don't apply
- `docker build --platform linux/arm/v7 -f Dockerfile.arm .`
- Manual artifact upload

### Technical Lessons Learned

1. **glibc compatibility matters**: Kindle's glibc 2.20 requires building on old distro (Debian Jessie)
2. **QEMU emulation is slow**: ~5-10x overhead makes CI builds impractical
3. **Optimization flags matter**: Removing PGO saved ~1 hour but not enough
4. **ARM branch range is real**: The original issue (bumble compilation) was valid
5. **Build time estimation**: 32-bit ARM builds from source take much longer than expected

### PR Status
- PR #3: Closed (build timeout)
- Branch: `build/exclude-bumble`
- All commits preserved for future reference
