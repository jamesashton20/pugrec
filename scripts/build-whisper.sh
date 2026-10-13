#!/usr/bin/env bash
# Build whisper.cpp's whisper-cli with the Vulkan backend (GPU transcription on AMD:
# RADV, no ROCm/CUDA) inside a distrobox, and copy it to ~/.cache/pugrec/bin.
# The host stays untouched (image-based PugPal: no compilers there, and no dnf).
#   scripts/build-whisper.sh            build (reuses the box if it exists)
#   distrobox rm -f pugrec-build        remove the box afterwards (the binary stays)
set -euo pipefail

VERSION=v1.9.4                      # pinned release (2026-09-11)
BOX=pugrec-build
IMAGE=registry.fedoraproject.org/fedora-toolbox:44   # same Fedora as the host: same libstdc++
cache="${XDG_CACHE_HOME:-$HOME/.cache}/pugrec"
src="$cache/build/whisper.cpp-$VERSION"
out="$cache/bin/whisper-cli-$VERSION"

if ! distrobox list | grep -qw "$BOX"; then
    distrobox create --yes --name "$BOX" --image "$IMAGE"
fi

mkdir -p "$cache/build" "$cache/bin"
[[ -d "$src" ]] || git clone --depth 1 --branch "$VERSION" https://github.com/ggml-org/whisper.cpp "$src"

distrobox enter "$BOX" -- bash -euo pipefail -c "
    sudo dnf install -y --setopt=install_weak_deps=False \
        cmake gcc-c++ make vulkan-headers vulkan-loader-devel glslc spirv-headers-devel >/dev/null
    cd '$src'
    cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON -DBUILD_SHARED_LIBS=OFF \
          -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON >/dev/null
    cmake --build build -j\$(nproc) --target whisper-cli
"
install -m 755 "$src/build/bin/whisper-cli" "$out"
echo "built $out"
ldd "$out" | grep -E 'vulkan|not found' || true
