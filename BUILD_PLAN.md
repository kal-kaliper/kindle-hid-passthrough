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
