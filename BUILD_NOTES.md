# Build Notes - ARM Binary Compilation Journey

## Problem Statement
Need to build `kindle-hid-passthrough` ARM binary that:
- Runs on Kindle (old Linux kernel 4.9.77)
- Is portable (minimal external dependencies)
- Compiles successfully (ARM32 has branch range limitations)

## Key Constraint: ARM32 Branch Range Error
**The Core Issue:** ARM32 conditional branches can only jump ±32MB. When Nuitka compiles large Python modules (especially `bumble.hci`), the resulting C code exceeds this limit.

**Error signature:**
```
{standard input}:109471: Error: conditional branch out of range
scons: *** [module.bumble.hci.o] Error 1
```

## Attempts Log

### Attempt 1: PyInstaller
**Status:** ❌ Failed  
**Reason:** Did not work (exact reason not documented, but mentioned "we tried PyInstaller before and it did not work")  
**Date:** Before this session

### Attempt 2: Musl + Alpine (Dynamic)
**Status:** ✅ Builds, ❌ Won't run  
**Config:** Alpine Linux, Python 3.11, Nuitka onefile  
**Commit:** f7a3e57  
**Result:** Build succeeds but requires `/lib/ld-musl-armhf.so.1` on target system  
**Error on Kindle:** `execv: No such file or directory`  
**Lesson:** Musl binaries need the musl loader, which Kindle doesn't have

### Attempt 3: Musl + staticx
**Status:** ❌ Failed  
**Commits:** 3a42f35, f10a3ed  
**Goal:** Use staticx to bundle musl loader into binary  
**Result:** Still hit ARM32 branch range error (same 109,471 assembly lines)  
**Lesson:** staticx doesn't solve the underlying branch range problem - just bundles the loader

### Attempt 4: Add -Os Optimization
**Status:** ❌ Failed  
**Reasoning:** Thought smaller code would help with branch range  
**Result:** Still exceeded branch range limit  
**Lesson:** -Os alone doesn't reduce the problem enough

### Attempt 5: Exclude Unused Transports
**Status:** ❌ Failed  
**Commit:** f10a3ed  
**Changes:** Added `--nofollow-import-to` for grpc, websockets, USB, serial, etc.  
**Result:** Still hit branch range error (109,471 lines unchanged)  
**Lesson:** The problem is `bumble.hci` itself, not the transports

### Attempt 6: Single-threaded Compilation
**Status:** ❌ Failed  
**Change:** `--jobs=$(nproc)` → `--jobs=1`  
**Reasoning:** Thought parallel builds might cause issues  
**Result:** Broke working configuration  
**Lesson:** Parallel compilation (`--jobs=$(nproc)`) is required for Nuitka to work properly

### Attempt 7: Debian Buster + glibc (Current)
**Status:** 🔄 In Progress  
**Branch:** glibc-static-build  
**PR:** #17  
**Reasoning:** Use glibc (which Kindle already has) instead of musl  
**Config:**
- Debian Buster (glibc 2.28)
- Nuitka onefile
- Dynamically linked to glibc
**Issues encountered:**
1. Debian Buster is EOL → repos moved to archive.debian.org (fixed)
2. Build currently running...

## What We Know

### ✅ What Works
- Nuitka onefile with parallel compilation (`--jobs=$(nproc)`)
- ARM-specific compiler flags: `-mword-relocations -mlong-calls -ffunction-sections -fdata-sections`
- Linker garbage collection: `-Wl,--gc-sections`
- C wrapper for `LD_PRELOAD` of syscall wrapper
- Hetzner CAX41 runners with 28GB RAM + 30GB swap

### ❌ What Doesn't Work
- PyInstaller (reason unclear)
- Musl without static linking (needs loader)
- staticx on ARM32 (branch range errors)
- `-Os` optimization alone
- Excluding unused imports (doesn't shrink bumble.hci)
- Single-threaded Nuitka builds

### 🤔 Unknown/Untested
- Whether glibc 2.28 (Debian Buster) is old enough for Kindle
- What glibc version Kindle actually has (need: `ssh kindle "ldd --version"`)
- Whether Debian Bullseye (glibc 2.31) would work
- Whether Debian Jessie (glibc 2.19) is too old for Python 3.11

## The Fundamental Problem
`bumble.hci` module compiles to >109,000 assembly lines. This is inherent to the module's complexity (Bluetooth HCI protocol implementation). No amount of optimization flags or exclusions will shrink it enough to fit within ARM32's 32MB branch range when using Nuitka.

## Possible Solutions (Not Yet Tried)

### Option A: Zipapp + Shell Wrapper
- Ship Python source code as zipapp
- No compilation → no branch range issues
- Still portable (bundle everything)
- Requires Python interpreter on Kindle

### Option B: Split Compilation
- Break bumble.hci into smaller modules before compiling
- Requires modifying bumble source
- Very invasive

### Option C: Switch to glibc (Current Approach)
- Accept dynamic linking to glibc
- Use old enough Debian version for Kindle compatibility
- Simpler, more maintainable
- **Critical Question:** What glibc version does Kindle have?

## Critical Information Needed
1. **Kindle glibc version:** `ssh kindle "ldd --version | head -1"`
2. **Exact PyInstaller failure:** Why didn't it work?
3. **Kindle system info:** Kernel version, libc version, architecture details

## Repository State
- **main branch:** Working version with Debian Bookworm + glibc
- **pr-16 (fix/use-older-glibc):** Musl attempts, various failures
- **glibc-static-build:** Current attempt with Debian Buster

## Commands for Future Reference

### Check build status:
```bash
gh run list --branch BRANCH_NAME --limit 5
gh run view RUN_ID --log-failed
```

### Check binary dependencies:
```bash
ldd binary_name                    # Show dynamic dependencies
file binary_name                   # Show binary type
readelf -d binary_name | grep NEED # Show required libraries
```

### Test on Kindle:
```bash
ssh kindle "file /path/to/binary"
ssh kindle "ldd /path/to/binary"
ssh kindle "./binary --help"
```

## Last Updated
2026-02-02 07:32 GMT-3
