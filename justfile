# Justfile for Kindle HID Passthrough
# Usage: just <recipe>

src_dir := justfile_directory()
remote_dir := "/mnt/us/kindle_hid_passthrough"
waf_dir := "/mnt/us/kindle_hid_passthrough/illusion/BTManager"
upstart_conf := "/etc/upstart/hid-passthrough.conf"
log_file := "/var/log/hid_passthrough.log"
python := "/mnt/us/python3.10-kindle/python3-wrapper.sh"

default:
    @just --list

# Deploy to Kindle over SSH and start API server
deploy:
    @echo "Deploying to Kindle..."
    @just kill
    @echo "Writing build SHA..."
    git -C {{src_dir}} rev-parse --short HEAD > {{src_dir}}/kindle_hid_passthrough/BUILD_SHA
    @echo "Remounting filesystems as writable..."
    ssh kindle "/usr/sbin/mntroot rw && mount -o remount,rw /mnt/base-us"
    @echo "Copying all files via tar pipe..."
    (cd {{src_dir}} && tar cf - \
        --transform='s|^kindle_hid_passthrough/hid-passthrough-dev.upstart|etc/upstart/hid-passthrough.conf|' \
        --transform='s|^kindle_hid_passthrough/|mnt/us/kindle_hid_passthrough/|' \
        --transform='s|^assets/99-hid-keyboard.rules|etc/udev/rules.d/99-hid-keyboard.rules|' \
        --transform='s|^scripts/dev_is_keyboard.sh|usr/local/bin/dev_is_keyboard.sh|' \
        --transform='s|^illusion/BTManager/|mnt/us/kindle_hid_passthrough/illusion/BTManager/|' \
        kindle_hid_passthrough/*.py \
        kindle_hid_passthrough/config.ini \
        kindle_hid_passthrough/BUILD_SHA \
        kindle_hid_passthrough/hid-passthrough-dev.upstart \
        assets/99-hid-keyboard.rules \
        scripts/dev_is_keyboard.sh \
        illusion/BTManager/* \
    ) | ssh kindle "mkdir -p /usr/local/bin && tar xf - -C /"
    ssh kindle "chmod +x /usr/local/bin/dev_is_keyboard.sh"
    -ssh kindle "udevadm control --reload-rules" 2>/dev/null || true
    @echo "Clearing Python bytecode cache..."
    ssh kindle "rm -rf {{remote_dir}}/__pycache__"
    @echo "Creating cache directory..."
    ssh kindle "mkdir -p {{remote_dir}}/cache"
    @echo "Starting API server..."
    @just server
    @sleep 8
    ssh kindle 'lipc-set-prop com.lab126.appmgrd start app://com.lzampier.btmanager'
    @echo "Deployment complete!"

# Kill daemon and close WAF app
kill:
    -ssh kindle 'lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.home 2>/dev/null; \
        /sbin/initctl stop hid-passthrough 2>/dev/null; \
        pkill -9 -f "main.py --daemon" 2>/dev/null; \
        pkill -9 -f daemon.py 2>/dev/null; \
        true'
    @echo "All processes stopped."

# Start daemon + API server
server:
    -ssh kindle "pkill -9 -f 'main.py --daemon'" 2>/dev/null || true
    ssh kindle "sleep 2 && /mnt/us/python3.10-kindle/python3-wrapper.sh /mnt/us/kindle_hid_passthrough/main.py --daemon > /dev/null 2>&1 &"
    @echo "Daemon + API starting (takes ~8s on Kindle)."

# Check daemon status
status:
    ssh kindle "/sbin/initctl status hid-passthrough"

# View daemon logs
logs:
    ssh kindle "tail -f {{log_file}}"

# View recent logs
logs-recent:
    ssh kindle "tail -n 50 {{log_file}}"

# Restart daemon
restart:
    ssh kindle "/sbin/initctl restart hid-passthrough"

# Stop daemon
stop:
    ssh kindle "/sbin/initctl stop hid-passthrough"

# Start daemon
start:
    ssh kindle "/sbin/initctl start hid-passthrough"

# Clear cache
clear-cache:
    ssh kindle "rm -rf {{remote_dir}}/cache/*.json"
    @echo "Cache cleared!"

# Show cache
show-cache:
    ssh kindle "ls -lh {{remote_dir}}/cache/ 2>/dev/null || echo 'Empty'"

# Show configured devices
devices:
    @ssh kindle "cat {{remote_dir}}/devices.conf 2>/dev/null || echo 'No devices configured'"

# Edit devices.conf
edit-devices:
    ssh kindle "vi {{remote_dir}}/devices.conf"

# Show pairing keys
keys:
    @ssh kindle "cat {{remote_dir}}/cache/pairing_keys.json 2>/dev/null | python3 -m json.tool || echo 'No pairing keys'"

# SSH into Kindle
ssh:
    ssh kindle

# Check Python syntax
check:
    python3 -m py_compile {{src_dir}}/kindle_hid_passthrough/*.py
    @echo "All files compile OK!"

# Run mock API server for local WAF app testing
mock-server:
    python3 {{src_dir}}/tests/mock_api_server.py

# Deploy and follow logs
deploy-watch: deploy
    @just logs

# Pair a new device (Classic)
pair-classic:
    ssh kindle "{{python}} {{remote_dir}}/main.py --pair --protocol classic"

# Pair a new device (BLE)
pair-ble:
    ssh kindle "{{python}} {{remote_dir}}/main.py --pair --protocol ble"

# Run manually (for debugging)
run:
    ssh kindle "{{python}} {{remote_dir}}/main.py"

# Remove autostart (removes upstart config)
remove-autostart:
    @echo "Removing autostart..."
    ssh kindle "/usr/sbin/mntroot rw"
    ssh kindle "rm -f {{upstart_conf}}"
    @echo "Autostart removed."

# --- Install from pre-built tarball ---

repo := "zampierilucas/kindle-hid-passthrough"
artifact_name := "kindle-hid-passthrough-armv7"
tarball_name := "kindle-hid-passthrough-armv7.tar.gz"

# Install a pre-built tarball onto Kindle over SSH
_install-tarball tarball:
    @echo "Installing {{tarball}} to Kindle..."
    @just kill
    @echo "Remounting filesystems as writable..."
    ssh kindle "/usr/sbin/mntroot rw && mount -o remount,rw /mnt/base-us"
    @echo "Extracting files to Kindle..."
    ssh kindle "mkdir -p {{remote_dir}}"
    cat {{tarball}} | ssh kindle "tar xzf - -C {{remote_dir}}"
    @echo "Installing system files..."
    ssh kindle "mkdir -p /usr/local/bin && \
        cp {{remote_dir}}/assets/hid-passthrough.upstart /etc/upstart/hid-passthrough.conf && \
        cp {{remote_dir}}/scripts/dev_is_keyboard.sh /usr/local/bin/dev_is_keyboard.sh && \
        chmod +x /usr/local/bin/dev_is_keyboard.sh && \
        cp {{remote_dir}}/assets/99-hid-keyboard.rules /etc/udev/rules.d/ && \
        /usr/sbin/udevadm control --reload-rules"
    @echo "Installing WAF app..."
    ssh kindle "cd {{remote_dir}} && sh illusion/install-waf-app.sh"
    @echo "Remounting read-only..."
    ssh kindle "/usr/sbin/mntroot ro"
    @echo "Starting daemon..."
    ssh kindle "/sbin/initctl start hid-passthrough"
    @sleep 8
    ssh kindle 'lipc-set-prop com.lab126.appmgrd start app://com.lzampier.btmanager'
    @echo "Install complete!"

# Install from GitHub release (default: latest)
install-release version="":
    #!/usr/bin/env bash
    set -euo pipefail
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT
    version="{{version}}"
    if [ -z "$version" ]; then
        echo "Fetching latest release..."
        version=$(curl -sfL "https://api.github.com/repos/{{repo}}/releases/latest" | grep '"tag_name"' | cut -d'"' -f4)
        if [ -z "$version" ]; then
            echo "ERROR: Could not determine latest release version" >&2
            exit 1
        fi
    fi
    echo "Downloading release $version..."
    curl -sfL -o "$tmpdir/{{tarball_name}}" \
        "https://github.com/{{repo}}/releases/download/${version}/{{tarball_name}}"
    echo "Downloaded to $tmpdir/{{tarball_name}}"
    just _install-tarball "$tmpdir/{{tarball_name}}"

# Install from latest CI build artifact
install-ci branch="main":
    #!/usr/bin/env bash
    set -euo pipefail
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT
    echo "Finding latest successful CI run on {{branch}}..."
    run_id=$(gh run list \
        --repo "{{repo}}" \
        --workflow build-arm.yml \
        --branch "{{branch}}" \
        --status success \
        --limit 1 \
        --json databaseId \
        --jq '.[0].databaseId')
    if [ -z "$run_id" ] || [ "$run_id" = "null" ]; then
        echo "ERROR: No successful CI run found on branch '{{branch}}'" >&2
        exit 1
    fi
    echo "Downloading artifact from run $run_id..."
    gh run download "$run_id" \
        --repo "{{repo}}" \
        --name "{{artifact_name}}" \
        --dir "$tmpdir"
    tarball=$(find "$tmpdir" -name "{{tarball_name}}" -print -quit)
    if [ -z "$tarball" ]; then
        echo "ERROR: Tarball not found in downloaded artifact" >&2
        ls -la "$tmpdir"/
        exit 1
    fi
    just _install-tarball "$tarball"

# Uninstall system integration (upstart, udev, WAF app) but leave code in place
uninstall:
    @echo "Uninstalling system integration..."
    @just kill
    @echo "Remounting filesystems as writable..."
    ssh kindle "/usr/sbin/mntroot rw"
    @echo "Removing upstart config..."
    -ssh kindle "rm -f {{upstart_conf}}"
    @echo "Removing udev rules..."
    -ssh kindle "rm -f /etc/udev/rules.d/99-hid-keyboard.rules"
    -ssh kindle "/usr/sbin/udevadm control --reload-rules"
    @echo "Removing helper script..."
    -ssh kindle "rm -f /usr/local/bin/dev_is_keyboard.sh"
    @echo "Removing WAF app scriptlet..."
    -ssh kindle "rm -f /mnt/us/documents/BTManager.sh"
    @echo "Remounting read-only..."
    -ssh kindle "/usr/sbin/mntroot ro"
    @echo "Uninstall complete. Code left at {{remote_dir}}/"
