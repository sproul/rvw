#!/bin/bash
set -e -o pipefail
# here
[[ $(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of 'csv=p=0:s=x' "$HOME/k.png") == 3840x2160 ]] || {
    printf 'FAIL expected a 3840x2160 image at %s\n' "$HOME/k.png" >&2
    exit 1
}
output_dir=$(mktemp -d /tmp/rvw-elgato.XXXXXX)
ffmpeg -hide_banner -loglevel error -i "$HOME/k.png" \
    -vf format=nv12 -frames:v 1 -f rawvideo "$output_dir/k.nv12"
ffmpeg -hide_banner -loglevel error -f rawvideo -pixel_format nv12 \
    -video_size 3840x2160 -i "$output_dir/k.nv12" \
    -frames:v 1 "$output_dir/k_after_nv12.png"
printf 'OK simulated capture: %s\n' "$output_dir/k_after_nv12.png"
exit