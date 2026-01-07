# Build Plan: ARM Binary for Kindle

## Summary of Attempts (2026-01-07)

All automated CI build approaches have failed due to fundamental limitations with QEMU ARMv7 emulation and GitHub Actions time constraints.

## Failed Approaches

### PR #3: Nuitka + QEMU Emulation ❌
**Approach**: Build from source (OpenSSL, Python, Nuitka) on Debian Jessie under QEMU ARMv7 emulation

**Result**: GitHub Actions 6-hour timeout (twice)
- First attempt: 6h0m23s
- Second attempt (optimizations removed): 6h0m25s

**Build time breakdown**:
- patchelf: ~1-2 minutes
- OpenSSL 1.1.1w: ~30-40 minutes
- Python 3.10.14: ~2-3 hours
- Nuitka compilation: ~2+ hours
- **Total**: 5-6+ hours (exceeds GitHub Actions limit)

**Issues**:
- QEMU adds ~5-10x compilation overhead
- No way to speed up enough to fit in 6-hour window
- Optimizations (PGO, low-memory) only saved ~1 hour

### PR #8: Cross-Compilation ❌
**Approach**: Use x86_64 runners with ARM cross-compilation toolchain

**Result**: Failed - Nuitka incompatible with cross-compilation
```
OSError: [Errno 8] Exec format error: '/opt/python/bin/python3'
```

**Issues**:
- Nuitka requires executing target Python during compilation
- Can't run ARM binaries on x86_64
- `--python-for-scons` doesn't actually support cross-compilation

### PR #8: Zipapp Packaging ❌
**Approach**: Create Python zipapp with `shiv` (no C compilation)

**Result**: Failed - Rust dependencies can't compile under QEMU
```
qemu-arm: Could not open '/lib/ld-linux-armhf.so.3': No such file or directory
Cargo, the Rust package manager, is not installed or is not on PATH
```

**Issues**:
- Bumble depends on cryptography (Rust package)
- QEMU can't execute ARM Rust toolchain installer
- Missing ARM libraries for QEMU

## Root Cause Analysis

**The fundamental problem**: Building for ARM v7 on GitHub Actions CI is not viable because:

1. **QEMU emulation is too slow**
   - ~5-10x overhead for CPU-intensive compilation
   - 6-hour timeout is insufficient for from-source builds

2. **Cross-compilation doesn't work**
   - Nuitka/PyInstaller need to execute target Python
   - Can't run ARM binaries on x86_64 host

3. **Native extensions fail under QEMU**
   - Missing ARM libraries (`ld-linux-armhf.so.3`)
   - Rust/C compilation breaks in emulated environment

4. **Pre-built wheels don't exist**
   - No ARMv7 wheels for bumble dependencies
   - Would need to compile cryptography/other Rust packages

## Viable Solutions

### Option 1: Local Docker Build ⭐ **Recommended**
Build locally where time limits don't apply:

```bash
docker build --platform linux/arm/v7 -f Dockerfile.arm -t kindle-hid-passthrough-builder .
docker create --name temp kindle-hid-passthrough-builder
docker cp temp:/build/kindle-hid-passthrough ./kindle-hid-passthrough
docker rm temp
```

**Pros**:
- No time limits
- Build completes in ~1-1.5 hours locally
- Uses same Dockerfile as CI

**Cons**:
- Manual process
- Requires Docker on local machine
- No automated releases

### Option 2: Use PR #4 or PR #5 Approaches
Check if these alternative approaches work:

- **PR #4**: Makeself self-extracting archive + pre-built Python
- **PR #5**: Vendor bumble library (avoid dependency compilation)

These may avoid the Rust compilation issue.

### Option 3: Self-Hosted ARM Runner
Set up dedicated ARM hardware for builds:

- Use Raspberry Pi 4, Orange Pi, or ARM cloud instance
- Run GitHub Actions runner on ARM hardware
- No QEMU emulation overhead
- No timeout issues

**Pros**:
- Fast native ARM compilation
- Automated CI/CD
- No GitHub Actions time limits

**Cons**:
- Infrastructure cost/maintenance
- Security considerations for self-hosted runners
- Need 24/7 available hardware

### Option 4: Simplify Dependencies
Reduce or eliminate dependencies that require compilation:

- Fork bumble and remove cryptography dependency
- Use pure-Python alternatives
- Reduce scope of application

**Pros**:
- Could work with zipapp approach
- Fast builds

**Cons**:
- May break functionality
- Maintenance burden
- Not addressing root cause

## Recommendations

1. **Short term**: Build locally with Docker (Option 1)
   - Fastest path to working binary
   - Can manually upload to GitHub releases
   - Proven to work (just takes time)

2. **Medium term**: Check PR #4/PR #5 approaches
   - May avoid Rust compilation
   - Worth investigating before building infrastructure

3. **Long term**: Consider self-hosted runner if frequent builds needed
   - One-time setup for ongoing automation
   - Best for active development

## Technical Specifications

### Kindle Requirements
- **Architecture**: ARMv7 hard-float (armhf)
- **glibc**: 2.20 (from 2014)
- **Compatible distros**: Debian Jessie (glibc 2.19)

### Build Environment
- **QEMU overhead**: ~5-10x for compilation
- **GitHub Actions limit**: 6 hours per job
- **Estimated build time**:
  - With QEMU: 5-6 hours
  - Native ARM: ~30-60 minutes
  - Local x86_64 with QEMU: ~1-1.5 hours

## Files Modified (PRs #3, #8)

- `Dockerfile.arm`: Debian Jessie with from-source build
- `Dockerfile.cross`: x86_64 cross-compilation (failed)
- `Dockerfile.zipapp`: Alpine Linux zipapp (failed)
- `.github/workflows/build-arm.yml`: CI configuration
- `BUILD_PLAN.md`: This file

## Lessons Learned

1. **glibc compatibility matters**: Kindle's old glibc requires old distros
2. **QEMU is slow but works**: Just needs more time than CI allows
3. **Cross-compilation is complex**: Not compatible with Python packaging tools
4. **Native extensions are problematic**: Rust/C deps fail under QEMU
5. **Time estimation is hard**: ARM builds take much longer than expected
6. **CI has limits**: Not all builds fit in cloud CI constraints

## Next Steps

User should decide:
1. Build locally for immediate solution?
2. Investigate PR #4/PR #5 for CI-compatible approach?
3. Set up self-hosted runner for long-term automation?
