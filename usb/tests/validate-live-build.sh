# Validates the exact `lb config` flag set usb/build.sh uses, against debian:trixie.
# Run via: docker run --rm -v "<repo>/usb/tests:/t" debian:trixie sh /t/validate-live-build.sh
set -e
apt-get update -qq >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq live-build >/dev/null 2>&1
# The live image needs non-free for nvidia-driver; prove the areas + names resolve.
echo 'deb http://deb.debian.org/debian trixie main contrib non-free non-free-firmware' \
    > /etc/apt/sources.list.d/nonfree.list
apt-get update -qq >/dev/null
cd /tmp
lb config \
  --architecture amd64 \
  --distribution trixie \
  --archive-areas 'main contrib non-free non-free-firmware' \
  --debian-installer none \
  --memtest none \
  --chroot-squashfs-compression-type zstd \
  --firmware-chroot true \
  --iso-application 'Onionmind Live' \
  --iso-volume ONIONMIND \
  --bootappend-live 'boot=live components hostname=onionmind username=onion' \
  && echo CONFIG_OK
echo '--- package candidates on trixie ---'
for p in nvidia-driver linux-headers-amd64 python3-socks obfs4proxy live-boot \
         live-config live-config-systemd network-manager firmware-misc-nonfree \
         nftables tor sudo; do
  printf '%-26s ' "$p"; apt-cache policy "$p" 2>/dev/null | sed -n 3p
done
