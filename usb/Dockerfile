# Build environment for the Onionmind live image, so you don't need a Debian
# box. See usb/README.md for the full story.
#
#   docker build -f usb/Dockerfile -t onionmind-usb .
#   docker run --rm --privileged \
#     -v "$(pwd)/usb/cache:/onionmind/usb/cache" \
#     -v "$(pwd)/usb/out:/onionmind/usb/out" \
#     onionmind-usb 12gb
#
# --privileged: live-build chroots and bind-mounts /proc, /sys and /dev inside
# the image it is assembling; an unprivileged container can't do that.
FROM debian:trixie
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      live-build curl ca-certificates zstd sudo \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /onionmind
COPY . /onionmind
ENTRYPOINT ["/onionmind/usb/build.sh"]
